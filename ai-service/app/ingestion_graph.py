import fitz
from typing import Dict, Any, List, TypedDict, Optional
from langgraph.graph import StateGraph, END
import httpx
from app.config import settings
from app.db import db_insert
from app.ingest import process_and_chunk_document

class IngestionState(TypedDict):
    file_bytes: bytes
    filename: str
    org_id: str
    domain: str
    raw_pages: List[Dict[str, Any]]
    chunks: List[Dict[str, Any]]
    embeddings: List[List[float]]
    errors: List[str]

async def extract_node(state: IngestionState) -> Dict[str, Any]:
    suffix = ".pdf" if state["filename"].lower().endswith(".pdf") else ".txt"
    errors = list(state.get("errors", []))
    raw_pages = []
    
    if suffix == ".pdf":
        try:
            doc = fitz.open(stream=state["file_bytes"], filetype="pdf")
            if doc.is_encrypted:
                errors.append("Validation failed: PDF is password-protected.")
                return {"errors": errors}
                
            for page_num, page in enumerate(doc, start=1):
                blocks = page.get_text("blocks")
                block_texts = [
                    b[4].strip()
                    for b in sorted(blocks, key=lambda b: (round(b[1] / 10), b[0]))
                    if b[4].strip()
                ]
                raw_pages.append({"page_num": page_num, "text": " ".join(block_texts)})
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
    errors = list(state.get("errors", []))
    if errors:
        return {"errors": errors} # FIXED: Do not return {}
        
    total_words = sum(len(p["text"].split()) for p in state["raw_pages"])
    if total_words < 50:
        errors.append(f"Validation failed: File contains insufficient content ({total_words} words).")
        
    return {"errors": errors}

async def chunk_node(state: IngestionState) -> Dict[str, Any]:
    errors = list(state.get("errors", []))
    if errors:
        return {"errors": errors} # FIXED: Do not return {}
        
    chunks = process_and_chunk_document(state["raw_pages"], state["filename"])
    return {"chunks": chunks}

async def embed_node(state: IngestionState) -> Dict[str, Any]:
    errors = list(state.get("errors", []))
    if errors or not state.get("chunks"):
        return {"errors": errors} # FIXED: Do not return {}
        
    texts = [c["text"] for c in state["chunks"]]
    all_vectors = []
    
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(texts), 32):
            batch = texts[i:i + 32]
            r = await client.post(
                "https://api.jina.ai/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.jina_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "input": batch,
                    "model": "jina-embeddings-v3",
                    "task": "retrieval.passage",
                },
            )
            if r.status_code != 200:
                errors.append(f"Jina Embed Error: {r.text}")
                return {"errors": errors} # FIXED: Return valid error tracking state block
                
            all_vectors.extend([item["embedding"] for item in r.json()["data"]])
            
    return {"embeddings": all_vectors}

async def store_node(state: IngestionState) -> Dict[str, Any]:
    errors = list(state.get("errors", []))
    if errors or not state.get("chunks") or not state.get("embeddings"):
        return {"errors": errors} # FIXED: Do not return {}
        
    rows = [
        {
            "org_id": state["org_id"],
            "doc_name": state["filename"],
            "chunk_text": state["chunks"][i]["text"],
            "embedding": state["embeddings"][i],
            "metadata": {
                "domain": state["domain"],
                "chunk_index": state["chunks"][i]["chunk_index"],
                "page_number": state["chunks"][i]["page"],
                "doc_name": state["filename"],
                "char_count": len(state["chunks"][i]["text"]),
                "section": f"Page {state['chunks'][i]['page']}",
            },
        }
        for i in range(len(state["chunks"]))
    ]
    await db_insert("documents", rows)
    return {"errors": errors} # FIXED: Return standard errors tracking context list rather than empty dict

def routing_decision(state: IngestionState) -> str:
    return "failed" if state.get("errors") else "continue"

# ─── GRAPH ARCHITECTURE DEFINITION ──────────────────────────────────────────
workflow = StateGraph(IngestionState)
workflow.add_node("extract", extract_node)
workflow.add_node("validate", validate_node)
workflow.add_node("chunk", chunk_node)
workflow.add_node("embed", embed_node)
workflow.add_node("store", store_node)

workflow.set_entry_point("extract")
workflow.add_edge("extract", "validate")
workflow.add_conditional_edges("validate", routing_decision, {"failed": END, "continue": "chunk"})
workflow.add_conditional_edges("chunk", routing_decision, {"failed": END, "continue": "embed"})
workflow.add_conditional_edges("embed", routing_decision, {"failed": END, "continue": "store"})
workflow.add_edge("store", END)

ingestion_graph = workflow.compile()