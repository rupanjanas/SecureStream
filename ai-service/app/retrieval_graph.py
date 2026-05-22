import re
import httpx
import json
import asyncio
from typing import Dict, Any, List, TypedDict, Optional
from langgraph.graph import StateGraph, END
from app.config import settings
from app.db import db_rpc, db_keyword_search, db_insert
from app.query import STOP_WORDS, filter_junk, deduplicate, rerank, RAG_PROMPT

class RetrievalState(TypedDict):
    question: str
    org_id: str
    doc_name: Optional[str]
    token_queue: asyncio.Queue       # Async queue injection wrapper for real-time streaming
    strategy: Dict[str, Any]
    query_vector: List[float]
    keywords: List[str]
    vector_results: List[Dict[str, Any]]
    keyword_results: List[Dict[str, Any]]
    combined_results: List[Dict[str, Any]]
    context: str
    generation: str
    grounded: bool

async def query_analysis_node(state: RetrievalState) -> Dict[str, Any]:
    q = state["question"].lower()
    
    if any(w in q for w in ["compare", "difference", "versus", "vs"]):
        strategy = {"top_k": settings.top_k_comparison, "use_keyword": True, "intent": "comparison"}
    elif any(w in q for w in ["summarize", "summary", "overview", "report"]):
        strategy = {"top_k": settings.top_k_summary, "use_keyword": False, "intent": "summary"}
    else:
        strategy = {"top_k": settings.top_k_default, "use_keyword": True, "intent": "factual"}
        
    words = re.findall(r'\b[a-zA-Z]{3,}\b', q)
    keywords = [w for w in words if w not in STOP_WORDS]
    stems = [w[:5] for w in keywords if len(w) >= 7]
    return {"strategy": strategy, "keywords": list(dict.fromkeys(keywords + stems))[:8]}

async def embed_query_node(state: RetrievalState) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.jina_api_key}", "Content-Type": "application/json"},
            json={"input": [state["question"]], "model": "jina-embeddings-v3", "task": "retrieval.query"},
        )
        r.raise_for_status()
        return {"query_vector": r.json()["data"][0]["embedding"]}

async def vector_search_node(state: RetrievalState) -> Dict[str, Any]:
    results = await db_rpc("match_documents", {
        "query_embedding": state["query_vector"],
        "match_count": 25,
        "filter_org_id": state["org_id"],
        "filter_doc_name": state["doc_name"] or "",
    })
    return {"vector_results": results or []}

async def keyword_search_node(state: RetrievalState) -> Dict[str, Any]:
    if not state["strategy"]["use_keyword"] or not state["keywords"]:
        return {"keyword_results": []}
    results = []
    tasks = [db_keyword_search(state["org_id"], kw, doc_name=state["doc_name"]) for kw in state["keywords"][:4]]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    for b in batches:
        if isinstance(b, list): results.extend(b)
    return {"keyword_results": results}

async def merge_rerank_node(state: RetrievalState) -> Dict[str, Any]:
    combined = state.get("keyword_results", []) + state.get("vector_results", [])
    filtered = filter_junk(deduplicate(combined))
    sorted_chunks = rerank(state["question"], filtered)
    final_chunks = sorted_chunks[:state["strategy"]["top_k"]]
    
    parts, word_count = [], 0
    for c in final_chunks:
        text, page = c.get("chunk_text", ""), (c.get("metadata") or {}).get("page_number", "?")
        words = text.split()
        if word_count + len(words) > settings.context_max_words:
            remaining = settings.context_max_words - word_count
            if remaining > 30: parts.append(f"[Page {page}]\n" + " ".join(words[:remaining]) + "…")
            break
        parts.append(f"[Page {page}]\n{text}")
        word_count += len(words)
        
    return {"context": "\n\n".join(parts), "combined_results": final_chunks}

async def generate_stream_node(state: RetrievalState) -> Dict[str, Any]:
    q_queue = state["token_queue"]
    if not state["context"].strip():
        await q_queue.put(None) # Signal stream completion directly
        return {"generation": "No relevant documents found."}
        
    prompt = RAG_PROMPT.format(context=state["context"], question=state["question"])
    full_generation = []
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST", "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                json={
                    "model": settings.groq_model,
                    "temperature": settings.groq_temperature,
                    "max_tokens": settings.groq_max_tokens,
                    "stream": True,
                    "messages": [{"role": "user", "content": prompt}],
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        chunk = json.loads(line[6:])
                        token = chunk["choices"][0]["delta"].get("content", "")
                        if token:
                            full_generation.append(token)
                            await q_queue.put(token) # Push token live to the API loop thread
                    except Exception:
                        continue
    finally:
        await q_queue.put(None) # Always signal complete
        
    return {"generation": "".join(full_generation)}

async def grounding_check_node(state: RetrievalState) -> Dict[str, Any]:
    gen_text = state["generation"]
    if gen_text == "No relevant documents found.":
        return {"grounded": True}
        
    gen_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', gen_text.lower()))
    context_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', state["context"].lower()))
    target_words = gen_words - {"found", "uploaded", "document", "context", "question", "assistant"}
    
    is_grounded = (len(target_words & context_words) > 0) or (not target_words)
    final_output = gen_text if is_grounded else "Not found in the uploaded document."
    
    asyncio.create_task(db_insert("query_logs", [{
        "org_id": state["org_id"], "question": state["question"], "answer": final_output,
    }]))
    return {"grounded": is_grounded}

# Build State Retrieval Compilation Layout Schema
retrieval_flow = StateGraph(RetrievalState)
retrieval_flow.add_node("analyze", query_analysis_node)
retrieval_flow.add_node("embed", embed_query_node)
retrieval_flow.add_node("vector_search", vector_search_node)
retrieval_flow.add_node("keyword_search", keyword_search_node)
retrieval_flow.add_node("merge_rerank", merge_rerank_node)
retrieval_flow.add_node("generate", generate_stream_node)
retrieval_flow.add_node("grounding_check", grounding_check_node)

# ─── ENTRY ───────────────────────────────────────────────────────────
retrieval_flow.set_entry_point("analyze")
retrieval_flow.add_edge("analyze", "embed")

# ─── CORRECT FAN-OUT PARALLEL BRANCHING ──────────────────────────────
# Defining the same source node multiple times creates a parallel split
retrieval_flow.add_edge("embed", "vector_search")
retrieval_flow.add_edge("embed", "keyword_search")

# ─── FAN-IN CONCURRENT AGGREGATION ───────────────────────────────────
# Pointing both back to merge_rerank automatically synchronizes them
retrieval_flow.add_edge("vector_search", "merge_rerank")
retrieval_flow.add_edge("keyword_search", "merge_rerank")

# ─── EXIT PIPELINE ───────────────────────────────────────────────────
retrieval_flow.add_edge("merge_rerank", "generate")
retrieval_flow.add_edge("generate", "grounding_check")
retrieval_flow.add_edge("grounding_check", END)

retrieval_graph = retrieval_flow.compile()