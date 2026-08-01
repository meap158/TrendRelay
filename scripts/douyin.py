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
DEFAULT_COOKIE_FILE = ROOT / ".data" / "douyin" / "cookies.json"
CONNECTION_STATUS_FILE = ROOT / ".data" / "douyin" / "connection-status.json"
COOKIE_CAPTURE_SCRIPT = ROOT / "scripts" / "douyin_cookie_capture.py"
SUPPORTED_MODES = ("post", "like", "mix", "music", "collect", "collectmix")
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
MEDIA_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".png",
    ".wav",
    ".webm",
    ".webp",
}
# Upstream CookieManager requires these three; msToken can be generated.
REQUIRED_COOKIE_KEYS = ("ttwid", "odin_tt", "passport_csrf_token")
COOKIE_ENV_KEYS = (
    ("msToken", "DOUYIN_MS_TOKEN"),
    ("ttwid", "DOUYIN_TTWID"),
    ("odin_tt", "DOUYIN_ODIN_TT"),
    ("passport_csrf_token", "DOUYIN_PASSPORT_CSRF_TOKEN"),
    ("sid_guard", "DOUYIN_SID_GUARD"),
)


def tool_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def tool_executable() -> Path:
    return VENV_DIR / ("Scripts/douyin-dl.exe" if os.name == "nt" else "bin/douyin-dl")


def login_browser_ready() -> bool:
    return any(
        (VENV_DIR / marker).is_file()
        for marker in ("login-browser-installed.txt", "browser-installed.txt")
    )


def run_checked(command: list[str], cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def install_provider(include_login_browser: bool) -> int:
    TOOL_ROOT.mkdir(parents=True, exist_ok=True)
    if not (SOURCE_DIR / ".git").is_dir():
        SOURCE_DIR.mkdir(parents=True, exist_ok=True)
        run_checked(["git", "init"], cwd=SOURCE_DIR)
        run_checked(["git", "remote", "add", "origin", REPOSITORY], cwd=SOURCE_DIR)

    run_checked(["git", "fetch", "--depth", "1", "origin", REVISION], cwd=SOURCE_DIR)
    run_checked(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SOURCE_DIR)

    if not tool_python().is_file():
        run_checked([sys.executable, "-m", "venv", str(VENV_DIR)])

    requirement = f"{SOURCE_DIR}[browser]" if include_login_browser else str(SOURCE_DIR)
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
    if include_login_browser:
        run_checked([str(tool_python()), "-m", "playwright", "install", "chromium"])

    MARKER.write_text(f"{REVISION}\n", encoding="utf-8")
    print(f"Douyin provider installed at revision {REVISION[:12]}.")
    return check_provider()


def check_provider() -> int:
    if not MARKER.is_file() or MARKER.read_text(encoding="utf-8").strip() != REVISION:
        print(
            "Douyin provider is not installed at the pinned revision.", file=sys.stderr
        )
        print("Run: npm run douyin -- install", file=sys.stderr)
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
    cookie_status = cookie_readiness()
    if cookie_status["ready"]:
        print(f"Douyin cookies ready ({cookie_status['source']}).")
    else:
        print(
            "Douyin cookies are missing or incomplete; downloads will fail anti-bot checks.",
            file=sys.stderr,
        )
        print("Run: npm run douyin -- connect", file=sys.stderr)
    return 0


def parse_cookie_header(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in header.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            cookies[key] = value
    return cookies


def load_cookie_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cookies: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        text = "" if value is None else str(value).strip()
        if text:
            cookies[key.strip()] = text
    return cookies


def resolve_cookies() -> tuple[dict[str, str], str]:
    """Resolve cookies from env header, discrete env keys, or cookie file."""
    header = os.getenv("DOUYIN_COOKIE", "").strip()
    if header:
        cookies = parse_cookie_header(header)
        if cookies:
            return cookies, "DOUYIN_COOKIE"

    cookies = {
        cookie_key: os.getenv(env_key, "").strip()
        for cookie_key, env_key in COOKIE_ENV_KEYS
        if os.getenv(env_key, "").strip()
    }
    if cookies:
        return cookies, "DOUYIN_* env"

    file_cookies = load_cookie_file(DEFAULT_COOKIE_FILE)
    if file_cookies:
        return file_cookies, str(DEFAULT_COOKIE_FILE)

    return {}, "none"


def cookies_are_ready(cookies: dict[str, str]) -> bool:
    return all(cookies.get(key) for key in REQUIRED_COOKIE_KEYS)


def cookie_readiness() -> dict[str, object]:
    cookies, source = resolve_cookies()
    return {
        "ready": cookies_are_ready(cookies),
        "source": source,
        "keys": sorted(cookies),
        "missing": [key for key in REQUIRED_COOKIE_KEYS if not cookies.get(key)],
    }


def cookie_setup_message() -> str:
    return (
        "Douyin cookies are required for media downloads. "
        "Run `npm run douyin -- connect` or set DOUYIN_COOKIE / "
        "DOUYIN_TTWID, DOUYIN_ODIN_TT, and DOUYIN_PASSPORT_CSRF_TOKEN."
    )


def write_connection_status(state: str, message: str) -> None:
    CONNECTION_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONNECTION_STATUS_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"state": state, "message": message}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(CONNECTION_STATUS_FILE)


def connect_provider() -> int:
    """Install login support if needed, then capture cookies without terminal input."""
    try:
        browser_marker = VENV_DIR / "login-browser-installed.txt"
        if not login_browser_ready():
            write_connection_status(
                "installing",
                "Installing the isolated browser used only for Douyin login.",
            )
            result = install_provider(include_login_browser=True)
            if result != 0:
                write_connection_status(
                    "failed", "Douyin login support could not be installed."
                )
                return result
            browser_marker.write_text("chromium\n", encoding="utf-8")

        write_connection_status("opening_browser", "Opening the Douyin login window.")
        completed = subprocess.run(
            [
                str(tool_python()),
                str(COOKIE_CAPTURE_SCRIPT),
                "--output",
                str(DEFAULT_COOKIE_FILE),
                "--status",
                str(CONNECTION_STATUS_FILE),
            ],
            cwd=ROOT,
            check=False,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        if completed.returncode != 0 and not CONNECTION_STATUS_FILE.is_file():
            write_connection_status("failed", "Douyin connection process failed.")
        return completed.returncode
    except (OSError, subprocess.SubprocessError) as error:
        write_connection_status("failed", f"Douyin connection failed: {error}")
        return 1


def login_provider() -> int:
    if check_provider() != 0:
        return 1
    if not login_browser_ready():
        print(
            "Use `npm run douyin -- connect` so TrendRelay can prepare login support automatically.",
            file=sys.stderr,
        )
        return 1

    DEFAULT_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    print("Opening Douyin in a browser. Log in, then return here and press Enter.")
    print(f"Cookies will be saved to {DEFAULT_COOKIE_FILE}")
    completed = subprocess.run(
        [
            str(tool_python()),
            "-m",
            "tools.cookie_fetcher",
            "--output",
            str(DEFAULT_COOKIE_FILE),
            "--include-all",
        ],
        cwd=SOURCE_DIR,
        check=False,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if completed.returncode != 0:
        return completed.returncode

    cookies = load_cookie_file(DEFAULT_COOKIE_FILE)
    if not cookies_are_ready(cookies):
        missing = [key for key in REQUIRED_COOKIE_KEYS if not cookies.get(key)]
        print(
            "Login finished but required cookies are still missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        print(
            "Make sure you fully log into douyin.com before pressing Enter.",
            file=sys.stderr,
        )
        return 1

    print(f"Saved {len(cookies)} cookie(s) to {DEFAULT_COOKIE_FILE}")
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
        supported_host = (
            host == "douyin.com"
            or host.endswith(".douyin.com")
            or host == "iesdouyin.com"
            or host.endswith(".iesdouyin.com")
            or host == "webcast.amemv.com"
        )
        supported_path = host == "v.douyin.com" and parsed.path not in {"", "/"}
        supported_path = supported_path or any(
            marker in parsed.path.lower()
            for marker in ("/video/", "/note/", "/user/", "/mix/", "/music/")
        )
        supported_path = supported_path or (
            host == "webcast.amemv.com"
            and parsed.path.startswith("/douyin/webcast/reflow/episode/")
        )
        if parsed.scheme not in {"http", "https"} or not supported_host or not supported_path:
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
    cookies, _source = resolve_cookies()
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
        "cookies": cookies,
    }


def redacted_config(config: dict[str, object]) -> dict[str, object]:
    safe = dict(config)
    cookies = config.get("cookies")
    if isinstance(cookies, dict):
        safe["cookies"] = {
            key: "***" if value else "" for key, value in cookies.items()
        }
    return safe


def list_media_files(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    }


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

    cookies = config.get("cookies")
    if not isinstance(cookies, dict) or not cookies_are_ready(cookies):
        print(cookie_setup_message(), file=sys.stderr)
        return 4

    args.output.mkdir(parents=True, exist_ok=True)
    DEFAULT_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    runtime_dir = ROOT / ".data" / "douyin" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    before_media = list_media_files(args.output)
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
        completed = subprocess.run(
            command,
            cwd=SOURCE_DIR,
            check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        if completed.returncode != 0:
            return completed.returncode

        # Upstream exits 0 even when every item fails anti-bot / auth checks.
        # Treat "no new media written" as a hard failure for TrendRelay jobs.
        after_media = list_media_files(args.output)
        new_media = after_media - before_media
        if not new_media:
            print(
                "Download finished without saving any media files. "
                "Douyin likely blocked the request (missing/expired cookies or anti-bot). "
                + cookie_setup_message(),
                file=sys.stderr,
            )
            return 3
        print(f"Saved {len(new_media)} media file(s).")
        return 0
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
        "--login-browser",
        action="store_true",
        help="install Chromium support used only to capture login cookies",
    )

    subparsers.add_parser("check", help="verify the pinned provider installation")
    subparsers.add_parser(
        "connect", help="open login and capture cookies automatically"
    )
    subparsers.add_parser(
        "login",
        help="open a browser, capture Douyin cookies, and save them for downloads",
    )

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
    batch.add_argument("--verbose", action="store_true")
    batch.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    load_prefixed_env(ROOT / ".env", "DOUYIN_")
    args = build_parser().parse_args()
    if args.command == "install":
        result = install_provider(args.login_browser)
        if result == 0 and args.login_browser:
            (VENV_DIR / "login-browser-installed.txt").write_text(
                "chromium\n", encoding="utf-8"
            )
        return result
    if args.command == "check":
        return check_provider()
    if args.command == "login":
        return login_provider()
    if args.command == "connect":
        return connect_provider()
    return batch_download(args)


if __name__ == "__main__":
    raise SystemExit(main())
