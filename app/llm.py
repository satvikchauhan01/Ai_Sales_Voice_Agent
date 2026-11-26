"""Thin wrapper around the Gemini API (google-genai SDK).

- create_chat(): multi-turn chat with *automatic function calling* -> the SDK runs our
  Python tool functions when Gemini asks for them, then sends results back to Gemini.
- generate_json(): one-shot structured output (used for post-call analysis).
- embed(): text embeddings for RAG.
"""
from google import genai
from google.genai import errors, types

from app.config import (
    ANALYSIS_MODEL,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    GEMINI_API_KEY,
    LLM_MODEL,
    LLM_THINKING_LEVEL,
    LLM_TIMEOUT_SECONDS,
)

_client = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing in .env")
        # Retry every request (embeddings, analysis too) on 429/5xx and on connection errors,
        # e.g. a network firewall breaking the HTTPS connection. The SDK's default is no retry.
        retry = types.HttpRetryOptions(attempts=3, initial_delay=0.5, max_delay=2)
        _client = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(retry_options=retry))
    return _client


def _thinking_config() -> types.ThinkingConfig | None:
    if not LLM_THINKING_LEVEL:
        return None
    return types.ThinkingConfig(thinking_level=LLM_THINKING_LEVEL.upper())


def _fail_fast() -> types.HttpOptions:
    """Live-call requests: give up on a slow Gemini request after LLM_TIMEOUT_SECONDS and retry once.

    Without this the SDK waits forever and never retries, so one overloaded request
    (free tier, 503/504) stalls the whole call for 20-40 s. If both tries fail,
    agent.py switches the call to LLM_FALLBACK_MODEL.
    """
    return types.HttpOptions(
        timeout=LLM_TIMEOUT_SECONDS * 1000,  # milliseconds
        retry_options=types.HttpRetryOptions(attempts=2, initial_delay=0.5, max_delay=2),
    )


def create_chat(system_prompt: str, tools: list, model: str = LLM_MODEL, history: list | None = None):
    """New chat. Pass `history` (from chat.get_history()) to continue a conversation on another model."""
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=tools,
        temperature=0.4,
        thinking_config=_thinking_config(),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=6),
        http_options=_fail_fast(),
    )
    return get_client().chats.create(model=model, config=config, history=history or [])


def is_temporary_error(exc: Exception) -> bool:
    """True for errors worth retrying on another model: overload, rate limit, timeout, connection."""
    if isinstance(exc, errors.APIError):
        return exc.code == 429 or exc.code >= 500
    # httpx / httpx2 ConnectError (incl. SSL failures) and timeouts after the SDK's own retries
    name = type(exc).__name__
    return "ConnectError" in name or "Timeout" in name


def generate_json(prompt: str, schema):
    """Ask Gemini for JSON that matches a Pydantic model; returns a model instance."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
    )
    response = get_client().models.generate_content(
        model=ANALYSIS_MODEL, contents=prompt, config=config
    )
    if response.parsed is not None:
        return response.parsed
    return schema.model_validate_json(response.text)


def generate_text(prompt: str) -> str:
    response = get_client().models.generate_content(model=ANALYSIS_MODEL, contents=prompt)
    return response.text or ""


def embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    batch_size = 20
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        response = get_client().models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
        )
        for item in response.embeddings:
            vectors.append(list(item.values))
    return vectors
