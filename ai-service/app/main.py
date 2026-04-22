from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.auth import verify_token
from app.ingest import ingest_document
from app.query import answer_question, get_embedder, RAG_PROMPT,extract_keywords
from app.models import IngestResponse, QueryRequest, QueryResponse
from app.db import db_test, db_insert, db_rpc,db_keyword_search, HEADERS, BASE
from app.config import settings

from pydantic import BaseModel
from typing import Optional

import httpx
import json

# ------------------ APP ------------------

app = FastAPI(title="SecureStream AI Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ MODELS ------------------

class AnnotationCreate(BaseModel):
    doc_name: str
    selected_text: str
    note: str
    color: Optional[str] = "#FCD34D"
    is_shared: Optional[bool] = False


class AnnotationUpdate(BaseModel):
    is_shared: bool

# ------------------ ANNOTATIONS ------------------

@app.post("/annotations")
async def create_annotation(
    body: AnnotationCreate,
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("sub")
    user_email = claims.get("email", "unknown")

    rows = await db_insert("annotations", [{
        "org_id": org_id,
        "doc_name": body.doc_name,
        "user_email": user_email,
        "selected_text": body.selected_text,
        "note": body.note,
        "color": body.color,
        "is_shared": body.is_shared
    }])

    return rows[0] if rows else {}


@app.get("/annotations/{doc_name}")
async def get_annotations(
    doc_name: str,
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("sub")
    user_email = claims.get("email", "unknown")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/annotations",
            headers=HEADERS,
            params={
                "doc_name": f"eq.{doc_name}",
                "or": f"(user_email.eq.{user_email},is_shared.eq.true)",
                "org_id": f"eq.{org_id}",
                "order": "created_at.asc"
            }
        )
        return r.json()


@app.patch("/annotations/{annotation_id}")
async def update_annotation(
    annotation_id: str,
    body: AnnotationUpdate,
    claims: dict = Depends(verify_token)
):
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{BASE}/rest/v1/annotations",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={"id": f"eq.{annotation_id}"},
            json={"is_shared": body.is_shared}
        )
        data = r.json()
        return data[0] if data else {}

# ------------------ HEALTH ------------------

@app.get("/health")
async def health():
    db_ok = await db_test()
    return {
        "status": "ok",
        "db": "connected" if db_ok else "error"
    }

# ------------------ INGEST ------------------

@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: UploadFile = File(...),
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("sub")

    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")

    file_bytes = await file.read()
    return await ingest_document(file_bytes, file.filename, org_id)

# ------------------ STREAMING QUERY ------------------

@app.post("/query/stream")
async def query_stream(
    body: QueryRequest,
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("sub")

    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")

    # -------- EMBEDDING --------
    query_vector = get_embedder().embed_query(body.question)

    chunks = await db_rpc("match_documents", {
        "query_embedding": query_vector,
        "match_count":     6,
        "filter_org_id":   org_id
    })

    # Keyword search with actual question keywords
    keywords = extract_keywords(body.question)
    keyword_chunks = []
    for kw in keywords:
        kw_results = await db_keyword_search(org_id, kw)
        keyword_chunks.extend(kw_results)

    # Merge deduplicated — keyword first
    seen, all_chunks = set(), []
    for c in keyword_chunks + chunks:
        if c["chunk_text"] not in seen:
            seen.add(c["chunk_text"])
            all_chunks.append(c)

    top_chunks = all_chunks[:3]

    context = "\n\n---\n\n".join(
    f"[Doc: {c['doc_name']}]\n{c['chunk_text']}"
    for c in top_chunks
    )

    prompt = RAG_PROMPT.format(context=context, question=body.question)

    source_passages = [
        {
            "doc_name": c["doc_name"],
            "passage": c["chunk_text"],
            "similarity": round(c.get("similarity", 0), 3)
        }
        for c in top_chunks
    ]

    # -------- STREAM TOKENS --------
    async def stream_tokens():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.llm_model,
                        "prompt": prompt,
                        "stream": True,
                        "options": {
                            "temperature": 0.1,
                            "num_predict": 80,
                            "num_ctx": 512
                        }
                    }
                ) as resp:

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue

                        try:
                            data = json.loads(line)

                            if "response" in data:
                                yield f"data: {json.dumps({'token': data['response']})}\n\n"

                            if data.get("done"):
                                yield f"data: {json.dumps({
                                    'done': True,
                                    'sources': [c['chunk_text'][:200] + '...' for c in top_chunks],
                                    'source_passages': source_passages
                                })}\n\n"
                                break

                        except Exception:
                            continue

        except Exception:
            yield f"data: {json.dumps({'token': 'Error generating response'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(stream_tokens(), media_type="text/event-stream")

# ------------------ NORMAL QUERY ------------------

@app.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("sub")

    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")

    return await answer_question(body.question, org_id, body.top_k)

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
                "order": "created_at.desc"
            }
        )

        data = r.json()

        # -------- DEDUP --------
        seen = set()
        docs = []

        for d in data:
            name = d.get("doc_name")

            if name not in seen:
                seen.add(name)

                metadata = d.get("metadata") or {}

                docs.append({
                    "doc_name": name,
                    "created_at": d.get("created_at"),
                    "chunks": metadata.get("total_chunks", 0),
                    "file_url": d.get("file_url") 
                })

        return {"documents": docs, "org_id": org_id}


# ---------------- GET FULL TEXT ----------------
@app.get("/documents/{doc_name}/text")
async def get_document_text(
    doc_name: str,
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("custom:org_id") or claims.get("sub")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/rest/v1/documents",
            headers=HEADERS,
            params={
                "org_id": f"eq.{org_id}",
                "doc_name": f"eq.{doc_name}",
                "select": "chunk_text,metadata"
            }
        )

        chunks = r.json()

        # -------- SAFE SORT (IMPORTANT) --------
        chunks_sorted = sorted(
            chunks,
            key=lambda x: (x.get("metadata") or {}).get("chunk_index", 0)
        )

        # -------- REBUILD TEXT --------
        full_text = " ".join(c.get("chunk_text", "") for c in chunks_sorted)

        return {
            "doc_name": doc_name,
            "text": full_text,
            "chunk_count": len(chunks_sorted)
        }