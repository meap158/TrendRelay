"""Install and check the isolated browser runtime for TikTok Discovery.

TikTok Creative Center renders its trend tables with JavaScript, so reading them
needs a real browser. This provisions a dedicated virtual environment with
Playwright and Chromium, kept out of the API service's own dependencies.

    python scripts/tiktok_creative.py install
    python scripts/tiktok_creative.py check
    python scripts/tiktok_creative.py fetch --category hashtag --region US
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / ".tools" / "catalog" / "tiktok-creative"
RUNTIME_DIR = TOOL_ROOT / "runtime"
RUNTIME_PYTHON = RUNTIME_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
BRIDGE = ROOT / "scripts" / "tiktok_creative_bridge.py"
PLAYWRIGHT_PIN = "playwright>=1.49,<2"

sys.path.insert(0, str(ROOT / "services" / "api" / "src"))


def run_checked(command: list[str]) -> None:
    print("$", " ".join(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def install() -> int:
    RUNTIME_DIR.parent.mkdir(parents=True, exist_ok=True)
    if not RUNTIME_PYTHON.is_file():
        run_checked([sys.executable, "-m", "venv", str(RUNTIME_DIR)])
    run_checked(
        [
            str(RUNTIME_PYTHON), "-m", "pip", "install",
            "--disable-pip-version-check", "--upgrade", PLAYWRIGHT_PIN,
        ]
    )
    # Reuses an already-downloaded browser when another tool installed one.
    run_checked([str(RUNTIME_PYTHON), "-m", "playwright", "install", "chromium"])
    print("TikTok Discovery runtime installed.")
    return check()


def check() -> int:
    from trendrelay_api.integrations.tiktok_creative import provider_status

    status = provider_status()
    print(json.dumps(status, indent=2))
    if not status["ready"]:
        print(
            "Runtime is not ready. Run: python scripts/tiktok_creative.py install",
            file=sys.stderr,
        )
        return 1
    return 0


def fetch(category: str, region: str, period: int, limit: int) -> int:
    from trendrelay_api.integrations.tiktok_creative import (
        TikTokTrendRequest,
        TikTokUnavailable,
        fetch_tiktok_trends,
    )

    try:
        result = fetch_tiktok_trends(
            TikTokTrendRequest(category=category, region=region, period=period, limit=limit),
            use_cache=False,
        )
    except TikTokUnavailable as error:
        print(f"unavailable: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="create the isolated Playwright runtime")
    sub.add_parser("check", help="report runtime readiness")
    read = sub.add_parser("fetch", help="read one public trend list")
    read.add_argument("--category", default="hashtag")
    read.add_argument("--region", default="US")
    read.add_argument("--period", type=int, default=7, choices=[7, 30, 120])
    read.add_argument("--limit", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "install":
        return install()
    if args.command == "check":
        return check()
    return fetch(args.category, args.region, args.period, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
