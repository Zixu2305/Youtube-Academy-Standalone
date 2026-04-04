"""
llm_client.py
-------------
Provider-agnostic chat completion helper for Groq/OpenAI-compatible APIs.

Environment variables (in priority order):
- LLM_PROVIDER: groq | openai (default: groq)
- LLM_MODEL: provider model override
- LLM_API_KEY: provider api key override

Provider-specific fallbacks:
- groq: GROQ_MODEL, GROQ_API_KEY
- openai: OPENAI_MODEL, OPENAI_API_KEY
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

_SUPPORTED_PROVIDERS = {"groq", "openai"}
_DEFAULT_PROVIDER = "groq"
_DEFAULT_MODELS = {
    "groq": "llama-3.1-8b-instant",
    "openai": "gpt-4o-mini",
}
_PROVIDER_URLS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}


def _provider() -> str:
    provider = os.getenv("LLM_PROVIDER", _DEFAULT_PROVIDER).strip().lower()
    if provider not in _SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(_SUPPORTED_PROVIDERS))
        raise RuntimeError(f"Unsupported LLM_PROVIDER '{provider}'. Supported: {supported}")
    return provider


def _api_key(provider: str) -> str:
    shared_key = os.getenv("LLM_API_KEY", "").strip()
    if shared_key:
        return shared_key
    if provider == "groq":
        return os.getenv("GROQ_API_KEY", "").strip()
    return os.getenv("OPENAI_API_KEY", "").strip()


def _model(provider: str) -> str:
    shared_model = os.getenv("LLM_MODEL", "").strip()
    if shared_model:
        return shared_model
    if provider == "groq":
        return os.getenv("GROQ_MODEL", _DEFAULT_MODELS["groq"]).strip()
    return os.getenv("OPENAI_MODEL", _DEFAULT_MODELS["openai"]).strip()


def _retry_after_seconds(resp: requests.Response, attempt: int, base_seconds: int) -> int:
    retry_after = resp.headers.get("Retry-After", "").strip()
    if retry_after.isdigit():
        return max(1, int(retry_after))
    return max(1, base_seconds * (2 ** attempt))


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def call_llm_chat(
    prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    timeout: int,
    expect_json: bool = False,
    rate_limit_retries: int = 2,
    retry_backoff_seconds: int = 1,
) -> str:
    """
    Calls the configured provider using an OpenAI-compatible chat completion API.
    Returns raw text content from the first choice.
    """
    provider = _provider()
    api_key = _api_key(provider)
    if not api_key:
        raise RuntimeError(f"API key is missing for provider '{provider}'")

    url = _PROVIDER_URLS[provider]
    payload: dict[str, Any] = {
        "model": _model(provider),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if expect_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_exc: Exception | None = None
    for attempt in range(rate_limit_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            continue

        if resp.status_code == 429:
            if attempt < rate_limit_retries:
                time.sleep(_retry_after_seconds(resp, attempt, retry_backoff_seconds))
                continue
            raise RuntimeError(f"{provider} API rate limit reached. Please retry shortly.")

        if resp.status_code >= 400:
            try:
                err_payload = resp.json()
                err_msg = err_payload.get("error", {}).get("message", "")
            except ValueError:
                err_msg = ""
            suffix = f": {err_msg}" if err_msg else ""
            raise RuntimeError(f"{provider} request failed ({resp.status_code}){suffix}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"{provider} response was not valid JSON") from exc

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return _extract_text_content(content).strip()

    raise RuntimeError(f"{provider} request failed: {last_exc}")
