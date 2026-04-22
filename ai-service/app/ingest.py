from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_ollama import OllamaEmbeddings
from app.config import settings
from app.db import db_insert
import tempfile, os, re
import httpx
import uuid

# Lazy — don't init at import time
_embedder = None

def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbeddings(model=settings.embed_model)
    return _embedder

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n\n", "\n", ".", " "]
)

def clean_text(text: str) -> str:
    text = re.sub(r"-\n", "", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

async def ingest_document(file_bytes: bytes, filename: str, org_id: str) -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        loader = PyPDFLoader(tmp_path) if suffix == ".pdf" else TextLoader(tmp_path, encoding="utf-8")
        raw_docs = loader.load()
        chunks   = splitter.split_documents(raw_docs)
        texts    = [clean_text(c.page_content) for c in chunks if c.page_content.strip()]

        if not texts:
            return {"message": "No text extracted", "chunks_stored": 0, "doc_name": filename}

        print(f"Embedding {len(texts)} chunks for org={org_id}...")
        vectors = get_embedder().embed_documents(texts)
        print("Uploading to Supabase...")
        file_url = await upload_to_storage(file_bytes, filename)
        print("Upload success:", file_url)
        rows = [
            {
                "org_id":     org_id,
                "doc_name":   filename,
                "chunk_text": texts[i],
                "embedding":  vectors[i],
                "file_url": file_url,
                "metadata":   {"chunk_index": i, "total_chunks": len(texts)}
            }
            for i in range(len(texts))
        ]
        
        await db_insert("documents", rows)
        print(f"Ingested {len(rows)} chunks for org={org_id}")
        return {"message": "Ingested successfully", "chunks_stored": len(rows), "doc_name": filename}
    finally:
        os.unlink(tmp_path)
        
async def upload_to_storage(file_bytes: bytes, filename: str):
    filename = f"{uuid.uuid4()}_{filename}"
    url = f"{settings.supabase_url}/storage/v1/object/documents/{filename}"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type": "application/pdf"
            },
            content=file_bytes
        )

        if r.status_code not in (200, 201):
            raise Exception(f"Storage upload failed: {r.text}")

    # public URL
    return f"{settings.supabase_url}/storage/v1/object/public/documents/{filename}"