import asyncio
import json
import os
import time

import httpx
from fastapi import (
    Depends, FastAPI, File, Header, HTTPException,
    Request, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from typing import Optional, Any

from app.auth import verify_token
from app.config import settings
from app.db import db_insert, db_test, HEADERS, BASE
from app.ingest import ingest_document
from app.models import IngestResponse, QueryRequest
from app.query import retrieve, build_context, RAG_PROMPT
from app.ratelimit import check_rate_limit


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="SecureStream AI Service", version="3.0.0")


# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://securestream1.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Security headers ──────────────────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]   = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]        = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)


# ── Models ────────────────────────────────────────────────────────────────────

class AnnotationCreate(BaseModel):
    doc_name:      str
    selected_text: str
    note:          str
    color:         Optional[str]  = "#FCD34D"
    is_shared:     Optional[bool] = False

class ChatHistoryBody(BaseModel):
    doc_name: str
    messages: list[Any]
    sources:  list[Any]


# ── Shared helper: resolve org_id ─────────────────────────────────────────────
# Rules:
#   • If X-Org-Id header is present and non-empty → org mode, use that value
#   • Otherwise → personal mode, use the token's sub claim
# This is the single source of truth used by every endpoint.

def resolve_org_id(x_org_id: str, claims: dict) -> str:
    explicit = x_org_id.strip()
    if explicit:
        return explicit
    return claims.get("sub", "")


# ── Rate limiting ─────────────────────────────────────────────────────────────

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if not request.url.path.startswith("/query"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    user_id = "anonymous"
    tier    = "free"

    if auth_header.startswith("Bearer "):
        try:
            import base64 as _b64, json as _json
            payload_b64  = auth_header[7:].split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload      = _json.loads(_b64.b64decode(payload_b64))
            user_id      = payload.get("sub", "anonymous")
            tier         = payload.get("custom:tier", "free")
        except Exception:
            pass

    allowed, remaining, retry_after = await check_rate_limit(user_id, request.url.path, tier)

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please slow down."},
            headers={
                "Retry-After":           str(retry_after),
                "X-RateLimit-Limit":     "100",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset":     str(int(time.time()) + retry_after),
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    db_ok = await db_test()
    return {"status": "ok", "db": "connected" if db_ok else "error"}


# ── Annotations ───────────────────────────────────────────────────────────────

@app.post("/annotations")
async def create_annotation(
    body:     AnnotationCreate,
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id     = resolve_org_id(x_org_id, claims)
    user_email = claims.get("email", "dev@securestream.local")

    rows = await db_insert("annotations", [{
        "org_id":        org_id,
        "doc_name":      body.doc_name,
        "user_email":    user_email,
        "selected_text": body.selected_text,
        "note":          body.note,
        "color":         body.color,
        "is_shared":     body.is_shared,
    }])
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to save annotation")
    return rows[0]


@app.get("/annotations/{doc_name}")
async def get_annotations(
    doc_name: str,
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id     = resolve_org_id(x_org_id, claims)
    user_email = claims.get("email", "dev@securestream.local")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/annotations",
            headers=HEADERS,
            params={
                "org_id":   f"eq.{org_id}",
                "doc_name": f"eq.{doc_name}",
                "or":       f"(user_email.eq.{user_email},is_shared.eq.true)",
                "order":    "created_at.asc",
                "select":   "*",
            },
        )
        if r.status_code != 200:
            print(f"[ANNOTATIONS] Supabase error: {r.status_code} {r.text}")
            return []
        data = r.json()
        return data if isinstance(data, list) else []


@app.put("/annotations/{annotation_id}")
async def update_annotation(
    annotation_id: str,
    body:          AnnotationCreate,
    claims:        dict = Depends(verify_token),
):
    user_email = claims.get("email", "dev@securestream.local")
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{BASE}/rest/v1/annotations",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={
                "id":         f"eq.{annotation_id}",
                "user_email": f"eq.{user_email}",
            },
            json={
                "note":          body.note,
                "color":         body.color,
                "selected_text": body.selected_text,
            },
        )
        data = r.json()
        if not data:
            raise HTTPException(status_code=403, detail="Not authorized or not found")
        return data[0]


@app.delete("/annotations/{annotation_id}")
async def delete_annotation(
    annotation_id: str,
    claims:        dict = Depends(verify_token),
):
    user_email = claims.get("email", "dev@securestream.local")
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{BASE}/rest/v1/annotations",
            headers=HEADERS,
            params={
                "id":         f"eq.{annotation_id}",
                "user_email": f"eq.{user_email}",
            },
        )
        return {"deleted": r.status_code == 204}


# ── Ingest ────────────────────────────────────────────────────────────────────

ALLOWED_MIME = {"application/pdf", "text/plain"}
MAX_FILE_MB  = 10


@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file:     UploadFile = File(...),
    claims:   dict       = Depends(verify_token),
    x_domain: str        = Header(default="general"),
    x_org_id: str        = Header(default=""),
):
    org_id = resolve_org_id(x_org_id, claims)
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id resolved")

    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {file.content_type}")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_MB} MB limit")

    safe_filename = os.path.basename(file.filename or "upload").replace("..", "")
    return await ingest_document(file_bytes, safe_filename, org_id, domain=x_domain)


# ── Query log helper ──────────────────────────────────────────────────────────

async def _save_query_log(org_id, question, answer, sources):
    try:
        await db_insert("query_logs", [{
            "org_id":   org_id,
            "question": question,
            "answer":   answer,
            "sources":  sources,
        }])
    except Exception as e:
        print(f"[QUERY LOG] Failed to save: {e}")


# ── Streaming query ───────────────────────────────────────────────────────────

@app.post("/query/stream")
async def query_stream(
    body:     QueryRequest,
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id = resolve_org_id(x_org_id, claims)
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id resolved")

    print(f"[SSE] question={body.question!r} org={org_id} doc={body.doc_name!r}")

    combined = await retrieve(
        question=body.question,
        org_id=org_id,
        doc_name=body.doc_name or "",
        top_k=body.top_k,
    )

    if not combined:
        async def empty():
            yield f"data: {json.dumps({'token': 'No relevant content found in the document.', 'done': False})}\n\n"
            yield f"data: {json.dumps({'done': True, 'sources': [], 'source_passages': []})}\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    context = build_context(combined)
    prompt  = RAG_PROMPT.format(context=context, question=body.question)

    source_passages = [
        {
            "doc_name":    c["doc_name"],
            "passage":     c["chunk_text"],
            "similarity":  round(c.get("similarity", 0), 3),
            "section":     (c.get("metadata") or {}).get("section", ""),
            "page_number": (c.get("metadata") or {}).get("page_number", 1),
        }
        for c in combined
    ]
    unique_sources = list({c["doc_name"] for c in combined})

    async def stream_groq():
        full_answer: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST",
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.groq_api_key}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":      "llama3-70b-8192",
                        "messages":   [{"role": "user", "content": prompt}],
                        "stream":     True,
                        "max_tokens": 1024,
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                            continue
                        try:
                            chunk = json.loads(line[6:])
                            token = chunk["choices"][0]["delta"].get("content", "")
                            if token:
                                full_answer.append(token)
                                yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"
                        except Exception:
                            continue
        except Exception as e:
            yield f"data: {json.dumps({'token': f'Error: {str(e)}', 'done': False})}\n\n"

        yield f"data: {json.dumps({'done': True, 'sources': unique_sources, 'source_passages': source_passages})}\n\n"
        asyncio.create_task(
            _save_query_log(org_id, body.question, "".join(full_answer), source_passages)
        )

    return StreamingResponse(
        stream_groq(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Documents list ────────────────────────────────────────────────────────────

@app.get("/documents")
async def list_documents(
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id = resolve_org_id(x_org_id, claims)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/documents",
            headers=HEADERS,
            params={
                "org_id": f"eq.{org_id}",
                "select": "doc_name,created_at,metadata,file_url",
                "order":  "created_at.desc",
            },
        )
        data = r.json()

    seen: set[str] = set()
    docs: list[dict] = []
    for d in data:
        name = d.get("doc_name")
        if name not in seen:
            seen.add(name)
            meta = d.get("metadata") or {}
            docs.append({
                "doc_name":   name,
                "created_at": d.get("created_at"),
                "chunks":     meta.get("total_chunks", 0),
                "file_url":   d.get("file_url"),
                "domain":     meta.get("domain", "general"),
            })
    return {"documents": docs, "org_id": org_id}


# ── File URL ──────────────────────────────────────────────────────────────────

@app.get("/documents/file-url")
async def get_document_file_url(
    doc_name: str,
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id = resolve_org_id(x_org_id, claims)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/documents",
            headers=HEADERS,
            params={
                "org_id":   f"eq.{org_id}",
                "doc_name": f"eq.{doc_name}",
                "select":   "file_url",
                "limit":    "1",
            },
        )
        rows = r.json()

    if not rows or not rows[0].get("file_url"):
        raise HTTPException(status_code=404, detail="file_url not found for this document")
    return {"file_url": rows[0]["file_url"]}


# ── Document text ─────────────────────────────────────────────────────────────

@app.get("/documents/{doc_name}/text")
async def get_document_text(
    doc_name: str,
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id = resolve_org_id(x_org_id, claims)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/documents",
            headers=HEADERS,
            params={
                "org_id":   f"eq.{org_id}",
                "doc_name": f"eq.{doc_name}",
                "select":   "chunk_text,metadata",
            },
        )
        chunks = r.json()

    chunks_sorted = sorted(
        chunks,
        key=lambda x: (x.get("metadata") or {}).get("chunk_index", 0),
    )
    full_text = " ".join(c.get("chunk_text", "") for c in chunks_sorted)
    return {"doc_name": doc_name, "text": full_text, "chunk_count": len(chunks_sorted)}


# ── Chat history ──────────────────────────────────────────────────────────────

@app.get("/chat-history/{doc_name}")
async def get_chat_history(
    doc_name: str,
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id = resolve_org_id(x_org_id, claims)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/chat_history",
            headers=HEADERS,
            params={
                "org_id":   f"eq.{org_id}",
                "doc_name": f"eq.{doc_name}",
                "select":   "messages,sources",
                "limit":    "1",
            },
        )
        data = r.json()

    if not data or not isinstance(data, list) or len(data) == 0:
        return {"messages": [], "sources": []}
    return {
        "messages": data[0].get("messages", []),
        "sources":  data[0].get("sources",  []),
    }


@app.post("/chat-history")
async def save_chat_history(
    body:     ChatHistoryBody,
    claims:   dict = Depends(verify_token),
    x_org_id: str  = Header(default=""),
):
    org_id = resolve_org_id(x_org_id, claims)

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/rest/v1/chat_history",
            headers={
                **HEADERS,
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            json={
                "org_id":     org_id,
                "doc_name":   body.doc_name,
                "messages":   body.messages,
                "sources":    body.sources,
                "updated_at": "now()",
            },
        )
        print(f"[CHAT-HISTORY] status={r.status_code} body={r.text[:300]}")
        if r.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"Supabase error {r.status_code}: {r.text}"
            )
        data = r.json()
        return data[0] if isinstance(data, list) else data