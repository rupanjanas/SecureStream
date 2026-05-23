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


async def analyze_and_embed_node(state: RetrievalState) -> Dict[str, Any]:
    q = state["question"]
    q_lower = q.lower()

    print(f"\n{'='*60}")
    print(f"[RETRIEVAL] Question: {q!r}")
    print(f"[RETRIEVAL] org_id={state['org_id']} doc_name={state.get('doc_name')!r}")

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

    print(f"[ANALYZE]  Intent={strategy['intent']} top_k={strategy['top_k']} use_keyword={strategy['use_keyword']}")
    print(f"[ANALYZE]  Keywords extracted: {keywords}")

    print(f"[EMBED]    Calling Jina API (retrieval.query task)...")
    try:
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
            print(f"[EMBED]    OK — vector dim={len(query_vector)}")
    except Exception as e:
        print(f"[EMBED]    ERROR: {e}")
        raise

    return {"strategy": strategy, "keywords": keywords, "query_vector": query_vector}


async def parallel_search_node(state: RetrievalState) -> Dict[str, Any]:
    print(f"[SEARCH]   Firing vector + keyword search in parallel...")

    async def vector_search():
        try:
            results = await db_rpc("match_documents", {
                "query_embedding": state["query_vector"],
                "match_count": 25,
                "filter_org_id": state["org_id"],
                "filter_doc_name": state["doc_name"] or "",
            })
            print(f"[VECTOR]   Returned {len(results) if results else 0} chunks")
            if results:
                for i, c in enumerate(results[:3]):
                    sim = round(c.get("similarity", 0), 3)
                    preview = c.get("chunk_text", "")[:80].replace("\n", " ")
                    page = (c.get("metadata") or {}).get("page_number", "?")
                    print(f"[VECTOR]   [{i}] sim={sim} page={page} | {preview!r}")
            return results or []
        except Exception as e:
            print(f"[VECTOR]   ERROR: {e}")
            raise

    async def keyword_search():
        if not state["strategy"]["use_keyword"] or not state["keywords"]:
            print(f"[KEYWORD]  Skipped (use_keyword={state['strategy']['use_keyword']})")
            return []
        try:
            tasks = [
                db_keyword_search(state["org_id"], kw, doc_name=state["doc_name"])
                for kw in state["keywords"][:4]
            ]
            batches = await asyncio.gather(*tasks, return_exceptions=True)
            results = []
            for kw, b in zip(state["keywords"][:4], batches):
                if isinstance(b, list):
                    print(f"[KEYWORD]  '{kw}' → {len(b)} hits")
                    results.extend(b)
                else:
                    print(f"[KEYWORD]  '{kw}' → ERROR: {b}")
            print(f"[KEYWORD]  Total raw hits: {len(results)}")
            return results
        except Exception as e:
            print(f"[KEYWORD]  ERROR: {e}")
            raise

    vec_results, kw_results = await asyncio.gather(vector_search(), keyword_search())
    return {"vector_results": vec_results, "keyword_results": kw_results}


async def merge_rerank_node(state: RetrievalState) -> Dict[str, Any]:
    vec = state.get("vector_results", [])
    kw  = state.get("keyword_results", [])

    print(f"[MERGE]    Input: {len(vec)} vector + {len(kw)} keyword = {len(vec)+len(kw)} total")

    combined = kw + vec
    after_dedup = deduplicate(combined)
    after_junk  = filter_junk(after_dedup)

    print(f"[MERGE]    After dedup: {len(after_dedup)} | After junk filter: {len(after_junk)}")

    if not after_junk:
        print(f"[MERGE]    ⚠️  NO CHUNKS SURVIVED filtering — context will be empty!")
        return {"context": "", "combined_results": []}

    sorted_chunks = rerank(state["question"], after_junk)
    final_chunks  = sorted_chunks[:state["strategy"]["top_k"]]

    print(f"[MERGE]    Top {len(final_chunks)} chunks after rerank:")
    for i, c in enumerate(final_chunks):
        sim     = round(c.get("similarity", 0), 3)
        page    = (c.get("metadata") or {}).get("page_number", "?")
        preview = c.get("chunk_text", "")[:100].replace("\n", " ")
        print(f"[MERGE]    [{i}] sim={sim} page={page} | {preview!r}")

    parts, word_count = [], 0
    max_words = getattr(settings, "context_max_words", 2500)

    for c in final_chunks:
        text  = c.get("chunk_text", "")
        page  = (c.get("metadata") or {}).get("page_number", "?")
        words = text.split()
        if word_count + len(words) > max_words:
            remaining = max_words - word_count
            if remaining > 30:
                parts.append(f"[Page {page}]\n" + " ".join(words[:remaining]) + "…")
            break
        parts.append(f"[Page {page}]\n{text}")
        word_count += len(words)

    context = "\n\n".join(parts)
    print(f"[MERGE]    Context built: {word_count} words across {len(parts)} chunks")
    print(f"[MERGE]    Context preview:\n{context[:300].replace(chr(10), ' ')!r}")

    return {"context": context, "combined_results": final_chunks}


async def generate_stream_node(state: RetrievalState) -> Dict[str, Any]:
    q_queue = state["token_queue"]

    if not state["context"].strip():
        print(f"[GENERATE] ⚠️  Empty context — returning 'No relevant documents found.'")
        await q_queue.put(None)
        return {"generation": "No relevant documents found."}

    intent     = state["strategy"].get("intent", "factual")
    max_tokens = 600 if intent == "summary" else 400
    prompt     = RAG_PROMPT.format(context=state["context"], question=state["question"])

    print(f"[GENERATE] Calling Groq (model={settings.groq_model} max_tokens={max_tokens})...")
    print(f"[GENERATE] Prompt length: {len(prompt)} chars")

    full_generation = []
    token_count = 0

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
                    "model":       settings.groq_model,
                    "temperature": 0.1,
                    "max_tokens":  max_tokens,
                    "stream":      True,
                    "messages":    [{"role": "user", "content": prompt}],
                },
            ) as resp:
                print(f"[GENERATE] Groq HTTP status: {resp.status_code}")
                if resp.status_code != 200:
                    body = await resp.aread()
                    print(f"[GENERATE] Groq error body: {body.decode()}")
                    await q_queue.put(None)
                    return {"generation": ""}

                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        chunk = json.loads(line[6:])
                        token = chunk["choices"][0]["delta"].get("content", "")
                        if token:
                            full_generation.append(token)
                            token_count += 1
                            await q_queue.put(token)
                    except Exception as parse_err:
                        print(f"[GENERATE] Parse error on line {line!r}: {parse_err}")
                        continue

        print(f"[GENERATE] Done — {token_count} tokens streamed")

    except Exception as e:
        print(f"[GENERATE] ERROR during Groq stream: {e}")
        raise
    finally:
        await q_queue.put(None)

    return {"generation": "".join(full_generation)}


async def grounding_check_node(state: RetrievalState) -> Dict[str, Any]:
    gen_text = state["generation"]

    if gen_text == "No relevant documents found.":
        print(f"[GROUNDING] Skipped — no content returned")
        return {"grounded": True}

    gen_words     = set(re.findall(r'\b[a-zA-Z]{4,}\b', gen_text.lower()))
    context_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', state["context"].lower()))
    skip          = {"found", "uploaded", "document", "context", "question", "assistant"}
    target_words  = gen_words - skip

    overlap     = target_words & context_words
    is_grounded = bool(overlap) or not target_words

    print(f"[GROUNDING] grounded={is_grounded} | overlap_sample={list(overlap)[:5]}")

    final_output = gen_text if is_grounded else "Not found in the uploaded document."
    print(f"{'='*60}\n")

    asyncio.create_task(db_insert("query_logs", [{
        "org_id":   state["org_id"],
        "question": state["question"],
        "answer":   final_output,
    }]))

    return {"grounded": is_grounded}


# ── Graph ─────────────────────────────────────────────────────────────────────

retrieval_flow = StateGraph(RetrievalState)
retrieval_flow.add_node("analyze_and_embed", analyze_and_embed_node)
retrieval_flow.add_node("parallel_search",   parallel_search_node)
retrieval_flow.add_node("merge_rerank",      merge_rerank_node)
retrieval_flow.add_node("generate",          generate_stream_node)
retrieval_flow.add_node("grounding_check",   grounding_check_node)

retrieval_flow.set_entry_point("analyze_and_embed")
retrieval_flow.add_edge("analyze_and_embed", "parallel_search")
retrieval_flow.add_edge("parallel_search",   "merge_rerank")
retrieval_flow.add_edge("merge_rerank",      "generate")
retrieval_flow.add_edge("generate",          "grounding_check")
retrieval_flow.add_edge("grounding_check",   END)

retrieval_graph = retrieval_flow.compile()