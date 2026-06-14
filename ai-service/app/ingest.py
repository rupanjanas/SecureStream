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

# ── Jina embedding ────────────────────────────────────────────────────────────

_JINA_SEM             = asyncio.Semaphore(4)
_JINA_RETRYABLE_CODES = {429, 500, 502, 503, 504}


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    all_vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for i in range(0, len(texts), 32):
            batch    = texts[i : i + 32]
            last_exc: Optional[Exception] = None
            for attempt in range(3):
                try:
                    async with _JINA_SEM:
                        r = await client.post(
                            "https://api.jina.ai/v1/embeddings",
                            headers={
                                "Authorization": f"Bearer {settings.jina_api_key}",
                                "Content-Type":  "application/json",
                            },
                            json={
                                "input": batch,
                                "model": "jina-embeddings-v3",
                                "task":  "retrieval.passage",
                            },
                        )
                    if r.status_code in _JINA_RETRYABLE_CODES:
                        wait = 2 ** attempt
                        logger.warning("Jina HTTP %d — retry %d in %ds", r.status_code, attempt + 1, wait)
                        await asyncio.sleep(wait)
                        continue
                    r.raise_for_status()
                    all_vectors.extend(item["embedding"] for item in r.json()["data"])
                    break
                except httpx.TimeoutException as exc:
                    last_exc = exc
                    wait     = 2 ** attempt
                    logger.warning("Jina timeout — retry %d in %ds", attempt + 1, wait)
                    await asyncio.sleep(wait)
            else:
                raise RuntimeError(
                    f"Jina embed failed after 3 attempts for batch starting at index {i}"
                ) from last_exc
    return all_vectors


# ── Supabase Storage upload ───────────────────────────────────────────────────

async def upload_to_storage(file_bytes: bytes, filename: str, user_id: str) -> Optional[str]:
    path       = f"{user_id}/{filename}"
    public_url = f"{BASE}/storage/v1/object/public/documents/{path}"
    headers    = {
        "apikey":        settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type":  "application/octet-stream",
        "x-upsert":      "true",
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
                logger.info("Storage upload OK → %s", public_url)
                return public_url
            logger.warning("Storage upload HTTP %d attempt %d", r.status_code, attempt + 1)
        except Exception:
            logger.exception("Storage upload exception attempt %d", attempt + 1)
        if attempt == 0:
            await asyncio.sleep(2)
    return None


# ── Text utilities ────────────────────────────────────────────────────────────

def fingerprint(text: str) -> str:
    normalised = re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", text.lower().strip()))
    return hashlib.md5(normalised.encode()).hexdigest()


def clean_document_text(text: str) -> str:
    text = re.sub(r"-\s*\n\s*",       "",      text)
    text = re.sub(r"([a-z])([A-Z])",  r"\1 \2", text)
    text = re.sub(r"([.!?,:;])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"\s+",             " ",      text)
    return text.strip()


_JUNK_RE = re.compile(r"(https?://|www\.|doi\.org)", re.IGNORECASE)


def is_junk(text: str) -> bool:
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if len(sentences) <= 1:
        return False
    junk_count = sum(1 for s in sentences if _JUNK_RE.search(s))
    return junk_count / len(sentences) > 0.5


# ── Chunker ───────────────────────────────────────────────────────────────────

_SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'])')


def _units(text: str) -> list[str]:
    result: list[str] = []
    for para in re.split(r'\n{2,}', text):
        para = para.strip()
        if not para:
            continue
        sents = [s.strip() for s in _SENT_RE.split(para) if s.strip()]
        result.extend(sents if sents else [para])
    return result


def chunk_text(
    text:       str,
    chunk_size: Optional[int] = None,
    overlap:    Optional[int] = None,
) -> list[str]:
    cs = chunk_size if chunk_size is not None else settings.chunk_size
    ov = min(overlap  if overlap  is not None else settings.chunk_overlap, cs // 4)

    units = _units(text)
    if not units:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    buf:    list[str] = []
    buf_len = 0

    for unit in units:
        unit_len = len(unit) + 1

        if buf_len + unit_len > cs and buf:
            chunks.append(" ".join(buf))
            carry, carry_len = [], 0
            for s in reversed(buf):
                sl = len(s) + 1
                if carry_len + sl > ov:
                    break
                carry.insert(0, s)
                carry_len += sl
            buf, buf_len = carry, carry_len

        if len(unit) > cs:
            if buf:
                chunks.append(" ".join(buf))
                buf, buf_len = [], 0
            for start in range(0, len(unit), cs - ov):
                part = unit[start : start + cs].strip()
                if part:
                    chunks.append(part)
        else:
            buf.append(unit)
            buf_len += unit_len

    if buf:
        chunks.append(" ".join(buf))

    return [c for c in chunks if c.strip()]


# ── Per-page chunking pipeline ────────────────────────────────────────────────

def process_and_chunk_document(
    doc_pages: list[dict[str, Any]],
    filename:  str,
) -> list[dict[str, Any]]:
    result:      list[dict[str, Any]] = []
    chunk_index: int = 0

    for page in doc_pages:
        cleaned = clean_document_text(page["text"])
        if not cleaned or len(cleaned.split()) < 10:
            continue
        for text in chunk_text(cleaned):
            if not text.strip() or is_junk(text):
                continue
            result.append({
                "text":        text,
                "page":        page["page_num"],
                "doc_name":    filename,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

    return result


# ── PDF / TXT extraction ──────────────────────────────────────────────────────

def extract_pdf_pages(path: str) -> list[dict[str, Any]]:
    doc = fitz.open(path)
    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF is password-protected")
    pages: list[dict[str, Any]] = []
    for page_num, page in enumerate(doc, start=1):
        if page_num > settings.max_ingest_pages:
            logger.warning("PDF truncated at page %d (limit=%d)", page_num, settings.max_ingest_pages)
            break
        blocks = page.get_text("blocks")
        texts  = [
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


# ── Main entry point ──────────────────────────────────────────────────────────

async def ingest_document(
    file_bytes: bytes,
    filename:   str,
    user_id:    str,
    domain:     str = "general",
) -> dict:
    suffix   = ".pdf" if filename.lower().endswith(".pdf") else ".txt"
    file_url = await upload_to_storage(file_bytes, filename, user_id)

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

    total_words = sum(len(p["text"].split()) for p in pages)
    if total_words > settings.max_ingest_words:
        logger.warning("%s: %d words exceeds limit — truncating", filename, total_words)
        trimmed, count = [], 0
        for p in pages:
            words     = p["text"].split()
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

    seen: set[str] = set()
    unique: list[dict] = []
    for c in chunks:
        fp = fingerprint(c["text"])
        if fp not in seen:
            seen.add(fp)
            unique.append(c)
    logger.info("%s: %d chunks → %d unique after dedup", filename, len(chunks), len(unique))

    all_vectors = await embed_texts([c["text"] for c in unique])

    rows = [
        {
            "user_id":     user_id,
            "doc_name":    filename,
            "chunk_text":  unique[i]["text"],
            "chunk_index": unique[i]["chunk_index"],
            "embedding":   all_vectors[i],
            "file_url":    file_url,
            "metadata": {
                "domain":      domain,
                "chunk_index": unique[i]["chunk_index"],
                "page_number": unique[i]["page"],
                "doc_name":    filename,
                "char_count":  len(unique[i]["text"]),
                "section":     f"Page {unique[i]['page']}",
                "file_url":    file_url,
            },
        }
        for i in range(len(unique))
    ]

    await db_upsert("documents", rows, on_conflict="user_id,doc_name,chunk_index")

    return {
        "message":       "Ingested successfully",
        "chunks_stored": len(rows),
        "doc_name":      filename,
        "file_url":      file_url,
    }