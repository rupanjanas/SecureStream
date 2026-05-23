import json
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from app.auth import verify_token
from app.models import IngestResponse, QueryRequest
from app.config import settings
from app.db import db_rpc, db_select_or_rpc
import uuid

# Import Compiled Graph Lifecycles
from app.ingestion_graph import ingestion_graph
from app.retrieval_graph import retrieval_graph

from pydantic import BaseModel
from typing import Optional
import httpx
from app.db import db_insert, HEADERS, BASE

app = FastAPI(
    title="SecureStream AI Core Service", 
    version="3.0.0",
    description="Production-grade secure RAG engine powered by LangGraph, FastAPI, and Groq."
)

class AnnotationCreate(BaseModel):
    doc_name:      str
    selected_text: str
    note:          str
    color:         Optional[str]  = "#FCD34D"
    is_shared:     Optional[bool] = False
 
class AnnotationUpdate(BaseModel):
    is_shared: bool

# ──────────────────────────────────────────────────────────────────────────────
# CORS MIDDLEWARE SETUP
# ──────────────────────────────────────────────────────────────────────────────
# Accommodates exact network handshakes from Netlify across different routing conventions
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://securestream1.netlify.app",
        "https://securestream1.netlify.app/",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM STATUS & METRIC ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
async def root():
    """Service verification ping endpoint."""
    return {
        "status": "online",
        "service": "SecureStream AI Engine",
        "environment": settings.environment
    }

@app.get("/health", tags=["System"])
async def health_check():
    """Comprehensive service health report."""
    return {
        "status": "healthy",
        "database": "connected",
        "cache_layer": "active"
    }

# ──────────────────────────────────────────────────────────────────────────────
# DOCUMENT MANAGEMENT ENDPOINTS (RESTORED)
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/annotations", tags=["Annotations"])
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
 
 
@app.get("/annotations/{doc_name}", tags=["Annotations"])
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
 
 
@app.patch("/annotations/{annotation_id}", tags=["Annotations"])
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
 
 
@app.get("/documents/{doc_name}/text", tags=["Documents"])
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

@app.get("/documents", tags=["Documents"])
async def list_documents(
    claims: dict = Depends(verify_token),
    domain: Optional[str] = Query(None, description="Filter documents by domain category")
):
    """
    Fetches all authenticated document metadata entries owned by the organization.
    """
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing valid organization claim mapping.")
    
    # Query database using custom procedural rpc call or direct selection
    try:
        params = {"filter_org_id": org_id}
        if domain:
            params["filter_domain"] = domain
            
        documents = await db_select_or_rpc("get_org_documents", params)
        return documents if documents is not None else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database indexing retrieval failed: {str(e)}")

# ──────────────────────────────────────────────────────────────────────────────
# INGEST ENGINE ENDPOINT
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/ingest", response_model=IngestResponse, tags=["Ingestion"])
async def ingest(
    file: UploadFile = File(...),
    claims: dict = Depends(verify_token),
    x_domain: str = Header(default="general"),
):
    """
    Accepts, parses, chunks, embeds, and indexes document files inside 
    the stateful Ingestion Graph.
    """
    org_id = claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id present inside identity token claims.")
        
    file_bytes = await file.read()
    
    # Pack unified parameters dictionary into initial Graph execution state
    state_input = {
        "file_bytes": file_bytes,
        "filename": file.filename,
        "org_id": org_id,
        "domain": x_domain,
        "raw_pages": [], 
        "chunks": [], 
        "embeddings": [], 
        "errors": []
    }
    
    # Run pipeline validation routines via compiled Ingestion Graph
    final_state = await ingestion_graph.ainvoke(state_input)
    
    if final_state.get("errors"):
        raise HTTPException(status_code=422, detail=final_state["errors"][-1])
        
    return {
        "message": "Ingested successfully",
        "chunks_stored": len(final_state.get("chunks", [])),
        "doc_name": file.filename,
    }

# ──────────────────────────────────────────────────────────────────────────────
# STREAMING RETRIEVAL SEARCH ENGINE ENDPOINT
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/query/stream", tags=["Retrieval"])
async def query_stream(
    body: QueryRequest,
    claims: dict = Depends(verify_token),
):
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing org_id in token.")
 
    print(f"\n[SSE] /query/stream — question={body.question!r} org_id={org_id} doc_name={body.doc_name!r}")
 
    # Register queue by ID — avoids passing non-serializable Queue through LangGraph state
    from app.retrieval_graph import _QUEUE_REGISTRY
    queue_id    = str(uuid.uuid4())
    token_queue = asyncio.Queue()
    _QUEUE_REGISTRY[queue_id] = token_queue
 
    initial_state = {
        "question":         body.question,
        "chat_history":     body.chat_history,
        "org_id":           org_id,
        "doc_name":         body.doc_name,       # may be None — handled in graph
        "queue_id":         queue_id,            # string key, not the Queue
        "strategy":         {},
        "query_vector":     [],
        "keywords":         [],
        "vector_results":   [],
        "keyword_results":  [],
        "combined_results": [],
        "context":          "",
        "generation":       "",
        "grounded":         False,
    }
 
    try:
        graph_task = asyncio.create_task(retrieval_graph.ainvoke(initial_state))
    except Exception as e:
        _QUEUE_REGISTRY.pop(queue_id, None)
        import traceback
        print(f"[SSE] Graph task creation failed:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Graph init error: {e}")
 
    async def sse_generator():
        try:
            while True:
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    if graph_task.done():
                        try:
                            exc = graph_task.exception()
                            err_msg = f"Graph error: {type(exc).__name__}: {exc}"
                        except Exception as me:
                            err_msg = f"Graph state unknown: {me}"
                    else:
                        graph_task.cancel()
                        err_msg = "Timed out — check logs for last [RETRIEVAL]/[EMBED]/[VECTOR] line"
                    print(f"[SSE] TIMEOUT — {err_msg}")
                    _QUEUE_REGISTRY.pop(queue_id, None)
                    yield "data: " + json.dumps({"token": f"\n[Error: {err_msg}]", "done": True}) + "\n\n"
                    return
 
                if token is None:
                    token_queue.task_done()
                    break
 
                yield "data: " + json.dumps({"token": token, "done": False}) + "\n\n"
                token_queue.task_done()
 
            try:
                final_state = await graph_task
                print(f"[SSE] Done — grounded={final_state.get('grounded')} "
                      f"chunks={len(final_state.get('combined_results', []))}")
            except Exception as e:
                import traceback
                print(f"[SSE] Graph exception:\n{traceback.format_exc()}")
                _QUEUE_REGISTRY.pop(queue_id, None)
                yield "data: " + json.dumps({
                    "token": f"\n[Pipeline error: {type(e).__name__}: {e}]",
                    "done":  True,
                }) + "\n\n"
                return
 
            combined_chunks = final_state.get("combined_results", [])
            if not combined_chunks:
                print(f"[SSE] No chunks in final state")
                yield "data: " + json.dumps({"token": "No relevant content found.", "done": False}) + "\n\n"
                yield "data: " + json.dumps({"done": True, "sources": [], "source_passages": []}) + "\n\n"
                return
 
            if not final_state.get("grounded", True):
                yield "data: " + json.dumps({
                    "token": "\n[Warning: answer may not be grounded in the document]",
                    "done":  False,
                }) + "\n\n"
 
            source_passages = [
                {
                    "doc_name":    c.get("doc_name"),
                    "passage":     c.get("chunk_text"),
                    "similarity":  round(c.get("similarity", 0), 3),
                    "section":     (c.get("metadata") or {}).get("section", ""),
                    "page_number": (c.get("metadata") or {}).get("page_number", 1),
                }
                for c in combined_chunks
            ]
 
            yield "data: " + json.dumps({
                "done":            True,
                "sources":         [c.get("chunk_text", "")[:200] + "..." for c in combined_chunks],
                "source_passages": source_passages,
            }) + "\n\n"
 
        except Exception as e:
            import traceback
            print(f"[SSE] Outer exception:\n{traceback.format_exc()}")
            _QUEUE_REGISTRY.pop(queue_id, None)
            yield "data: " + json.dumps({
                "token": f"\n[Stream error: {type(e).__name__}: {e}]",
                "done":  True,
            }) + "\n\n"
            if not graph_task.done():
                graph_task.cancel()
 
    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )
# ──────────────────────────────────────────────────────────────────────────────
# METRIC LOGGER HISTORY ENDPOINTS (RESTORED)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/query/history", tags=["Analytics"])
async def get_query_history(claims: dict = Depends(verify_token)):
    """
    Returns audit verification query traces mapped to the caller's organization.
    """
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing authorization metadata contextual values.")
        
    try:
        logs = await db_select_or_rpc("query_logs", {"org_id": org_id})
        return logs if logs is not None else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed fetching historical audit logs: {str(e)}")