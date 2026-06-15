"""Application configuration from environment variables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # EspoCRM (single source of truth)
    ESPOCRM_BASE_URL: str = "http://localhost:8080"
    ESPOCRM_API_KEY: str = ""

    # OpenRouter — used for Excel extraction
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    EXTRACTION_MODEL: str = "google/gemini-3.1-flash-lite"

    # Exa — web search tool for agents
    EXA_API_KEY: str = ""

    # LLM (Phase 2 agents)
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
