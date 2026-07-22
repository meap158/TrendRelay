"""CLI for safe Agent Reach channel diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_SOURCE = Path(__file__).resolve().parents[1] / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.integrations.agent_reach import diagnostic_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Agent Reach channel readiness without network or credential access."
    )
    parser.add_argument("command", choices=["check", "channels"])
    args = parser.parse_args()
    report = diagnostic_report()
    if args.command == "channels":
        print(json.dumps({"channels": report["channels"]}, indent=2))
    else:
        print(json.dumps(report, indent=2))
    return 0 if report["provider"]["installed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
