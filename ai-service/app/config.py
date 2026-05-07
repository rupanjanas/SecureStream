from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    ollama_base_url: str        = "https://securestream-2-2le8.onrender.com"
    embed_model: str            = "nomic-embed-text"
    llm_model: str              = "mistral:7b-instruct-q4_0"
    cognito_jwks_url: Optional[str] = None   # optional — None = dev mode
    aws_region: Optional[str]       = None
    cognito_user_pool_id: Optional[str] = None
    cognito_client_id: Optional[str]   = None
    redis_url: str              = "redis://red-d7tiup1j2pic73ac0cl0:6379"
    environment: str            = "dev"

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()