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
    
from langchain.prompts import PromptTemplate  # keep existing imports
from app.config import settings
from app.db import db_rpc, db_insert
from app.cache import get_cached, set_cached
import tempfile, os, re, hashlib
import httpx
import fitz  # NEW: replaces PyPDFLoader

# ── Jina embeddings ──────────────────────────────────────────────────────────
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
                "task": "retrieval.passage"
            }
        )
        r.raise_for_status()
        data = r.json()
        return [item["embedding"] for item in data["data"]]


# ── Reference/junk filtering ──────────────────────────────────────────────────
_REF_HEADER = re.compile(
    r'^\s*(references?|bibliography|works\s+cited|sources?|citations?|'
    r'further\s+reading|footnotes?|endnotes?|notes?)\s*$',
    re.IGNORECASE | re.MULTILINE
)

_JUNK_LINE = re.compile(
    r'(https?://|www\.|doi\.org|visited\s+on|IP\s+Bulletin|Volume\s+[IVX]+|'
    r'Issue\s+\d|Jan[-\s]June|available\s+at)',
    re.IGNORECASE
)

def strip_references(text: str) -> str:
    match = _REF_HEADER.search(text)
    if match:
        text = text[:match.start()]
    return text.strip()

def is_junk_chunk(text: str) -> bool:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return True
    junk_lines = sum(1 for l in lines if _JUNK_LINE.search(l))
    return (junk_lines / len(lines)) > 0.4


# ── Semantic chunking ─────────────────────────────────────────────────────────
def semantic_chunks(text: str) -> list[dict]:
    MAX_CHARS = 800
    MIN_CHARS = 80
    chunks = []
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

    for para in paragraphs:
        if len(para) <= MAX_CHARS:
            if len(para) >= MIN_CHARS:
                chunks.append({"text": para, "level": "paragraph"})
        else:
            sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) <= MAX_CHARS:
                    current += (" " if current else "") + sent
                else:
                    if len(current) >= MIN_CHARS:
                        chunks.append({"text": current.strip(), "level": "sentence"})
                    if len(sent) > MAX_CHARS:
                        clauses = re.split(r'(?<=[;:])\s+', sent)
                        buf = ""
                        for clause in clauses:
                            if len(buf) + len(clause) <= MAX_CHARS:
                                buf += (" " if buf else "") + clause
                            else:
                                if len(buf) >= MIN_CHARS:
                                    chunks.append({"text": buf.strip(), "level": "clause"})
                                buf = clause
                        if len(buf) >= MIN_CHARS:
                            chunks.append({"text": buf.strip(), "level": "clause"})
                        current = ""
                    else:
                        current = sent
            if len(current) >= MIN_CHARS:
                chunks.append({"text": current.strip(), "level": "sentence"})

    return chunks


def clean(text: str) -> str:
    text = strip_references(text)          # drop bibliography section
    text = re.sub(r'-\n', '', text)        # fix PDF hyphenation
    text = re.sub(r'\n', ' ', text)        # flatten newlines
    text = re.sub(r'\s+', ' ', text)       # normalize spaces
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    return text.strip()


def fingerprint(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    normalized = re.sub(r'[^\w\s]', '', normalized)
    return hashlib.md5(normalized.encode()).hexdigest()


# ── Hierarchical metadata ─────────────────────────────────────────────────────
def build_hierarchy(chunks: list[dict], doc_name: str) -> list[dict]:
    result           = []
    current_section  = "Introduction"
    section_idx      = 0
    chunk_in_section = 0

    for i, chunk in enumerate(chunks):
        text = chunk["text"]

        if is_junk_chunk(text):      # skip URL/citation-heavy chunks
            continue

        is_header = (
            len(text) < 120
            and not text.endswith('.')
            and (text.istitle() or text.isupper() or re.match(r'^\d+[\.\)]\s+', text))
        )

        if is_header:
            current_section  = text
            section_idx     += 1
            chunk_in_section = 0
            continue

        chunk_in_section += 1
        result.append({
            "text":  text,
            "level": chunk.get("level", "paragraph"),
            "metadata": {
                "domain":              "general",
                "chunk_index":         i,
                "section":             current_section,
                "section_index":       section_idx,
                "position_in_section": chunk_in_section,
                "doc_name":            doc_name,
                "char_count":          len(text)
            }
        })

    return result


# ── Main ingest function ──────────────────────────────────────────────────────
async def ingest_document(file_bytes: bytes, filename: str, org_id: str, domain: str = "general") -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # ── PDF extraction via pymupdf (fixes word-spacing bug) ──
        if suffix == ".pdf":
            doc = fitz.open(tmp_path)
            pages = [page.get_text("text") for page in doc]
            doc.close()
            raw_text = "\n\n".join(pages)
        else:
            with open(tmp_path, "r", encoding="utf-8") as f:
                raw_text = f.read()

        full_text = clean(raw_text)

        if not full_text:
            return {"message": "No text extracted", "chunks_stored": 0, "doc_name": filename}

        raw_chunks = semantic_chunks(full_text)
        structured = build_hierarchy(raw_chunks, filename)

        if not structured:
            return {"message": "No chunks after processing", "chunks_stored": 0, "doc_name": filename}

        seen_fps = set()
        unique   = []
        for c in structured:
            fp = fingerprint(c["text"])
            if fp not in seen_fps:
                seen_fps.add(fp)
                unique.append(c)

        print(f"[{filename}] {len(raw_chunks)} raw → {len(structured)} structured → {len(unique)} after dedup")

        texts = [c["text"] for c in unique]

        all_vectors = []
        for i in range(0, len(texts), 32):
            batch   = texts[i:i+32]
            vectors = await embed_texts(batch)
            all_vectors.extend(vectors)

        rows = [
            {
                "org_id":     org_id,
                "doc_name":   filename,
                "chunk_text": unique[i]["text"],
                "embedding":  all_vectors[i],
                "metadata":   {**unique[i]["metadata"], "domain": domain}
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