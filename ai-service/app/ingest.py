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

import asyncio
import hashlib
import logging
import os
import re
import tempfile
from typing import Any, Optional

import fitz
import httpx

from app.config import settings
from app.db import BASE, db_upsert

logger = logging.getLogger(__name__)

# ── Jina embedding ───────────────────────────────────────────────────────────

_JINA_SEM = asyncio.Semaphore(4)
_JINA_RETRYABLE = {429, 500, 502, 503, 504}


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    all_vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for i in range(0, len(texts), 32):
            batch = texts[i:i + 32]
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
                        logger.warning("Jina HTTP %d — retry %d in %ds", r.status_code, attempt + 1, wait)
                        await asyncio.sleep(wait)
                        continue
                    r.raise_for_status()
                    all_vectors.extend(item["embedding"] for item in r.json()["data"])
                    break
                except httpx.TimeoutException as exc:
                    last_exc = exc
                    wait = 2 ** attempt
                    logger.warning("Jina timeout — retry %d in %ds", attempt + 1, wait)
                    await asyncio.sleep(wait)
            else:
                raise RuntimeError(
                    f"Jina embed failed after 3 attempts for batch starting at index {i}"
                ) from last_exc
    return all_vectors


# ── Storage upload ───────────────────────────────────────────────────────────

async def upload_to_storage(file_bytes: bytes, filename: str, org_id: str) -> Optional[str]:
    path = f"{org_id}/{filename}"
    url = f"{BASE}/storage/v1/object/public/documents/{path}"
    headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/octet-stream",
        "x-upsert": "true",
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                r = await client.post(
                    f"{BASE}/storage/v1/object/documents/{path}",
                    headers=headers,
                    content=file_bytes,
                )
            if r.status_code in (200, 201):
                logger.info("Storage upload OK → %s", url)
                return url
            logger.warning("Storage upload HTTP %d attempt %d", r.status_code, attempt + 1)
        except Exception:
            logger.exception("Storage upload exception attempt %d", attempt + 1)
        if attempt == 0:
            await asyncio.sleep(2)
    return None


# ── Text utilities ───────────────────────────────────────────────────────────

def fingerprint(text: str) -> str:
    normalised = re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", text.lower().strip()))
    return hashlib.md5(normalised.encode()).hexdigest()


def clean_document_text(text: str) -> str:
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([.!?,:;])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


_JUNK_RE = re.compile(r"(https?://|www\.|doi\.org)", re.IGNORECASE)


def is_junk(text: str) -> bool:
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if not sentences:
        return True
    return sum(1 for s in sentences if _JUNK_RE.search(s)) / len(sentences) > 0.5


# ── Lightweight chunker (replaces SemanticSplitterNodeParser + HuggingFace) ─
#
# Removes ~350 MB of RAM used by the local BAAI/bge-small-en-v1.5 model.
# Strategy: split on paragraph → sentence boundaries, add character-level
# overlap, hard-split any unit that still exceeds chunk_size.

_SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'])')


def _units(text: str) -> list[str]:
    """Paragraph-then-sentence split; handles both structured and raw text."""
    result: list[str] = []
    for para in re.split(r'\n{2,}', text):
        para = para.strip()
        if not para:
            continue
        sents = [s.strip() for s in _SENT_RE.split(para) if s.strip()]
        result.extend(sents if sents else [para])
    return result


def chunk_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    cs = chunk_size if chunk_size is not None else settings.chunk_size
    ov = min(overlap if overlap is not None else settings.chunk_overlap, cs // 4)

    units = _units(text)
    if not units:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for unit in units:
        unit_len = len(unit) + 1

        if buf_len + unit_len > cs and buf:
            chunks.append(" ".join(buf))
            # carry-over: keep last `ov` chars of sentences for context
            carry, carry_len = [], 0
            for s in reversed(buf):
                sl = len(s) + 1
                if carry_len + sl > ov:
                    break
                carry.insert(0, s)
                carry_len += sl
            buf, buf_len = carry, carry_len

        if len(unit) > cs:
            # Single unit longer than chunk_size (table row, long list item)
            if buf:
                chunks.append(" ".join(buf))
                buf, buf_len = [], 0
            for start in range(0, len(unit), cs - ov):
                part = unit[start:start + cs].strip()
                if part:
                    chunks.append(part)
        else:
            buf.append(unit)
            buf_len += unit_len

    if buf:
        chunks.append(" ".join(buf))

    return [c for c in chunks if c.strip()]


# ── Per-page chunking pipeline ───────────────────────────────────────────────

def process_and_chunk_document(
    doc_pages: list[dict[str, Any]],
    filename: str,
) -> list[dict[str, Any]]:
    """
    Chunk each page independently for accurate page attribution.
    Returns list of {text, page, doc_name, chunk_index}.
    """
    result: list[dict[str, Any]] = []
    chunk_index = 0

    for page in doc_pages:
        cleaned = clean_document_text(page["text"])
        if not cleaned or len(cleaned.split()) < 10:
            continue
        for text in chunk_text(cleaned):
            if not text.strip() or is_junk(text):
                continue
            result.append({
                "text": text,
                "page": page["page_num"],
                "doc_name": filename,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

    return result


# ── PDF / TXT extraction ─────────────────────────────────────────────────────

def extract_pdf_pages(path: str) -> list[dict[str, Any]]:
    doc = fitz.open(path)
    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF is password-protected")
    pages: list[dict[str, Any]] = []
    for page_num, page in enumerate(doc, start=1):
        if page_num > settings.max_ingest_pages:
            logger.warning("PDF truncated at %d pages", settings.max_ingest_pages)
            break
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


def extract_txt_pages(raw: str) -> list[dict[str, Any]]:
    return [{"page_num": 1, "text": raw}]


# ── Main ingest entry-point ──────────────────────────────────────────────────

async def ingest_document(
    file_bytes: bytes,
    filename: str,
    org_id: str,
    domain: str = "general",
) -> dict:
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".txt"
    file_url = await upload_to_storage(file_bytes, filename, org_id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            pages = extract_pdf_pages(tmp_path)
        else:
            pages = extract_txt_pages(file_bytes.decode("utf-8", errors="ignore"))
    except ValueError as exc:
        return {"message": str(exc), "chunks_stored": 0, "doc_name": filename, "file_url": file_url}
    finally:
        os.unlink(tmp_path)

    # Word-count guard: truncate pages before chunking, not after
    total_words = sum(len(p["text"].split()) for p in pages)
    if total_words > settings.max_ingest_words:
        logger.warning("%s: %d words > limit, truncating", filename, total_words)
        trimmed, count = [], 0
        for p in pages:
            words = p["text"].split()
            remaining = settings.max_ingest_words - count
            if count + len(words) > settings.max_ingest_words:
                if remaining > 100:
                    trimmed.append({"page_num": p["page_num"], "text": " ".join(words[:remaining])})
                break
            trimmed.append(p)
            count += len(words)
        pages = trimmed

    chunks = process_and_chunk_document(pages, filename)
    if not chunks:
        return {"message": "No content extracted", "chunks_stored": 0, "doc_name": filename, "file_url": file_url}

    # Dedup
    seen: set[str] = set()
    unique = [c for c in chunks if (fp := fingerprint(c["text"])) not in seen and not seen.add(fp)]  # type: ignore[func-returns-value]
    logger.info("%s: %d chunks → %d unique after dedup", filename, len(chunks), len(unique))

    all_vectors = await embed_texts([c["text"] for c in unique])

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

    await db_upsert("documents", rows, on_conflict="org_id,doc_name,chunk_index")
    return {
        "message": "Ingested successfully",
        "chunks_stored": len(rows),
        "doc_name": filename,
        "file_url": file_url,
    }