from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from app.auth import verify_token
from app.cache import get_cached, get_semantic_cache, set_cached, set_semantic_cache
from app.config import settings
from app.db import close_http, db_get, db_insert, db_patch, db_test, db_upsert
from app.ingest import ingest_document
from app.models import IngestResponse, QueryRequest
from app.query import RAG_PROMPT, build_context, retrieve, save_query_log
from app.ratelimit import check_rate_limit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_http()


app = FastAPI(title="SecureStream AI Service", version="4.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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



@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if not request.url.path.startswith("/query"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    user_id     = "anonymous"
    tier        = "free"

    if auth_header.startswith("Bearer "):
        try:
            import base64 as _b64
            payload_b64  = auth_header[7:].split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload      = json.loads(_b64.b64decode(payload_b64))
            user_id      = payload.get("sub", "anonymous")
            tier         = payload.get("custom:tier", "free")
        except Exception:
            pass

    if user_id == "anonymous":
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip        = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )
        user_id = f"anon:{ip}"

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


class ChatHistoryBody(BaseModel):
    doc_name: str
    messages: list[Any]
    sources:  list[Any]


def get_user_id(claims: dict) -> str:
    uid = claims.get("sub", "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token: missing sub claim")
    return uid


ALLOWED_MIME = {"application/pdf", "text/plain"}
MAX_FILE_MB  = 10



@app.get("/health")
async def health():
    db_status = await db_test()
    return {"status": "ok", "db": "connected" if db_status["ok"] else "error"}


@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file:     UploadFile = File(...),
    claims:   dict       = Depends(verify_token),
    x_domain: str        = Header(default="general"),
):
    user_id = get_user_id(claims)

    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {file.content_type}")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_MB} MB limit")

    safe_filename = os.path.basename(file.filename or "upload").replace("..", "")
    result = await ingest_document(file_bytes, safe_filename, user_id, domain=x_domain)

    if result["chunks_stored"] == 0 and result["message"] not in ("No content extracted",):
        raise HTTPException(status_code=422, detail=result["message"])

    return result


@app.post("/query/stream")
async def query_stream(
    body:   QueryRequest,
    claims: dict = Depends(verify_token),
):
    user_id = get_user_id(claims)
    logger.info("[SSE] question=%r user=%s doc=%r", body.question, user_id, body.doc_name)

    # ------------------------------------------------------------------
    # Layer 1: exact cache — MD5 of the question, no embedding needed.
    # ------------------------------------------------------------------
    cached = await get_cached(user_id, body.question)
    if cached:
        logger.info("[SSE] exact cache HIT user=%s", user_id)

        async def stream_cached_exact():
            answer = cached.get("answer", "")
            # Stream the cached answer token-by-token so the UI behaves
            # identically whether the answer is live or cached.
            for word in answer.split(" "):
                yield f"data: {json.dumps({'token': word + ' ', 'done': False})}\n\n"
            yield f"data: {json.dumps({'done': True, 'sources': cached.get('sources', []), 'source_passages': cached.get('source_passages', [])})}\n\n"

        return StreamingResponse(stream_cached_exact(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------
    # Retrieval — also returns the query vector produced during vector
    # search so we can (a) check semantic cache and (b) write it back
    # later without a second Jina round-trip.
    # ------------------------------------------------------------------
    combined, query_vector = await retrieve(
        question=body.question,
        org_id=user_id,
        doc_name=body.doc_name or "",
        top_k=body.top_k,
    )

    if query_vector:
        sem_cached = await get_semantic_cache(user_id, query_vector)
        if sem_cached:
            logger.info("[SSE] semantic cache HIT user=%s", user_id)

            async def stream_cached_semantic():
                answer = sem_cached.get("answer", "")
                for word in answer.split(" "):
                    yield f"data: {json.dumps({'token': word + ' ', 'done': False})}\n\n"
                yield f"data: {json.dumps({'done': True, 'sources': sem_cached.get('sources', []), 'source_passages': sem_cached.get('source_passages', [])})}\n\n"

            return StreamingResponse(stream_cached_semantic(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------
    # No cache hit — full pipeline.
    # ------------------------------------------------------------------
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
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
                async with client.stream(
                    "POST",
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.groq_api_key}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":      settings.groq_model,
                        "messages":   [{"role": "user", "content": prompt}],
                        "stream":     True,
                        "max_tokens": settings.groq_max_tokens,
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
        except Exception as exc:
            logger.exception("Groq stream error")
            yield f"data: {json.dumps({'token': f'Error: {exc}', 'done': False})}\n\n"

        yield f"data: {json.dumps({'done': True, 'sources': unique_sources, 'source_passages': source_passages})}\n\n"
        full_answer_str = "".join(full_answer)

        result_to_cache = {
            "answer":          full_answer_str,
            "sources":         unique_sources,
            "source_passages": source_passages,
        }

        # Exact cache — synchronous write in a background task.
        asyncio.create_task(
            set_cached(user_id, body.question, result_to_cache, ttl=300)
        )

        # Semantic cache — only if we have the query vector from retrieval.
        if query_vector:
            asyncio.create_task(
                set_semantic_cache(user_id, body.question, query_vector, result_to_cache, ttl=300)
            )

        # Query log.
        asyncio.create_task(
            save_query_log(user_id, body.question, full_answer_str, source_passages)
        )

    return StreamingResponse(
        stream_groq(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/documents")
async def list_documents(claims: dict = Depends(verify_token)):
    user_id = get_user_id(claims)
    data    = await db_get("documents", {
        "org_id": f"eq.{user_id}",
        "select": "doc_name,created_at,metadata,file_url",
        "order":  "created_at.desc",
    })
    seen: set[str] = set()
    docs: list[dict] = []
    for d in data:
        name = d.get("doc_name")
        if name and name not in seen:
            seen.add(name)
            meta = d.get("metadata") or {}
            docs.append({
                "doc_name":   name,
                "created_at": d.get("created_at"),
                "chunks":     meta.get("total_chunks", 0),
                "file_url":   d.get("file_url") or meta.get("file_url"),
                "domain":     meta.get("domain", "general"),
            })
    return {"documents": docs}


@app.get("/documents/file-url")
async def get_document_file_url(doc_name: str, claims: dict = Depends(verify_token)):
    user_id = get_user_id(claims)
    rows    = await db_get("documents", {
        "org_id":   f"eq.{user_id}",
        "doc_name": f"eq.{doc_name}",
        "select":   "file_url,metadata",
        "limit":    "1",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="Document not found")
    row      = rows[0]
    file_url = row.get("file_url") or (row.get("metadata") or {}).get("file_url")
    if not file_url:
        raise HTTPException(status_code=404, detail="file_url not found for this document")
    return {"file_url": file_url}


@app.get("/documents/{doc_name}/text")
async def get_document_text(
    doc_name:  str,
    claims:    dict = Depends(verify_token),
    page:      int  = 1,
    page_size: int  = 50,
):
    user_id = get_user_id(claims)
    chunks  = await db_get("documents", {
        "org_id":   f"eq.{user_id}",
        "doc_name": f"eq.{doc_name}",
        "select":   "chunk_text,metadata",
        "order":    "metadata->chunk_index.asc",
        "limit":    str(page_size),
        "offset":   str((page - 1) * page_size),
    })
    full_text = " ".join(c.get("chunk_text", "") for c in chunks)
    return {
        "doc_name":    doc_name,
        "text":        full_text,
        "chunk_count": len(chunks),
        "page":        page,
        "page_size":   page_size,
    }


@app.get("/chat-history/{doc_name}")
async def get_chat_history(doc_name: str, claims: dict = Depends(verify_token)):
    user_id = get_user_id(claims)
    try:
        data = await db_get("chat_history", {
            "org_id":   f"eq.{user_id}",
            "doc_name": f"eq.{doc_name}",
            "select":   "messages,sources",
            "limit":    "1",
        })
    except Exception:
        return {"messages": [], "sources": []}
    if not data:
        return {"messages": [], "sources": []}
    return {
        "messages": data[0].get("messages", []),
        "sources":  data[0].get("sources",  []),
    }


@app.post("/chat-history")
async def save_chat_history(
    body:   ChatHistoryBody,
    claims: dict = Depends(verify_token),
):
    user_id = get_user_id(claims)
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "messages":   body.messages,
        "sources":    body.sources,
        "updated_at": now_iso,
    }

    try:
        patched = await db_patch(
            "chat_history",
            filters={"org_id": user_id, "doc_name": body.doc_name},
            data=payload,
        )
        if patched:
            return patched[0]
    except Exception:
        logger.debug("chat_history PATCH found no rows — falling through to upsert")

    rows = await db_upsert(
        "chat_history",
        [{"org_id": user_id, "doc_name": body.doc_name, **payload}],
        on_conflict="org_id,doc_name",
    )
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to save chat history")
    return rows[0] if isinstance(rows, list) else rows