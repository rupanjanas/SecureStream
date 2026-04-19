from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    ollama_base_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"
    llm_model: str = "llama3"
    cognito_jwks_url: str
    aws_region: str
    cognito_user_pool_id: str
    cognito_client_id: str


    class Config:
        env_file = ".env"

settings = Settings()