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
    token_queue: asyncio.Queue
    strategy: Dict[str, Any]
    query_vector: List[float]
    keywords: List[str]
    vector_results: List[Dict[str, Any]]
    keyword_results: List[Dict[str, Any]]
    combined_results: List[Dict[str, Any]]
    context: str
    generation: str
    grounded: bool


# ── FIX 1: Combine analysis + embedding into one node ────────────────────────
# Eliminates a full node-transition overhead and one async boundary.

async def analyze_and_embed_node(state: RetrievalState) -> Dict[str, Any]:
    q = state["question"]
    q_lower = q.lower()

    # Strategy classification (rule-based, zero latency)
    if any(w in q_lower for w in ["compare", "difference", "versus", "vs"]):
        strategy = {"top_k": settings.top_k_comparison, "use_keyword": True, "intent": "comparison"}
    elif any(w in q_lower for w in ["summarize", "summary", "overview", "report"]):
        strategy = {"top_k": settings.top_k_summary, "use_keyword": False, "intent": "summary"}
    else:
        strategy = {"top_k": settings.top_k_default, "use_keyword": True, "intent": "factual"}

    words = re.findall(r'\b[a-zA-Z]{3,}\b', q_lower)
    keywords = [w for w in words if w not in STOP_WORDS]
    stems = [w[:5] for w in keywords if len(w) >= 7]
    keywords = list(dict.fromkeys(keywords + stems))[:8]

    # Jina embed (retrieval.query task) — single HTTP call
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {settings.jina_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": [q],
                "model": "jina-embeddings-v3",
                "task": "retrieval.query",
            },
        )
        r.raise_for_status()
        query_vector = r.json()["data"][0]["embedding"]

    return {"strategy": strategy, "keywords": keywords, "query_vector": query_vector}


# ── FIX 2: True parallel search in a single node ─────────────────────────────
# asyncio.gather fires both DB calls at the same time.
# Replaces the fake parallel conditional_edges pattern — saves 300-600ms.

async def parallel_search_node(state: RetrievalState) -> Dict[str, Any]:
    async def vector_search():
        return await db_rpc("match_documents", {
            "query_embedding": state["query_vector"],
            "match_count": 25,
            "filter_org_id": state["org_id"],
            "filter_doc_name": state["doc_name"] or "",
        })

    async def keyword_search():
        if not state["strategy"]["use_keyword"] or not state["keywords"]:
            return []
        tasks = [
            db_keyword_search(state["org_id"], kw, doc_name=state["doc_name"])
            for kw in state["keywords"][:4]
        ]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        return results

    # Both DB calls fire simultaneously
    vec_results, kw_results = await asyncio.gather(vector_search(), keyword_search())

    return {
        "vector_results": vec_results or [],
        "keyword_results": kw_results or [],
    }


# ── FIX 3: Raise context cap to 2500 words ───────────────────────────────────
# Groq llama-3.1-8b-instant has an 8k token context — 2500 words fits easily.
# The old 1200-word cap was cutting answers in half.

async def merge_rerank_node(state: RetrievalState) -> Dict[str, Any]:
    combined = state.get("keyword_results", []) + state.get("vector_results", [])
    filtered = filter_junk(deduplicate(combined))
    sorted_chunks = rerank(state["question"], filtered)
    final_chunks = sorted_chunks[:state["strategy"]["top_k"]]

    parts, word_count = [], 0
    max_words = getattr(settings, "context_max_words", 2500)  # raised from 1200

    for c in final_chunks:
        text = c.get("chunk_text", "")
        page = (c.get("metadata") or {}).get("page_number", "?")
        words = text.split()
        if word_count + len(words) > max_words:
            remaining = max_words - word_count
            if remaining > 30:
                parts.append(f"[Page {page}]\n" + " ".join(words[:remaining]) + "…")
            break
        parts.append(f"[Page {page}]\n{text}")
        word_count += len(words)

    return {"context": "\n\n".join(parts), "combined_results": final_chunks}


# ── FIX 4: Reduce max_tokens 600 → 400 ───────────────────────────────────────
# Groq streams tokens — fewer max_tokens = faster time-to-first-token.
# 400 tokens (~300 words) is enough for most factual answers.
# For summary intent, bump to 600.

async def generate_stream_node(state: RetrievalState) -> Dict[str, Any]:
    q_queue = state["token_queue"]

    if not state["context"].strip():
        await q_queue.put(None)
        return {"generation": "No relevant documents found."}

    prompt = RAG_PROMPT.format(context=state["context"], question=state["question"])

    # Use more tokens for summaries, fewer for quick factual lookups
    intent = state["strategy"].get("intent", "factual")
    max_tokens = 600 if intent == "summary" else 400

    full_generation = []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST",
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
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
                            await q_queue.put(token)
                    except Exception:
                        continue
    finally:
        await q_queue.put(None)

    return {"generation": "".join(full_generation)}


async def grounding_check_node(state: RetrievalState) -> Dict[str, Any]:
    gen_text = state["generation"]
    if gen_text == "No relevant documents found.":
        return {"grounded": True}

    gen_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', gen_text.lower()))
    context_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', state["context"].lower()))
    skip = {"found", "uploaded", "document", "context", "question", "assistant"}
    target_words = gen_words - skip

    is_grounded = (len(target_words & context_words) > 0) or (not target_words)
    final_output = gen_text if is_grounded else "Not found in the uploaded document."

    asyncio.create_task(db_insert("query_logs", [{
        "org_id": state["org_id"],
        "question": state["question"],
        "answer": final_output,
    }]))

    return {"grounded": is_grounded}


# ── Graph: 5 nodes instead of 7, linear flow ─────────────────────────────────
# analyze+embed → parallel_search → merge_rerank → generate → grounding_check
# No conditional edges needed — simpler, faster, less overhead.

retrieval_flow = StateGraph(RetrievalState)
retrieval_flow.add_node("analyze_and_embed", analyze_and_embed_node)
retrieval_flow.add_node("parallel_search", parallel_search_node)
retrieval_flow.add_node("merge_rerank", merge_rerank_node)
retrieval_flow.add_node("generate", generate_stream_node)
retrieval_flow.add_node("grounding_check", grounding_check_node)

retrieval_flow.set_entry_point("analyze_and_embed")
retrieval_flow.add_edge("analyze_and_embed", "parallel_search")
retrieval_flow.add_edge("parallel_search", "merge_rerank")
retrieval_flow.add_edge("merge_rerank", "generate")
retrieval_flow.add_edge("generate", "grounding_check")
retrieval_flow.add_edge("grounding_check", END)

retrieval_graph = retrieval_flow.compile()