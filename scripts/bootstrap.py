"""Prepare and verify TrendRelay's Python API environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_PROJECT = ROOT / "services" / "api"
API_MANIFEST = API_PROJECT / "pyproject.toml"
STAMP = ROOT / ".venv" / ".trendrelay-api-dependencies.json"
DEFAULT_TIMEOUT_SECONDS = 15 * 60
STATUS_INTERVAL_SECONDS = 20

REQUIRED_MODULES = {
    "alembic": "Alembic",
    "fastapi": "FastAPI",
    "httpx": "HTTPX",
    "jwt": "PyJWT",
    "psycopg": "Psycopg",
    "pydantic_settings": "Pydantic Settings",
    "pytest": "Pytest",
    "sqlalchemy": "SQLAlchemy",
    "trendrelay_api": "TrendRelay API",
    "uvicorn": "Uvicorn",
    "yaml": "PyYAML",
}


def dependency_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(API_MANIFEST.read_bytes())
    digest.update(
        (
            f"{sys.version_info.major}.{sys.version_info.minor}|"
            f"{platform.system()}|{platform.machine()}"
        ).encode()
    )
    return digest.hexdigest()


def missing_modules() -> list[str]:
    return [
        label
        for module, label in REQUIRED_MODULES.items()
        if importlib.util.find_spec(module) is None
    ]


def stamp_matches(fingerprint: str) -> bool:
    try:
        payload = json.loads(STAMP.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return payload.get("fingerprint") == fingerprint


def setup_timeout_seconds() -> int:
    raw = os.getenv("TRENDRELAY_SETUP_TIMEOUT_SECONDS", "")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def run_visible(command: list[str], timeout_seconds: int) -> int:
    environment = {
        **os.environ,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    process = subprocess.Popen(command, cwd=ROOT, env=environment)
    started_at = time.monotonic()
    next_status_at = started_at + STATUS_INTERVAL_SECONDS
    while process.poll() is None:
        elapsed = time.monotonic() - started_at
        if elapsed >= timeout_seconds:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise RuntimeError(
                f"API dependency setup exceeded {timeout_seconds // 60} minutes."
            )
        if time.monotonic() >= next_status_at:
            print(
                f"[API setup] Still working ({int(elapsed)} seconds elapsed)...",
                flush=True,
            )
            next_status_at += STATUS_INTERVAL_SECONDS
        time.sleep(0.25)
    return process.returncode or 0


def install_api_dependencies() -> int:
    timeout_seconds = setup_timeout_seconds()
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--prefer-binary",
        "--progress-bar",
        "on",
        "--timeout",
        "45",
        "--retries",
        "2",
        "-e",
        f"{API_PROJECT}[dev]",
    ]
    print(
        "[API setup] Installing required packages. "
        "The first run normally takes 1-5 minutes.",
        flush=True,
    )
    try:
        return run_visible(command, timeout_seconds)
    except RuntimeError as error:
        print(f"[API setup] {error}", file=sys.stderr)
        return 1


def write_stamp(fingerprint: str) -> None:
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    temporary = STAMP.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "python": platform.python_version(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(STAMP)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify imports without installing or changing the environment",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing = missing_modules()
    if args.check:
        if missing:
            print(
                "Missing API dependencies: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 1
        print("API dependency imports are ready.")
        return 0

    fingerprint = dependency_fingerprint()
    if not missing and stamp_matches(fingerprint):
        print("[API setup] Dependencies are ready.", flush=True)
        return 0

    if missing:
        print("[API setup] Missing: " + ", ".join(missing), flush=True)
    else:
        print("[API setup] Dependency definition changed; refreshing packages.", flush=True)

    if install_api_dependencies() != 0:
        print(
            "[API setup] Installation failed. Check the network connection, then "
            "run start.cmd again. Existing downloads and settings are safe.",
            file=sys.stderr,
        )
        return 1

    remaining = missing_modules()
    if remaining:
        print(
            "[API setup] Installation finished but imports are still missing: "
            + ", ".join(remaining),
            file=sys.stderr,
        )
        return 1

    write_stamp(fingerprint)
    print("[API setup] Dependencies are ready.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
