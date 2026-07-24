"""Install and verify TrendRelay's pinned Meta Ads Kit runtime."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / ".tools" / "catalog" / "meta-ads-kit"
SOURCE_ROOT = TOOL_ROOT / "source"
RUNTIME_ROOT = TOOL_ROOT / "runtime"
REPOSITORY = "https://github.com/TheMattBerman/meta-ads-kit"
REVISION = "0879bb4566a836670f33beb509ff7d8d4779849e"
RUNTIME_PACKAGE = "@vishalgojha/social-flow@0.2.17"


def social_executable() -> Path:
    suffix = "social.cmd" if os.name == "nt" else "social"
    return RUNTIME_ROOT / "node_modules" / ".bin" / suffix


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

    npm = "npm.cmd" if os.name == "nt" else "npm"
    run_checked(
        [
            npm,
            "install",
            "--prefix",
            str(RUNTIME_ROOT),
            "--save-exact",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            RUNTIME_PACKAGE,
        ]
    )
    return check()


def check() -> int:
    if installed_revision() != REVISION:
        print(
            "Meta Ads Kit source is not installed at the pinned revision.",
            file=sys.stderr,
        )
        return 1
    executable = social_executable()
    if not executable.is_file():
        print("Meta Ads Kit's isolated social runtime is missing.", file=sys.stderr)
        return 1
    result = subprocess.run(
        [str(executable), "--version"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print("Meta Ads Kit runtime verification failed.", file=sys.stderr)
        return 1
    print(f"Meta Ads Kit ready at {REVISION[:12]} ({result.stdout.strip()}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "check"))
    args = parser.parse_args()
    return install() if args.command == "install" else check()


if __name__ == "__main__":
    raise SystemExit(main())
