from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .ai import AIAPIError, extract_json_object as _extract_json_object, read_environment_value


DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_CHAT_MODEL = "minimaxai/minimax-m3"
DEFAULT_EMBEDDING_MODEL = "nvidia/nemotron-3-embed-1b"


class NvidiaAPIError(AIAPIError):
    """Raised when a NVIDIA hosted endpoint cannot return a valid response."""


@dataclass
class NvidiaClient:
    api_key: str
    base_url: str = DEFAULT_NVIDIA_BASE_URL
    timeout_seconds: int = 120
    max_attempts: int = 3
    provider: str = "nvidia"

    @classmethod
    def from_env(
        cls,
        env_name: str = "NVIDIA_API_KEY",
        *,
        base_url: str = DEFAULT_NVIDIA_BASE_URL,
        timeout_seconds: int = 120,
    ) -> NvidiaClient:
        api_key = read_environment_value(env_name)
        if not api_key:
            raise NvidiaAPIError(f"{env_name} is not set")
        return cls(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    def chat_json(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str = DEFAULT_CHAT_MODEL,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": 1,
            "max_tokens": max_tokens,
            "stream": False,
        }
        response = self._post_json("/chat/completions", payload)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise NvidiaAPIError("chat response has no message content") from error
        if not isinstance(content, str):
            raise NvidiaAPIError("chat response content is not text")
        parsed = extract_json_object(content)
        if not isinstance(parsed, dict):
            raise NvidiaAPIError("chat response JSON is not an object")
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
        payload = {
            "model": model,
            "input": texts,
            "input_type": input_type,
            "encoding_format": "float",
            "truncate": "END",
        }
        response = self._post_json("/embeddings", payload)
        data = response.get("data")
        if not isinstance(data, list):
            raise NvidiaAPIError("embedding response has no data array")
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise NvidiaAPIError("embedding response contains an invalid vector")
            vectors.append([float(value) for value in vector])
        if len(vectors) != len(texts):
            raise NvidiaAPIError(
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
                "Authorization": f"Bearer {self.api_key}",
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
                    raise NvidiaAPIError("NVIDIA API returned non-object JSON")
                return parsed
            except urllib.error.HTTPError as error:
                error_body = error.read(2000).decode("utf-8", errors="replace")
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt == self.max_attempts:
                    raise NvidiaAPIError(
                        f"NVIDIA API HTTP {error.code}: {error_body}"
                    ) from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == self.max_attempts:
                    raise NvidiaAPIError(f"NVIDIA API request failed: {error}") from error
            time.sleep(min(2 ** (attempt - 1), 4))
        raise NvidiaAPIError("NVIDIA API request failed")


def extract_json_object(value: str) -> Any:
    try:
        return _extract_json_object(value)
    except AIAPIError as error:
        raise NvidiaAPIError(str(error)) from error
