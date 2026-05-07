"""
cache.py — two-layer caching
─────────────────────────────
Layer 1 — exact cache    : MD5(org_id + question) → JSON result  (unchanged)
Layer 2 — semantic cache : embed(question) stored alongside result;
                           at lookup time cosine-compare vs all stored query
                           embeddings for the org and return if sim > threshold.

Both layers degrade gracefully when Redis is down.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

import numpy as np
import redis.asyncio as redis

from app.config import settings

# ──────────────────────────────────────────────
# Connection singleton
# ──────────────────────────────────────────────

_redis: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


# ──────────────────────────────────────────────
# Layer 1: exact cache (MD5 key)
# ──────────────────────────────────────────────

def _exact_key(org_id: str, question: str) -> str:
    h = hashlib.md5(f"{org_id}:{question.lower().strip()}".encode()).hexdigest()
    return f"query:exact:{h}"


async def get_cached(org_id: str, question: str) -> Optional[dict]:
    try:
        r   = get_redis()
        val = await r.get(_exact_key(org_id, question))
        return json.loads(val) if val else None
    except Exception:
        return None


async def set_cached(
    org_id:   str,
    question: str,
    result:   dict,
    ttl:      int = 300,
) -> None:
    try:
        r = get_redis()
        await r.setex(_exact_key(org_id, question), ttl, json.dumps(result))
    except Exception:
        pass


async def invalidate_org(org_id: str) -> None:
    """Flush all cache entries (exact + semantic) for an org."""
    try:
        r    = get_redis()
        keys = await r.keys(f"query:*:{org_id}:*")
        keys += await r.keys(f"query:exact:*")   # blunt flush — acceptable
        if keys:
            await r.delete(*keys)
    except Exception:
        pass


# ──────────────────────────────────────────────
# Layer 2: semantic cache
# ──────────────────────────────────────────────
#
# Storage layout in Redis:
#   query:sem:{org_id}:index   →  JSON list of { key, question }
#   query:sem:{org_id}:{key}   →  JSON { "vec": [...], "result": {...} }
#
# At lookup:
#   1. Load all (key, question) pairs for the org
#   2. Batch-load their stored vectors
#   3. Cosine-compare each vs incoming query vector
#   4. Return result of best match if sim >= threshold

_VEC_KEY_PREFIX = "query:sem"


def _sem_index_key(org_id: str) -> str:
    return f"{_VEC_KEY_PREFIX}:{org_id}:index"


def _sem_entry_key(org_id: str, entry_key: str) -> str:
    return f"{_VEC_KEY_PREFIX}:{org_id}:{entry_key}"


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom  = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


async def get_semantic_cache(
    org_id:    str,
    query_vec: list[float],
    threshold: float = 0.92,
) -> Optional[dict]:
    """
    Return cached result whose stored query embedding cosine-matches
    query_vec above threshold.  Returns None on cache miss or Redis error.
    """
    try:
        r           = get_redis()
        index_raw   = await r.get(_sem_index_key(org_id))
        if not index_raw:
            return None

        index: list[dict] = json.loads(index_raw)   # [{key, question}, ...]
        if not index:
            return None

        # Batch-fetch all stored vectors for this org
        pipe    = r.pipeline()
        for entry in index:
            pipe.get(_sem_entry_key(org_id, entry["key"]))
        raw_entries = await pipe.execute()

        best_sim    = 0.0
        best_result = None

        for entry, raw in zip(index, raw_entries):
            if raw is None:
                continue
            data   = json.loads(raw)
            sim    = _cosine(query_vec, data["vec"])
            if sim > best_sim:
                best_sim    = sim
                best_result = data.get("result")

        if best_sim >= threshold and best_result is not None:
            print(f"[semantic cache] HIT sim={best_sim:.4f}")
            return best_result

        return None

    except Exception as e:
        print(f"[semantic cache] error during lookup: {e}")
        return None


async def set_semantic_cache(
    org_id:    str,
    question:  str,
    query_vec: list[float],
    result:    dict,
    ttl:       int = 300,
) -> None:
    """
    Store (query_vec, result) for future semantic lookups.
    Index is a JSON list of {key, question} stored at the org-level index key.
    Individual entries are stored separately so they can expire independently.
    """
    try:
        import uuid as _uuid
        r         = get_redis()
        entry_key = _uuid.uuid4().hex

        # Store the vector + result
        payload   = json.dumps({"vec": query_vec, "result": result})
        await r.setex(_sem_entry_key(org_id, entry_key), ttl, payload)

        # Update index (load → append → save)
        index_raw = await r.get(_sem_index_key(org_id))
        index: list[dict] = json.loads(index_raw) if index_raw else []

        # Prune entries that have expired (key no longer exists)
        existing_keys = set()
        if index:
            pipe  = r.pipeline()
            for e in index:
                pipe.exists(_sem_entry_key(org_id, e["key"]))
            exists_flags = await pipe.execute()
            index = [e for e, ex in zip(index, exists_flags) if ex]

        index.append({"key": entry_key, "question": question})
        await r.setex(_sem_index_key(org_id), ttl + 60, json.dumps(index))

    except Exception as e:
        print(f"[semantic cache] error during set: {e}")