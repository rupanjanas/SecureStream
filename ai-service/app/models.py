from pydantic import BaseModel
from typing import Optional

class IngestResponse(BaseModel):
    message:       str
    chunks_stored: int
    doc_name:      str
    file_url:      Optional[str] = None   # ← ADD: Supabase Storage public URL

class QueryRequest(BaseModel):
    question:     str
    top_k:        int          = 5
    doc_name:     Optional[str] = None
    chat_history: list[dict]   = []

class QueryResponse(BaseModel):
    answer:          str
    sources:         list[str]
    org_id:          str
    source_passages: list[dict] = []      # ← ADD: passage metadata for the viewer
    chat_history:    list[dict] = []