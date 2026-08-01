"""Install and verify TrendRelay's pinned Meta Ads Collector runtime."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / ".tools" / "catalog" / "meta-ads-collector"
SOURCE_ROOT = TOOL_ROOT / "source"
RUNTIME_ROOT = TOOL_ROOT / "runtime"
REPOSITORY = "https://github.com/promisingcoder/MetaAdsCollector"
REVISION = "0ffb2fb1af94eae6542b328ab3ae31fc1c9a5897"


def runtime_python() -> Path:
    return RUNTIME_ROOT / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run_checked(command: list[str], cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def installed_revision() -> str | None:
    if not (SOURCE_ROOT / ".git").is_dir():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SOURCE_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def install() -> int:
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    if not (SOURCE_ROOT / ".git").is_dir():
        run_checked(["git", "init"], cwd=SOURCE_ROOT)
        run_checked(["git", "remote", "add", "origin", REPOSITORY], cwd=SOURCE_ROOT)
    run_checked(["git", "fetch", "--depth", "1", "origin", REVISION], cwd=SOURCE_ROOT)
    run_checked(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SOURCE_ROOT)

    if not runtime_python().is_file():
        run_checked([sys.executable, "-m", "venv", str(RUNTIME_ROOT)])
    run_checked(
        [
            str(runtime_python()),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            str(SOURCE_ROOT),
        ]
    )
    return check()


def check() -> int:
    if installed_revision() != REVISION:
        print(
            "Meta Ads Collector source is not at the pinned revision.", file=sys.stderr
        )
        return 1
    if not runtime_python().is_file():
        print("Meta Ads Collector isolated runtime is missing.", file=sys.stderr)
        return 1
    result = subprocess.run(
        [
            str(runtime_python()),
            "-c",
            "from meta_ads_collector import MetaAdsCollector; print('ready')",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != "ready":
        print("Meta Ads Collector runtime verification failed.", file=sys.stderr)
        return 1
    print(f"Meta Ads Collector ready at {REVISION[:12]}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "check"))
    args = parser.parse_args()
    return install() if args.command == "install" else check()


if __name__ == "__main__":
    raise SystemExit(main())
