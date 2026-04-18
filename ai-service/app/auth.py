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


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer)
):
    token = credentials.credentials

    try:
        keys = await get_jwks()

        header = jwt.get_unverified_header(token)

        # 🔥 safer key lookup
        key = next((k for k in keys if k["kid"] == header.get("kid")), None)
        if not key:
            raise HTTPException(status_code=401, detail="Public key not found")

        public_key = jwk.construct(key)

        # 🔥 verify signature manually
        message, encoded_signature = token.rsplit(".", 1)
        decoded_signature = base64url_decode(encoded_signature.encode())

        if not public_key.verify(message.encode(), decoded_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

        # 🔥 decode claims
        claims = jwt.get_unverified_claims(token)

        # ✅ VERIFY ISSUER (VERY IMPORTANT)
        expected_issuer = f"https://cognito-idp.{settings.region}.amazonaws.com/{settings.user_pool_id}"
        if claims.get("iss") != expected_issuer:
            raise HTTPException(status_code=401, detail="Invalid issuer")

        # ✅ VERIFY TOKEN USE
        if claims.get("token_use") not in ["id", "access"]:
            raise HTTPException(status_code=401, detail="Invalid token type")

        # ✅ OPTIONAL: VERIFY AUDIENCE
        if claims.get("client_id") != settings.client_id:
            raise HTTPException(status_code=401, detail="Invalid client_id")

        return claims

    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")