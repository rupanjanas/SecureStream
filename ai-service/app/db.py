import httpx
from app.config import settings

HEADERS = {
    "apikey": settings.supabase_service_key,
    "Authorization": f"Bearer {settings.supabase_service_key}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

BASE = settings.supabase_url

# ──────────────────────────────────────────────────────────────────────────────
# ADDED UNIFIED SELECT-OR-RPC HELPER FOR MAIN.PY ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────
async def db_select_or_rpc(target: str, params: dict) -> list:
    """
    A smart data-access layer that handles both direct table/view queries 
    and procedural RPC execution dynamically based on how it's called.
    """
    async with httpx.AsyncClient() as client:
        # 1. If it's the specific documents RPC call, route it via /rpc/
        if target == "get_org_documents":
            # Map main.py parameters to your database function argument names
            rpc_payload = {
                "org_id_param": params.get("filter_org_id"),
                "domain_param": params.get("filter_domain")
            }
            r = await client.post(
                f"{BASE}/rest/v1/rpc/{target}",
                headers=HEADERS,
                json=rpc_payload,
                timeout=30
            )
        
        # 2. Otherwise, treat it as a direct Table/View fetch (like query_logs or documents)
        else:
            # Convert incoming parameters into standard PostgREST equal filters: {"key": "eq.value"}
            rest_filters = {}
            for k, v in params.items():
                if v is not None:
                    rest_filters[k] = f"eq.{v}"
                    
            r = await client.get(
                f"{BASE}/rest/v1/{target}",
                headers=HEADERS,
                params=rest_filters,
                timeout=30
            )

        if r.status_code not in (200, 201):
            # Fallback gracefully rather than crashing completely if tables are empty
            if r.status_code == 404:
                return []
            raise Exception(f"Supabase data access layer error ({target}) {r.status_code}: {r.text}")
            
        return r.json()

# ──────────────────────────────────────────────────────────────────────────────
# ORIGINAL IMPLEMENTATIONS PRESERVED UNTOUCHED
# ──────────────────────────────────────────────────────────────────────────────

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

async def db_keyword_search(org_id: str, keyword: str, doc_name: str = None) -> list:
    params = {
        "org_id":  f"eq.{org_id}",
        "chunk_text": f"ilike.%{keyword}%",
        "select":  "id,doc_name,chunk_text,metadata,file_url",
        "limit":   "5",
    }
    if doc_name:
        params["doc_name"] = f"eq.{doc_name}"

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/documents",
            headers=HEADERS,
            params=params,
        )
        print(f"KEYWORD SEARCH '{keyword}' found: {len(r.json())} chunks")
        return r.json()

async def db_patch(table: str, filters: dict, data: dict) -> list:
    params = {k: f"eq.{v}" for k, v in filters.items()}
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{BASE}/rest/v1/{table}",
            headers={**HEADERS, "Prefer": "return=representation"},
            params=params,
            json=data,
            timeout=30
        )
        if r.status_code not in (200, 201):
            raise Exception(f"Supabase patch error {r.status_code}: {r.text}")
        return r.json()