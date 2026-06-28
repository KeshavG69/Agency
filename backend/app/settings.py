"""Application configuration from environment variables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MongoDB — the CRM / pipeline source of truth
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "collecct"

    # EspoCRM (legacy — superseded by MongoDB; kept for reference only)
    ESPOCRM_BASE_URL: str = "http://localhost:8080"
    ESPOCRM_API_KEY: str = ""

    # OpenRouter — used for Excel extraction
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    EXTRACTION_MODEL: str = "google/gemini-3.1-flash-lite"

    # OpenAI — embeddings (optional, used by the cached LLM client)
    OPENAI_API_KEY: str = ""

    # iDrive e2 — S3-compatible storage for generated documents
    IDRIVE_E2_ENDPOINT: str = ""
    IDRIVE_E2_ACCESS_KEY: str = ""
    IDRIVE_E2_SECRET_KEY: str = ""
    IDRIVE_E2_BUCKET: str = ""

    # Exa — web search tool for agents
    EXA_API_KEY: str = ""

    # Explorium — resolve a contact's name / title / company from their email
    EXPLORIUM_API_KEY: str = ""
    EXPLORIUM_BASE_URL: str = "https://api.explorium.ai/v1"

    # SAM.gov Entity API — fetch a company's registration details from its UEI
    # (free key from api.data.gov / sam.gov). Used by the Organisation settings.
    SAM_GOV_API_KEY: str = ""
    SAM_GOV_BASE_URL: str = "https://api.sam.gov"

    # FalkorDB — the CRM knowledge graph (people / companies / relationships)
    GRAPH_DATABASE_URL: str = "localhost"  # host
    GRAPH_DATABASE_PORT: int = 6379
    GRAPH_DATABASE_USERNAME: str = ""
    GRAPH_DATABASE_PASSWORD: str = ""
    GRAPH_DATABASE_SSL: bool = False
    GRAPH_DATABASE_NAME: str = "collecct_network"

    # Redis / Celery (background tasks)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # Composio — managed auth + tools (Outlook mail + calendar for the Relation Agent)
    COMPOSIO_API_KEY: str = ""
    COMPOSIO_OUTLOOK_AUTH_CONFIG_ID: str = ""
    COMPOSIO_SHAREPOINT_AUTH_CONFIG_ID: str = "ac_yZpaj2ORI7Fx"

    # SharePoint structure graph (separate FalkorDB graph from the contact network)
    SHAREPOINT_GRAPH_NAME: str = "sharepoint_structure"

    # LLM (Phase 2 agents) — Analyst Agent runs Claude via OpenRouter (mirrors PriceIQ)
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    ANALYST_MODEL: str = "google/gemini-3.5-flash"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
