from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .ai import AIAPIError, extract_json_object, read_environment_value


DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_CHAT_MODEL = "gemini-3.5-flash"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"


class GeminiAPIError(AIAPIError):
    """Raised when the Gemini Developer API cannot return a valid response."""


@dataclass
class GeminiClient:
    api_key: str
    base_url: str = DEFAULT_GEMINI_BASE_URL
    timeout_seconds: int = 120
    max_attempts: int = 3
    provider: str = "gemini"

    @classmethod
    def from_env(
        cls,
        env_name: str = "GEMINI_API_KEY",
        *,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        timeout_seconds: int = 120,
    ) -> GeminiClient:
        api_key = read_environment_value(env_name)
        if not api_key:
            raise GeminiAPIError(f"{env_name} is not set")
        return cls(api_key=api_key, base_url=base_url, timeout_seconds=timeout_seconds)

    def chat_json(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str = DEFAULT_CHAT_MODEL,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        system_instruction, contents = _convert_messages(messages)
        model_id = _model_id(model)
        generation_config: dict[str, Any] = {
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        }
        if model_id.startswith("gemini-3"):
            # Gemini 3 counts hidden thinking against maxOutputTokens. These jobs are
            # constrained extraction/classification tasks, so reserve a small buffer
            # and request minimal reasoning to avoid truncating the JSON response.
            generation_config["maxOutputTokens"] = max_tokens + 1024
            generation_config["thinkingConfig"] = {"thinkingLevel": "minimal"}
        else:
            generation_config["temperature"] = temperature
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }
        response = self._post_json(
            f"/models/{model_id}:generateContent",
            payload,
        )
        try:
            candidate = response["candidates"][0]
            if candidate.get("finishReason") == "MAX_TOKENS":
                raise GeminiAPIError("Gemini JSON response was truncated at the token limit")
            parts = candidate["content"]["parts"]
            text = "\n".join(
                str(part["text"])
                for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ).strip()
        except GeminiAPIError:
            raise
        except (KeyError, IndexError, TypeError) as error:
            raise GeminiAPIError(_missing_candidate_message(response)) from error
        if not text:
            raise GeminiAPIError(_missing_candidate_message(response))
        try:
            parsed = extract_json_object(text)
        except AIAPIError as error:
            raise GeminiAPIError(str(error)) from error
        if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            raise GeminiAPIError("Gemini response JSON is not an object")
        return parsed

    def embeddings(
        self,
        texts: list[str],
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        input_type: str = "passage",
    ) -> list[list[float]]:
        if not texts:
            return []
        model_name = f"models/{_model_id(model)}"
        task_type = {
            "query": "RETRIEVAL_QUERY",
            "passage": "RETRIEVAL_DOCUMENT",
        }.get(input_type, "SEMANTIC_SIMILARITY")
        payload = {
            "requests": [
                {
                    "model": model_name,
                    "taskType": task_type,
                    "content": {"parts": [{"text": text}]},
                }
                for text in texts
            ]
        }
        response = self._post_json(
            f"/{model_name}:batchEmbedContents",
            payload,
        )
        embeddings = response.get("embeddings")
        if not isinstance(embeddings, list):
            raise GeminiAPIError("Gemini embedding response has no embeddings array")
        vectors: list[list[float]] = []
        for embedding in embeddings:
            values = embedding.get("values") if isinstance(embedding, dict) else None
            if not isinstance(values, list) or not values:
                raise GeminiAPIError("Gemini embedding response contains an invalid vector")
            vectors.append([float(value) for value in values])
        if len(vectors) != len(texts):
            raise GeminiAPIError(
                f"embedding count mismatch: expected {len(texts)}, got {len(vectors)}"
            )
        return vectors

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "x-goog-api-key": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "pnu-notice-agent-tools/0.1",
            },
        )
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise GeminiAPIError("Gemini API returned non-object JSON")
                return parsed
            except urllib.error.HTTPError as error:
                error_body = error.read(2000).decode("utf-8", errors="replace")
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt == self.max_attempts:
                    raise GeminiAPIError(
                        f"Gemini API HTTP {error.code}: {error_body}"
                    ) from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == self.max_attempts:
                    raise GeminiAPIError(f"Gemini API request failed: {error}") from error
            time.sleep(min(2 ** (attempt - 1), 4))
        raise GeminiAPIError("Gemini API request failed")


def _convert_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role == "system":
            system_parts.extend(_text_parts(content))
            continue
        parts = _content_parts(content)
        if parts:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    if not contents:
        raise GeminiAPIError("Gemini request has no user or model content")
    return "\n\n".join(system_parts), contents


def _text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return [str(content)] if content is not None else []
    return [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
    ]


def _content_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        return [{"text": str(content)}] if content is not None else []
    parts: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and item.get("text") is not None:
            parts.append({"text": str(item["text"])})
        elif item.get("type") == "image_url":
            image_url = item.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            parts.append({"inline_data": _parse_data_uri(str(url or ""))})
    return parts


def _parse_data_uri(value: str) -> dict[str, str]:
    if not value.startswith("data:") or ";base64," not in value:
        raise GeminiAPIError("Gemini image input must be a base64 data URI")
    header, data = value.split(",", 1)
    mime_type = header[5:].split(";", 1)[0]
    if not mime_type or not data:
        raise GeminiAPIError("Gemini image data URI is incomplete")
    return {"mime_type": mime_type, "data": data}


def _model_id(model: str) -> str:
    return model.removeprefix("models/").strip()


def _missing_candidate_message(response: dict[str, Any]) -> str:
    block_reason = (response.get("promptFeedback") or {}).get("blockReason")
    return (
        f"Gemini response has no text candidate (block_reason={block_reason})"
        if block_reason
        else "Gemini response has no text candidate"
    )
