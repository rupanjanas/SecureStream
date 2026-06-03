from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

HEADERS: dict[str, str] = {
    "apikey": settings.supabase_service_key,
    "Authorization": f"Bearer {settings.supabase_service_key}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

BASE: str = settings.supabase_url

_UNSAFE_ILIKE_RE = re.compile(r"[*%?\\]")

_http: Optional[httpx.AsyncClient] = None


def get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(limits=_LIMITS, timeout=_TIMEOUT)
    return _http


async def close_http() -> None:
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()
        _http = None


def _sanitize_ilike(value: str) -> str:
    return _UNSAFE_ILIKE_RE.sub("", value)


def _merge_headers(*overrides: dict) -> dict:
    h = dict(HEADERS)
    for o in overrides:
        h.update(o)
    return h


async def db_insert(table: str, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    r = await get_http().post(f"{BASE}/rest/v1/{table}", headers=HEADERS, json=rows)
    if r.status_code not in (200, 201):
        logger.error("db_insert %s → %d: %s", table, r.status_code, r.text[:400])
        raise RuntimeError(f"Supabase insert error {r.status_code}: {r.text}")
    return r.json()


async def db_upsert(table: str, rows: list[dict], on_conflict: str) -> list[dict]:
    if not rows:
        return []
    headers = _merge_headers({"Prefer": "resolution=merge-duplicates,return=representation"})
    r = await get_http().post(
        f"{BASE}/rest/v1/{table}",
        headers=headers,
        params={"on_conflict": on_conflict},
        json=rows,
    )
    if r.status_code not in (200, 201):
        logger.error("db_upsert %s → %d: %s", table, r.status_code, r.text[:400])
        raise RuntimeError(f"Supabase upsert error {r.status_code}: {r.text}")
    return r.json()


async def db_get(table: str, params: dict[str, str], extra_headers: dict | None = None) -> list[dict]:
    headers = _merge_headers(extra_headers) if extra_headers else HEADERS
    r = await get_http().get(f"{BASE}/rest/v1/{table}", headers=headers, params=params)
    if r.status_code != 200:
        logger.error("db_get %s → %d: %s", table, r.status_code, r.text[:400])
        raise RuntimeError(f"Supabase get error {r.status_code}: {r.text}")
    if not r.text.strip():
        return []
    return r.json()


async def db_rpc(func: str, params: dict) -> list[Any]:
    r = await get_http().post(
        f"{BASE}/rest/v1/rpc/{func}",
        headers=HEADERS,
        json=params,
        timeout=60.0,
    )
    if r.status_code not in (200, 201):
        logger.error("db_rpc %s → %d: %s", func, r.status_code, r.text[:400])
        raise RuntimeError(f"Supabase rpc error {r.status_code}: {r.text}")
    return r.json()


async def db_test() -> dict:
    try:
        r = await get_http().get(
            f"{BASE}/rest/v1/documents",
            headers=HEADERS,
            params={"limit": "1"},
            timeout=10.0,
        )
        return {"ok": r.status_code == 200, "status_code": r.status_code}
    except Exception as exc:
        logger.exception("db_test failed")
        return {"ok": False, "status_code": 0, "error": str(exc)}


async def db_keyword_search(org_id: str, keyword: str, doc_name: str | None = None) -> list[dict]:
    safe_kw = _sanitize_ilike(keyword.strip())
    if not safe_kw:
        return []
    params: dict[str, str] = {
        "org_id": f"eq.{org_id}",
        "chunk_text": f"ilike.*{safe_kw}*",
        "select": "id,doc_name,chunk_text,metadata",
        "limit": "10",
    }
    if doc_name:
        params["doc_name"] = f"eq.{doc_name}"
    r = await get_http().get(f"{BASE}/rest/v1/documents", headers=HEADERS, params=params)
    logger.debug("KW search '%s' → status=%d len=%d", safe_kw, r.status_code, len(r.text))
    if r.status_code != 200 or not r.text.strip():
        return []
    try:
        return r.json()
    except Exception:
        logger.exception("db_keyword_search JSON parse error kw='%s'", safe_kw)
        return []


async def db_patch(table: str, filters: dict[str, Any], data: dict[str, Any]) -> list[dict]:
    params = {k: f"eq.{str(v)}" for k, v in filters.items()}
    r = await get_http().patch(
        f"{BASE}/rest/v1/{table}",
        headers=_merge_headers({"Prefer": "return=representation"}),
        params=params,
        json=data,
    )
    if r.status_code not in (200, 201):
        logger.error("db_patch %s → %d: %s", table, r.status_code, r.text[:400])
        raise RuntimeError(f"Supabase patch error {r.status_code}: {r.text}")
    return r.json()