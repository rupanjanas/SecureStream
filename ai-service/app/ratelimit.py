from __future__ import annotations

import logging
import time
import uuid

from app.cache import get_redis
from app.config import settings

logger = logging.getLogger(__name__)

TIER_LIMITS: dict[str, int | None] = {
    "free": 100,
    "pro": 1000,
    "enterprise": None,
}

_WINDOW = 60


async def check_rate_limit(
    user_id: str,
    endpoint: str,
    tier: str = "free",
) -> tuple[bool, int, int]:
    if tier not in TIER_LIMITS:
        logger.warning("Unknown tier %r — defaulting to free", tier)
        tier = "free"

    limit = TIER_LIMITS[tier]
    if limit is None:
        return True, 9999, 0

    r = get_redis()
    now_ms = int(time.time() * 1000)
    window_ms = _WINDOW * 1000
    key = f"ratelimit:sliding:{user_id}:{endpoint}"
    member = f"{now_ms}:{uuid.uuid4().hex[:8]}"

    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now_ms - window_ms)
        pipe.zadd(key, {member: now_ms})
        pipe.zcard(key)
        pipe.expire(key, _WINDOW * 2)
        results = await pipe.execute()

        count: int = results[2]
        remaining = max(0, limit - count)
        allowed = count <= limit
        return allowed, remaining, (_WINDOW if not allowed else 0)

    except Exception:
        logger.exception("check_rate_limit Redis error user=%s", user_id)
        if settings.rate_limit_fail_open:
            return True, limit, 0
        return False, 0, _WINDOW