"""
ingest_graph.py — LangGraph pipeline for document ingestion.

Key changes from original:
- Added `file_url` to IngestionState so it flows through all nodes and
  is stored in every row (the original store_node was missing it entirely).
- Added a `deduplicate_node` between chunk and embed so we never embed
  duplicate text chunks.
- `validate_node` now also checks for encrypted PDFs so we fail fast.
- `embed_node` uses the shared embed_texts helper (with semaphore) from ingest.py.
- `store_node` now writes `file_url` at the row level, matching ingest.py schema.
- Routing helper is a named function (not a lambda) so LangGraph can serialise it.
- Added an `upload_node` as the first step so file_url is resolved once and
  available to all subsequent nodes.
"""

import asyncio
import fitz
from typing import Dict, Any, List, TypedDict, Optional

from langgraph.graph import StateGraph, END

from app.config import settings
from app.db import db_insert
from app.ingest import (
    process_and_chunk_document,
    embed_texts,
    upload_to_storage,
    fingerprint,
)


# ── State ─────────────────────────────────────────────────────────────────────

class IngestionState(TypedDict):
    file_bytes:  bytes
    filename:    str
    org_id:      str
    domain:      str
    file_url:    Optional[str]          # resolved in upload_node
    raw_pages:   List[Dict[str, Any]]
    chunks:      List[Dict[str, Any]]
    embeddings:  List[List[float]]
    errors:      List[str]


# ── Routing ───────────────────────────────────────────────────────────────────

def routing_decision(state: IngestionState) -> str:
    """Route to END on first error, or continue to the next node."""
    return "failed" if state.get("errors") else "continue"


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def upload_node(state: IngestionState) -> Dict[str, Any]:
    """Upload original file to Supabase Storage and capture the public URL."""
    errors = list(state.get("errors", []))
    file_url = await upload_to_storage(
        state["file_bytes"], state["filename"], state["org_id"]
    )
    if not file_url:
        # Not a fatal error — ingest proceeds but viewer won't have a URL.
        print("[UPLOAD] Warning: file_url is None — storage upload failed.")
    return {"file_url": file_url, "errors": errors}


async def extract_node(state: IngestionState) -> Dict[str, Any]:
    """Extract text pages from PDF or plain-text bytes."""
    suffix = ".pdf" if state["filename"].lower().endswith(".pdf") else ".txt"
    errors = list(state.get("errors", []))
    raw_pages: List[Dict[str, Any]] = []

    if suffix == ".pdf":
        try:
            doc = fitz.open(stream=state["file_bytes"], filetype="pdf")

            if doc.is_encrypted:
                errors.append("Validation failed: PDF is password-protected.")
                doc.close()
                return {"errors": errors, "raw_pages": raw_pages}

            for page_num, page in enumerate(doc, start=1):
                blocks = page.get_text("blocks")
                block_texts = [
                    b[4].strip()
                    for b in sorted(blocks, key=lambda b: (round(b[1] / 10), b[0]))
                    if b[4].strip()
                ]
                text = " ".join(block_texts)
                if text.strip():
                    raw_pages.append({"page_num": page_num, "text": text})
            doc.close()

        except Exception as e:
            errors.append(f"Extraction exception: {str(e)}")
    else:
        try:
            text = state["file_bytes"].decode("utf-8", errors="ignore")
            raw_pages.append({"page_num": 1, "text": text})
        except Exception as e:
            errors.append(f"Txt parse exception: {str(e)}")

    return {"raw_pages": raw_pages, "errors": errors}


async def validate_node(state: IngestionState) -> Dict[str, Any]:
    """Reject files with insufficient content."""
    errors = list(state.get("errors", []))
    if errors:
        return {"errors": errors}

    total_words = sum(len(p["text"].split()) for p in state.get("raw_pages", []))
    if total_words < 50:
        errors.append(
            f"Validation failed: file contains only {total_words} words (minimum 50)."
        )

    return {"errors": errors}


async def chunk_node(state: IngestionState) -> Dict[str, Any]:
    """Semantically chunk the extracted pages."""
    errors = list(state.get("errors", []))
    if errors:
        return {"errors": errors, "chunks": []}

    chunks = process_and_chunk_document(state["raw_pages"], state["filename"])

    if not chunks:
        errors.append("Chunking produced no usable content.")

    return {"chunks": chunks, "errors": errors}


async def deduplicate_node(state: IngestionState) -> Dict[str, Any]:
    """Remove duplicate chunks before embedding to avoid wasting Jina API quota."""
    errors = list(state.get("errors", []))
    if errors:
        return {"errors": errors, "chunks": state.get("chunks", [])}

    seen: set[str] = set()
    unique: list[dict] = []
    for c in state.get("chunks", []):
        fp = fingerprint(c["text"])
        if fp not in seen:
            seen.add(fp)
            unique.append(c)

    before = len(state.get("chunks", []))
    after = len(unique)
    print(f"[DEDUP] {before} → {after} chunks after deduplication")

    return {"chunks": unique, "errors": errors}


async def embed_node(state: IngestionState) -> Dict[str, Any]:
    """Embed all unique chunks using Jina v3."""
    errors = list(state.get("errors", []))
    if errors or not state.get("chunks"):
        return {"errors": errors, "embeddings": []}

    try:
        texts = [c["text"] for c in state["chunks"]]
        all_vectors = await embed_texts(texts)
    except Exception as e:
        errors.append(f"Embedding failed: {str(e)}")
        return {"errors": errors, "embeddings": []}

    if len(all_vectors) != len(state["chunks"]):
        errors.append(
            f"Embedding count mismatch: {len(all_vectors)} vectors for "
            f"{len(state['chunks'])} chunks."
        )
        return {"errors": errors, "embeddings": []}

    return {"embeddings": all_vectors, "errors": errors}


async def store_node(state: IngestionState) -> Dict[str, Any]:
    """Insert all chunks with embeddings into the documents table."""
    errors = list(state.get("errors", []))
    chunks = state.get("chunks", [])
    embeddings = state.get("embeddings", [])

    if errors or not chunks or not embeddings:
        return {"errors": errors}

    file_url = state.get("file_url")

    rows = [
        {
            "org_id":     state["org_id"],
            "doc_name":   state["filename"],
            "chunk_text": chunks[i]["text"],
            "embedding":  embeddings[i],
            "file_url":   file_url,           # top-level column
            "metadata": {
                "domain":      state["domain"],
                "chunk_index": chunks[i]["chunk_index"],
                "page_number": chunks[i]["page"],
                "doc_name":    state["filename"],
                "char_count":  len(chunks[i]["text"]),
                "section":     f"Page {chunks[i]['page']}",
                "file_url":    file_url,       # fallback
            },
        }
        for i in range(len(chunks))
    ]

    try:
        await db_insert("documents", rows)
        print(f"[STORE] Inserted {len(rows)} rows for '{state['filename']}'")
    except Exception as e:
        errors.append(f"DB insert failed: {str(e)}")

    return {"errors": errors}


# ── Graph definition ──────────────────────────────────────────────────────────

workflow = StateGraph(IngestionState)

workflow.add_node("upload",      upload_node)
workflow.add_node("extract",     extract_node)
workflow.add_node("validate",    validate_node)
workflow.add_node("chunk",       chunk_node)
workflow.add_node("deduplicate", deduplicate_node)
workflow.add_node("embed",       embed_node)
workflow.add_node("store",       store_node)

workflow.set_entry_point("upload")
workflow.add_edge("upload", "extract")
workflow.add_edge("extract", "validate")

workflow.add_conditional_edges(
    "validate",
    routing_decision,
    {"failed": END, "continue": "chunk"},
)
workflow.add_conditional_edges(
    "chunk",
    routing_decision,
    {"failed": END, "continue": "deduplicate"},
)
workflow.add_conditional_edges(
    "deduplicate",
    routing_decision,
    {"failed": END, "continue": "embed"},
)
workflow.add_conditional_edges(
    "embed",
    routing_decision,
    {"failed": END, "continue": "store"},
)
workflow.add_edge("store", END)

ingestion_graph = workflow.compile()


# ── Public entry point (replaces ingest_document for graph path) ──────────────

async def ingest_via_graph(
    file_bytes: bytes,
    filename: str,
    org_id: str,
    domain: str = "general",
) -> dict:
    """
    Run the full LangGraph ingestion pipeline and return a status dict
    matching the IngestResponse model.
    """
    initial_state: IngestionState = {
        "file_bytes": file_bytes,
        "filename":   filename,
        "org_id":     org_id,
        "domain":     domain,
        "file_url":   None,
        "raw_pages":  [],
        "chunks":     [],
        "embeddings": [],
        "errors":     [],
    }

    final_state = await ingestion_graph.ainvoke(initial_state)

    if final_state.get("errors"):
        return {
            "message":       f"Ingestion failed: {'; '.join(final_state['errors'])}",
            "chunks_stored": 0,
            "doc_name":      filename,
            "file_url":      final_state.get("file_url"),
        }

    return {
        "message":       "Ingested successfully",
        "chunks_stored": len(final_state.get("chunks", [])),
        "doc_name":      filename,
        "file_url":      final_state.get("file_url"),
    }