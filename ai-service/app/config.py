from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    groq_api_key: str
    jina_api_key: str
    redis_url: str          = "redis://localhost:6379"
    environment: str        = "dev"
    cognito_jwks_url: Optional[str] = None

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()