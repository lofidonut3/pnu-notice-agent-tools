from __future__ import annotations

from typing import Any

from pnu_event_gate.ai import resolve_ai_runtime
import pytest

from pnu_event_gate.gemini import GeminiAPIError, GeminiClient


def test_chat_json_converts_system_text_and_inline_image(monkeypatch) -> None:
    client = GeminiClient(api_key="test")
    captured: dict[str, Any] = {}

    def fake_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": '{"pages":[{"id":"E001","text":"ok"}]}'}]
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_post_json", fake_post)
    result = client.chat_json(
        messages=[
            {"role": "system", "content": "Return JSON."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe E001."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,dGVzdA=="},
                    },
                ],
            },
        ],
        model="gemini-3.5-test",
        max_tokens=100,
        temperature=0.0,
    )

    assert result["pages"][0]["id"] == "E001"
    assert captured["path"] == "/models/gemini-3.5-test:generateContent"
    assert captured["payload"]["systemInstruction"]["parts"][0]["text"] == "Return JSON."
    inline = captured["payload"]["contents"][0]["parts"][1]["inline_data"]
    assert inline == {"mime_type": "image/png", "data": "dGVzdA=="}
    assert captured["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["payload"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "minimal"
    }
    assert "temperature" not in captured["payload"]["generationConfig"]


def test_embeddings_use_batch_retrieval_task_type(monkeypatch) -> None:
    client = GeminiClient(api_key="test")
    captured: dict[str, Any] = {}

    def fake_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {"embeddings": [{"values": [1, 0]}, {"values": [0, 1]}]}

    monkeypatch.setattr(client, "_post_json", fake_post)
    assert client.embeddings(
        ["first", "second"],
        model="gemini-embedding-001",
        input_type="passage",
    ) == [[1.0, 0.0], [0.0, 1.0]]
    assert captured["path"] == "/models/gemini-embedding-001:batchEmbedContents"
    assert all(
        request["taskType"] == "RETRIEVAL_DOCUMENT"
        for request in captured["payload"]["requests"]
    )


def test_chat_json_unwraps_one_object_array(monkeypatch) -> None:
    client = GeminiClient(api_key="test")
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda _path, _payload: {
            "candidates": [{"content": {"parts": [{"text": '[{"ok":true}]'}]}}]
        },
    )

    assert client.chat_json(
        messages=[{"role": "user", "content": "Return one object."}],
        model="gemini-test",
        max_tokens=100,
        temperature=0.0,
    ) == {"ok": True}


def test_chat_json_rejects_truncated_nested_json(monkeypatch) -> None:
    client = GeminiClient(api_key="test")
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda _path, _payload: {
            "candidates": [
                {
                    "finishReason": "MAX_TOKENS",
                    "content": {"parts": [{"text": '{"items":[{"ok":true}]'}]},
                }
            ]
        },
    )

    with pytest.raises(GeminiAPIError, match="truncated"):
        client.chat_json(
            messages=[{"role": "user", "content": "Return one object."}],
            model="gemini-3.5-test",
            max_tokens=100,
            temperature=0.0,
        )


def test_runtime_defaults_to_gemini_and_keeps_nvidia_available() -> None:
    gemini = resolve_ai_runtime(provider=None)
    assert gemini.provider == "gemini"
    assert gemini.api_key_env == "GEMINI_API_KEY"
    assert gemini.chat_model == "gemini-3.5-flash"

    nvidia = resolve_ai_runtime(provider="nvidia")
    assert nvidia.provider == "nvidia"
    assert nvidia.api_key_env == "NVIDIA_API_KEY"
