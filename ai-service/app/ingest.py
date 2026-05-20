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
    
from app.config import settings
from app.db import db_insert
import tempfile, os, re, hashlib
import httpx
import fitz
from app.db import HEADERS, BASE


# ── Jina embeddings ───────────────────────────────────────────────────────────

async def embed_texts(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {settings.jina_api_key}",
                "Content-Type": "application/json"
            },
            json={
                "input": texts,
                "model": "jina-embeddings-v3",
                "task":  "retrieval.passage"
            }
        )
        r.raise_for_status()
        data = r.json()
        return [item["embedding"] for item in data["data"]]


# ── Storage ───────────────────────────────────────────────────────────────────

async def upload_to_storage(file_bytes: bytes, filename: str, org_id: str) -> str:
    path = f"{org_id}/{filename}"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/storage/v1/object/documents/{path}",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            content=file_bytes,
        )
    return f"{BASE}/storage/v1/object/public/documents/{path}"


# ── Junk filter ───────────────────────────────────────────────────────────────

_JUNK_LINE = re.compile(
    r'(https?://|www\.|doi\.org)',
    re.IGNORECASE
)

def is_junk_chunk(text: str) -> bool:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return True
    junk = sum(1 for l in lines if _JUNK_LINE.search(l))
    return (junk / len(lines)) > 0.5


# ── Fingerprint for dedup ─────────────────────────────────────────────────────

def fingerprint(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    normalized = re.sub(r'[^\w\s]', '', normalized)
    return hashlib.md5(normalized.encode()).hexdigest()


# ── PDF text extraction per page ──────────────────────────────────────────────

def extract_pages(doc) -> list[dict]:
    """
    Extract text per page preserving page numbers.
    Uses simple block extraction — no font analysis.
    """
    pages = []
    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("blocks")
        texts  = [
            b[4].strip()
            for b in sorted(blocks, key=lambda b: (round(b[1] / 10), b[0]))
            if b[4].strip()
        ]
        text = " ".join(texts)
        # Basic cleanup
        text = re.sub(r'-\s+', '', text)           # fix hyphenated breaks
        text = re.sub(r'\s+', ' ', text)           # collapse whitespace
        text = re.sub(r'([.!?,:;])([A-Za-z])', r'\1 \2', text)
        text = text.strip()
        if text:
            pages.append({"text": text, "page": page_num})
    return pages


# ── Sliding window chunker ────────────────────────────────────────────────────

def sliding_window_chunks(
    pages:      list[dict],
    chunk_size: int = 400,   # words per chunk
    overlap:    int = 80,    # words overlap between chunks
) -> list[dict]:
    """
    Split document into overlapping fixed-size chunks by word count.
    Overlap ensures context at chunk boundaries is never lost.
    Works reliably on any document regardless of formatting.
    """
    # Build a flat list of (word, page_number) pairs
    word_pages = []
    for p in pages:
        words = p["text"].split()
        for w in words:
            word_pages.append((w, p["page"]))

    if not word_pages:
        return []

    chunks  = []
    start   = 0
    total   = len(word_pages)

    while start < total:
        end        = min(start + chunk_size, total)
        chunk_wps  = word_pages[start:end]
        chunk_text = " ".join(w for w, _ in chunk_wps)
        chunk_page = chunk_wps[0][1]   # page of first word in chunk

        if len(chunk_text.strip()) >= 80 and not is_junk_chunk(chunk_text):
            chunks.append({
                "text": chunk_text,
                "page": chunk_page,
            })

        if end == total:
            break
        start += chunk_size - overlap   # slide forward with overlap

    return chunks


# ── Main ingest function ──────────────────────────────────────────────────────

async def ingest_document(
    file_bytes: bytes,
    filename:   str,
    org_id:     str,
    domain:     str = "general"
) -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            doc    = fitz.open(tmp_path)
            pages  = extract_pages(doc)
            doc.close()
            chunks = sliding_window_chunks(pages, chunk_size=400, overlap=80)
        else:
            raw   = file_bytes.decode("utf-8", errors="ignore")
            raw   = re.sub(r'\s+', ' ', raw).strip()
            pages = [{"text": raw, "page": 1}]
            chunks = sliding_window_chunks(pages, chunk_size=400, overlap=80)

        if not chunks:
            return {
                "message":       "No chunks after processing",
                "chunks_stored": 0,
                "doc_name":      filename
            }

        # Deduplicate
        seen_fps = set()
        unique   = []
        for c in chunks:
            fp = fingerprint(c["text"])
            if fp not in seen_fps:
                seen_fps.add(fp)
                unique.append(c)

        print(f"[{filename}] {len(chunks)} chunks → {len(unique)} after dedup")
        print("Sample chunks:")
        for c in unique[:5]:
            print(f"  page={c['page']} text={c['text'][:80]!r}")

        # Embed in batches of 32
        texts       = [c["text"] for c in unique]
        all_vectors = []
        for i in range(0, len(texts), 32):
            batch   = texts[i:i + 32]
            vectors = await embed_texts(batch)
            all_vectors.extend(vectors)

        rows = [
            {
                "org_id":     org_id,
                "doc_name":   filename,
                "chunk_text": unique[i]["text"],
                "embedding":  all_vectors[i],
                "metadata": {
                    "domain":      domain,
                    "chunk_index": i,
                    "page_number": unique[i]["page"],
                    "doc_name":    filename,
                    "char_count":  len(unique[i]["text"]),
                    "section":     f"Page {unique[i]['page']}",
                }
            }
            for i in range(len(unique))
        ]

        await db_insert("documents", rows)

        return {
            "message":       "Ingested successfully",
            "chunks_stored": len(rows),
            "doc_name":      filename
        }
    finally:
        os.unlink(tmp_path)