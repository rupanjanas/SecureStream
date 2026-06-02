"""
query.py — retrieval, context building, and non-streaming answer pipeline.

Key changes from original:
- STOP_WORDS, filter_junk, and rerank are now properly defined (they were
  imported by query_graph.py but never existed here — startup crash).
- save_query_log was accidentally indented inside answer_question (unreachable).
  It is now a proper top-level async function.
- result dict no longer references `combined` from an outer scope after the
  badly-indented function definition broke the control flow.
- Keyword similarity scoring uses term-frequency / chunk-length normalisation
  instead of the ad-hoc +0.05-per-occurrence heuristic.
- deduplicate uses chunk_index as primary key then falls back to text prefix,
  matching query_graph.py's expectations.
"""

import asyncio
import json
import re
from typing import Optional

import httpx
from langchain_core.prompts import PromptTemplate

from app.cache import get_cached, set_cached
from app.config import settings
from app.db import db_insert, db_rpc, db_keyword_search


# ── RAG prompt ────────────────────────────────────────────────────────────────

RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template="""You are a helpful document assistant. Answer the question using ONLY the context below.

Rules:
- Summarize and paraphrase clearly. You do not need to quote verbatim.
- If the context contains relevant information, always use it — even if partial.
- Only say "Not found in the uploaded document." if there is truly NO relevant information at all.
- Never make up information not in the context.
- Never reference URLs or bibliography entries.

Context:
{context}

Question: {question}

Answer:""",
)


# ── Stop words (used here and exported to query_graph.py) ────────────────────

STOP_WORDS: set[str] = {
    "what", "where", "when", "which", "that", "this", "with", "from",
    "have", "will", "been", "were", "they", "them", "their", "about",
    "does", "show", "tell", "give", "find", "list", "please", "just",
    "also", "some", "more", "very", "can", "the", "and", "for", "are",
    "was", "but", "not", "you", "all", "any", "had", "his", "her",
    "she", "how", "its", "our", "out", "use",
}


# ── Junk filter (exported to query_graph.py) ──────────────────────────────────

_JUNK_RE = re.compile(r"(https?://|www\.|doi\.org|\[\d+\])", re.IGNORECASE)


def filter_junk(chunks: list[dict]) -> list[dict]:
    """Remove chunks that are predominantly URLs, citations, or bibliography lines."""
    clean = []
    for c in chunks:
        text = c.get("chunk_text", "")
        sentences = [s.strip() for s in text.split(".") if s.strip()]
        if not sentences:
            continue
        junk_ratio = sum(1 for s in sentences if _JUNK_RE.search(s)) / len(sentences)
        if junk_ratio <= 0.5:
            clean.append(c)
    return clean


# ── Reranker (exported to query_graph.py) ─────────────────────────────────────

def rerank(question: str, chunks: list[dict]) -> list[dict]:
    """
    Simple lexical re-ranking on top of vector similarity.

    Score = vector_similarity * 0.7 + keyword_overlap * 0.3

    This avoids the original ad-hoc +0.05-per-hit heuristic and produces
    scores that are interpretable and bounded in [0, 1].
    """
    q_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", question.lower())) - STOP_WORDS

    for c in chunks:
        vec_sim = c.get("similarity", 0.0)
        chunk_words = set(
            re.findall(r"\b[a-zA-Z]{3,}\b", c.get("chunk_text", "").lower())
        )
        overlap = len(q_words & chunk_words) / max(len(q_words), 1)
        c["similarity"] = round(vec_sim * 0.7 + overlap * 0.3, 4)

    return sorted(chunks, key=lambda c: c.get("similarity", 0), reverse=True)


# ── Jina query embedder ───────────────────────────────────────────────────────

async def embed_query(question: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {settings.jina_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": [question],
                "model": "jina-embeddings-v3",
                "task": "text-matching",
            },
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


# ── Groq non-streaming ────────────────────────────────────────────────────────

async def ask_groq(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b-instant",
                "temperature": 0.1,
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


# ── Context builder ───────────────────────────────────────────────────────────

def build_context(chunks: list[dict], max_words: int = 1200) -> str:
    parts: list[str] = []
    word_count = 0
    for c in chunks:
        text = c.get("chunk_text", "")
        page = (c.get("metadata") or {}).get("page_number", "?")
        words = text.split()
        if word_count + len(words) > max_words:
            remaining = max_words - word_count
            if remaining > 30:
                parts.append(f"[Page {page}]\n" + " ".join(words[:remaining]) + "…")
            break
        parts.append(f"[Page {page}]\n{text}")
        word_count += len(words)
    return "\n\n".join(parts)


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(chunks: list[dict]) -> list[dict]:
    seen: set = set()
    result: list[dict] = []
    for c in chunks:
        meta = c.get("metadata") or {}
        key = meta.get("chunk_index")
        if key is None:
            key = c.get("chunk_text", "")[:200].lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


# ── Keyword search helpers ────────────────────────────────────────────────────

def _extract_keywords(question: str) -> list[str]:
    """Extract meaningful query keywords, skipping stop words."""
    q_lower = question.lower().strip()
    terms = list(
        dict.fromkeys(
            w for w in re.findall(r"\b[a-zA-Z]{4,}\b", q_lower) if w not in STOP_WORDS
        )
    )[:4]
    if not terms:
        terms = [q_lower]
    return terms


def _score_keyword_chunk(chunk: dict, kw_terms: list[str]) -> float:
    """
    Normalised keyword score: (total term hits / chunk word count), capped at 0.95.
    More principled than the original +0.05-per-hit heuristic.
    """
    text = chunk.get("chunk_text", "").lower()
    words = text.split()
    if not words:
        return 0.6
    hits = sum(text.count(kw) for kw in kw_terms)
    # normalise by chunk length so short chunks with one hit don't outscore long ones
    score = min(0.6 + (hits / max(len(words), 1)) * 10, 0.95)
    return round(score, 4)


# ── Core retrieve ─────────────────────────────────────────────────────────────

async def retrieve(
    question: str,
    org_id: str,
    doc_name: str = "",
    top_k: int = 5,
) -> list[dict]:
    kw_terms = _extract_keywords(question)

    async def keyword_search() -> list[dict]:
        results: list[dict] = []
        batches = await asyncio.gather(
            *[
                db_keyword_search(org_id, kw, doc_name=doc_name or None)
                for kw in kw_terms
            ],
            return_exceptions=True,
        )
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        for c in results:
            if not c.get("similarity"):
                c["similarity"] = _score_keyword_chunk(c, kw_terms)
        print(f"[KW] terms={kw_terms} → {len(results)} chunks")
        return results

    async def vector_search() -> list[dict]:
        try:
            query_vector = await embed_query(question)
            result = await db_rpc(
                "match_documents",
                {
                    "query_embedding": query_vector,
                    "match_count": 20,
                    "filter_org_id": org_id,
                    "filter_doc_name": doc_name or "",
                },
            )
            top_sim = result[0].get("similarity", 0) if result else 0
            print(f"[VEC] → {len(result)} chunks, top sim={top_sim:.3f}")
            return result
        except Exception as e:
            print(f"[VEC ERROR] {e}")
            return []

    kw_chunks, vec_chunks = await asyncio.gather(keyword_search(), vector_search())

    combined = deduplicate(kw_chunks + vec_chunks)
    combined = rerank(question, combined)
    combined = combined[:top_k]

    print(f"[RETRIEVE] final={len(combined)} chunks")
    for i, c in enumerate(combined):
        meta = c.get("metadata") or {}
        print(
            f"  [{i}] page={meta.get('page_number')} "
            f"sim={c.get('similarity', 0):.3f} "
            f"text={c.get('chunk_text', '')[:60]!r}"
        )

    return combined


# ── Query log ─────────────────────────────────────────────────────────────────

async def save_query_log(
    org_id: str,
    question: str,
    answer: str,
    source_passages: list,
) -> None:
    """Persist a query + answer to the query_logs table (fire-and-forget)."""
    try:
        await db_insert(
            "query_logs",
            [
                {
                    "org_id":   org_id,
                    "question": question,
                    "answer":   answer,
                    "sources":  json.dumps(source_passages),
                }
            ],
        )
    except Exception as e:
        print(f"[QUERY LOG] Failed to save: {e}")


# ── Non-streaming pipeline ────────────────────────────────────────────────────

async def answer_question(
    question: str,
    org_id: str,
    top_k: int = 5,
) -> dict:
    cached = await get_cached(org_id, question)
    if cached:
        print(f"[CACHE HIT] org={org_id}")
        return cached

    combined = await retrieve(question, org_id, top_k=top_k)

    if not combined:
        return {
            "answer":          "No relevant documents found.",
            "sources":         [],
            "source_passages": [],
            "org_id":          org_id,
        }

    context = build_context(combined)
    prompt = RAG_PROMPT.format(context=context, question=question)
    answer = await ask_groq(prompt)

    source_passages = [
        {
            "doc_name":    c.get("doc_name", ""),
            "passage":     c.get("chunk_text", ""),
            "similarity":  round(c.get("similarity", 0), 3),
            "section":     (c.get("metadata") or {}).get("section", ""),
            "page_number": (c.get("metadata") or {}).get("page_number", 1),
        }
        for c in combined
    ]

    result = {
        "answer":          answer,
        "sources":         [c.get("chunk_text", "")[:200] + "..." for c in combined],
        "source_passages": source_passages,
        "org_id":          org_id,
    }

    await set_cached(org_id, question, result, ttl=300)
    asyncio.create_task(save_query_log(org_id, question, answer, source_passages))

    return result


# ── Compat exports ────────────────────────────────────────────────────────────

def get_embedder():
    return embed_query

def filter_junk_chunks(chunks: list[dict]) -> list[dict]:
    return filter_junk(chunks)

def deduplicate_chunks(chunks: list[dict]) -> list[dict]:
    return deduplicate(chunks)

def compress_context(chunks: list[dict], max_tokens: int = 1500) -> list[dict]:
    return chunks

def group_by_section(chunks: list[dict]) -> str:
    return build_context(chunks)

async def hybrid_retrieve(question: str, org_id: str, top_k: int = 5) -> list[dict]:
    return await retrieve(question, org_id, top_k=top_k)