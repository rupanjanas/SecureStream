import httpx
from app.config import settings

HEADERS = {
    "apikey": settings.supabase_service_key,
    "Authorization": f"Bearer {settings.supabase_service_key}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

BASE = settings.supabase_url

async def db_insert(table: str, rows: list) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/rest/v1/{table}",
            headers=HEADERS,
            json=rows,
            timeout=30
        )
        if r.status_code not in (200, 201):
            raise Exception(f"Supabase insert error {r.status_code}: {r.text}")
        return r.json()

async def db_rpc(func: str, params: dict) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/rest/v1/rpc/{func}",
            headers=HEADERS,
            json=params,
            timeout=60
        )
        if r.status_code not in (200, 201):
            raise Exception(f"Supabase rpc error {r.status_code}: {r.text}")
        return r.json()

async def db_test() -> bool:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/documents?limit=1",
            headers=HEADERS,
            timeout=10
        )
        return r.status_code == 200