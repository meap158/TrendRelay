"""Manage TrendRelay's pinned third-party tool catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from trendrelay_api.tool_registry import (  # noqa: E402
    ToolRegistryError,
    install_tool,
    list_tools,
    set_active,
    uninstall_tool,
)


def print_tools() -> None:
    print(json.dumps({"tools": list_tools()}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    for command in ("install", "uninstall", "activate", "deactivate"):
        operation = subparsers.add_parser(command)
        operation.add_argument("tool_id")
        if command in {"install", "uninstall"}:
            operation.add_argument("--confirm-external-action", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "list":
            print_tools()
            return 0
        if (
            args.command in {"install", "uninstall"}
            and not args.confirm_external_action
        ):
            print(
                f"{args.command} requires --confirm-external-action.", file=sys.stderr
            )
            return 2
        if args.command == "install":
            result = install_tool(args.tool_id)
        elif args.command == "uninstall":
            result = uninstall_tool(args.tool_id)
        else:
            result = set_active(args.tool_id, args.command == "activate")
    except ToolRegistryError as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
