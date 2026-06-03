from __future__ import annotations

import logging
import time
import uuid

from app.cache import get_redis
from app.config import settings

logger = logging.getLogger(__name__)

TIER_LIMITS: dict[str, int | None] = {
    "free":       100,
    "pro":        1000,
    "enterprise": None,   # unlimited
}

_WINDOW = 60  # seconds


async def check_rate_limit(
    user_id: str,
    endpoint: str,
    tier:     str = "free",
) -> tuple[bool, int, int]:
    """
    Sliding-window rate limiter using a Redis sorted set.

    Returns (allowed, remaining, retry_after_seconds).

    Sliding window: every request is stored as a timestamped member.
    Members older than `now - window` are pruned on each call.
    This prevents the 2× burst at window boundaries that a fixed-window
    counter allows.
    """
    if tier not in TIER_LIMITS:
        logger.warning("Unknown tier %r — defaulting to free", tier)
        tier = "free"

    limit = TIER_LIMITS[tier]
    if limit is None:
        return True, 9999, 0

    r      = get_redis()
    now_ms = int(time.time() * 1000)
    key    = f"ratelimit:sliding:{user_id}:{endpoint}"
    member = f"{now_ms}:{uuid.uuid4().hex[:8]}"   # unique even at same millisecond

    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now_ms - (_WINDOW * 1000))  # prune old
        pipe.zadd(key, {member: now_ms})                           # record this request
        pipe.zcard(key)                                            # count in window
        pipe.expire(key, _WINDOW * 2)                             # auto-cleanup
        results = await pipe.execute()

        count:     int = results[2]
        remaining: int = max(0, limit - count)
        allowed:   bool = count <= limit
        return allowed, remaining, (_WINDOW if not allowed else 0)

    except Exception:
        logger.exception("check_rate_limit Redis error user=%s", user_id)
        if settings.rate_limit_fail_open:
            return True, limit, 0
        return False, 0, _WINDOW