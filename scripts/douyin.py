"""Install and run TrendRelay's pinned Douyin batch-download provider."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

if __package__:
    from .local_env import load_prefixed_env
else:
    from local_env import load_prefixed_env

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / ".tools" / "douyin-downloader"
SOURCE_DIR = TOOL_ROOT / "source"
VENV_DIR = TOOL_ROOT / "venv"
MARKER = TOOL_ROOT / "installed-revision.txt"
REPOSITORY = "https://github.com/jiji262/douyin-downloader.git"
REVISION = "ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7"
DEFAULT_OUTPUT = ROOT / ".data" / "downloads" / "douyin"
DEFAULT_DATABASE = ROOT / ".data" / "douyin" / "dy_downloader.db"
SUPPORTED_MODES = ("post", "like", "mix", "music", "collect", "collectmix")
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


def tool_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def tool_executable() -> Path:
    return VENV_DIR / ("Scripts/douyin-dl.exe" if os.name == "nt" else "bin/douyin-dl")


def run_checked(command: list[str], cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def install_provider(include_browser: bool) -> int:
    TOOL_ROOT.mkdir(parents=True, exist_ok=True)
    if not (SOURCE_DIR / ".git").is_dir():
        SOURCE_DIR.mkdir(parents=True, exist_ok=True)
        run_checked(["git", "init"], cwd=SOURCE_DIR)
        run_checked(["git", "remote", "add", "origin", REPOSITORY], cwd=SOURCE_DIR)

    run_checked(["git", "fetch", "--depth", "1", "origin", REVISION], cwd=SOURCE_DIR)
    run_checked(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SOURCE_DIR)

    if not tool_python().is_file():
        run_checked([sys.executable, "-m", "venv", str(VENV_DIR)])

    requirement = f"{SOURCE_DIR}[browser]" if include_browser else str(SOURCE_DIR)
    run_checked(
        [
            str(tool_python()),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            requirement,
        ]
    )
    if include_browser:
        run_checked([str(tool_python()), "-m", "playwright", "install", "chromium"])

    MARKER.write_text(f"{REVISION}\n", encoding="utf-8")
    print(f"Douyin provider installed at revision {REVISION[:12]}.")
    return check_provider()


def check_provider() -> int:
    if not MARKER.is_file() or MARKER.read_text(encoding="utf-8").strip() != REVISION:
        print(
            "Douyin provider is not installed at the pinned revision.", file=sys.stderr
        )
        print("Run: douyin.cmd install", file=sys.stderr)
        return 1
    if not tool_executable().is_file():
        print(
            "Douyin provider executable is missing. Re-run installation.",
            file=sys.stderr,
        )
        return 1

    result = subprocess.run(
        [str(tool_executable()), "--version"],
        cwd=SOURCE_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    print(f"Douyin provider ready: {result.stdout.strip() or REVISION[:12]}")
    return 0


def extract_urls(value: str) -> list[str]:
    candidates = URL_PATTERN.findall(value)
    if not candidates and value.strip():
        candidates = [value.strip()]

    urls: list[str] = []
    for candidate in candidates:
        url = candidate.rstrip(".,;:!?)]}，。；：！？）】》")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            host == "douyin.com"
            or host.endswith(".douyin.com")
            or host == "iesdouyin.com"
            or host.endswith(".iesdouyin.com")
            or (
                host == "webcast.amemv.com"
                and parsed.path.startswith("/douyin/webcast/reflow/episode/")
            )
        ):
            raise ValueError(f"Unsupported Douyin URL: {url}")
        urls.append(url)
    return urls


def collect_urls(values: list[str], batch_file: Path | None) -> list[str]:
    inputs = list(values)
    if batch_file:
        for line in batch_file.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                inputs.append(stripped)

    unique: list[str] = []
    seen: set[str] = set()
    for value in inputs:
        for url in extract_urls(value):
            if url not in seen:
                seen.add(url)
                unique.append(url)
    if not unique:
        raise ValueError("Provide at least one Douyin URL or a non-empty --file.")
    return unique


def build_config(args: argparse.Namespace, urls: list[str]) -> dict[str, object]:
    modes = args.mode or ["post"]
    limit_by_mode = {mode: args.limit for mode in modes}
    incremental = {
        mode: args.incremental
        for mode in modes
        if mode in {"post", "like", "mix", "music"}
    }
    return {
        "link": urls,
        "path": str(args.output.resolve()),
        "mode": modes,
        "number": limit_by_mode,
        "increase": incremental,
        "thread": args.threads,
        "retry_times": args.retries,
        "proxy": args.proxy,
        "database": True,
        "database_path": str(DEFAULT_DATABASE.resolve()),
        "folderstyle": True,
        "progress": {"quiet_logs": not args.verbose},
        "cookies": {
            "msToken": os.getenv("DOUYIN_MS_TOKEN", ""),
            "ttwid": os.getenv("DOUYIN_TTWID", ""),
            "odin_tt": os.getenv("DOUYIN_ODIN_TT", ""),
            "passport_csrf_token": os.getenv("DOUYIN_PASSPORT_CSRF_TOKEN", ""),
            "sid_guard": os.getenv("DOUYIN_SID_GUARD", ""),
        },
        "browser_fallback": {
            "enabled": args.browser_fallback,
            "headless": False,
            "max_scrolls": 240,
            "idle_rounds": 8,
            "wait_timeout_seconds": 600,
        },
    }


def redacted_config(config: dict[str, object]) -> dict[str, object]:
    safe = dict(config)
    safe["cookies"] = {
        key: "***" if value else "" for key, value in config["cookies"].items()
    }
    return safe


def batch_download(args: argparse.Namespace) -> int:
    try:
        urls = collect_urls(args.urls, args.file)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2

    modes = args.mode or ["post"]
    if any(mode in {"collect", "collectmix"} for mode in modes) and len(modes) > 1:
        print("collect and collectmix must each be used alone.", file=sys.stderr)
        return 2

    config = build_config(args, urls)
    if args.dry_run:
        print(json.dumps(redacted_config(config), ensure_ascii=False, indent=2))
        return 0
    if check_provider() != 0:
        return 1
    if args.browser_fallback and not (VENV_DIR / "browser-installed.txt").is_file():
        print(
            "Browser fallback requires: douyin.cmd install --browser", file=sys.stderr
        )
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    DEFAULT_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    runtime_dir = ROOT / ".data" / "douyin" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="config-",
            dir=runtime_dir,
            encoding="utf-8",
            delete=False,
        ) as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2)
            config_path = Path(config_file.name)
        command = [str(tool_executable()), "--config", str(config_path)]
        if args.verbose:
            command.append("--verbose")
        print(
            f"Downloading {len(urls)} Douyin source(s) to {args.output.resolve()} "
            f"with modes {', '.join(args.mode or ['post'])}."
        )
        print("Only download content you are authorized to retain and reuse.")
        return subprocess.run(command, cwd=SOURCE_DIR, check=False).returncode
    finally:
        if config_path:
            config_path.unlink(missing_ok=True)


def non_negative_integer(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be one or greater")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="install the pinned provider")
    install.add_argument(
        "--browser", action="store_true", help="also install browser fallback"
    )

    subparsers.add_parser("check", help="verify the pinned provider installation")

    batch = subparsers.add_parser(
        "batch", help="batch download Douyin URLs or profiles"
    )
    batch.add_argument("urls", nargs="*", help="Douyin URL(s) or copied share text")
    batch.add_argument(
        "--file", type=Path, help="UTF-8 file with one URL/share text per line"
    )
    batch.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    batch.add_argument("--mode", action="append", choices=SUPPORTED_MODES)
    batch.add_argument(
        "--limit",
        type=non_negative_integer,
        default=50,
        help="items per mode; 0 downloads all",
    )
    batch.add_argument("--threads", type=positive_integer, default=5)
    batch.add_argument("--retries", type=non_negative_integer, default=3)
    batch.add_argument("--proxy", default="")
    batch.add_argument("--incremental", action="store_true")
    batch.add_argument("--browser-fallback", action="store_true")
    batch.add_argument("--verbose", action="store_true")
    batch.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    load_prefixed_env(ROOT / ".env", "DOUYIN_")
    args = build_parser().parse_args()
    if args.command == "install":
        result = install_provider(args.browser)
        if result == 0 and args.browser:
            (VENV_DIR / "browser-installed.txt").write_text(
                "chromium\n", encoding="utf-8"
            )
        return result
    if args.command == "check":
        return check_provider()
    return batch_download(args)


if __name__ == "__main__":
    raise SystemExit(main())
