from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

import json
import httpx
from app.auth import verify_token
from app.ingest import ingest_document
from app.query import retrieve, build_context, RAG_PROMPT, ask_groq
from app.models import IngestResponse, QueryRequest, QueryResponse
from app.db import db_insert, db_test, HEADERS, BASE
from app.config import settings


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
    org_id     = claims.get("sub")
    user_email = claims.get("email", "unknown")
    rows = await db_insert("annotations", [{
        "org_id":        org_id,
        "doc_name":      body.doc_name,
        "user_email":    user_email,
        "selected_text": body.selected_text,
        "note":          body.note,
        "color":         body.color,
        "is_shared":     body.is_shared,
    }])
    return rows[0] if rows else {}


@app.get("/annotations/{doc_name}")
async def get_annotations(
    doc_name: str,
    claims:   dict = Depends(verify_token),
):
    org_id     = claims.get("sub")
    user_email = claims.get("email", "unknown")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/annotations",
            headers=HEADERS,
            params={
                "doc_name": f"eq.{doc_name}",
                "or":       f"(user_email.eq.{user_email},is_shared.eq.true)",
                "org_id":   f"eq.{org_id}",
                "order":    "created_at.asc",
            },
        )
        return r.json()


@app.patch("/annotations/{annotation_id}")
async def update_annotation(
    annotation_id: str,
    body:          AnnotationUpdate,
    claims:        dict = Depends(verify_token),
):
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{BASE}/rest/v1/annotations",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={"id": f"eq.{annotation_id}"},
            json={"is_shared": body.is_shared},
        )
        data = r.json()
        return data[0] if data else {}


# ──────────────────────────────────────────────
# Ingest
# ──────────────────────────────────────────────

@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file:     UploadFile = File(...),
    claims:   dict       = Depends(verify_token),
    x_domain: str        = Header(default="general"),
):
    org_id = claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")
    file_bytes = await file.read()
    return await ingest_document(file_bytes, file.filename, org_id, domain=x_domain)


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
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST",
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "llama-3.1-8b-instant",
                    "temperature": 0.1,
                    "max_tokens":  600,
                    "stream":      True,
                    "messages":    [{"role": "user", "content": prompt}],
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        chunk = json.loads(line[6:])
                        token = chunk["choices"][0]["delta"].get("content", "")
                        if token:
                            yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"
                    except Exception:
                        continue

                yield f"data: {json.dumps({'done': True, 'sources': [c['chunk_text'][:200] + '...' for c in combined], 'source_passages': source_passages})}\n\n"

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