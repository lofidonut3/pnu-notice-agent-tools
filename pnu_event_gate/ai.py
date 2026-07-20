from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol


DEFAULT_AI_PROVIDER = "gemini"
SUPPORTED_AI_PROVIDERS = ("gemini", "nvidia")


class AIAPIError(RuntimeError):
    """Raised when a hosted AI endpoint cannot return a valid response."""


class AIClient(Protocol):
    provider: str

    def chat_json(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]: ...

    def embeddings(
        self,
        texts: list[str],
        *,
        model: str,
        input_type: str,
    ) -> list[list[float]]: ...


@dataclass(frozen=True)
class AIRuntime:
    provider: str
    api_key_env: str
    chat_model: str
    embedding_model: str


def resolve_ai_runtime(
    *,
    provider: str | None = None,
    api_key_env: str | None = None,
    chat_model: str | None = None,
    embedding_model: str | None = None,
) -> AIRuntime:
    normalized = (provider or os.environ.get("PNU_AI_PROVIDER") or DEFAULT_AI_PROVIDER)
    normalized = normalized.strip().casefold()
    if normalized not in SUPPORTED_AI_PROVIDERS:
        supported = ", ".join(SUPPORTED_AI_PROVIDERS)
        raise ValueError(f"unsupported AI provider {normalized!r}; choose one of: {supported}")

    if normalized == "gemini":
        from .gemini import DEFAULT_CHAT_MODEL, DEFAULT_EMBEDDING_MODEL

        prefix = "GEMINI"
    else:
        from .nvidia import DEFAULT_CHAT_MODEL, DEFAULT_EMBEDDING_MODEL

        prefix = "NVIDIA"

    return AIRuntime(
        provider=normalized,
        api_key_env=(api_key_env or f"{prefix}_API_KEY").strip(),
        chat_model=(
            chat_model
            or os.environ.get("PNU_CHAT_MODEL")
            or os.environ.get(f"{prefix}_CHAT_MODEL")
            or DEFAULT_CHAT_MODEL
        ).strip(),
        embedding_model=(
            embedding_model
            or os.environ.get("PNU_EMBEDDING_MODEL")
            or os.environ.get(f"{prefix}_EMBEDDING_MODEL")
            or DEFAULT_EMBEDDING_MODEL
        ).strip(),
    )


def create_ai_client(runtime: AIRuntime) -> AIClient:
    if runtime.provider == "gemini":
        from .gemini import GeminiClient

        return GeminiClient.from_env(runtime.api_key_env)
    from .nvidia import NvidiaClient

    return NvidiaClient.from_env(runtime.api_key_env)


def extract_json_object(value: str) -> Any:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
            return parsed
        except json.JSONDecodeError:
            continue
    raise AIAPIError("model response does not contain valid JSON")


def read_environment_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            registry_value, _kind = winreg.QueryValueEx(key, name)
        return str(registry_value).strip()
    except (FileNotFoundError, OSError):
        return ""
