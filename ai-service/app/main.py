import json
import asyncio
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from app.auth import verify_token
from app.models import IngestResponse, QueryRequest

# System Orchestration Modules Imports
from app.ingestion_graph import ingestion_graph
from app.retrieval_graph import retrieval_graph

app = FastAPI(title="SecureStream AI Service", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://securestream1.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: UploadFile = File(...),
    claims: dict = Depends(verify_token),
    x_domain: str = Header(default="general"),
):
    org_id = claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id present inside token claims.")
        
    file_bytes = await file.read()
    
    state_input = {
        "file_bytes": file_bytes,
        "filename": file.filename,
        "org_id": org_id,
        "domain": x_domain,
        "raw_pages": [], "chunks": [], "embeddings": [], "errors": []
    }
    
    # Run structural extraction via LangGraph orchestration loop
    final_state = await ingestion_graph.ainvoke(state_input)
    if final_state.get("errors"):
        raise HTTPException(status_code=422, detail=final_state["errors"][-1])
        
    return {
        "message": "Ingested successfully",
        "chunks_stored": len(final_state.get("chunks", [])),
        "doc_name": file.filename,
    }

@app.post("/query/stream")
async def query_stream(
    body: QueryRequest,
    claims: dict = Depends(verify_token),
):
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing valid organizational target identifier mapping.")

    # Shared thread-safe communication primitive
    token_communication_queue = asyncio.Queue()

    initial_state = {
        "question": body.question,
        "org_id": org_id,
        "doc_name": body.doc_name,
        "token_queue": token_communication_queue,
        "strategy": {}, "query_vector": [], "keywords": [],
        "vector_results": [], "keyword_results": [], "combined_results": [],
        "context": "", "generation": "", "grounded": False
    }

    # Start graph computation in a separate concurrent task background worker loop
    graph_task = asyncio.create_task(retrieval_graph.ainvoke(initial_state))

    async def sse_stream_adapter_generator():
        try:
            while True:
                token = await token_communication_queue.get()
                if token is None: # Termination signal captured safely
                    token_communication_queue.task_done()
                    break
                # FIX: Use normal string concatenation with json.dumps instead of an inline f-string backslash
                yield "data: " + json.dumps({'token': token, 'done': False}) + "\n\n"
                token_communication_queue.task_done()
            
            # Wait for graph processing optimization components to wrap evaluation calculations
            final_graph_context = await graph_task
            combined_chunks = final_graph_context.get("combined_results", [])
            
            if not combined_chunks:
                yield "data: " + json.dumps({'token': 'No relevant content found.', 'done': False}) + "\n\n"
                yield "data: " + json.dumps({'done': True, 'sources': [], 'source_passages': []}) + "\n\n"
                return

            # Apply final grounding modifications dynamically if hallucinations are detected
            if not final_graph_context.get("grounded", True):
                # FIX: Backslash-safe syntax for the warning token
                warning_payload = {'token': '\n[Validation Warning: Generation detached from reference context]', 'done': False}
                yield "data: " + json.dumps(warning_payload) + "\n\n"

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

            # Dispatch final SSE metadata payload closure packet
            final_payload = {'done': True, 'sources': sources_summary, 'source_passages': source_passages}
            yield "data: " + json.dumps(final_payload) + "\n\n"
            
        except Exception as e:
            error_payload = {'token': f'\nStream execution fault: {str(e)}', 'done': True}
            yield "data: " + json.dumps(error_payload) + "\n\n"
            if not graph_task.done():
                graph_task.cancel()

    return StreamingResponse(
        sse_stream_adapter_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )