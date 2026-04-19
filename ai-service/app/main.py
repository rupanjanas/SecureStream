from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.auth import verify_token
from app.ingest import ingest_document
from app.query import answer_question
from app.models import IngestResponse, QueryRequest, QueryResponse
from app.db import db_test, db_insert
from pydantic import BaseModel
from typing import Optional
from fastapi import Query
from urllib.parse import quote

app = FastAPI(title="SecureStream AI Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnnotationCreate(BaseModel):
    doc_name: str
    selected_text: str
    note: str
    color: Optional[str] = "#FCD34D"
    is_shared: Optional[bool] = False

class AnnotationUpdate(BaseModel):
    is_shared: bool


@app.post("/annotations")
async def create_annotation(
    body: AnnotationCreate,
    claims: dict = Depends(verify_token)
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
        "is_shared":     body.is_shared
    }])
    return rows[0] if rows else {}

@app.get("/annotations/{doc_name}")
async def get_annotations(
    doc_name: str,
    claims: dict = Depends(verify_token)
):
    import httpx
    from app.db import HEADERS, BASE
    org_id     = claims.get("sub")
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
    import httpx
    from app.db import HEADERS, BASE
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{BASE}/rest/v1/annotations",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={"id": f"eq.{annotation_id}"},
            json={"is_shared": body.is_shared}
        )
        data = r.json()
        return data[0] if data else {}

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
    org_id =claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")
    file_bytes = await file.read()
    return await ingest_document(file_bytes, file.filename, org_id)

@app.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    claims: dict = Depends(verify_token)
):
    org_id = claims.get("sub")
    if not org_id:
        raise HTTPException(status_code=400, detail="No org_id in token")
    return await answer_question(body.question, org_id, body.top_k)