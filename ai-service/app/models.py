from pydantic import BaseModel
from typing import Optional

class IngestResponse(BaseModel):
    message: str
    chunks_stored: int
    doc_name: str

class QueryRequest(BaseModel):
    question: str
    top_k: int =3          # how many chunks to retrieve

class QueryResponse(BaseModel):
    answer: str
    sources: list[str]      # chunk previews used to answer
    org_id: str