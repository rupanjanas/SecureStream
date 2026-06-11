from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Required ───────────────────────────────────────────────────────────────
    supabase_url:         str
    supabase_service_key: str
    groq_api_key:         str
    jina_api_key:         str

    # ── Optional / defaulted ───────────────────────────────────────────────────
    # FIX: no hardcoded Render hostname — must be set in env
    redis_url:     str = "redis://localhost:6379"
    environment:   str = "production"

    cognito_jwks_url:  Optional[str] = None
    cognito_client_id: Optional[str] = None

    # Retrieval
    top_k_default:    int = 5
    top_k_comparison: int = 8
    top_k_summary:    int = 12
    context_max_words: int = 2500

    # Chunking (character counts — 2000 chars ≈ 350 words)
    chunk_size:    int = 2000
    chunk_overlap: int = 300

    # Groq
    groq_model:       str   = "llama-3.1-8b-instant"
    groq_temperature: float = 0.1
    groq_max_tokens:  int   = 600

    # Ingest guards
    max_ingest_pages: int = 500
    max_ingest_words: int = 200_000

    # Ops
    rate_limit_fail_open: bool      = True
    cors_origins:         list[str] = ["https://securestream1.netlify.app"]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
print("Redis URL:", settings.redis_url)