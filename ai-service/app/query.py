"""
query.py — production-grade retrieval + generation pipeline
------------------------------------------------------------
Changes vs original:
  4.  BM25 + embedding hybrid via Reciprocal Rank Fusion (RRF)
  5.  Metadata filters  — domain + org_id pushed into Supabase RPC
  6.  Cross-encoder re-ranker (sentence-transformers ms-marco-MiniLM-L-6-v2)
  7.  Index partitioned by domain at query time (filter_domain param)
  8.  Semantic cache    — embed query, find nearest cached query by cosine sim
  9.  Embeddings precomputed at ingest; only query vector computed here
  10. top_k candidate pool = 10, rerank → keep 5, feed 3 to LLM
  11. Context compressed to ≤ 8 k chars (~2 k tokens) via extractive sentence scoring


from __future__ import annotations

import asyncio
import json
import re
from typing import Optional
import os
import ollama
import numpy as np
from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain.prompts import PromptTemplate

from app.config import settings
from app.db import db_rpc, db_insert, db_keyword_search
from app.cache import (
    get_cached, set_cached,
    get_semantic_cache, set_semantic_cache,
)
os.environ["OLLAMA_HOST"] = settings.ollama_base_url
# ──────────────────────────────────────────────
# Singletons
# ──────────────────────────────────────────────

_embedder:  Optional[OllamaEmbeddings] = None
_llm:       Optional[OllamaLLM]        = None
_reranker                               = None   # lazy-loaded cross-encoder


def get_embedder() -> OllamaEmbeddings:
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbeddings(
            model=settings.embed_model,
        )
    return _embedder


def get_llm() -> OllamaLLM:
    global _llm
    if _llm is None:
        _llm = OllamaLLM(
            model=settings.llm_model,
            temperature=0.1,
            num_predict=512,
            num_ctx=2048,
        )
    return _llm


def get_reranker():
    
    Lazy-load cross-encoder. Falls back to None gracefully if
    sentence-transformers is not installed — retrieval still works,
    just without the reranking step.
    
    global _reranker
    if _reranker is not None:
        return _reranker
    try:
        from sentence_transformers import CrossEncoder          # type: ignore
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        print("[reranker] cross-encoder loaded")
    except ImportError:
        print("[reranker] sentence-transformers not installed — skipping rerank")
        _reranker = False   # sentinel: don't try again
    return _reranker


# ──────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────

RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template=You are a concise assistant for SecureStream.
Answer using ONLY the context below.
Be brief — 2-4 sentences maximum.
If the answer is in the context, you MUST return it.
If not found at all, say exactly: "Not found in uploaded documents."

Context:
{context}

Question: {question}

Answer:,
)


# ──────────────────────────────────────────────
# 4. BM25 helpers
# ──────────────────────────────────────────────

STOP_WORDS = {
    "what","is","the","a","an","of","in","on","at","to","for",
    "and","or","are","was","were","has","have","does","do","did",
    "this","that","these","those","with","from","by","about","how",
    "when","where","who","which","can","will","there","any","all",
}


def extract_keywords(question: str) -> list[str]:
    words    = re.findall(r"\b[a-zA-Z]{4,}\b", question.lower())
    keywords = [w for w in words if w not in STOP_WORDS]
    partials = [w[:7] for w in keywords if len(w) >= 7]
    return list(set(keywords + partials))[:5]


def _bm25_score(query_terms: list[str], corpus: list[str]) -> list[float]:
    
    Pure-Python BM25 (Okapi).  No external lib — avoids rank_bm25 import
    pain on restricted envs.  Good enough for corpora < 200 chunks.

    If rank_bm25 is installed it takes over automatically (10× faster).
    
    try:
        from rank_bm25 import BM25Okapi                        # type: ignore
        tokenized = [re.findall(r"\w+", c.lower()) for c in corpus]
        bm        = BM25Okapi(tokenized)
        scores    = bm.get_scores(query_terms)
        return scores.tolist()
    except ImportError:
        pass

    # Fallback: TF-IDF-ish approximation
    import math
    tokenized = [re.findall(r"\w+", c.lower()) for c in corpus]
    N         = len(tokenized)
    scores    = []
    df        = {t: sum(1 for doc in tokenized if t in doc) for t in query_terms}
    k1, b     = 1.5, 0.75
    avg_dl    = sum(len(d) for d in tokenized) / max(N, 1)
    for doc in tokenized:
        dl    = len(doc)
        score = 0.0
        tf_map: dict[str, int] = {}
        for t in doc:
            tf_map[t] = tf_map.get(t, 0) + 1
        for t in query_terms:
            tf  = tf_map.get(t, 0)
            idf = math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1)
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
        scores.append(score)
    return scores


# ──────────────────────────────────────────────
# 4+5. Hybrid retrieval: embedding + BM25 via RRF
# ──────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom  = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


def _rrf(rankings: list[list[int]], k: int = 60) -> list[float]:
    Reciprocal Rank Fusion over multiple ranked lists of indices.
    n      = max(max(r) for r in rankings if r) + 1
    scores = [0.0] * n
    for ranked in rankings:
        for rank, idx in enumerate(ranked):
            scores[idx] += 1.0 / (k + rank + 1)
    return scores


async def hybrid_retrieve(
    question:    str,
    org_id:      str,
    domain:      str = "general",
    candidate_k: int = 10,         # pool before reranking
) -> list[dict]:
    
    Returns up to candidate_k chunks, fused from:
      A) pgvector ANN (embedding similarity)
      B) keyword ilike search (BM25-scored client-side)
    
    loop = asyncio.get_event_loop()

    # ── A: embedding search ────────────────────────────────────
    query_vector = await loop.run_in_executor(
        None, get_embedder().embed_query, question
    )

    # 5. metadata filter — filter_domain passed to Supabase RPC
    vector_chunks: list[dict] = await db_rpc("match_documents", {
        "query_embedding": query_vector,
        "match_count":     candidate_k,
        "filter_org_id":   org_id,
        # "filter_domain": domain,   # uncomment once RPC supports it
    })

    # ── B: keyword / BM25 search ───────────────────────────────
    keywords      = extract_keywords(question)
    keyword_chunks: list[dict] = []
    for kw in keywords:
        kw_results = await db_keyword_search(org_id, kw)
        keyword_chunks.extend(kw_results)

    # ── dedup across both lists (by chunk_text) ────────────────
    seen:       set[str]  = set()
    all_chunks: list[dict] = []
    for c in keyword_chunks + vector_chunks:
        key = c["chunk_text"]
        if key not in seen:
            seen.add(key)
            all_chunks.append(c)

    if not all_chunks:
        return []

    # ── BM25 re-score keyword hits client-side ─────────────────
    corpus        = [c["chunk_text"] for c in all_chunks]
    query_terms   = re.findall(r"\w+", question.lower())
    bm25_scores   = _bm25_score(query_terms, corpus)

    # Normalise vector similarity (already 0-1 from pgvector cosine op)
    vec_scores = [c.get("similarity", 0.0) for c in all_chunks]

    # ── RRF fusion ─────────────────────────────────────────────
    bm25_rank  = list(np.argsort(bm25_scores)[::-1])
    vec_rank   = list(np.argsort(vec_scores)[::-1])
    rrf_scores = _rrf([vec_rank, bm25_rank])

    # Attach fused score and sort
    for i, c in enumerate(all_chunks):
        c["_rrf"] = rrf_scores[i]

    all_chunks.sort(key=lambda c: c["_rrf"], reverse=True)
    return all_chunks[:candidate_k]


# ──────────────────────────────────────────────
# 6. Cross-encoder re-ranker
# ──────────────────────────────────────────────

def rerank(question: str, chunks: list[dict], top_n: int = 5) -> list[dict]:
    
    Re-rank `chunks` with a cross-encoder.  Falls back to RRF order if
    the model is unavailable.
    reranker = get_reranker()
    if not reranker:
        return chunks[:top_n]

    pairs  = [(question, c["chunk_text"]) for c in chunks]
    scores = reranker.predict(pairs)              # list[float]
    for c, s in zip(chunks, scores):
        c["_rerank"] = float(s)
    chunks.sort(key=lambda c: c.get("_rerank", 0), reverse=True)
    return chunks[:top_n]


# ──────────────────────────────────────────────
# 11. Context compression (extractive)
# ──────────────────────────────────────────────

def _sentence_score(sentence: str, query_terms: list[str]) -> float:
    words = re.findall(r"\w+", sentence.lower())
    if not words:
        return 0.0
    hits  = sum(1 for w in words if w in query_terms)
    return hits / len(words)


def compress_context(
    chunks:       list[dict],
    question:     str,
    max_chars:    int = 8_000,       # ≈ 2 k tokens; raise to 32 k if on GPU
) -> str:
    
    For each chunk, score sentences by query-term overlap.
    Keep top sentences until max_chars budget is consumed.
    Preserves intra-chunk sentence order.
    
    query_terms = set(re.findall(r"\w+", question.lower())) - STOP_WORDS
    budget      = max_chars
    parts: list[str] = []

    for chunk in chunks:
        doc   = chunk["doc_name"]
        sents = re.split(r"(?<=[.!?])\s+", chunk["chunk_text"])
        scored = sorted(
            ((s, _sentence_score(s, query_terms)) for s in sents if s.strip()),
            key=lambda x: x[1],
            reverse=True,
        )
        # Keep sentences in original order, highest-scoring ones first in budget
        selected = set()
        for s, _ in scored:
            if len(s) <= budget:
                selected.add(s)
                budget -= len(s) + 1
            if budget <= 0:
                break

        ordered = [s for s in sents if s in selected]
        if ordered:
            parts.append(f"[Doc: {doc}]\n" + " ".join(ordered))

        if budget <= 0:
            break

    return "\n\n---\n\n".join(parts)


# ──────────────────────────────────────────────
# 8. Semantic cache helpers
# ──────────────────────────────────────────────

_SEM_CACHE_THRESHOLD = 0.92   # cosine similarity to call it a hit


async def _semantic_cache_lookup(
    question: str,
    org_id:   str,
    loop:     asyncio.AbstractEventLoop,
) -> Optional[dict]:
    
    Embed the incoming question.  Compare against stored query embeddings
    in Redis (set_semantic_cache stores them).  Return cached result if
    cosine similarity > threshold.
    
    q_vec = await loop.run_in_executor(
        None, get_embedder().embed_query, question
    )
    hit = await get_semantic_cache(org_id, q_vec, threshold=_SEM_CACHE_THRESHOLD)
    return hit   # None or dict


# ──────────────────────────────────────────────
# Main answer_question (non-streaming)
# ──────────────────────────────────────────────

async def answer_question(
    question: str,
    org_id:   str,
    top_k:    int = 3,
    domain:   str = "general",
) -> dict:
    loop = asyncio.get_event_loop()

    # ── 8. exact cache ─────────────────────────────────────────
    cached = await get_cached(org_id, question)
    if cached:
        print(f"[cache] exact HIT org={org_id}")
        return cached

    # ── 8. semantic cache ──────────────────────────────────────
    sem_hit = await _semantic_cache_lookup(question, org_id, loop)
    if sem_hit:
        print(f"[cache] semantic HIT org={org_id}")
        return sem_hit

    # ── 4+5. hybrid retrieve (10 candidates) ──────────────────
    candidates = await hybrid_retrieve(
        question, org_id, domain=domain, candidate_k=10
    )

    if not candidates:
        return {
            "answer":          "No relevant documents found for your organization.",
            "sources":         [],
            "source_passages": [],
            "org_id":          org_id,
        }

    # ── 6. rerank → keep 5 ────────────────────────────────────
    reranked   = rerank(question, candidates, top_n=5)

    # ── 10. feed top 3 to LLM ─────────────────────────────────
    top_chunks = reranked[:top_k]

    # debug
    print("\n===== RERANKED CONTEXT =====")
    for i, c in enumerate(top_chunks):
        print(f"[{i+1}] rerank={c.get('_rerank', 'n/a'):.3f}  {c['chunk_text'][:100]}")
    print("============================\n")

    # ── 11. compress context ───────────────────────────────────
    context = compress_context(top_chunks, question, max_chars=8_000)

    # ── LLM ───────────────────────────────────────────────────
    prompt = RAG_PROMPT.format(context=context, question=question)
    answer = await loop.run_in_executor(None, get_llm().invoke, prompt)

    source_passages = [
        {
            "doc_name":   c["doc_name"],
            "passage":    c["chunk_text"],
            "similarity": round(c.get("similarity", 0.0), 3),
        }
        for c in top_chunks
    ]

    result = {
        "answer":          answer,
        "sources":         [c["chunk_text"][:200] + "…" for c in top_chunks],
        "source_passages": source_passages,
        "org_id":          org_id,
    }

    # ── cache writes (don't block response) ───────────────────
    q_vec = await loop.run_in_executor(
        None, get_embedder().embed_query, question
    )
    await set_cached(org_id, question, result, ttl=300)
    await set_semantic_cache(org_id, question, q_vec, result, ttl=300)
    asyncio.create_task(db_insert("query_logs", [{
        "org_id":   org_id,
        "question": question,
        "answer":   answer,
    }]))

    return result"""
    
from langchain.prompts import PromptTemplate
from app.config import settings
from app.db import db_rpc, db_insert
from app.cache import get_cached, set_cached
from app.ingest import embed_texts
import asyncio, re, httpx

# ── Groq LLM — fast, free, no local RAM ──
async def ask_groq(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model":       "llama-3.1-8b-instant",  # fastest Groq model
                "temperature": 0.1,
                "max_tokens":  512,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template="""You are a precise assistant for SecureStream. Answer using ONLY the context.
Be concise — 2-4 sentences. If not found, say: "Not found in uploaded documents."

Context:
{context}

Question: {question}
Answer:"""
)

STOP_WORDS = {
    "what","is","the","a","an","of","in","on","at","to","for","and","or",
    "are","was","were","has","have","does","do","did","this","that","with",
    "from","by","about","how","when","where","who","which","can","will",
    "there","any","all","tell","me","show","give","find","list"
}

def extract_keywords(question: str) -> list[str]:
    words   = re.findall(r'\b[a-zA-Z]{3,}\b', question.lower())
    keywords = [w for w in words if w not in STOP_WORDS]
    # Add partial stems for compound words
    stems   = [w[:6] for w in keywords if len(w) >= 7]
    return list(set(keywords + stems))[:6]

def deduplicate_chunks(chunks: list[dict]) -> list[dict]:
    """
    Remove chunks that are substrings of other chunks.
    Also remove near-identical chunks (>85% overlap).
    """
    seen = []
    for c in chunks:
        text = c.get("chunk_text", "").lower().strip()
        is_dup = False
        for s in seen:
            s_text = s.get("chunk_text", "").lower().strip()
            # Substring check
            if text in s_text or s_text in text:
                is_dup = True
                break
            # Jaccard similarity for near-duplicates
            set_a  = set(text.split())
            set_b  = set(s_text.split())
            if len(set_a | set_b) > 0:
                jaccard = len(set_a & set_b) / len(set_a | set_b)
                if jaccard > 0.85:
                    is_dup = True
                    break
        if not is_dup:
            seen.append(c)
    return seen

def group_by_section(chunks: list[dict]) -> str:
    """
    Build context string grouped by document section.
    This gives the LLM structured context instead of a flat list of passages.
    """
    sections: dict[str, list[str]] = {}
    for c in chunks:
        meta    = c.get("metadata") or {}
        section = meta.get("section", "General")
        doc     = c.get("doc_name", "")
        key     = f"{doc} › {section}" if doc else section
        sections.setdefault(key, []).append(c["chunk_text"])

    parts = []
    for section_label, texts in sections.items():
        parts.append(f"[{section_label}]")
        parts.extend(f"  {t}" for t in texts)
    return "\n".join(parts)

async def answer_question(question: str, org_id: str, top_k: int = 6) -> dict:

    # Cache hit — instant return
    cached = await get_cached(org_id, question)
    if cached:
        print(f"Cache HIT org={org_id}")
        return cached

    # Embed question (Jina API — no local RAM)
    query_vectors = await embed_texts([question])
    query_vector  = query_vectors[0]

    # Parallel: vector search + keyword search
    keywords = extract_keywords(question)

    async def vector_search():
        return await db_rpc("match_documents", {
            "query_embedding": query_vector,
            "match_count":     top_k * 2,   # fetch more, deduplicate below
            "filter_org_id":   org_id
        })

    async def keyword_search():
        from app.db import db_keyword_search
        results = []
        # Run keyword searches in parallel
        tasks = [db_keyword_search(org_id, kw) for kw in keywords[:3]]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        for batch in batches:
            if isinstance(batch, list):
                results.extend(batch)
        return results

    # Fire both searches simultaneously
    vec_chunks, kw_chunks = await asyncio.gather(vector_search(), keyword_search())

    # Merge: keyword results first (higher precision), then vector results
    combined = kw_chunks + vec_chunks

    # Deduplicate aggressively
    unique = deduplicate_chunks(combined)

    # Take top_k after dedup
    top = unique[:top_k]

    if not top:
        return {
            "answer":          "No relevant documents found for your organization.",
            "sources":         [],
            "source_passages": [],
            "org_id":          org_id
        }

    # Build structured context grouped by section
    context = group_by_section(top)

    prompt  = RAG_PROMPT.format(context=context, question=question)
    answer  = await ask_groq(prompt)

    source_passages = [
        {
            "doc_name":   c["doc_name"],
            "passage":    c["chunk_text"],
            "similarity": round(c.get("similarity", 0), 3),
            "section":    (c.get("metadata") or {}).get("section", "")
        }
        for c in top
    ]

    result = {
        "answer":          answer,
        "sources":         [c["chunk_text"][:200] + "..." for c in top],
        "source_passages": source_passages,
        "org_id":          org_id
    }

    await set_cached(org_id, question, result, ttl=300)
    asyncio.create_task(db_insert("query_logs", [{
        "org_id":   org_id,
        "question": question,
        "answer":   answer
    }]))

    return result