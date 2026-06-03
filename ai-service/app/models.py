from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class IngestResponse(BaseModel):
    message:       str
    chunks_stored: int
    doc_name:      str
    file_url:      Optional[str] = None


class QueryRequest(BaseModel):
    question:    str            = Field(..., min_length=1, max_length=2000)
    top_k:       int            = Field(default=5, ge=1, le=20)
    doc_name:    Optional[str]  = None
    chat_history: list[dict]   = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped

    @field_validator("doc_name")
    @classmethod
    def strip_doc_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            return v or None
        return None


class QueryResponse(BaseModel):
    answer:          str
    sources:         list[str]
    org_id:          str
    source_passages: list[dict] = Field(default_factory=list)
    chat_history:    list[dict] = Field(default_factory=list)
    grounded:        bool       = True