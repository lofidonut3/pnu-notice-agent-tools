"""Bounded live AI contract check with no user data, database writes, or email."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pnu_event_gate.ai import create_ai_client, resolve_ai_runtime


def probe(provider: str) -> dict:
    runtime = resolve_ai_runtime(provider=provider)
    result = {"provider": provider, "configured": bool(os.environ.get(runtime.api_key_env)),
              "chat_model": runtime.chat_model, "embedding_model": runtime.embedding_model,
              "chat_ok": False, "embedding_ok": False}
    if not result["configured"]:
        return result
    try:
        client = create_ai_client(runtime)
        client.max_attempts = 1
        client.timeout_seconds = 60
        reply = client.chat_json(messages=[{"role": "user", "content": 'Return only the JSON object {"status":"ok"}.'}],
                                 model=runtime.chat_model, max_tokens=128, temperature=0.0)
        if reply.get("status") != "ok":
            raise ValueError("probe JSON contract was not satisfied")
        result["chat_ok"] = True
        vectors = client.embeddings(["Public university notice"], model=runtime.embedding_model, input_type="passage")
        if len(vectors) != 1 or not vectors[0]:
            raise ValueError("probe embedding contract was not satisfied")
        result["embedding_ok"] = True
    except Exception as error:
        result["error_type"] = type(error).__name__
        match = re.search(r"HTTP (\d{3})", str(error))
        if match:
            result["http_status"] = int(match.group(1))
        result["quota_limit_zero"] = "limit: 0" in str(error) or '"limit": 0' in str(error)
    return result


def main() -> int:
    selected = os.environ.get("PROBE_PROVIDERS", "all")
    if selected not in {"all", "gemini", "nvidia"}:
        raise ValueError("unsupported provider selection")
    providers = ["gemini", "nvidia"] if selected == "all" else [selected]
    results = [probe(provider) for provider in providers]
    report = {"results": results, "ready_providers": [r["provider"] for r in results if r["chat_ok"] and r["embedding_ok"]]}
    serialized = json.dumps(report, indent=2)
    Path("runtime-probe.json").write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["ready_providers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
