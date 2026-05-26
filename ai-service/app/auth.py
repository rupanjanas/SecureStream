from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, jwk
import httpx, time
from app.config import settings

bearer = HTTPBearer(auto_error=False)

_JWKS_CACHE: list | None = None
_JWKS_FETCHED_AT: float  = 0.0
_JWKS_TTL: float         = 3600.0   # re-fetch keys every hour


async def _get_jwks() -> list:
    global _JWKS_CACHE, _JWKS_FETCHED_AT
    if _JWKS_CACHE and (time.time() - _JWKS_FETCHED_AT) < _JWKS_TTL:
        return _JWKS_CACHE
    async with httpx.AsyncClient() as client:
        r = await client.get(settings.cognito_jwks_url)
        r.raise_for_status()
        _JWKS_CACHE      = r.json()["keys"]
        _JWKS_FETCHED_AT = time.time()
    return _JWKS_CACHE


def _decode(token: str, keys: list) -> dict:
    """Shared decode logic used by both HTTP and WebSocket paths."""
    header = jwt.get_unverified_header(token)
    key    = next((k for k in keys if k["kid"] == header["kid"]), None)
    if not key:
        raise ValueError("Matching JWKS key not found")

    public_key = jwk.construct(key)
    claims     = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        options={"verify_aud": False, "verify_at_hash": False},
    )
    if claims.get("token_use") != "access":
        raise ValueError("Invalid token type — expected access token")
    return claims


# ── Used by normal HTTP endpoints via Depends() ─────────────────────────────

async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer),
) -> dict:
    # Dev shortcut
    if settings.environment == "dev" and (
        not credentials or credentials.credentials == "dev-token"
    ):
        return {"sub": "dev-user-001", "email": "dev@securestream.local"}

    if not credentials:
        raise HTTPException(status_code=401, detail="No token provided")

    try:
        keys   = await _get_jwks()
        claims = _decode(credentials.credentials, keys)
        return claims
    except Exception as e:
        print(f"[AUTH] JWT error: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Used by WebSocket endpoints (token arrives as query param) ───────────────

async def verify_token_ws(token: str) -> dict:
    """
    Verifies a raw JWT string.
    Raises HTTPException(403) so the WebSocket handler can close cleanly.
    """
    if settings.environment == "dev" and token in ("", "dev-token"):
        return {"sub": "dev-user-001", "email": "dev@securestream.local",
                "custom:org_id": "dev-user-001"}

    if not token:
        raise HTTPException(status_code=403, detail="No token provided")

    try:
        keys   = await _get_jwks()
        claims = _decode(token, keys)
        return claims
    except Exception as e:
        print(f"[AUTH-WS] JWT error: {e}")
        raise HTTPException(status_code=403, detail="Invalid token")