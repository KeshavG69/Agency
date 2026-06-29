"""Unified LLM client with thread-safe caching.

Mirrors the PriceIQ llm_client: a single place that builds and caches LLM
instances so agents/tools reuse them instead of re-instantiating per call.
Defaults point at OpenRouter (our agent model provider).

- get_chat_llm      -> LangChain ChatOpenAI  (used by tools, e.g. the python REPL)
- get_chat_llm_agno -> Agno OpenAIChat        (used by the agents)
- get_embeddings    -> OpenAI embeddings      (optional, for later RAG)
"""
import threading
from typing import Dict, Optional

from agno.models.openai import OpenAIChat
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.settings import settings

_DEFAULT_MODEL = "openai/gpt-5.4-mini"


class LLMClient:
    """Thread-safe, cached LLM/embeddings client."""

    def __init__(self):
        self._lock = threading.RLock()
        self._chat_cache: Dict[str, ChatOpenAI] = {}
        self._agno_cache: Dict[str, OpenAIChat] = {}
        self._emb_cache: Dict[str, OpenAIEmbeddings] = {}

    def get_chat_llm(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatOpenAI:
        model = model or _DEFAULT_MODEL
        api_key = api_key or settings.OPENROUTER_API_KEY
        base_url = base_url or settings.OPENROUTER_BASE_URL
        key = f"{model}:{base_url}:{max_tokens}"
        with self._lock:
            if key not in self._chat_cache:
                kwargs = {
                    "model": model,
                    "openai_api_key": api_key,
                    "openai_api_base": base_url,
                }
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                self._chat_cache[key] = ChatOpenAI(**kwargs)
            return self._chat_cache[key]

    def get_chat_llm_agno(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = 0.1,
    ) -> OpenAIChat:
        model = model or _DEFAULT_MODEL
        api_key = api_key or settings.OPENROUTER_API_KEY
        base_url = base_url or settings.OPENROUTER_BASE_URL
        max_tokens = max_tokens or 10000
        temperature = 0.1 if temperature is None else temperature
        key = f"{model}:{base_url}:{max_tokens}:{temperature}"
        with self._lock:
            if key not in self._agno_cache:
                self._agno_cache[key] = OpenAIChat(
                    id=model, api_key=api_key, base_url=base_url,
                    max_tokens=max_tokens, temperature=temperature,
                )
            return self._agno_cache[key]

    def get_embeddings(self) -> OpenAIEmbeddings:
        key = "text-embedding-3-small"
        with self._lock:
            if key not in self._emb_cache:
                self._emb_cache[key] = OpenAIEmbeddings(
                    openai_api_key=settings.OPENAI_API_KEY,
                    model=key,
                    dimensions=1536,
                    request_timeout=60,
                    max_retries=3,
                )
            return self._emb_cache[key]

    def clear_cache(self) -> None:
        with self._lock:
            self._chat_cache.clear()
            self._agno_cache.clear()
            self._emb_cache.clear()


_llm_client: Optional[LLMClient] = None
_client_lock = threading.RLock()


def get_llm_client() -> LLMClient:
    global _llm_client
    with _client_lock:
        if _llm_client is None:
            _llm_client = LLMClient()
        return _llm_client


def get_chat_llm(
    model: Optional[str] = None, api_key: Optional[str] = None,
    base_url: Optional[str] = None, max_tokens: Optional[int] = None,
) -> ChatOpenAI:
    return get_llm_client().get_chat_llm(model=model, api_key=api_key, base_url=base_url, max_tokens=max_tokens)


def get_chat_llm_agno(
    model: Optional[str] = None, api_key: Optional[str] = None,
    base_url: Optional[str] = None, max_tokens: Optional[int] = None,
    temperature: Optional[float] = 0.1,
) -> OpenAIChat:
    return get_llm_client().get_chat_llm_agno(
        model=model, api_key=api_key, base_url=base_url, max_tokens=max_tokens, temperature=temperature
    )


def get_embeddings() -> OpenAIEmbeddings:
    return get_llm_client().get_embeddings()
