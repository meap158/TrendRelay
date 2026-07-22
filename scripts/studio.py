"""CLI for guarded OpenMontage production proposals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_SOURCE = Path(__file__).resolve().parents[1] / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.integrations.openmontage_runtime import (  # noqa: E402
    RenderRequest,
    create_render_job,
    list_render_jobs,
    run_render_job,
    runtime_status,
)

from trendrelay_api.integrations.openmontage import (  # noqa: E402
    ProductionApproval,
    ProductionRequest,
    approve_proposal,
    create_proposal,
    list_pipelines,
    list_productions,
    provider_status,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Manage guarded video-production proposals."
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("pipelines")
    listing = commands.add_parser("list")
    listing.add_argument("--workspace", default="local")
    propose = commands.add_parser("propose")
    propose.add_argument("title")
    propose.add_argument("--source", required=True)
    propose.add_argument(
        "--rights", required=True, choices=["owned", "licensed", "public-domain"]
    )
    propose.add_argument(
        "--pipeline",
        choices=["clip-factory", "podcast-repurpose"],
        default="clip-factory",
    )
    propose.add_argument(
        "--platform",
        action="append",
        choices=["tiktok", "instagram", "youtube"],
        default=[],
    )
    propose.add_argument("--clips", type=int, default=3)
    propose.add_argument("--budget", type=float, default=1.0)
    propose.add_argument("--workspace", default="local")
    propose.add_argument("--confirm-external-action", action="store_true")
    approve = commands.add_parser("approve")
    approve.add_argument("production_id")
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--confirm-external-action", action="store_true")
    render = commands.add_parser("render")
    render.add_argument("production_id")
    render.add_argument("--workspace", default="local")
    render.add_argument(
        "--segment",
        action="append",
        required=True,
        help="Clip as LABEL:START_SECONDS:END_SECONDS",
    )
    render.add_argument("--confirm-external-action", action="store_true")
    commands.add_parser("runtime")
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "check":
        print(json.dumps(provider_status(), indent=2))
        return 0
    if args.command == "pipelines":
        print(json.dumps({"pipelines": list_pipelines()}, indent=2))
        return 0
    if args.command == "list":
        print(
            json.dumps(
                {
                    "productions": list_productions(args.workspace),
                    "renders": list_render_jobs(args.workspace),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "runtime":
        print(json.dumps(runtime_status(), indent=2))
        return 0
    if not args.confirm_external_action:
        print("This operation requires --confirm-external-action.")
        return 2
    if args.command == "propose":
        proposal = create_proposal(
            ProductionRequest(
                workspace_id=args.workspace,
                title=args.title,
                source_asset=args.source,
                source_rights=args.rights,
                pipeline=args.pipeline,
                target_platforms=args.platform or ["tiktok"],
                clip_count=args.clips,
                budget_usd=args.budget,
                confirm_external_action=True,
            )
        )
        print(json.dumps(proposal, indent=2))
        return 0
    if args.command == "render":
        try:
            segments = [
                {
                    "label": value.rsplit(":", 2)[0],
                    "start_seconds": float(value.rsplit(":", 2)[1]),
                    "end_seconds": float(value.rsplit(":", 2)[2]),
                }
                for value in args.segment
            ]
        except (IndexError, ValueError):
            print("Each --segment must use LABEL:START_SECONDS:END_SECONDS.")
            return 2
        job = create_render_job(
            RenderRequest(
                workspace_id=args.workspace,
                production_id=args.production_id,
                segments=segments,
                confirm_external_action=True,
            )
        )
        print(json.dumps(run_render_job(job["id"]), indent=2, default=str))
        return 0
    production = approve_proposal(
        args.production_id,
        ProductionApproval(approved_by=args.approved_by, confirm_external_action=True),
    )
    print(json.dumps(production, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
