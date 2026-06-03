from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, jwk

from app.config import settings

logger = logging.getLogger(__name__)

bearer = HTTPBearer(auto_error=False)

_JWKS_CACHE: Optional[list] = None
_JWKS_FETCHED_AT: float = 0.0
_JWKS_TTL: float = 3600.0
_DEV_TOKENS: frozenset[str] = frozenset({"dev-token"})


async def _get_jwks() -> list:
    global _JWKS_CACHE, _JWKS_FETCHED_AT
    if not settings.cognito_jwks_url:
        raise HTTPException(status_code=503, detail="Auth not configured: missing COGNITO_JWKS_URL")
    now = time.time()
    if _JWKS_CACHE and (now - _JWKS_FETCHED_AT) < _JWKS_TTL:
        return _JWKS_CACHE
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(settings.cognito_jwks_url)
            r.raise_for_status()
            _JWKS_CACHE = r.json()["keys"]
            _JWKS_FETCHED_AT = now
        logger.info("JWKS refreshed: %d keys", len(_JWKS_CACHE))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("JWKS fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="Unable to fetch auth keys")
    return _JWKS_CACHE


def _decode(token: str, keys: list) -> dict:
    header = jwt.get_unverified_header(token)
    key = next((k for k in keys if k["kid"] == header["kid"]), None)
    if not key:
        raise ValueError("No matching JWKS key")
    public_key = jwk.construct(key)
    options: dict = {"verify_at_hash": False}
    kwargs: dict = {"algorithms": ["RS256"], "options": options}
    if settings.cognito_client_id:
        kwargs["audience"] = settings.cognito_client_id
    else:
        options["verify_aud"] = False
    claims = jwt.decode(token, public_key, **kwargs)
    if claims.get("token_use") != "access":
        raise ValueError("Expected access token")
    return claims


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer),
) -> dict:
    if settings.environment == "dev":
        if not credentials or credentials.credentials in _DEV_TOKENS:
            logger.debug("verify_token: dev bypass")
            return {"sub": "dev-user-001", "email": "dev@securestream.local"}
    if not credentials:
        raise HTTPException(status_code=401, detail="No token provided")
    try:
        keys = await _get_jwks()
        return _decode(credentials.credentials, keys)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token")