"""
Leaky bucket rate limiter using Redis.
Tiers: free=100 RPM, pro=1000 RPM, enterprise=unlimited
"""
import time
import asyncio
from typing import Optional
from app.cache import get_redis

TIER_LIMITS = {
    "free":       100,
    "pro":        1000,
    "enterprise": None,   # unlimited
}

async def check_rate_limit(
    user_id:  str,
    endpoint: str,
    tier:     str = "free",
) -> tuple[bool, int, int]:
    """
    Returns (allowed, remaining, retry_after_seconds).
    Uses a sliding window counter in Redis.
    """
    limit = TIER_LIMITS.get(tier)
    if limit is None:
        return True, 9999, 0   # enterprise = unlimited

    r          = get_redis()
    now        = int(time.time())
    window     = 60            # 1 minute window
    key        = f"ratelimit:{user_id}:{endpoint}:{now // window}"

    try:
        pipe  = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window * 2)
        results = await pipe.execute()
        count   = results[0]

        remaining    = max(0, limit - count)
        retry_after  = window - (now % window)
        allowed      = count <= limit

        return allowed, remaining, retry_after if not allowed else 0
    except Exception:
        return True, limit, 0   # Redis down → fail open