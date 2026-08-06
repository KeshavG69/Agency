"""Application configuration from environment variables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MongoDB — the CRM / pipeline source of truth
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "collecct"

    

    # OpenRouter — used for Excel extraction
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    EXTRACTION_MODEL: str = "google/gemini-3.1-flash-lite"
     # The mail sweep reads signatures with this model (one call per sender per sweep). Gemma 4
    # 26B MoE (~4B active) is the pick: A/B'd against gemma-4-31b and nemotron-3-super-120b on
    # real signatures, all three gave identical titles/phones — so the small, cheap, highly
    # parallel MoE wins. Reading a 5-line signature needs no bigger or reasoning model.
    SIGNATURE_MODEL: str = "google/gemma-4-26b-a4b-it"

    # OpenAI — embeddings (optional, used by the cached LLM client)
    OPENAI_API_KEY: str = ""

    # Langfuse — LLM observability. When both keys are set, client/langfuse_client.py
    # instruments the openai SDK (which Agno agents and LangChain both use) so every model
    # call is traced. Empty keys => tracing is a silent no-op.
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "https://us.cloud.langfuse.com"

    # iDrive e2 — S3-compatible storage for generated documents
    IDRIVE_E2_ENDPOINT: str = ""
    IDRIVE_E2_ACCESS_KEY: str = ""
    IDRIVE_E2_SECRET_KEY: str = ""
    IDRIVE_E2_BUCKET: str = ""

    # Exa — web search tool for agents
    EXA_API_KEY: str = ""


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

    # Redis / Celery (background tasks).
    # Prefer REDIS_URL (a full redis://[:user]:pass@host:port URL — supports auth, as on
    # Railway). HOST/PORT are the local-dev fallback when REDIS_URL is empty.
    REDIS_URL: str = ""
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    @property
    def redis_base_url(self) -> str:
        """Base Redis URL with NO db suffix (callers append /0, /1, ...). Uses REDIS_URL
        (incl. auth) when set, else builds from HOST/PORT. Strips a trailing /<db> if given."""
        import re

        url = self.REDIS_URL or f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"
        return re.sub(r"/\d+$", "", url.rstrip("/"))

    # Composio — managed auth + tools (Outlook mail + calendar for the Relation Agent)
    COMPOSIO_API_KEY: str = ""
    COMPOSIO_OUTLOOK_AUTH_CONFIG_ID: str = "ac_PsLAI3OTqliY"
    # SharePoint uses TWO chained connections (one "Connect Library" click, back-to-back
    # Microsoft consent — see routers/composio.py SHAREPOINT_STAGES):
    #  1. Microsoft GRAPH (sharepoint_graph) — structure, per-item permissions, M365/Entra
    #     group expansion, and the write scopes (Sites.ReadWrite.All / Sites.FullControl.All /
    #     Files.ReadWrite.All) needed to provision Bid folders.
    #  2. SharePoint REST (share_point) — the one thing Graph can't do: list the members of a
    #     native SharePoint site group (Owners/Members/Visitors), for EXACT per-person ACLs.
    COMPOSIO_SHAREPOINT_AUTH_CONFIG_ID: str = "ac_0AMz8kC7i7ML"
    COMPOSIO_SHAREPOINT_REST_AUTH_CONFIG_ID: str = "ac_8-hZJL5A6HFi"
    # Mail triage: verifies POST /api/webhooks/composio (OUTLOOK_MESSAGE_TRIGGER events).
    # From the Composio dashboard: Project Settings -> Webhook, after pointing the webhook
    # URL there at this backend's /api/webhooks/composio. MUST be set for the webhook to
    # accept anything — an empty secret fails every signature check by design.
    COMPOSIO_WEBHOOK_SECRET: str = ""

    # SharePoint structure graph (separate FalkorDB graph from the contact network)
    SHAREPOINT_GRAPH_NAME: str = "sharepoint_structure"
    
    
    ANALYST_MODEL: str = "openai/gpt-5.4-mini"

    # Manual opportunity upload — the small/fast model that digests big solicitation
    # packages. If the whole package fits (<= STUFF_MAX) it's kept verbatim with no
    # model call; otherwise each document is summarized in ONE call (natural boundaries),
    # then the per-document digests are merged.
    DOC_DIGEST_MODEL: str = "openai/gpt-5.6-luna"
    # ~500k tokens @ ~4 chars/token. <= this total => keep verbatim, no LLM call.
    DOC_DIGEST_STUFF_MAX_CHARS: int = 2000000

    # Capture agent — text-to-image generation via OpenRouter's Image API (POST /images).
    IMAGE_GEN_MODEL: str = "openai/gpt-image-2"
    IMAGE_GEN_SIZE: str = "1024x1024"


    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
