from __future__ import annotations

import hashlib
import json
import logging
import uuid as _uuid
from typing import Optional

import numpy as np
import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
    return _redis


# ── Layer 1: exact cache ──────────────────────────────────────────────────────

def _exact_key(user_id: str, question: str) -> str:
    h = hashlib.md5(question.lower().strip().encode()).hexdigest()
    return f"query:exact:{user_id}:{h}"


async def get_cached(user_id: str, question: str) -> Optional[dict]:
    try:
        val = await get_redis().get(_exact_key(user_id, question))
        if val:
            logger.debug("Exact cache HIT user=%s", user_id)
            return json.loads(val)
        return None
    except Exception:
        logger.exception("Exact cache GET error")
        return None


async def set_cached(user_id: str, question: str, result: dict, ttl: int = 300) -> None:
    try:
        await get_redis().setex(_exact_key(user_id, question), ttl, json.dumps(result))
    except Exception:
        logger.exception("Exact cache SET error")


# ── Layer 2: semantic cache ───────────────────────────────────────────────────

_VEC_KEY_PREFIX = "query:sem"


def _sem_index_key(user_id: str) -> str:
    return f"{_VEC_KEY_PREFIX}:{user_id}:index"


def _sem_entry_key(user_id: str, entry_key: str) -> str:
    return f"{_VEC_KEY_PREFIX}:{user_id}:{entry_key}"


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


async def get_semantic_cache(
    user_id: str,
    query_vec: list[float],
    threshold: float = 0.92,
    ttl: int = 300,
) -> Optional[dict]:
    if not query_vec:
        return None
    try:
        r = get_redis()
        index_raw = await r.get(_sem_index_key(user_id))
        if not index_raw:
            return None
        index: list[dict] = json.loads(index_raw)
        if not index:
            return None

        pipe = r.pipeline()
        for entry in index:
            pipe.get(_sem_entry_key(user_id, entry["key"]))
        raw_entries: list[Optional[str]] = await pipe.execute()

        best_sim = 0.0
        best_result = None
        best_entry_key: Optional[str] = None

        for entry, raw in zip(index, raw_entries):
            if raw is None:
                continue
            data = json.loads(raw)
            sim  = _cosine(query_vec, data.get("vec", []))
            if sim > best_sim:
                best_sim       = sim
                best_result    = data.get("result")
                best_entry_key = entry["key"]

        if best_sim >= threshold and best_result is not None:
            logger.info("Semantic cache HIT sim=%.4f user=%s", best_sim, user_id)
            if best_entry_key:
                await r.expire(_sem_entry_key(user_id, best_entry_key), ttl)
            return best_result
        return None
    except Exception:
        logger.exception("Semantic cache GET error")
        return None


async def set_semantic_cache(
    user_id: str,
    question: str,
    query_vec: list[float],
    result: dict,
    ttl: int = 300,
) -> None:
    if not query_vec:
        return
    try:
        r         = get_redis()
        entry_key = _uuid.uuid4().hex
        payload   = json.dumps({"vec": query_vec, "result": result})
        await r.setex(_sem_entry_key(user_id, entry_key), ttl, payload)

        index_key = _sem_index_key(user_id)
        for attempt in range(2):
            try:
                async with r.pipeline(transaction=True) as pipe:
                    await pipe.watch(index_key)
                    index_raw = await pipe.get(index_key)
                    index: list[dict] = json.loads(index_raw) if index_raw else []

                    if index:
                        exists_pipe = r.pipeline()
                        for e in index:
                            exists_pipe.exists(_sem_entry_key(user_id, e["key"]))
                        exists_flags: list[int] = await exists_pipe.execute()
                        index = [e for e, ex in zip(index, exists_flags) if ex]

                    index.append({"key": entry_key, "question": question})
                    pipe.multi()
                    pipe.setex(index_key, ttl + 60, json.dumps(index))
                    await pipe.execute()
                    return
            except redis.WatchError:
                if attempt == 0:
                    logger.warning("Semantic cache WatchError — retrying")
                    continue
                await r.delete(_sem_entry_key(user_id, entry_key))
                logger.error("Semantic cache WatchError on retry — orphan cleaned")
    except Exception:
        logger.exception("Semantic cache SET error")


async def invalidate_user(user_id: str) -> None:
    """Flush all exact + semantic cache entries for one user."""
    try:
        r = get_redis()

        exact_keys: list[str] = []
        async for key in r.scan_iter(match=f"query:exact:{user_id}:*", count=200):
            exact_keys.append(key)
        if exact_keys:
            await r.delete(*exact_keys)
            logger.info("Invalidated %d exact-cache keys user=%s", len(exact_keys), user_id)

        sem_index_key = _sem_index_key(user_id)
        index_raw     = await r.get(sem_index_key)
        if index_raw:
            index      = json.loads(index_raw)
            entry_keys = [_sem_entry_key(user_id, e["key"]) for e in index]
            if entry_keys:
                await r.delete(*entry_keys)
            await r.delete(sem_index_key)
            logger.info("Invalidated %d semantic entries user=%s", len(entry_keys), user_id)
    except Exception:
        logger.exception("Cache invalidation error user=%s", user_id)