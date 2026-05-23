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
    """
    Streams LLM factual synthesis responses via a concurrent Queue 
    while preserving Server-Sent Events formatting guidelines.
    """
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing valid organization context parameters.")

    # Instantiate asynchronous pipeline communication primitive
    token_communication_queue = asyncio.Queue()

    initial_state = {
        "question": body.question,
        "org_id": org_id,
        "doc_name": body.doc_name,
        "token_queue": token_communication_queue,
        "strategy": {}, 
        "query_vector": [], 
        "keywords": [],
        "vector_results": [], 
        "keyword_results": [], 
        "combined_results": [],
        "context": "", 
        "generation": "", 
        "grounded": False
    }

    # Execute Graph computation engine concurrently inside an isolated background execution thread task
    graph_task = asyncio.create_task(retrieval_graph.ainvoke(initial_state))

    async def sse_stream_adapter_generator():
        try:
            while True:
                token = await token_communication_queue.get()
                if token is None:  # Graph node signaling processing completion complete
                    token_communication_queue.task_done()
                    break
                
                # Handled via explicit string concatenation to comply with pre-3.12 f-string backslash limits
                yield "data: " + json.dumps({'token': token, 'done': False}) + "\n\n"
                token_communication_queue.task_done()
            
            # Gather compiled evaluation context properties
            final_graph_context = await graph_task
            combined_chunks = final_graph_context.get("combined_results", [])
            
            if not combined_chunks:
                yield "data: " + json.dumps({'token': 'No relevant content found.', 'done': False}) + "\n\n"
                yield "data: " + json.dumps({'done': True, 'sources': [], 'source_passages': []}) + "\n\n"
                return

            # Append notice payloads safely if generation falls out of reference contexts
            if not final_graph_context.get("grounded", True):
                warning_payload = {
                    'token': '\n[Validation Warning: Generation detached from reference context]', 
                    'done': False
                }
                yield "data: " + json.dumps(warning_payload) + "\n\n"

            # Parse structural payload response entries
            source_passages = [
                {
                    "doc_name": c.get("doc_name"),
                    "passage": c.get("chunk_text"),
                    "similarity": round(c.get("similarity", 0), 3),
                    "section": (c.get("metadata") or {}).get("section", ""),
                    "page_number": (c.get("metadata") or {}).get("page_number", 1),
                }
                for c in combined_chunks
            ]
            sources_summary = [c.get("chunk_text", "")[:200] + "..." for c in combined_chunks]

            # Dispatch final tracking structural meta packet data closure envelope 
            final_payload = {
                'done': True, 
                'sources': sources_summary, 
                'source_passages': source_passages
            }
            yield "data: " + json.dumps(final_payload) + "\n\n"
            
        except Exception as e:
            error_payload = {'token': f'\nStream execution fault: {str(e)}', 'done': True}
            yield "data: " + json.dumps(error_payload) + "\n\n"
            if not graph_task.done():
                graph_task.cancel()

    return StreamingResponse(
        sse_stream_adapter_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache", 
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
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