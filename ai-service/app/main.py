from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.auth import verify_token
from app.ingest import ingest_document
from app.query import answer_question
from app.models import IngestResponse, QueryRequest, QueryResponse
from app.db import db_test

app = FastAPI(title="SecureStream AI Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    db_ok = await db_test()
    return {
        "status": "ok",
        "db": "connected" if db_ok else "error"
    }

@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: UploadFile = File(...),
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")
    file_bytes = await file.read()
    return await ingest_document(file_bytes, file.filename, org_id)

@app.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("custom:org_id") or claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")
    return await answer_question(body.question, org_id, body.top_k)