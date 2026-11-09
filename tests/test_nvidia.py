from __future__ import annotations

import pytest

from pnu_event_gate.nvidia import NvidiaAPIError, NvidiaClient, extract_json_object


def test_extract_json_object_accepts_fenced_and_prefixed_json() -> None:
    assert extract_json_object('```json\n{"ok": true}\n```') == {"ok": True}
    assert extract_json_object('result: {"count": 2}') == {"count": 2}


def test_extract_json_object_rejects_plain_text() -> None:
    with pytest.raises(NvidiaAPIError):
        extract_json_object("not json")


def test_client_orders_embedding_vectors_by_index(monkeypatch) -> None:
    client = NvidiaClient(api_key="test")
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda _path, _payload: {
            "data": [
                {"index": 1, "embedding": [0, 1]},
                {"index": 0, "embedding": [1, 0]},
            ]
        },
    )

    assert client.embeddings(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_chat_unwraps_only_a_single_complete_object(monkeypatch):
    client = NvidiaClient(api_key="test")
    monkeypatch.setattr(client, "_post_json", lambda *args: {
        "choices": [{"finish_reason": "stop", "message": {"content": '[{"classification":"not_matched"}]'}}]
    })
    assert client.chat_json(messages=[]) == {"classification": "not_matched"}


def test_chat_rejects_multiple_alternative_objects(monkeypatch):
    client = NvidiaClient(api_key="test")
    monkeypatch.setattr(client, "_post_json", lambda *args: {
        "choices": [{"finish_reason": "stop", "message": {"content": '[{"ok":true},{"ok":false}]'}}]
    })
    with pytest.raises(NvidiaAPIError, match="type=list, length=2"):
        client.chat_json(messages=[])


def test_chat_rejects_truncated_nested_json_before_extracting_inner_objects(monkeypatch):
    client = NvidiaClient(api_key="test")
    monkeypatch.setattr(client, "_post_json", lambda *args: {
        "choices": [{"finish_reason": "length", "message": {"content": '{"facts":[{"text":"partial"}]'}}]
    })
    with pytest.raises(NvidiaAPIError, match="truncated"):
        client.chat_json(messages=[])
