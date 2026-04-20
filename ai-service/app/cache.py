import redis.asyncio as redis
import hashlib, json
from app.config import settings

_redis = None

def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis

def cache_key(org_id: str, question: str) -> str:
    h = hashlib.md5(f"{org_id}:{question.lower().strip()}".encode()).hexdigest()
    return f"query:{h}"

async def get_cached(org_id: str, question: str):
    try:
        r   = get_redis()
        key = cache_key(org_id, question)
        val = await r.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None   # Redis down — degrade gracefully

async def set_cached(org_id: str, question: str, result: dict, ttl: int = 300):
    try:
        r   = get_redis()
        key = cache_key(org_id, question)
        await r.setex(key, ttl, json.dumps(result))
    except Exception:
        pass   # Redis down — don't crash the app

async def invalidate_org(org_id: str):
    try:
        r    = get_redis()
        keys = await r.keys(f"query:{org_id}:*")
        if keys:
            await r.delete(*keys)
    except Exception:
        pass