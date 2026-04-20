from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain.prompts import PromptTemplate
from app.config import settings
from app.db import db_rpc, db_insert
import asyncio
from app.cache import get_cached, set_cached
_embedder = None
_llm = None

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
            temperature=0.1,      # lower = faster + more factual
            num_predict=512,      # cap output length — stops runaway responses
            num_ctx=2048,         # smaller context window = faster
        )
    return _llm

RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template="""You are a concise assistant for SecureStream. Answer using ONLY the context below.
Be brief — 2-4 sentences maximum unless the question needs more detail.
If the answer is not in the context, say exactly: "Not found in uploaded documents."

Context:
{context}

Question: {question}

Answer:"""
)

async def answer_question(question: str, org_id: str, top_k: int = 4) -> dict:
    cached = await get_cached(org_id, question)
    if cached:
        print(f"Cache HIT for org={org_id} q='{question[:40]}'")
        return cached# Step 1: embed question
    loop = asyncio.get_event_loop()
    query_vector = await loop.run_in_executor(
        None, get_embedder().embed_query, question
    )

    # Step 2: fetch top chunks
    chunks = await db_rpc("match_documents", {
        "query_embedding": query_vector,
        "match_count": top_k,
        "filter_org_id": org_id
    })

    if not chunks:
        return {
            "answer": "No relevant documents found for your organization.",
            "sources": [],
            "source_passages": [],
            "org_id": org_id
        }

    # Step 3: build context — only use top 3 chunks to keep prompt small
    top_chunks = chunks[:3]
    context = "\n\n---\n\n".join(
        f"[Doc: {c['doc_name']} | chunk {i+1}]\n{c['chunk_text']}"
        for i, c in enumerate(top_chunks)
    )

    # Step 4: call LLM in thread so it doesn't block the event loop
    prompt = RAG_PROMPT.format(context=context, question=question)
    answer = await loop.run_in_executor(
        None, get_llm().invoke, prompt
    )

    # Step 5: extract exact passages for highlighting
    source_passages = [
        {
            "doc_name":   c["doc_name"],
            "passage":    c["chunk_text"],
            "similarity": round(c.get("similarity", 0), 3)
        }
        for c in top_chunks
    ]

    # Step 6: audit log (fire and forget)
    asyncio.create_task(db_insert("query_logs", [{
        "org_id":   org_id,
        "question": question,
        "answer":   answer
    }]))

    return {
        "answer":          answer,
        "sources":         [c["chunk_text"][:200] + "..." for c in top_chunks],
        "source_passages": source_passages,
        "org_id":          org_id
    }
    await set_cached(org_id, question, result, ttl=300)
    return result