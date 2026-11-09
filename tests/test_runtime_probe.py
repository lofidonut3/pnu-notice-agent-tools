import json
from io import BytesIO
from PIL import Image
from scripts import probe_ai_runtime as runtime_probe


def test_probe_makes_one_attempt_and_checks_both_contracts(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    calls = []
    class Client:
        def chat_json(self, **kwargs):
            assert self.max_attempts == 1
            calls.append("chat")
            return {"status": "ok"}
        def embeddings(self, texts, **kwargs):
            calls.append("embedding")
            return [[0.1, 0.2]]
    monkeypatch.setattr(runtime_probe, "create_ai_client", lambda _: Client())
    result = runtime_probe.probe("gemini")
    assert result["chat_ok"] and result["embedding_ok"]
    assert calls == ["chat", "embedding"]


def test_probe_reports_rejection_without_exposing_provider_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    class Client:
        def chat_json(self, **kwargs):
            raise RuntimeError("HTTP 429: private response, limit: 0")
    monkeypatch.setattr(runtime_probe, "create_ai_client", lambda _: Client())
    result = runtime_probe.probe("gemini")
    assert result["http_status"] == 429 and result["quota_limit_zero"]
    assert not result["chat_ok"]
    assert "private" not in json.dumps(result)


def test_probe_skips_unconfigured_provider(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(runtime_probe, "create_ai_client", lambda _: (_ for _ in ()).throw(AssertionError("unused")))
    assert not runtime_probe.probe("nvidia")["configured"]


def test_probe_vision_fixture_is_a_valid_red_png():
    image = Image.open(BytesIO(runtime_probe.red_square_png()))
    assert image.size == (64, 64)
    assert image.getpixel((20, 20)) == (255, 0, 0)


def test_vision_probe_requires_correct_image_answer(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-only")
    class Client:
        def chat_json(self, **kwargs):
            assert kwargs["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
            return {"color": "red"}
        def embeddings(self, texts, **kwargs):
            return [[0.1]]
    monkeypatch.setattr(runtime_probe, "create_ai_client", lambda _: Client())
    result = runtime_probe.probe("nvidia", vision=True)
    assert result["vision_ok"] and result["chat_ok"] and result["embedding_ok"]
