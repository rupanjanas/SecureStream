from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Optional

import fitz
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import settings
from app.db import db_upsert
from app.ingest import (
    clean_document_text,
    embed_texts,
    fingerprint,
    process_and_chunk_document,
    upload_to_storage,
)

logger = logging.getLogger(__name__)


class IngestionState(TypedDict):
    file_bytes_b64: str
    filename: str
    org_id: str
    domain: str
    file_url: Optional[str]
    raw_pages: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    embeddings: list[list[float]]
    errors: list[str]


# FIX: two routing functions.
# _route_errors_only is used after validate (chunks are empty here by design).
# _route_chunks is used after chunk/deduplicate/embed (checks both errors and chunks).

def _route_errors_only(state: IngestionState) -> str:
    return "failed" if state.get("errors") else "continue"


def _route_chunks(state: IngestionState) -> str:
    if state.get("errors"):
        return "failed"
    if not state.get("chunks"):
        return "no_content"
    return "continue"


async def upload_node(state: IngestionState) -> dict[str, Any]:
    errors = list(state.get("errors", []))
    file_bytes = base64.b64decode(state["file_bytes_b64"])
    file_url = await upload_to_storage(file_bytes, state["filename"], state["org_id"])
    if not file_url:
        logger.warning("upload_node: storage upload failed (non-fatal)")
    return {"file_url": file_url, "errors": errors}


async def extract_node(state: IngestionState) -> dict[str, Any]:
    suffix = ".pdf" if state["filename"].lower().endswith(".pdf") else ".txt"
    errors = list(state.get("errors", []))
    raw_pages: list[dict[str, Any]] = []
    file_bytes = base64.b64decode(state["file_bytes_b64"])

    if suffix == ".pdf":
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            if doc.is_encrypted:
                errors.append("Validation failed: PDF is password-protected.")
                doc.close()
                return {"errors": errors, "raw_pages": raw_pages}
            max_pages = settings.max_ingest_pages
            for page_num, page in enumerate(doc, start=1):
                if page_num > max_pages:
                    logger.warning("extract_node: truncating at %d pages", max_pages)
                    break
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
        except Exception as exc:
            errors.append(f"Extraction exception: {exc}")
    else:
        try:
            raw_pages.append({"page_num": 1, "text": file_bytes.decode("utf-8", errors="ignore")})
        except Exception as exc:
            errors.append(f"Txt parse exception: {exc}")

    return {"raw_pages": raw_pages, "errors": errors}


async def validate_node(state: IngestionState) -> dict[str, Any]:
    errors = list(state.get("errors", []))
    if errors:
        return {"errors": errors}
    total_words = sum(len(p["text"].split()) for p in state.get("raw_pages", []))
    if total_words < 50:
        errors.append(f"Validation failed: only {total_words} words extracted (minimum 50).")
    return {"errors": errors}


async def chunk_node(state: IngestionState) -> dict[str, Any]:
    errors = list(state.get("errors", []))
    if errors:
        return {"errors": errors, "chunks": []}
    chunks = await process_and_chunk_document(state["raw_pages"], state["filename"])
    if not chunks:
        errors.append("Chunking produced no usable content.")
    return {"chunks": chunks, "errors": errors}


async def deduplicate_node(state: IngestionState) -> dict[str, Any]:
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
    logger.info("deduplicate_node: %d → %d chunks", len(state.get("chunks", [])), len(unique))
    if not unique:
        errors.append("All chunks were duplicates — nothing to store.")
    return {"chunks": unique, "errors": errors}


async def embed_node(state: IngestionState) -> dict[str, Any]:
    errors = list(state.get("errors", []))
    if errors or not state.get("chunks"):
        return {"errors": errors, "embeddings": []}
    try:
        all_vectors = await embed_texts([c["text"] for c in state["chunks"]])
    except Exception as exc:
        errors.append(f"Embedding failed: {exc}")
        return {"errors": errors, "embeddings": []}
    if len(all_vectors) != len(state["chunks"]):
        errors.append(f"Embedding count mismatch: {len(all_vectors)} vectors for {len(state['chunks'])} chunks.")
        return {"errors": errors, "embeddings": []}
    return {"embeddings": all_vectors, "errors": errors}


async def store_node(state: IngestionState) -> dict[str, Any]:
    errors = list(state.get("errors", []))
    chunks = state.get("chunks", [])
    embeddings = state.get("embeddings", [])
    file_url = state.get("file_url")

    if errors or not chunks or not embeddings:
        return {"errors": errors}

    rows = [
        {
            "org_id": state["org_id"],
            "doc_name": state["filename"],
            "chunk_text": chunks[i]["text"],
            "chunk_index": chunks[i]["chunk_index"],
            "embedding": embeddings[i],
            "file_url": file_url,
            "metadata": {
                "domain": state["domain"],
                "chunk_index": chunks[i]["chunk_index"],
                "page_number": chunks[i]["page"],
                "doc_name": state["filename"],
                "char_count": len(chunks[i]["text"]),
                "section": f"Page {chunks[i]['page']}",
                "file_url": file_url,
            },
        }
        for i in range(len(chunks))
    ]

    try:
        await db_upsert("documents", rows, on_conflict="org_id,doc_name,chunk_index")
        logger.info("store_node: upserted %d rows for '%s'", len(rows), state["filename"])
    except Exception as exc:
        errors.append(f"DB upsert failed: {exc}")

    return {"errors": errors}


_ingestion_graph = None
_graph_lock = asyncio.Lock()


async def _get_graph():
    global _ingestion_graph
    if _ingestion_graph is None:
        async with _graph_lock:
            if _ingestion_graph is None:
                workflow = StateGraph(IngestionState)
                workflow.add_node("upload", upload_node)
                workflow.add_node("extract", extract_node)
                workflow.add_node("validate", validate_node)
                workflow.add_node("chunk", chunk_node)
                workflow.add_node("deduplicate", deduplicate_node)
                workflow.add_node("embed", embed_node)
                workflow.add_node("store", store_node)

                workflow.set_entry_point("upload")
                workflow.add_edge("upload", "extract")
                workflow.add_edge("extract", "validate")

                # FIX: validate uses _route_errors_only — chunks are [] here by design
                workflow.add_conditional_edges(
                    "validate",
                    _route_errors_only,
                    {"failed": END, "continue": "chunk"},
                )
                workflow.add_conditional_edges(
                    "chunk",
                    _route_chunks,
                    {"failed": END, "no_content": END, "continue": "deduplicate"},
                )
                workflow.add_conditional_edges(
                    "deduplicate",
                    _route_chunks,
                    {"failed": END, "no_content": END, "continue": "embed"},
                )
                workflow.add_conditional_edges(
                    "embed",
                    _route_chunks,
                    {"failed": END, "no_content": END, "continue": "store"},
                )
                workflow.add_edge("store", END)
                _ingestion_graph = workflow.compile()
    return _ingestion_graph


async def ingest_via_graph(
    file_bytes: bytes,
    filename: str,
    org_id: str,
    domain: str = "general",
) -> dict:
    graph = await _get_graph()
    initial_state: IngestionState = {
        "file_bytes_b64": base64.b64encode(file_bytes).decode(),
        "filename": filename,
        "org_id": org_id,
        "domain": domain,
        "file_url": None,
        "raw_pages": [],
        "chunks": [],
        "embeddings": [],
        "errors": [],
    }
    final_state = await graph.ainvoke(initial_state)
    errors = final_state.get("errors", [])
    if errors:
        return {
            "message": f"Ingestion failed: {'; '.join(errors)}",
            "chunks_stored": 0,
            "doc_name": filename,
            "file_url": final_state.get("file_url"),
        }
    return {
        "message": "Ingested successfully",
        "chunks_stored": len(final_state.get("chunks", [])),
        "doc_name": filename,
        "file_url": final_state.get("file_url"),
    }