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
    
    
    # ---- Agent models — one per agent, so each can be tuned independently ----
    ANALYST_MODEL: str = "openai/gpt-5.4-mini"   # bid / no-bid analyst
    CRM_MODEL: str = "openai/gpt-5.6-terra"      # relation / contact-finding agent
    RESEARCH_MODEL: str = "openai/gpt-5.4-mini"  # company research (contact companies)
    # The org's OWN company profile, researched once when an admin saves their UEI. Small
    # model on purpose: it runs a handful of times per org, not per contact. Swap via env
    # if the output quality is not good enough — the result is admin-editable either way.
    ORG_PROFILE_MODEL: str = "openai/gpt-5.6-luna"
    CAPTURE_MODEL: str = "openai/gpt-5.6-terra"  # capture strategy + deliverables
    MAIL_MODEL: str = "openai/gpt-5.6-terra"     # outreach drafting
    BRIEF_MODEL: str = "openai/gpt-5.6-terra"    # call brief (org-level meeting prep)

    # Manual opportunity upload — the small/fast model that digests big solicitation
    # packages. If the whole package fits (<= STUFF_MAX) it's kept verbatim with no
    # model call; otherwise each document is summarized in ONE call (natural boundaries),
    # then the per-document digests are merged.
    DOC_DIGEST_MODEL: str = "openai/gpt-5.6-luna"
    # ~50k tokens @ ~4 chars/token. <= this total => keep verbatim, no LLM call.
    # Sized to FIT A CONTEXT WINDOW, not to the model's advertised maximum: the self-hosted
    # Gemma runs with n_ctx=65536 (even though the weights train to 262k), so the old 2,000,000
    # chars (~500k tokens) would have been stuffed verbatim into a prompt eight times too big
    # for the server — which fails at request time, after the whole document was assembled.
    DOC_DIGEST_STUFF_MAX_CHARS: int = 200000

    # Capture agent — text-to-image generation via OpenRouter's Image API (POST /images).
    # NOTE: image generation always stays on OpenRouter — a self-hosted text model (Gemma)
    # cannot serve it — so this one deliberately does NOT follow LLM_BASE_URL below.
    IMAGE_GEN_MODEL: str = "openai/gpt-image-2"
    IMAGE_GEN_SIZE: str = "1024x1024"

    # ---- Text-model provider (optional self-hosted override) -------------------------
    # Every TEXT/chat call — agents, signature + personal extraction, Excel ingest, the
    # REPL tool — resolves its endpoint through the three properties below. They default
    # to OpenRouter, so leaving these unset changes nothing.
    #
    # To run the whole system against a self-hosted OpenAI-compatible server (e.g. Gemma
    # behind vLLM / llama.cpp / Ollama on the lab box):
    #
    #   LLM_BASE_URL=http://localhost:8020/v1     # the SSM port-forward, from a laptop
    #   LLM_API_KEY=local                         # most local servers ignore it
    #   LLM_MODEL=google/gemma-3-27b-it           # whatever GET /v1/models reports
    #
    # IMPORTANT — "localhost" is relative to whatever runs THIS process. It is correct when
    # the backend runs on the same machine as the port-forward. If the backend runs on the
    # NJ server (Orionhub) or in a container, localhost is that box's own loopback and will
    # not reach the tunnel: use the address that box can actually reach the model on (e.g.
    # its Tailscale IP, http://100.x.y.z:8020/v1, or the container-host address).
    #
    # `scripts/check_llm.py` verifies whatever is configured before you run the agents.
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    # When set, this model id REPLACES every per-agent model below. A single self-hosted
    # server typically serves one model, and without this you would have to override
    # ANALYST_MODEL, CRM_MODEL, MAIL_MODEL, SIGNATURE_MODEL … individually and keep them
    # in sync. Leave empty to keep the per-agent models above.
    LLM_MODEL: str = ""

    # Reasoning ("thinking") models emit a chain of thought BEFORE the answer, and it is
    # charged against the same token budget. Measured on the lab box's gemma-4-31B, one
    # one-sentence answer cost 272 completion tokens with thinking vs 24 without — 11x the
    # tokens and 3.8x the wall-clock, for the same answer. Worse, when the budget runs out
    # mid-thought the reply comes back HTTP 200 with an EMPTY content field, which reads
    # downstream as "the model returned nothing" rather than as an error.
    #
    # Off by default: almost every call this system makes is extraction or short drafting,
    # where thinking buys nothing. Set LLM_ENABLE_THINKING=true for judgement-heavy work.
    #
    # Only `chat_template_kwargs.enable_thinking` actually works on llama.cpp — the
    # `reasoning_budget` and top-level `thinking` parameters are accepted and SILENTLY
    # IGNORED (both verified against the live server).
    LLM_ENABLE_THINKING: bool = False

    @property
    def llm_extra_body(self) -> dict:
        """Non-standard JSON body fields for the chat API.

        Returns {} for a hosted provider: `chat_template_kwargs` is a llama.cpp extension,
        and sending it to OpenRouter is at best ignored and at worst a 400. It is applied
        ONLY when we are pointed at a self-hosted server.
        """
        if not self.llm_is_self_hosted:
            return {}
        return {"chat_template_kwargs": {"enable_thinking": bool(self.LLM_ENABLE_THINKING)}}

    @property
    def llm_base_url(self) -> str:
        """The endpoint every text/chat call uses."""
        return (self.LLM_BASE_URL or self.OPENROUTER_BASE_URL).rstrip("/")

    @property
    def llm_api_key(self) -> str:
        """The key for that endpoint. Self-hosted servers usually ignore auth, but the
        OpenAI SDK refuses an empty key outright — so a placeholder stands in."""
        if self.LLM_BASE_URL:
            return self.LLM_API_KEY or "local"
        return self.OPENROUTER_API_KEY

    @property
    def llm_ready(self) -> bool:
        """Is a text model reachable at all? Self-hosting needs no API key, so the old
        `if not OPENROUTER_API_KEY: give up` guard would wrongly disable everything."""
        return bool(self.LLM_BASE_URL or self.OPENROUTER_API_KEY)

    def llm_model(self, configured: str) -> str:
        """`configured` unless a single self-hosted model overrides everything."""
        return self.LLM_MODEL or configured

    @property
    def llm_is_self_hosted(self) -> bool:
        return bool(self.LLM_BASE_URL)


    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
