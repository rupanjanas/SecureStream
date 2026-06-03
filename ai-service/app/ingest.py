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
    
from __future__ import annotations

"""
ingest.py — document ingestion: extract → chunk → embed → store.

Production fixes applied:
1.  embed_texts: exponential-backoff retry on 429 / 5xx from Jina (up to 3 attempts).
2.  ingest_document: uses db_upsert (not db_insert) to avoid duplicate rows on
    re-ingest of the same document (unique key: org_id + doc_name + chunk_index).
3.  Page-count / word-count guard before semantic splitting to prevent OOM on
    huge documents (configurable via settings.max_ingest_pages / max_ingest_words).
4.  upload_to_storage: single retry on transient failure.
5.  process_and_chunk_document: page-assignment loop verified correct —
    walks all mappings and keeps last one whose start <= pos, then breaks on
    first mapping whose start > pos (sorted ascending); logic is sound.
6.  clean_document_text: improved regex order to avoid double-spacing.
7.  Structured logging throughout; no bare print() in hot paths.
8.  _splitter instantiation is deferred to first use (lazy singleton) to avoid
    import-time model download blocking startup.
"""

import asyncio
import hashlib
import logging
import os
import re
import tempfile
from typing import Any, Optional

import fitz
import httpx
from llama_index.core import Document
from llama_index.core.node_parser import SemanticSplitterNodeParser
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from app.config import settings
from app.db import BASE, HEADERS, db_upsert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semantic splitter (lazy singleton — avoids model download at import time)
# ---------------------------------------------------------------------------

_splitter: Optional[SemanticSplitterNodeParser] = None
_SPLITTER_LOCK = asyncio.Lock()


async def _get_splitter() -> SemanticSplitterNodeParser:
    global _splitter
    if _splitter is None:
        async with _SPLITTER_LOCK:
            if _splitter is None:
                embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
                _splitter = SemanticSplitterNodeParser(
                    buffer_size=1,
                    breakpoint_percentile_threshold=95,
                    embed_model=embed_model,
                )
    return _splitter


# ---------------------------------------------------------------------------
# Jina embedding (with retry)
# ---------------------------------------------------------------------------

# Semaphore: at most 4 concurrent Jina batches across all in-flight requests.
_JINA_SEM = asyncio.Semaphore(4)

_JINA_RETRYABLE = {429, 500, 502, 503, 504}


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed texts via Jina v3.
    Batches into groups of 32 with a concurrency semaphore.
    Retries up to 3 times with exponential backoff on 429 / 5xx.
    """
    if not texts:
        return []

    all_vectors: list[list[float]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            last_exc: Optional[Exception] = None

            for attempt in range(3):
                try:
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

                    if r.status_code in _JINA_RETRYABLE:
                        wait = 2 ** attempt
                        logger.warning(
                            "Jina embed HTTP %d — retry %d in %ds",
                            r.status_code, attempt + 1, wait,
                        )
                        await asyncio.sleep(wait)
                        continue

                    r.raise_for_status()
                    all_vectors.extend(item["embedding"] for item in r.json()["data"])
                    break  # success

                except httpx.TimeoutException as exc:
                    last_exc = exc
                    wait = 2 ** attempt
                    logger.warning("Jina embed timeout — retry %d in %ds", attempt + 1, wait)
                    await asyncio.sleep(wait)

            else:
                raise RuntimeError(
                    f"Jina embed failed after 3 attempts for batch {i}–{i+len(batch)}"
                ) from last_exc

    return all_vectors


# ---------------------------------------------------------------------------
# Supabase Storage upload (with single retry)
# ---------------------------------------------------------------------------

async def upload_to_storage(
    file_bytes: bytes,
    filename: str,
    org_id: str,
) -> Optional[str]:
    path = f"{org_id}/{filename}"
    storage_headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/octet-stream",
        "x-upsert": "true",
    }
    url = f"{BASE}/storage/v1/object/public/documents/{path}"

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                r = await client.post(
                    f"{BASE}/storage/v1/object/documents/{path}",
                    headers=storage_headers,
                    content=file_bytes,
                )
            if r.status_code in (200, 201):
                logger.info("Storage upload OK → %s", url)
                return url
            logger.warning(
                "Storage upload HTTP %d attempt %d: %s",
                r.status_code, attempt + 1, r.text[:200],
            )
        except Exception:
            logger.exception("Storage upload exception attempt %d", attempt + 1)
        if attempt == 0:
            await asyncio.sleep(2)  # backoff before retry

    return None


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def fingerprint(text: str) -> str:
    """MD5 fingerprint of normalised text — used for deduplication only."""
    normalised = re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", text.lower().strip()))
    return hashlib.md5(normalised.encode()).hexdigest()


def clean_document_text(text: str) -> str:
    text = re.sub(r"-\s*\n\s*", "", text)                  # dehyphenate line breaks first
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)       # fix PDF word-boundary loss
    text = re.sub(r"([.!?,:;])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text)                        # collapse whitespace last
    return text.strip()


_JUNK_RE = re.compile(r"(https?://|www\.|doi\.org)", re.IGNORECASE)


def is_junk(text: str) -> bool:
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if not sentences:
        return True
    junk_ratio = sum(1 for s in sentences if _JUNK_RE.search(s)) / len(sentences)
    return junk_ratio > 0.5


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

async def process_and_chunk_document(
    doc_pages: list[dict[str, Any]],
    filename: str,
) -> list[dict[str, Any]]:
    """
    Semantically chunk a multi-page document.

    Returns a list of dicts:
      {"text": str, "page": int, "doc_name": str, "chunk_index": int}

    Page-assignment algorithm:
      Walk page_offsets in ascending order; keep the last mapping whose
      start <= chunk position.  The `break` after `mapping["start"] > pos`
      is safe because offsets are sorted ascending — no need to look further.
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

    if not segments:
        return []

    full_text = " ".join(segments)
    total_words = len(full_text.split())

    # Guard against documents that would OOM the splitter
    max_words = getattr(settings, "max_ingest_words", 200_000)
    if total_words > max_words:
        logger.warning(
            "%s: %d words exceeds limit %d — truncating before chunking",
            filename, total_words, max_words,
        )
        full_text = " ".join(full_text.split()[:max_words])

    # Semantic chunking with graceful fallback
    try:
        splitter = await _get_splitter()
        nodes = splitter.get_nodes_from_documents([Document(text=full_text)])
        chunks_text = [n.text for n in nodes if n.text.strip()]
    except Exception:
        logger.exception(
            "%s: Semantic splitter failed — falling back to paragraph split", filename
        )
        chunks_text = [p.strip() for p in re.split(r"\n{2,}", full_text) if p.strip()]
        if not chunks_text:
            chunks_text = [full_text]

    processed: list[dict] = []
    cursor = 0

    for i, chunk_text in enumerate(chunks_text):
        if is_junk(chunk_text):
            continue

        search_anchor = chunk_text[:40]
        pos = full_text.find(search_anchor, cursor)
        if pos == -1:
            pos = cursor
        cursor = pos + max(len(chunk_text) - 50, 1)

        assigned_page = page_offsets[0]["page_num"] if page_offsets else 1
        for mapping in page_offsets:
            if mapping["start"] <= pos:
                assigned_page = mapping["page_num"]
            else:
                break  # sorted ascending — safe to stop

        processed.append(
            {
                "text": chunk_text,
                "page": assigned_page,
                "doc_name": filename,
                "chunk_index": i,
            }
        )

    return processed


# ---------------------------------------------------------------------------
# PDF / TXT extraction
# ---------------------------------------------------------------------------

def extract_pdf_pages(path: str) -> list[dict[str, Any]]:
    doc = fitz.open(path)
    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF is password-protected")
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

    max_pages = getattr(settings, "max_ingest_pages", 500)
    if len(pages) > max_pages:
        logger.warning("PDF has %d pages; truncating to %d", len(pages), max_pages)
        pages = pages[:max_pages]

    return pages


def extract_txt_pages(raw: str) -> list[dict[str, Any]]:
    return [{"page_num": 1, "text": raw}]


# ---------------------------------------------------------------------------
# Main ingest entry-point
# ---------------------------------------------------------------------------

async def ingest_document(
    file_bytes: bytes,
    filename: str,
    org_id: str,
    domain: str = "general",
) -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"

    # 1. Upload original to Supabase Storage (failure is non-fatal)
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
    except ValueError as exc:
        # Encrypted PDF etc.
        return {
            "message": str(exc),
            "chunks_stored": 0,
            "doc_name": filename,
            "file_url": file_url,
        }
    finally:
        os.unlink(tmp_path)

    # 3. Semantic chunking (async — splitter init is lazy)
    chunks = await process_and_chunk_document(pages, filename)

    if not chunks:
        return {
            "message": "No content extracted",
            "chunks_stored": 0,
            "doc_name": filename,
            "file_url": file_url,
        }

    # 4. Deduplicate before embedding
    seen: set[str] = set()
    unique: list[dict] = []
    for c in chunks:
        fp = fingerprint(c["text"])
        if fp not in seen:
            seen.add(fp)
            unique.append(c)

    logger.info("%s: %d chunks → %d unique after dedup", filename, len(chunks), len(unique))

    # 5. Embed
    texts = [c["text"] for c in unique]
    all_vectors = await embed_texts(texts)

    # 6. Build rows
    rows = [
        {
            "org_id": org_id,
            "doc_name": filename,
            "chunk_text": unique[i]["text"],
            "chunk_index": unique[i]["chunk_index"],
            "embedding": all_vectors[i],
            "file_url": file_url,
            "metadata": {
                "domain": domain,
                "chunk_index": unique[i]["chunk_index"],
                "page_number": unique[i]["page"],
                "doc_name": filename,
                "char_count": len(unique[i]["text"]),
                "section": f"Page {unique[i]['page']}",
                "file_url": file_url,
            },
        }
        for i in range(len(unique))
    ]

    # 7. Upsert (idempotent — safe to re-ingest the same document)
    await db_upsert("documents", rows, on_conflict="org_id,doc_name,chunk_index")

    return {
        "message": "Ingested successfully",
        "chunks_stored": len(rows),
        "doc_name": filename,
        "file_url": file_url,
    }