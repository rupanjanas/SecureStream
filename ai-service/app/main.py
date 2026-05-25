import asyncio

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
import json
import httpx
from app.auth import verify_token
from app.ingest import ingest_document
from app.query import retrieve, build_context, RAG_PROMPT, ask_groq
from app.models import IngestResponse, QueryRequest, QueryResponse
from app.db import db_insert, db_test, HEADERS, BASE
from app.config import settings
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from app.ratelimit import check_rate_limit
from fastapi import WebSocket, WebSocketDisconnect, Query as QQuery
from app.ws import hub


# ──────────────────────────────────────────────
# App + CORS
# ──────────────────────────────────────────────

app = FastAPI(title="SecureStream AI Service", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://securestream1.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]    = "nosniff"
        response.headers["X-Frame-Options"]           = "DENY"
        response.headers["X-XSS-Protection"]          = "1; mode=block"
        response.headers["Referrer-Policy"]            = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]         = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"]  = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only rate-limit AI endpoints
    if not request.url.path.startswith("/query"):
        return await call_next(request)

    # Extract user identity from token (best effort)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Decode without verification just to get sub for rate limiting
        try:
            import base64, json as _json
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload     = _json.loads(base64.b64decode(payload_b64))
            user_id     = payload.get("sub", "anonymous")
            # Determine tier from token claims
            tier        = payload.get("custom:tier", "free")
        except Exception:
            user_id = "anonymous"
            tier    = "free"
    else:
        user_id = "anonymous"
        tier    = "free"

    endpoint = request.url.path
    allowed, remaining, retry_after = await check_rate_limit(user_id, endpoint, tier)

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please slow down."},
            headers={
                "Retry-After":           str(retry_after),
                "X-RateLimit-Limit":     str(100),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset":     str(int(time.time()) + retry_after),
            }
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response

@app.websocket("/ws/doc/{doc_name}")
async def doc_websocket(
    websocket: WebSocket,
    doc_name:  str,
    token:     str = QQuery(...),
    org_id:    str = QQuery(...),
    email:     str = QQuery("anonymous"),
):
    """
    Real-time collaborative channel for a document.
    Client sends JSON events:
      { type: "cursor",     x, y, page }
      { type: "annotation", action: "create"|"update"|"delete", data: {...} }
      { type: "ping" }
    """
    await hub.connect(websocket, org_id, doc_name, email)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            # Attach sender identity and broadcast
            msg["email"] = email
            await hub.broadcast_event(websocket, msg)

    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception as e:
        print(f"[WS] Error: {e}")
        await hub.disconnect(websocket)
# ──────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────

class AnnotationCreate(BaseModel):
    doc_name:      str
    selected_text: str
    note:          str
    color:         Optional[str]  = "#FCD34D"
    is_shared:     Optional[bool] = False


class AnnotationUpdate(BaseModel):
    is_shared: bool


# ──────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────

@app.get("/health")
async def health():
    db_ok = await db_test()
    return {"status": "ok", "db": "connected" if db_ok else "error"}


# ──────────────────────────────────────────────
# Annotations
# ──────────────────────────────────────────────

@app.post("/annotations")
async def create_annotation(
    body:   AnnotationCreate,
    claims: dict = Depends(verify_token),
):
    org_id     = claims.get("custom:org_id") or claims.get("sub")
    user_email = claims.get("email", "dev@securestream.local")

    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")

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
):
    org_id     = claims.get("custom:org_id") or claims.get("sub")
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
async def update_annotation_note(
    annotation_id: str,
    body:          AnnotationCreate,
    claims:        dict = Depends(verify_token),
):
    """Update text content of an annotation — only owner can edit"""
    user_email = claims.get("email", "dev@securestream.local")

    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{BASE}/rest/v1/annotations",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={
                "id":         f"eq.{annotation_id}",
                "user_email": f"eq.{user_email}",   # owner-only guard
            },
            json={
                "note":          body.note,
                "color":         body.color,
                "selected_text": body.selected_text,
            },
        )
        data = r.json()
        if not data:
            raise HTTPException(status_code=403, detail="Not authorized or annotation not found")
        return data[0]


@app.delete("/annotations/{annotation_id}")
async def delete_annotation(
    annotation_id: str,
    claims:        dict = Depends(verify_token),
):
    """Delete annotation — only owner can delete"""
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


# ──────────────────────────────────────────────
# Ingest
# ──────────────────────────────────────────────

ALLOWED_MIME = {"application/pdf", "text/plain"}
MAX_FILE_MB  = 10

@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file:     UploadFile = File(...),
    claims:   dict       = Depends(verify_token),
    x_domain: str        = Header(default="general"),
):
    org_id = claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")

    # Validate MIME type
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {file.content_type}")

    file_bytes = await file.read()

    # Validate file size
    if len(file_bytes) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_MB}MB limit")

    # Sanitize filename — strip path traversal
    import os
    safe_filename = os.path.basename(file.filename or "upload").replace("..", "")

    return await ingest_document(file_bytes, safe_filename, org_id, domain=x_domain)


# ──────────────────────────────────────────────
# Streaming query — uses single retrieve() function
# ──────────────────────────────────────────────

@app.post("/query/stream")
async def query_stream(
    body:   QueryRequest,
    claims: dict = Depends(verify_token),
):
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")

    print(f"[SSE] question={body.question!r} org={org_id} doc={body.doc_name!r}")

    # ── Single retrieve call — handles vector + keyword internally ──
    combined = await retrieve(
        question = body.question,
        org_id   = org_id,
        doc_name = body.doc_name or "",
        top_k    = body.top_k,
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

async def stream_groq():
    full_answer = []
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream("POST", ...) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    chunk = json.loads(line[6:])
                    token = chunk["choices"][0]["delta"].get("content", "")
                    if token:
                        full_answer.append(token)
                        yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"
                except Exception:
                    continue

                answer_text = "".join(full_answer)
                asyncio.create_task(save_query_log(org_id, body.question, answer_text, source_passages))
                yield f"data: {json.dumps({'done': True, 'sources': [...], 'source_passages': source_passages})}\n\n"

    return StreamingResponse(
        stream_groq(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────────
# Documents list
# ──────────────────────────────────────────────

@app.get("/documents")
async def list_documents(claims: dict = Depends(verify_token)):
    org_id = claims.get("custom:org_id") or claims.get("sub")
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


# ──────────────────────────────────────────────
# Full document text
# ──────────────────────────────────────────────

@app.get("/documents/{doc_name}/text")
async def get_document_text(
    doc_name: str,
    claims:   dict = Depends(verify_token),
):
    org_id = claims.get("custom:org_id") or claims.get("sub")
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