from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import httpx

from app.cache import get_cached, get_semantic_cache, set_cached, set_semantic_cache
from app.config import settings
from app.db import db_insert, db_keyword_search, db_rpc

logger = logging.getLogger(__name__)

RAG_PROMPT = (
    "You are a helpful document assistant. "
    "Answer the question using ONLY the context below.\n\n"
    "Rules:\n"
    "- Summarize and paraphrase clearly. You do not need to quote verbatim.\n"
    "- If the context contains relevant information, always use it — even if partial.\n"
    "- Only say \"Not found in the uploaded document.\" if there is truly NO relevant information at all.\n"
    "- Never make up information not in the context.\n"
    "- Never reference URLs or bibliography entries.\n\n"
    "Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"
)

STOP_WORDS: set[str] = {
    "what", "where", "when", "which", "that", "this", "with", "from",
    "have", "will", "been", "were", "they", "them", "their", "about",
    "does", "show", "tell", "give", "find", "list", "please", "just",
    "also", "some", "more", "very", "can", "the", "and", "for", "are",
    "was", "but", "not", "you", "all", "any", "had", "his", "her",
    "she", "how", "its", "our", "out", "use",
}

_JUNK_RE = re.compile(r"(https?://|www\.|doi\.org|\[\d+\])", re.IGNORECASE)


def filter_junk(chunks: list[dict]) -> list[dict]:
    clean = []
    for c in chunks:
        text = c.get("chunk_text", "")
        sentences = [s.strip() for s in text.split(".") if s.strip()]
        if not sentences:
            continue
        if len(sentences) <= 1:
            clean.append(c)
            continue
        if sum(1 for s in sentences if _JUNK_RE.search(s)) / len(sentences) <= 0.5:
            clean.append(c)
    return clean


def rerank(question: str, chunks: list[dict]) -> list[dict]:
    """
    score = vector_similarity * 0.7 + keyword_overlap * 0.3
    Operates on copies — does NOT mutate the caller's list.
    """
    q_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", question.lower())) - STOP_WORDS
    result = []
    for c in chunks:
        copy = dict(c)
        vec_sim = copy.get("similarity", 0.0)
        chunk_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", copy.get("chunk_text", "").lower()))
        overlap = len(q_words & chunk_words) / max(len(q_words), 1)
        copy["similarity"] = round(vec_sim * 0.7 + overlap * 0.3, 4)
        result.append(copy)
    return sorted(result, key=lambda c: c.get("similarity", 0), reverse=True)


def deduplicate(chunks: list[dict]) -> list[dict]:
    """
    Dedup key = (doc_name, chunk_index).
    Falls back to text prefix when chunk_index is absent.
    When two chunks share a key, the one with higher similarity is kept.
    """
    seen: dict = {}
    for c in chunks:
        meta = c.get("metadata") or {}
        chunk_index = meta.get("chunk_index")
        doc_name = meta.get("doc_name") or c.get("doc_name", "")
        if chunk_index is not None:
            key = ("idx", doc_name, int(chunk_index))
        else:
            key = ("txt", c.get("chunk_text", "")[:200].lower().strip())

        if key not in seen or c.get("similarity", 0) > seen[key].get("similarity", 0):
            seen[key] = c
    return list(seen.values())


async def embed_query(question: str) -> list[float]:
    """Embed a user question with the retrieval.query task type."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        r = await client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {settings.jina_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": [question],
                "model": "jina-embeddings-v3",
                "task": "retrieval.query",
            },
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_context(chunks: list[dict], max_words: int = 1200) -> str:
    if not chunks:
        return ""
    parts: list[str] = []
    word_count: int = 0
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


def _extract_keywords(question: str) -> list[str]:
    terms = list(dict.fromkeys(
        w for w in re.findall(r"\b[a-zA-Z]{4,}\b", question.lower())
        if w not in STOP_WORDS
    ))[:4]
    return terms if terms else [question.lower().strip()]


def _score_keyword_chunk(chunk: dict, kw_terms: list[str]) -> float:
    text = chunk.get("chunk_text", "").lower()
    words = text.split()
    if not words:
        return 0.6
    hits = sum(text.count(kw) for kw in kw_terms)
    return round(min(0.6 + (hits / max(len(words), 1)) * 10, 0.95), 4)


async def retrieve(
    question: str,
    org_id: str,
    doc_name: str = "",
    top_k: int = 5,
) -> tuple[list[dict], Optional[list[float]]]:
    """
    Returns (chunks, query_vector).

    query_vector is None only if the Jina call failed; callers should handle
    that gracefully (semantic cache simply won't be populated).
    """
    kw_terms = _extract_keywords(question)

    async def keyword_search() -> list[dict]:
        results: list[dict] = []
        batches = await asyncio.gather(
            *[db_keyword_search(org_id, kw, doc_name=doc_name or None) for kw in kw_terms],
            return_exceptions=True,
        )
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        for c in results:
            if not c.get("similarity"):
                c["similarity"] = _score_keyword_chunk(c, kw_terms)
        logger.debug("KW terms=%s → %d chunks", kw_terms, len(results))
        return results

    query_vector: Optional[list[float]] = None
    async def vector_search() -> list[dict]:
        nonlocal query_vector
        try:
            query_vector = await embed_query(question)
            result = await db_rpc("match_documents", {
                "query_embedding": query_vector,
                "match_count": 20,
                "filter_org_id": org_id,
                "filter_doc_name": doc_name or "",
            })
            top_sim = result[0].get("similarity", 0) if result else 0
            logger.debug("Vector → %d chunks, top sim=%.3f", len(result), top_sim)
            return result
        except Exception:
            logger.exception("Vector search error")
            return []

    kw_chunks, vec_chunks = await asyncio.gather(keyword_search(), vector_search())
    combined = deduplicate(kw_chunks + vec_chunks)
    combined = filter_junk(combined)
    combined = rerank(question, combined)
    combined = combined[:top_k]
    logger.info("retrieve: final=%d chunks org=%s", len(combined), org_id)
    return combined, query_vector


async def save_query_log(
    org_id: str,
    question: str,
    answer: str,
    source_passages: list,
) -> None:
    try:
        await db_insert("query_logs", [{
            "org_id": org_id,
            "question": question,
            "answer": answer,
            "sources": json.dumps(source_passages),
        }])
    except Exception:
        logger.exception("Failed to save query log")


# ---------------------------------------------------------------------------
# Compat shims — keep existing call-sites working
# ---------------------------------------------------------------------------

def get_embedder():                                   return embed_query
def filter_junk_chunks(chunks: list) -> list:        return filter_junk(chunks)
def deduplicate_chunks(chunks: list) -> list:        return deduplicate(chunks)
def compress_context(chunks: list, max_tokens=1500): return chunks
def group_by_section(chunks: list) -> str:           return build_context(chunks)


async def hybrid_retrieve(q, org, top_k=5):
    chunks, _ = await retrieve(q, org, top_k=top_k)
    return chunks