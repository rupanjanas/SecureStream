from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_ollama import OllamaEmbeddings
from app.config import settings
from app.db import db_insert
import tempfile, os

embedder = OllamaEmbeddings(
    model=settings.embed_model
)

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n\n", "\n", ".", " "]
)

async def ingest_document(file_bytes: bytes, filename: str, org_id: str) -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            loader = PyPDFLoader(tmp_path)
        else:
            loader = TextLoader(tmp_path, encoding="utf-8")

        raw_docs = loader.load()
        chunks = splitter.split_documents(raw_docs)
        texts = [c.page_content for c in chunks if c.page_content.strip()]

        if not texts:
            return {"message": "No text extracted", "chunks_stored": 0, "doc_name": filename}

        print(f"Embedding {len(texts)} chunks for org={org_id} ...")
        vectors = embedder.embed_documents(texts)

        rows = [
            {
                "org_id": org_id,
                "doc_name": filename,
                "chunk_text": texts[i],
                "embedding": vectors[i],
                "metadata": {"chunk_index": i, "total_chunks": len(texts)}
            }
            for i in range(len(texts))
        ]

        await db_insert("documents", rows)

        return {
            "message": "Ingested successfully",
            "chunks_stored": len(rows),
            "doc_name": filename
        }
    finally:
        os.unlink(tmp_path)