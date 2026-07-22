"""CLI for workspace-scoped Last 30 Days research jobs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_SOURCE = Path(__file__).resolve().parents[1] / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.integrations.last30days import (  # noqa: E402
    ResearchRequest,
    create_job,
    list_jobs,
    provider_status,
    run_job,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run TrendRelay trend research jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check", help="Inspect the pinned provider and activation state."
    )
    listing = commands.add_parser("list", help="List persisted research jobs.")
    listing.add_argument("--workspace", default="local")
    listing.add_argument("--limit", type=int, default=20)
    run = commands.add_parser("run", help="Run research and ingest its evidence.")
    run.add_argument("topic")
    run.add_argument("--workspace", default="local")
    run.add_argument("--days", type=int, default=30)
    run.add_argument("--source", action="append", default=[])
    run.add_argument(
        "--mode", choices=["standard", "quick", "deep"], default="standard"
    )
    run.add_argument("--mock", action="store_true")
    run.add_argument("--confirm-external-action", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "check":
        status = provider_status()
        print(json.dumps(status, indent=2))
        return 0 if status["installed"] and status["engine_present"] else 1
    if args.command == "list":
        print(json.dumps({"jobs": list_jobs(args.workspace, args.limit)}, indent=2))
        return 0
    if not args.mock and not args.confirm_external_action:
        print("Live research requires --confirm-external-action.")
        return 2
    request = ResearchRequest(
        workspace_id=args.workspace,
        topic=args.topic,
        days=args.days,
        sources=args.source,
        mode=args.mode,
        mock=args.mock,
        confirm_external_action=args.confirm_external_action,
    )
    job = create_job(request)
    run_job(job["id"], request)
    completed = list_jobs(args.workspace, 1)[0]
    print(json.dumps(completed, indent=2, ensure_ascii=False))
    return 0 if completed["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
