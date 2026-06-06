from __future__ import annotations

import asyncio
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

_JWKS_CACHE: Optional[list]  = None # cached JWKS keys; refreshed on rotation or TTL expiry what is JWKS? JSON Web Key Set, a standard format for representing public keys used to verify JWT signatures
_JWKS_FETCHED_AT: float       = 0.0 # timestamp of when JWKS were last fetched, for TTL-based refresh
_JWKS_TTL: float              = 3600.0 # 1 hour TTL for JWKS cache; adjust based on expected key rotation frequency and acceptable latency on rotation
_JWKS_LOCK                    = asyncio.Lock()          # prevents stampede on cold start
_KEY_CACHE: dict[str, object] = {}                      # per-kid RSA key cache
_DEV_TOKENS: frozenset[str]   = frozenset({"dev-token"})


async def _get_jwks() -> list:
    global _JWKS_CACHE, _JWKS_FETCHED_AT

    if not settings.cognito_jwks_url:
        raise HTTPException(status_code=503, detail="Auth not configured: missing COGNITO_JWKS_URL")

    # Fast path — atomic in CPython, no lock needed
    if _JWKS_CACHE and (time.time() - _JWKS_FETCHED_AT) < _JWKS_TTL:
        return _JWKS_CACHE

    # Slow path — one coroutine fetches; the rest wait then get the filled cache
    async with _JWKS_LOCK:
        # Double-check after acquiring lock
        if _JWKS_CACHE and (time.time() - _JWKS_FETCHED_AT) < _JWKS_TTL:
            return _JWKS_CACHE
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(settings.cognito_jwks_url)
                r.raise_for_status()
                _JWKS_CACHE = r.json()["keys"]
                _JWKS_FETCHED_AT = time.time()
                _KEY_CACHE.clear()   # invalidate per-kid cache on rotation
            logger.info("JWKS refreshed: %d keys", len(_JWKS_CACHE))
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("JWKS fetch failed: %s", exc)
            raise HTTPException(status_code=503, detail="Unable to fetch auth keys")

    return _JWKS_CACHE


def _get_public_key(kid: str, keys: list) -> object:## kid = Key ID in JWT header and keys is _JWKS_CACHE, a list of JWKs fetched from Cognito. This function finds the JWK with the matching kid and constructs a public key object for verifying JWT signatures.
    """Cache jwk.construct() (RSA operation) per kid so it runs once per rotation."""
    if kid not in _KEY_CACHE:## Check if the public key for this kid is already in the cache. If not, find the raw JWK with the matching kid from the keys list.
        raw_key = next((k for k in keys if k["kid"] == kid), None)
        if not raw_key:
            raise ValueError("No matching JWKS key")
        _KEY_CACHE[kid] = jwk.construct(raw_key)
    return _KEY_CACHE[kid]


def _decode(token: str, keys: list) -> dict:
    header     = jwt.get_unverified_header(token)
    public_key = _get_public_key(header["kid"], keys)
    options: dict = {"verify_at_hash": False}
    kwargs:  dict = {"algorithms": ["RS256"], "options": options}
    if settings.cognito_client_id:
        kwargs["audience"] = settings.cognito_client_id
    else:
        options["verify_aud"] = False
    claims = jwt.decode(token, public_key, **kwargs)## claims contains the decoded JWT payload, which includes user information and token metadata. The function also checks that the token is an access token by verifying the "token_use" claim. If the token is valid and is an access token, the claims are returned for use in authorization decisions in protected endpoints.
    if claims.get("token_use") != "access":## Ensure it's an access token, not an ID token or refresh token. This is important because only access tokens should be accepted for API authentication.
        raise ValueError("Expected access token")
    return claims


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer),
) -> dict:
    """FastAPI dependency for all protected HTTP endpoints."""
    if settings.environment == "dev":
        if not credentials or credentials.credentials in _DEV_TOKENS:
            logger.debug("verify_token: dev bypass")
            return {"sub": "dev-user-001", "email": "dev@securestream.local"}

    if not credentials:
        raise HTTPException(status_code=401, detail="No token provided")

    try:
        keys   = await _get_jwks()
        claims = _decode(credentials.credentials, keys)
        return claims
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token")