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
from collections import Counter
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
    r'(https?://|www\.|doi\.org|visited\s+on|IP\s+Bulletin|Volume\s+[IVX]+|'
    r'Issue\s+\d|Jan[-\s]June|available\s+at)',
    re.IGNORECASE
)

def is_junk_chunk(text: str) -> bool:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return True
    junk_lines = sum(1 for l in lines if _JUNK_LINE.search(l))
    return (junk_lines / len(lines)) > 0.4


# ── Text cleaning ─────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    text = re.sub(r'-\n', '', text)
    text = re.sub(r'([.!?,:;])([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'([)\]])([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


# ── Fingerprint for dedup ─────────────────────────────────────────────────────

def fingerprint(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    normalized = re.sub(r'[^\w\s]', '', normalized)
    return hashlib.md5(normalized.encode()).hexdigest()


# ── Header classification ─────────────────────────────────────────────────────

# Patterns that are definitely NOT section headers
_NOT_HEADER = re.compile(
    r'^(\d{6,}|'                          # pure roll numbers like 2305286
    r'\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|' # dates
    r'[A-Z][a-z]+ [A-Z][a-z]+$|'          # simple two-word proper names (John Smith)
    r'Phase|Focus Area|Primary Tasks|Key Deliverable|'
    r'Test|Title|Condition|Behavior|Expected Result|'
    r'Type|Stage|Description|Artifact Name|'
    r'Fig\.|Figure|Table\s+\d)',
    re.IGNORECASE
)

# Patterns that ARE real section headers
_REAL_HEADER = re.compile(
    r'^(abstract|introduction|conclusion|summary|methodology|'
    r'chapter\s+\d|section\s+\d|\d+\.\d*\s+\w|'
    r'related\s+work|literature\s+review|background|'
    r'implementation|results?|discussion|future\s+(scope|work)|'
    r'references?|acknowledgements?|appendix|'
    r'problem\s+statement|system\s+design|testing|'
    r'quality\s+assurance|standards?\s+adopted|'
    r'table\s+of\s+contents|list\s+of\s+(figures|tables)|'
    r'individual\s+contribution)',
    re.IGNORECASE
)

def is_real_header(text: str, size: float, bold: bool, body_size: float) -> bool:
    """
    Strict header detection — only treat text as a section header if:
    1. It's significantly larger than body text (chapter titles), OR
    2. It matches known academic section patterns
    
    Explicitly rejects: names, roll numbers, table column headers
    """
    stripped = text.strip()

    # Must be reasonably short
    if len(stripped) > 120 or len(stripped) < 3:
        return False

    # Reject known non-header patterns first
    if _NOT_HEADER.search(stripped):
        return False

    # Pure numbers are never headers
    if re.match(r'^\d+$', stripped):
        return False

    # Single words that are clearly table cells
    if len(stripped.split()) == 1 and not stripped.isupper():
        return False

    # Large font = definite chapter/section header (e.g. size 20 = chapter titles)
    if size >= body_size * 1.4:
        return True

    # Medium font + bold = subsection header (e.g. size 14 = section 2.1)
    if size >= body_size * 1.1 and bold:
        return True

    # Body size bold — only accept if it matches known academic patterns
    if bold and _REAL_HEADER.match(stripped):
        return True

    return False


# ── PDF structure extraction ──────────────────────────────────────────────────

def extract_pdf_spans(doc) -> list[dict]:
    spans = []
    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_text = ""
                line_size = 12.0
                line_bold = False
                line_page = page_num
                for span in line.get("spans", []):
                    t = span.get("text", "").strip()
                    if t:
                        line_text += " " + t
                        line_size  = span.get("size", 12.0)
                        line_bold  = bool(span.get("flags", 0) & 2 ** 4)
                line_text = line_text.strip()
                if line_text:
                    spans.append({
                        "text": line_text,
                        "page": line_page,
                        "size": round(line_size, 1),
                        "bold": line_bold,
                    })
    return spans


def build_chunks_from_spans(spans: list[dict], doc_name: str) -> list[dict]:
    if not spans:
        return []

    # Compute body font size from longer spans
    long_sizes = [s["size"] for s in spans if len(s["text"]) > 30]
    if not long_sizes:
        long_sizes = [s["size"] for s in spans]
    body_size = Counter(long_sizes).most_common(1)[0][0]

    print(f"[{doc_name}] body_size={body_size:.1f}")

    result          = []
    current_section = "General"
    section_idx     = 0
    buffer          = ""
    buffer_page     = 1

    def flush_buffer():
        nonlocal buffer, buffer_page
        text = clean_text(buffer.strip())
        if len(text) < 80 or is_junk_chunk(text):
            buffer = ""
            return
        # Split into ~600 char chunks
        while len(text) > 600:
            split_at = text.rfind('. ', 0, 600)
            if split_at == -1:
                split_at = text.rfind(' ', 0, 600)
            if split_at == -1:
                split_at = 600
            chunk_text = text[:split_at + 1].strip()
            if len(chunk_text) >= 80:
                result.append(_make_chunk(
                    chunk_text, buffer_page, current_section,
                    section_idx, doc_name, len(result)
                ))
            text = text[split_at + 1:].strip()
        if len(text) >= 80:
            result.append(_make_chunk(
                text, buffer_page, current_section,
                section_idx, doc_name, len(result)
            ))
        buffer = ""

    for span in spans:
        text = span["text"]
        size = span["size"]
        bold = span["bold"]
        page = span["page"]

        if is_real_header(text, size, bold, body_size):
            flush_buffer()
            current_section = text
            section_idx    += 1
            buffer_page     = page
            print(f"  SECTION: {text!r} (size={size}, bold={bold}, page={page})")
        else:
            if not buffer:
                buffer_page = page
            buffer += " " + text

    flush_buffer()
    return result


def _make_chunk(text, page, section, section_idx, doc_name, idx):
    return {
        "text":  text,
        "level": "paragraph",
        "metadata": {
            "domain":        "general",
            "chunk_index":   idx,
            "section":       section,
            "section_index": section_idx,
            "doc_name":      doc_name,
            "char_count":    len(text),
            "page_number":   page,
        }
    }


# ── TXT fallback ──────────────────────────────────────────────────────────────

def semantic_chunks_txt(text: str) -> list[dict]:
    MAX_CHARS = 600
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
                    current = sent if len(sent) <= MAX_CHARS else sent[:MAX_CHARS]
            if len(current) >= MIN_CHARS:
                chunks.append({"text": current.strip(), "level": "sentence"})

    return chunks


def build_hierarchy_txt(chunks: list[dict], doc_name: str) -> list[dict]:
    result = []
    for i, chunk in enumerate(chunks):
        text = chunk["text"]
        if is_junk_chunk(text):
            continue
        result.append({
            "text":  text,
            "level": chunk.get("level", "paragraph"),
            "metadata": {
                "domain":        "general",
                "chunk_index":   i,
                "section":       "General",
                "section_index": 0,
                "doc_name":      doc_name,
                "char_count":    len(text),
                "page_number":   1,
            }
        })
    return result


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
            doc        = fitz.open(tmp_path)
            spans      = extract_pdf_spans(doc)
            doc.close()
            structured = build_chunks_from_spans(spans, filename)
        else:
            raw_text   = clean_text(file_bytes.decode("utf-8", errors="ignore"))
            raw_chunks = semantic_chunks_txt(raw_text)
            structured = build_hierarchy_txt(raw_chunks, filename)

        if not structured:
            return {
                "message":       "No chunks after processing",
                "chunks_stored": 0,
                "doc_name":      filename
            }

        # Deduplicate
        seen_fps = set()
        unique   = []
        for c in structured:
            fp = fingerprint(c["text"])
            if fp not in seen_fps:
                seen_fps.add(fp)
                unique.append(c)

        print(f"[{filename}] {len(structured)} structured → {len(unique)} after dedup")
        print("Sample chunks:")
        for c in unique[:8]:
            print(
                f"  page={c['metadata']['page_number']} "
                f"section={c['metadata']['section'][:35]!r} "
                f"text={c['text'][:60]!r}"
            )

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