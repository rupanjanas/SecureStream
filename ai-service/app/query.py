from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain.prompts import PromptTemplate
from app.config import settings
from app.db import db_rpc, db_insert, db_keyword_search
from app.cache import get_cached, set_cached
import asyncio
import re

_embedder = None
_llm      = None

def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbeddings(model=settings.embed_model)
    return _embedder

def get_llm():
    global _llm
    if _llm is None:
        _llm = OllamaLLM(
            model=settings.llm_model,
            temperature=0.1,
            num_predict=512,
            num_ctx=2048,
        )
    return _llm

RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template="""You are a concise assistant for SecureStream. Answer using ONLY the context below.
Be brief — 2-4 sentences maximum.
If the answer is in the context, you MUST return it.
If not found at all, say exactly: "Not found in uploaded documents."

Context:
{context}

Question: {question}

Answer:"""
)

# Stop words to ignore when extracting keywords
STOP_WORDS = {
    "what","is","the","a","an","of","in","on","at","to","for",
    "and","or","are","was","were","has","have","does","do","did",
    "this","that","these","those","with","from","by","about","how",
    "when","where","who","which","can","will","there","any","all"
}

def extract_keywords(question: str) -> list[str]:
    """Extract meaningful keywords from the user's question."""
    words = re.findall(r'\b[a-zA-Z]{4,}\b', question.lower())
    keywords = [w for w in words if w not in STOP_WORDS]
    # Also include partial matches — e.g. "acknowledgment" → "acknowledg"
    partials = [w[:7] for w in keywords if len(w) >= 7]
    return list(set(keywords + partials))[:5]  # max 5 keywords

async def answer_question(question: str, org_id: str, top_k: int = 4) -> dict:

    # -------- CACHE --------
    cached = await get_cached(org_id, question)
    if cached:
        print(f"Cache HIT for org={org_id}")
        return cached

    loop = asyncio.get_event_loop()

    # -------- STEP 1: EMBED QUESTION --------
    query_vector = await loop.run_in_executor(
        None, get_embedder().embed_query, question
    )

    # -------- STEP 2: VECTOR SEARCH --------
    chunks = await db_rpc("match_documents", {
        "query_embedding": query_vector,
        "match_count":     10,
        "filter_org_id":   org_id
    })
    print(f"Vector search: {len(chunks)} chunks")

    # -------- STEP 3: KEYWORD SEARCH using actual question keywords --------
    keywords       = extract_keywords(question)
    keyword_chunks = []

    for kw in keywords:
        kw_results = await db_keyword_search(org_id, kw)
        keyword_chunks.extend(kw_results)

    print(f"Keyword search ({keywords}): {len(keyword_chunks)} chunks")

    # -------- STEP 4: MERGE + DEDUPLICATE --------
    seen, all_chunks = set(), []
    for c in keyword_chunks + chunks:   # keyword results first = higher priority
        if c["chunk_text"] not in seen:
            seen.add(c["chunk_text"])
            all_chunks.append(c)

    if not all_chunks:
        return {
            "answer":          "No relevant documents found for your organization.",
            "sources":         [],
            "source_passages": [],
            "org_id":          org_id
        }

    top_chunks = all_chunks[:top_k]

    # -------- DEBUG --------
    print("\n===== CONTEXT BEING SENT TO LLM =====")
    for i, c in enumerate(top_chunks):
        print(f"[{i+1}] {c['chunk_text'][:150]}")
    print("=====================================\n")

    # -------- STEP 5: BUILD CONTEXT --------
    context = "\n\n---\n\n".join(
        f"[Doc: {c['doc_name']}]\n{c['chunk_text']}"   # full chunk, not truncated
        for c in top_chunks
    )

    # -------- STEP 6: LLM --------
    prompt = RAG_PROMPT.format(context=context, question=question)
    answer = await loop.run_in_executor(None, get_llm().invoke, prompt)

    # -------- STEP 7: BUILD RESULT --------
    source_passages = [
        {
            "doc_name":   c["doc_name"],
            "passage":    c["chunk_text"],
            "similarity": round(c.get("similarity", 0), 3)
        }
        for c in top_chunks
    ]

    result = {
        "answer":          answer,
        "sources":         [c["chunk_text"][:200] + "..." for c in top_chunks],
        "source_passages": source_passages,
        "org_id":          org_id
    }

    # -------- CACHE + LOG --------
    await set_cached(org_id, question, result, ttl=300)
    asyncio.create_task(db_insert("query_logs", [{
        "org_id":   org_id,
        "question": question,
        "answer":   answer
    }]))

    return result