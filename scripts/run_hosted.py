"""Run existing CLI commands without logging raw infrastructure exceptions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pnu_event_gate.cli import main
from pnu_event_gate.runtime_reporting import write_health_report


def run(argv: list[str]) -> int:
    try:
        return main(argv)
    except Exception as error:
        if "--health-report" in argv:
            path = Path(argv[argv.index("--health-report") + 1])
            write_health_report(path, {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "status": "unhealthy",
                "issues": [{"fingerprint": "monitor-execution-failed", "component": "runtime", "severity": "critical"}],
            })
        # Exception messages can contain connection strings or API responses.
        print(f"::error::Runtime command failed ({type(error).__name__}); inspect private service state.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
