from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # ─── System Infrastructure & Secrets ──────────────────────────────────────
    supabase_url: str
    supabase_service_key: str
    groq_api_key: str
    jina_api_key: str
    redis_url: str = "redis://red-d7tiup1j2pic73ac0cl0:6379"
    environment: str = "dev"
    cognito_jwks_url: Optional[str] = None

    # ─── Shared RAG & Hyperparameters ─────────────────────────────────────────
    top_k_default: int = 5
    top_k_comparison: int = 8
    top_k_summary: int = 12
    context_max_words: int = 2500
    
    chunk_size: int = 512
    chunk_overlap: int = 64
    
    groq_model: str = "llama-3.1-8b-instant"
    groq_temperature: float = 0.1
    groq_max_tokens: int = 600

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()