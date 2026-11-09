"""Persist one real health observation per day, or when incident state changes."""
from __future__ import annotations

from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pnu_event_gate.runtime_reporting import health_summary


def record(source: Path, target: Path) -> bool:
    incoming = health_summary(json.loads(source.read_text(encoding="utf-8")))
    # Reject missing monitor output instead of making an empty success record.
    if not incoming["checked_at"] or incoming["status"] not in {"healthy", "degraded", "unhealthy"}:
        raise ValueError("runtime health observation is incomplete")
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        if (str(previous.get("checked_at"))[:10] == str(incoming["checked_at"])[:10]
                and previous.get("status") == incoming["status"]
                and previous.get("issues") == incoming["issues"]):
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(incoming, indent=2) + "\n", encoding="utf-8")
    return True


if __name__ == "__main__":
    record(Path(sys.argv[1]), Path(sys.argv[2]))
