from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain.prompts import PromptTemplate
from app.config import settings
from app.db import db_rpc, db_insert

embedder = OllamaEmbeddings(
    model=settings.embed_model
)

llm = OllamaLLM(
    model=settings.llm_model,
    temperature=0.2
)

RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template="""You are a helpful assistant for SecureStream, a secure document platform.
Answer the question using ONLY the context below.
If the answer is not in the context, say "I don't have enough information in the uploaded documents to answer this."

Context:
{context}

Question: {question}

Answer:"""
)

async def answer_question(question: str, org_id: str, top_k: int = 10) -> dict:
    # Embed the question
    query_vector = embedder.embed_query(question)

    # Search Supabase via RPC
    chunks = await db_rpc("match_documents", {
        "query_embedding": query_vector,
        "match_count": top_k,
        "filter_org_id": org_id
    })

    if not chunks:
        return {
            "answer": "No relevant documents found for your organization.",
            "sources": [],
            "org_id": org_id
        }
        
    print("CHUNKS RETURNED:", len(chunks))
    print("QUERY ORG_ID:", org_id)
    # Build context
    context = "\n\n---\n\n".join(
        f"[From: {c['doc_name']}]\n{c['chunk_text']}"
        for c in chunks
    )

    # Ask Ollama
    prompt = RAG_PROMPT.format(context=context, question=question)
    answer = llm.invoke(prompt)

    # Audit log
    await db_insert("query_logs", [{
        "org_id": org_id,
        "question": question,
        "answer": answer
    }])

    return {
        "answer": answer,
        "sources": [c["chunk_text"][:200] + "..." for c in chunks],
        "org_id": org_id
    }