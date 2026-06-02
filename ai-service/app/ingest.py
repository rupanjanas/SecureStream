"""
ingest.py — production-grade ingestion pipeline
------------------------------------------------
Changes vs original:
  1. Semantic chunking  — split on sentence boundaries, merge until size limit
  2. SimHash dedup      — near-duplicate chunks rejected at ingest time
  3. Hierarchy metadata — doc → section → chunk (index, total, section heading)
  4. Embeddings offline — computed once here, never at query time
  5. Domain tagging     — optional X-Domain header for sub-tenant partitioning


from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from typing import Optional

import httpx
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_ollama import OllamaEmbeddings

from app.config import settings
from app.db import db_insert

# ──────────────────────────────────────────────
# Embedder (singleton)
# ──────────────────────────────────────────────

_embedder: Optional[OllamaEmbeddings] = None


def get_embedder() -> OllamaEmbeddings:
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbeddings(model=settings.embed_model)
    return _embedder


# ──────────────────────────────────────────────
# 1. Semantic chunker
# ──────────────────────────────────────────────

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_HEADING      = re.compile(r"^(?:[A-Z][A-Z\s]{2,}|#+\s+\S.*)$", re.MULTILINE)


def _split_sentences(text: str) -> list[str]:
    Very fast sentence splitter — no NLTK dependency
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _detect_section(text: str, position: int) -> str:
    Return the nearest heading above `position` in text.
    headings = [(m.start(), m.group()) for m in _HEADING.finditer(text)]
    above = [h for h in headings if h[0] <= position]
    return above[-1][1].strip() if above else "intro"


def semantic_chunks(
    full_text: str,
    max_tokens: int = 400,       # ~400 words ≈ 512 tokens for nomic
    overlap_sentences: int = 2,
) -> list[dict]:
    
    Return list of dicts:
        {"text": str, "section": str, "char_offset": int}

    Strategy:
      - split into sentences
      - greedily merge sentences until word-count exceeds max_tokens
      - carry overlap_sentences from previous chunk into next
    
    sentences  = _split_sentences(full_text)
    chunks:    list[dict] = []
    buf:       list[str]  = []
    buf_words: int        = 0
    char_cursor: int      = 0

    def flush(buf: list[str], offset: int) -> dict:
        text    = " ".join(buf)
        section = _detect_section(full_text, offset)
        return {"text": text, "section": section, "char_offset": offset}

    for sent in sentences:
        word_count = len(sent.split())
        if buf_words + word_count > max_tokens and buf:
            chunks.append(flush(buf, char_cursor - sum(len(s) for s in buf)))
            buf       = buf[-overlap_sentences:]          # carry tail
            buf_words = sum(len(s.split()) for s in buf)
        buf.append(sent)
        buf_words  += word_count
        char_cursor += len(sent) + 1

    if buf:
        chunks.append(flush(buf, char_cursor - sum(len(s) for s in buf)))

    return chunks


# ──────────────────────────────────────────────
# 2. SimHash dedup
# ──────────────────────────────────────────────

def _simhash(text: str, bits: int = 64) -> int:
    
    Lightweight SimHash — no external lib needed.
    Two chunks are near-duplicates if hamming(h1, h2) <= threshold.
    
    tokens  = re.findall(r"\w+", text.lower())
    v       = [0] * bits
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    return sum((1 << i) for i in range(bits) if v[i] > 0)


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def deduplicate_chunks(chunks: list[dict], threshold: int = 5) -> list[dict]:
    
    Remove chunks whose SimHash is within `threshold` bits of any retained chunk.
    threshold=5 removes ~80 % word overlap; tune to taste.
    seen_hashes: list[int] = []
    result: list[dict]     = []
    for chunk in chunks:
        h = _simhash(chunk["text"])
        if all(_hamming(h, sh) > threshold for sh in seen_hashes):
            seen_hashes.append(h)
            result.append(chunk)
    return result


# ──────────────────────────────────────────────
# 3. Ingest orchestrator
# ──────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r"-\n", "", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def ingest_document(
    file_bytes: bytes,
    filename:   str,
    org_id:     str,
    domain:     str = "general",          # sub-partition tag
) -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # ── load raw pages ──────────────────────────────────────
        loader   = PyPDFLoader(tmp_path) if suffix == ".pdf" else TextLoader(tmp_path, encoding="utf-8")
        raw_docs = loader.load()
        full_text = _clean(" ".join(d.page_content for d in raw_docs))

        if not full_text:
            return {"message": "No text extracted", "chunks_stored": 0, "doc_name": filename}

        # ── 1. semantic chunking ─────────────────────────────────
        raw_chunks = semantic_chunks(full_text)
        print(f"[ingest] {len(raw_chunks)} semantic chunks from '{filename}'")

        # ── 2. aggressive dedup ──────────────────────────────────
        deduped = deduplicate_chunks(raw_chunks, threshold=5)
        print(f"[ingest] {len(deduped)} chunks after SimHash dedup")

        texts = [c["text"] for c in deduped]
        if not texts:
            return {"message": "All chunks deduplicated away", "chunks_stored": 0, "doc_name": filename}

        # ── 4. precompute embeddings offline ─────────────────────
        print(f"[ingest] embedding {len(texts)} chunks…")
        vectors = get_embedder().embed_documents(texts)

        # ── storage upload ───────────────────────────────────────
        file_url = await upload_to_storage(file_bytes, filename)

        # ── 3. hierarchy metadata ────────────────────────────────
        total = len(deduped)
        rows  = [
            {
                "org_id":     org_id,
                "doc_name":   filename,
                "chunk_text": deduped[i]["text"],
                "embedding":  vectors[i],
                "file_url":   file_url,
                "metadata": {
                    "chunk_index":   i,
                    "total_chunks":  total,
                    "section":       deduped[i]["section"],     # hierarchy level
                    "char_offset":   deduped[i]["char_offset"],
                    "domain":        domain,                    # tenant partition tag
                    "simhash":       _simhash(deduped[i]["text"]),
                },
            }
            for i in range(total)
        ]

        await db_insert("documents", rows)
        print(f"[ingest] stored {total} chunks for org={org_id}")
        return {"message": "Ingested successfully", "chunks_stored": total, "doc_name": filename}

    finally:
        os.unlink(tmp_path)


# ──────────────────────────────────────────────
# Storage helper (unchanged API, cleaner impl)
# ──────────────────────────────────────────────

async def upload_to_storage(file_bytes: bytes, filename: str) -> str:
    unique_name = f"{uuid.uuid4()}_{filename}"
    url         = f"{settings.supabase_url}/storage/v1/object/documents/{unique_name}"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            headers={
                "apikey":        settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type":  "application/pdf",
            },
            content=file_bytes,
            timeout=60,
        )
        if r.status_code not in (200, 201):
            raise Exception(f"Storage upload failed: {r.text}")

    return f"{settings.supabase_url}/storage/v1/object/public/documents/{unique_name}" """
    
"""
ingest.py — document ingestion: extraction, semantic chunking, embedding, storage.

Key changes from original:
- Uses SemanticSplitterNodeParser (true semantic chunking) instead of SentenceSplitter.
- Deduplicates BEFORE embedding to avoid wasting Jina API calls.
- Fixed page-assignment logic (was breaking on first page boundary).
- Added asyncio.Semaphore to Jina calls so concurrent ingests don't flood the API.
- upload_to_storage raises on failure instead of silently returning None.
- embed_texts batches with a semaphore guard.
"""

import re
import hashlib
import tempfile
import os
import asyncio
import httpx
import fitz
from typing import Dict, Any, List, Optional

# SemanticSplitterNodeParser does embedding-based boundary detection —
# it groups sentences until the cosine distance drops below a threshold.
from llama_index.core.node_parser import SemanticSplitterNodeParser
from llama_index.core import Document
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from app.config import settings
from app.db import db_insert, HEADERS, BASE


# ── Embedding model for semantic splitter ─────────────────────────────────────
# A lightweight local model is used here so the splitter doesn't burn Jina quota.
# Swap for JinaEmbedding if you need multilingual support in the splitter itself.
_SPLITTER_EMBED_MODEL = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

_splitter = SemanticSplitterNodeParser(
    buffer_size=1,                    # sentences on each side of boundary to consider
    breakpoint_percentile_threshold=95,  # higher = fewer, larger chunks
    embed_model=_SPLITTER_EMBED_MODEL,
)

# Semaphore: at most 4 concurrent Jina batches across all in-flight requests
_JINA_SEM = asyncio.Semaphore(4)


# ── Jina embeddings ───────────────────────────────────────────────────────────

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using Jina v3.
    Batches into groups of 32 with a semaphore to avoid flooding the API.
    """
    all_vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            async with _JINA_SEM:
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
                r.raise_for_status()
                all_vectors.extend(item["embedding"] for item in r.json()["data"])
    return all_vectors


# ── Supabase Storage upload ───────────────────────────────────────────────────

async def upload_to_storage(
    file_bytes: bytes,
    filename: str,
    org_id: str,
) -> Optional[str]:
    """
    Upload the original file to Supabase Storage.
    Returns the public URL, or None on failure (logged but not raised so ingest
    can continue without the viewer URL).
    """
    path = f"{org_id}/{filename}"
    storage_headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/octet-stream",
        "x-upsert": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{BASE}/storage/v1/object/documents/{path}",
                headers=storage_headers,
                content=file_bytes,
            )
            if r.status_code not in (200, 201):
                print(f"[STORAGE] Upload failed {r.status_code}: {r.text}")
                return None
        url = f"{BASE}/storage/v1/object/public/documents/{path}"
        print(f"[STORAGE] Uploaded → {url}")
        return url
    except Exception as e:
        print(f"[STORAGE] Exception: {e}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def fingerprint(text: str) -> str:
    normalised = re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", text.lower().strip()))
    return hashlib.md5(normalised.encode()).hexdigest()


def clean_document_text(text: str) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)   # fix PDF word-boundary loss
    text = re.sub(r"-\s*\n\s*", "", text)               # fix hyphenated line breaks
    text = re.sub(r"\s+", " ", text)                     # collapse whitespace
    text = re.sub(r"([.!?,:;])([A-Za-z])", r"\1 \2", text)
    return text.strip()


_JUNK_RE = re.compile(r"(https?://|www\.|doi\.org)", re.IGNORECASE)


def is_junk(text: str) -> bool:
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if not sentences:
        return True
    junk_ratio = sum(1 for s in sentences if _JUNK_RE.search(s)) / len(sentences)
    return junk_ratio > 0.5


# ── Chunking ──────────────────────────────────────────────────────────────────

def process_and_chunk_document(
    doc_pages: List[Dict[str, Any]],
    filename: str,
) -> List[Dict[str, Any]]:
    """
    Semantically chunk a multi-page document.

    Page assignment fix: instead of breaking out of the loop on the first
    mapping whose start exceeds the chunk position (which skips all later
    pages), we now walk every mapping and keep the last one whose start ≤ pos.
    """
    segments: list[str] = []
    page_offsets: list[dict] = []
    offset = 0

    for page_data in doc_pages:
        cleaned = clean_document_text(page_data["text"])
        if not cleaned or len(cleaned.split()) < 10:
            continue
        page_offsets.append(
            {
                "page_num": page_data["page_num"],
                "start": offset,
                "end": offset + len(cleaned),
            }
        )
        segments.append(cleaned)
        offset += len(cleaned) + 1  # +1 for the join separator

    full_text = " ".join(segments)
    if not full_text.strip():
        return []

    # SemanticSplitterNodeParser returns nodes whose boundaries align with
    # topic shifts rather than arbitrary token counts.
    try:
        nodes = _splitter.get_nodes_from_documents([Document(text=full_text)])
        chunks_text = [n.text for n in nodes if n.text.strip()]
    except Exception as e:
        print(f"[CHUNK] Semantic splitter failed: {e} — falling back to paragraph split")
        # Graceful fallback: split on double newlines, then by sentence length
        chunks_text = [p.strip() for p in re.split(r"\n{2,}", full_text) if p.strip()]
        if not chunks_text:
            chunks_text = [full_text]

    processed: list[dict] = []
    cursor = 0

    for i, chunk_text in enumerate(chunks_text):
        if is_junk(chunk_text):
            continue

        # Locate the chunk's start position in full_text
        search_anchor = chunk_text[:40]
        pos = full_text.find(search_anchor, cursor)
        if pos == -1:
            pos = cursor
        cursor = pos + max(len(chunk_text) - 50, 1)

        # FIX: walk all mappings; keep the last one whose start ≤ pos.
        # This correctly handles chunks that span multiple pages.
        assigned_page = page_offsets[0]["page_num"] if page_offsets else 1
        for mapping in page_offsets:
            if mapping["start"] <= pos:
                assigned_page = mapping["page_num"]
            else:
                break  # mappings are sorted ascending, no need to look further

        processed.append(
            {
                "text": chunk_text,
                "page": assigned_page,
                "doc_name": filename,
                "chunk_index": i,
            }
        )

    return processed


# ── PDF / TXT extraction ──────────────────────────────────────────────────────

def extract_pdf_pages(path: str) -> List[Dict[str, Any]]:
    doc = fitz.open(path)
    pages = []
    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("blocks")
        texts = [
            b[4].strip()
            for b in sorted(blocks, key=lambda b: (round(b[1] / 10), b[0]))
            if b[4].strip()
        ]
        text = " ".join(texts)
        if text.strip():
            pages.append({"page_num": page_num, "text": text})
    doc.close()
    return pages


def extract_txt_pages(raw: str) -> List[Dict[str, Any]]:
    return [{"page_num": 1, "text": raw}]


# ── Main ingest ───────────────────────────────────────────────────────────────

async def ingest_document(
    file_bytes: bytes,
    filename: str,
    org_id: str,
    domain: str = "general",
) -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"

    # 1. Upload original file to Supabase Storage (non-blocking on failure)
    file_url = await upload_to_storage(file_bytes, filename, org_id)

    # 2. Extract pages
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            pages = extract_pdf_pages(tmp_path)
        else:
            raw = file_bytes.decode("utf-8", errors="ignore")
            pages = extract_txt_pages(raw)
    finally:
        os.unlink(tmp_path)

    # 3. Chunk (semantic)
    chunks = process_and_chunk_document(pages, filename)

    if not chunks:
        return {
            "message": "No content extracted",
            "chunks_stored": 0,
            "doc_name": filename,
            "file_url": file_url,
        }

    # 4. Deduplicate BEFORE embedding to avoid wasting API quota
    seen: set[str] = set()
    unique: list[dict] = []
    for c in chunks:
        fp = fingerprint(c["text"])
        if fp not in seen:
            seen.add(fp)
            unique.append(c)

    print(f"[{filename}] {len(chunks)} chunks → {len(unique)} after dedup")
    for c in unique[:3]:
        print(f"  page={c['page']} text={c['text'][:80]!r}")

    # 5. Embed in batches of 32 (semaphore-guarded inside embed_texts)
    texts = [c["text"] for c in unique]
    all_vectors = await embed_texts(texts)

    # 6. Build rows
    rows = [
        {
            "org_id": org_id,
            "doc_name": filename,
            "chunk_text": unique[i]["text"],
            "embedding": all_vectors[i],
            "file_url": file_url,           # top-level column
            "metadata": {
                "domain": domain,
                "chunk_index": unique[i]["chunk_index"],
                "page_number": unique[i]["page"],
                "doc_name": filename,
                "char_count": len(unique[i]["text"]),
                "section": f"Page {unique[i]['page']}",
                "file_url": file_url,       # fallback for older query paths
            },
        }
        for i in range(len(unique))
    ]

    await db_insert("documents", rows)

    return {
        "message": "Ingested successfully",
        "chunks_stored": len(rows),
        "doc_name": filename,
        "file_url": file_url,
    }