from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, jwk
from jose.utils import base64url_decode
import httpx
from app.config import settings

bearer = HTTPBearer()

JWKS_CACHE = None  # simple cache

async def get_jwks():
    global JWKS_CACHE
    if JWKS_CACHE:
        return JWKS_CACHE

    async with httpx.AsyncClient() as client:
        r = await client.get(settings.cognito_jwks_url)
        JWKS_CACHE = r.json()["keys"]
        return JWKS_CACHE


async def verify_token(credentials: HTTPAuthorizationCredentials = Security(bearer)):
    token = credentials.credentials

    try:
        print("TOKEN RECEIVED:", token[:30], "...")  # debug

        keys = await get_jwks()

        header = jwt.get_unverified_header(token)
        print("HEADER:", header)

        key = next((k for k in keys if k["kid"] == header["kid"]), None)

        if not key:
            raise Exception("Matching JWKS key not found")

        public_key = jwk.construct(key)

        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={
                "verify_aud": False,
                "verify_at_hash": False
            }
        )

        print("CLAIMS:", claims)
        return claims

    except Exception as e:
        print("JWT ERROR >>>", str(e))   # 🔥 THIS IS WHAT WE NEED
        raise HTTPException(status_code=401, detail="Invalid token")