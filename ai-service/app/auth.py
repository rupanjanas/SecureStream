from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, jwk
import httpx
from app.config import settings

bearer = HTTPBearer(auto_error=False)

JWKS_CACHE = None

async def get_jwks():
    global JWKS_CACHE
    if JWKS_CACHE:
        return JWKS_CACHE

    async with httpx.AsyncClient() as client:
        r = await client.get(settings.cognito_jwks_url)
        JWKS_CACHE = r.json()["keys"]
        return JWKS_CACHE


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer)
):
    # ✅ DEV MODE (safe check)
    if settings.environment == "dev":
        return {
            "sub": "dev-user-001",
            "email": "dev@securestream.local"
        }

    # ❌ No token
    if not credentials:
        raise HTTPException(status_code=401, detail="No token provided")

    token = credentials.credentials

    # ✅ Dev token shortcut
    if token == "dev-token":
        return {
            "sub": "dev-user-001",
            "email": "dev@securestream.local"
        }

    try:
        keys = await get_jwks()
        header = jwt.get_unverified_header(token)

        key = next((k for k in keys if k["kid"] == header["kid"]), None)
        if not key:
            raise Exception("Matching JWKS key not found")

        public_key = jwk.construct(key)

        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_at_hash": False}
        )

        # ✅ IMPORTANT: ensure access_token
        if claims.get("token_use") != "access":
            raise Exception("Invalid token type")

        return claims

    except Exception as e:
        print("JWT ERROR:", str(e))
        raise HTTPException(status_code=401, detail="Invalid token")