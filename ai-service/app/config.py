from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    groq_api_key: str
    jina_api_key: str
    redis_url: str = "redis://red-d7tiup1j2pic73ac0cl0:6379"
    environment: str = "production"

    cognito_jwks_url: Optional[str] = None
    cognito_client_id: Optional[str] = None

    top_k_default: int = 5
    top_k_comparison: int = 8
    top_k_summary: int = 12
    context_max_words: int = 2500
    chunk_size: int = 512
    chunk_overlap: int = 64
    groq_model: str = "llama-3.1-8b-instant"
    groq_temperature: float = 0.1
    groq_max_tokens: int = 600

    max_ingest_pages: int = 500
    max_ingest_words: int = 200_000

    rate_limit_fail_open: bool = True
    cors_origins: list[str] = ["https://securestream1.netlify.app"]

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()