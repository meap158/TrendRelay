"""Install and run TrendRelay's pinned Douyin batch-download provider."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
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
TOPIC_VIDEOS_SCRIPT = ROOT / "scripts" / "douyin_topic_videos.py"
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
    if cookie_status["ready"] and cookie_status["signed_in"]:
        print(f"Douyin cookies ready, signed in ({cookie_status['source']}).")
    elif cookie_status["ready"]:
        print(
            f"Douyin cookies ready, anonymous ({cookie_status['source']}). "
            "Single links download in full; a profile fetches its first page "
            "(about 20 videos), Douyin's ceiling without an account. A "
            "connected account fetches whole profiles and topic search."
        )
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
        # `sessionid` is set only by an actual login. Without it Douyin serves
        # one page of a profile (about 20 posts) and refuses the rest.
        "signed_in": bool(cookies.get("sessionid")),
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


def extended_path(path: Path) -> str:
    """A path the Win32 API accepts past its 260-character limit.

    `\\\\?\\` skips the normalisation that enforces MAX_PATH: no registry
    change, no administrator, no restart. Measured against this provider's own
    path composition, identical names failed plainly at 280, 305 and 335
    characters and succeeded through the prefix at all three.

    Kept here rather than imported because this script is deliberately
    standalone - it runs from npm without the API package on the path.
    """
    if os.name != "nt":
        return str(path)
    absolute = os.path.abspath(str(path))
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


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
        # Prefixed so the provider can write a name Windows would otherwise
        # refuse. A caption of 80 Chinese characters plus a creator name lands
        # a path in the 200s and sometimes past 260, and past it the write
        # fails with FileNotFoundError - for a file that could not be created.
        # The API renames anything over-long back under the limit afterwards,
        # so nothing downstream ever sees this prefix.
        "path": extended_path(args.output.resolve()),
        "mode": modes,
        "number": limit_by_mode,
        "increase": incremental,
        "thread": args.threads,
        "retry_times": args.retries,
        "proxy": args.proxy,
        "database": True,
        "database_path": str(DEFAULT_DATABASE.resolve()),
        # Off deliberately. With it on, every post gets a directory named after
        # its own title and the file inside repeats that title, so a long
        # Chinese caption is spent twice on a path Windows caps at 260
        # characters. Downloads failed at the write, and the failure surfaced as
        # "Douyin rejected the saved session" - nothing on this machine had ever
        # written a path longer than 258.
        "folderstyle": False,
        # Extras the downloader only requests when asked, so declining one
        # saves the bandwidth rather than fetching and discarding it.
        "cover": bool(getattr(args, "covers", False)),
        "music": bool(getattr(args, "music", False)),
        # Off: the provider's browser fallback opens a window that Douyin's
        # anti-bot caps or challenges anyway, so it added a popup mid-download
        # for no gain. A profile is fetched to its first page over the signed
        # API instead; whole profiles need a signed-in account.
        "browser_fallback": {"enabled": False},
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
    """Media in the output folder, including files Windows cannot open plainly.

    `is_file()` answers False for a path over 260 characters - not "no" but "I
    could not look" - and this set is what decides whether anything was saved.
    A long Chinese caption therefore downloaded correctly, counted as nothing,
    and left the script reporting no new media; the API read that as a rejected
    session and told the whole workspace to reconnect Douyin. The download had
    worked. Nothing was wrong with the cookies.
    """
    if not os.path.isdir(extended_path(root)):
        return set()
    return {
        Path(os.path.abspath(str(path)))
        for path in root.rglob("*")
        if path.suffix.lower() in MEDIA_SUFFIXES
        and os.path.isfile(extended_path(path))
    }


def _search_snapshots(output: Path) -> list[Path]:
    board = output / "search"
    return sorted(board.glob("*.jsonl")) if board.is_dir() else []


def hot_topic(args: argparse.Namespace) -> int:
    """The videos on a hot-topic page, which needs no Douyin account.

    Search is the walled route: an anonymous session gets 2483, and someone
    without an account cannot use it at all. The board hands out a `sentence_id`
    for every term, and the page it names is served to signed-out visitors, so
    this is the route that works for everyone.

    Run in the provider's own venv because that is where the browser lives.
    """
    if not login_browser_ready():
        print(
            "The browser used to read topic pages is not installed. "
            "Run `npm run douyin -- connect` once to install it.",
            file=sys.stderr,
        )
        return 3
    command = [
        str(tool_python()), str(TOPIC_VIDEOS_SCRIPT), args.sentence_id,
        "--limit", str(args.limit),
    ]
    if DEFAULT_COOKIE_FILE.is_file():
        command += ["--cookies", str(DEFAULT_COOKIE_FILE)]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=args.timeout,
    )
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    if completed.returncode != 0:
        return completed.returncode
    print(completed.stdout.strip())
    return 0


def topic(args: argparse.Namespace) -> int:
    """Find the videos posted under a term and print them as JSON.

    The hot board ranks topics, not clips: its group_id is a topic identifier
    and asking the video endpoint for one fails every time. Searching the term
    is what turns a trending topic into videos that can actually be downloaded.
    """
    with contextlib.redirect_stdout(sys.stderr):
        if check_provider() != 0:
            return 1
        cookies, _source = resolve_cookies()
        if not cookies_are_ready(cookies):
            print(cookie_setup_message(), file=sys.stderr)
            return 4

    args.output.mkdir(parents=True, exist_ok=True)
    runtime_dir = ROOT / ".data" / "douyin" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "link": [],
        "path": str(args.output.resolve()),
        "mode": ["post"],
        "cookies": cookies,
        "proxy": args.proxy,
        "progress": {"quiet_logs": True},
    }
    before = _search_snapshots(args.output)
    config_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="search-", dir=runtime_dir,
            encoding="utf-8", delete=False,
        ) as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2)
            config_path = Path(config_file.name)
        # The term is Chinese and the provider prints it as it works, which a
        # cp1252 console cannot encode; the child is told to speak UTF-8.
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        completed = subprocess.run(
            [
                str(tool_executable()), "--config", str(config_path),
                "--search", args.term, "--search-max", str(args.limit),
            ],
            cwd=SOURCE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=args.timeout,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            # Search is the one thing here that needs a real account. Downloads
            # and the hot board work from the anonymous session `connect`
            # captures; searching answers 2483 "please log in first" to it, and
            # a traceback tail does not tell an operator what to do about that.
            if "LoginRequiredError" in detail or "2483" in detail:
                print(
                    "Douyin requires a signed-in account to search. The saved session is "
                    "anonymous, which is enough to download a known link but not to look "
                    "one up. A trending topic does not need this: it downloads through "
                    "its own page. To search anyway, connect Douyin again and log in.",
                    file=sys.stderr,
                )
                return 5
            print(detail[-1500:] or "The provider could not search.", file=sys.stderr)
            return 1
    finally:
        if config_path is not None:
            config_path.unlink(missing_ok=True)

    fresh = sorted(set(_search_snapshots(args.output)) - set(before))
    if not fresh:
        print("The provider reported success but wrote no results.", file=sys.stderr)
        return 1

    items = []
    for raw in _read_jsonl(fresh[-1]):
        aweme_id = str(raw.get("aweme_id") or "").strip()
        if not aweme_id:
            continue
        statistics = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
        author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
        items.append({
            "aweme_id": aweme_id,
            # A real video id, unlike the board's group_id, so this is a link
            # that downloads.
            "video_url": f"https://www.douyin.com/video/{aweme_id}",
            "title": str(raw.get("desc") or "").strip()[:300],
            "creator": str(author.get("nickname") or "").strip()[:120],
            "likes": int(statistics.get("digg_count") or 0),
            "plays": int(statistics.get("play_count") or 0),
        })

    # Escaped rather than raw: this is read by another process, and a console
    # using the Windows default codepage cannot encode Chinese titles.
    print(json.dumps(
        {"term": args.term, "items": items, "count": len(items)},
        ensure_ascii=True,
    ))
    return 0


def trending(args: argparse.Namespace) -> int:
    """Read Douyin's hot-search board and print it as JSON on stdout.

    The provider writes a JSONL snapshot of its own; this reads that file back
    and emits one object, so the caller gets a result without having to know
    the provider's directory layout or its timestamped filenames.
    """
    # stdout carries the JSON result and nothing else, so the readiness notes
    # these helpers print are sent to stderr where a human still sees them.
    with contextlib.redirect_stdout(sys.stderr):
        if check_provider() != 0:
            return 1
        cookies, _source = resolve_cookies()
        if not cookies_are_ready(cookies):
            print(cookie_setup_message(), file=sys.stderr)
            return 4

    args.output.mkdir(parents=True, exist_ok=True)
    runtime_dir = ROOT / ".data" / "douyin" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "link": [],
        "path": str(args.output.resolve()),
        "mode": ["post"],
        "cookies": cookies,
        "proxy": args.proxy,
        "progress": {"quiet_logs": True},
    }
    config_path: Path | None = None
    before = _hot_board_snapshots(args.output)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="discovery-", dir=runtime_dir,
            encoding="utf-8", delete=False,
        ) as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2)
            config_path = Path(config_file.name)
        # The board is Chinese text and the provider prints it as it goes. On a
        # cp1252 console that raises UnicodeEncodeError before any result is
        # written, so the child is told to speak UTF-8.
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        completed = subprocess.run(
            [str(tool_executable()), "--config", str(config_path), "--hot-board", str(args.limit)],
            cwd=SOURCE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=args.timeout,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            # The tail, not the head: a traceback puts the exception last, and
            # printing the first 600 characters showed only the call stack.
            print(detail[-1500:] or "The provider could not read the board.", file=sys.stderr)
            return 1
    finally:
        if config_path is not None:
            config_path.unlink(missing_ok=True)

    fresh = sorted(set(_hot_board_snapshots(args.output)) - set(before))
    if not fresh:
        print("The provider reported success but wrote no snapshot.", file=sys.stderr)
        return 1
    items = _read_jsonl(fresh[-1])
    # Escaped rather than raw: this is read by another process, and a console
    # or pipe using the Windows default codepage cannot encode Chinese titles.
    # JSON escapes decode back to the same string everywhere.
    print(json.dumps(
        {"items": items, "count": len(items), "snapshot": str(fresh[-1])},
        ensure_ascii=True,
    ))
    return 0


def _hot_board_snapshots(output: Path) -> list[Path]:
    board = output / "hot_board"
    return sorted(board.glob("*.jsonl")) if board.is_dir() else []


def _read_jsonl(path: Path) -> list[dict]:
    items: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            items.append(parsed)
    return items


_VIDEO_ID_IN_URL = re.compile(r"/video/(\d+)")


def _video_id(url: str) -> str | None:
    match = _VIDEO_ID_IN_URL.search(url)
    return match.group(1) if match else None


def downloaded_aweme_ids() -> set[str]:
    """Video ids the provider has already saved, read from its own database.

    The provider keeps an ``aweme`` table keyed by ``aweme_id`` for exactly this
    - so a re-run does not fetch what is already held. Read-only and defensive:
    a missing or unreadable database just means nothing is known to be done yet,
    which fetches everything rather than skipping wrongly.
    """
    if not DEFAULT_DATABASE.is_file():
        return set()
    try:
        connection = sqlite3.connect(f"file:{DEFAULT_DATABASE}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT aweme_id FROM aweme").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return set()
    return {str(row[0]) for row in rows if row and row[0]}


def skip_downloaded_videos(urls: list[str]) -> list[str]:
    """Drop per-video links whose id the provider has already downloaded.

    Matches the operator's ask: a profile re-run, or a batch that repeats a
    link, skips the videos already held rather than fetching them again. Only
    ``/video/`` links are matched by id; anything else passes through untouched.
    """
    done = downloaded_aweme_ids()
    if not done:
        return urls
    kept: list[str] = []
    skipped = 0
    for url in urls:
        video_id = _video_id(url)
        if video_id and video_id in done:
            skipped += 1
            continue
        kept.append(url)
    if skipped:
        print(f"Skipping {skipped} video(s) already downloaded.", file=sys.stderr)
    return kept


# A profile URL is left as-is for the provider, which fetches its first page
# (~20 videos) through the signed API - the reliable ceiling Douyin serves an
# anonymous caller. The browser enumerator that once tried to scroll past it was
# retired: Douyin's anti-bot caps or challenges any automated browser, so it
# only ever harvested an unpredictable 8-26 and added a popup window mid-run for
# no gain. Whole profiles need a signed-in account (see docs/third-party).


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

    if not args.dry_run:
        # Incremental is the "skip what we already have" intent, so honour it by
        # dropping per-video links already in the provider database. Profile URLs
        # pass straight to the provider, which fetches the first page (~20) - the
        # reliable anonymous ceiling Douyin serves without a signed-in account.
        if args.incremental:
            requested_videos = sum(1 for url in urls if _video_id(url))
            urls = skip_downloaded_videos(urls)
            if requested_videos and not urls:
                print("Everything requested is already downloaded; nothing new to fetch.")
                return 0

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
            # Stated, not guessed at. This also happens when a post has been
            # removed, when the link names a topic or a page rather than a
            # video, and when everything asked for is already held. Naming
            # cookies here made every one of those read as an expired session,
            # and the caller upstream believed it.
            print(
                "Download finished without saving any media files. "
                "The reason is above, if the provider gave one.",
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
    batch.add_argument(
        "--covers",
        action="store_true",
        help="Also fetch each post's cover image.",
    )
    batch.add_argument(
        "--music",
        action="store_true",
        help="Also fetch each post's audio track.",
    )
    batch.add_argument("--incremental", action="store_true")
    batch.add_argument("--verbose", action="store_true")
    batch.add_argument("--dry-run", action="store_true")

    hot = subparsers.add_parser(
        "trending", help="Read Douyin's hot-search board as JSON."
    )
    hot.add_argument("--limit", type=non_negative_integer, default=50)
    hot.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    hot.add_argument("--proxy", default="")
    hot.add_argument("--timeout", type=positive_integer, default=180)
    hot.set_defaults(handler=trending)

    topics = subparsers.add_parser(
        "topic", help="Find downloadable videos posted under a term."
    )
    topics.add_argument("term")
    topics.add_argument("--limit", type=positive_integer, default=20)
    topics.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    topics.add_argument("--proxy", default="")
    topics.add_argument("--timeout", type=positive_integer, default=180)
    topics.set_defaults(handler=topic)

    hot_topics = subparsers.add_parser(
        "hot",
        help="List a hot topic's videos by its board sentence_id. No account needed.",
    )
    hot_topics.add_argument("sentence_id")
    hot_topics.add_argument("--limit", type=positive_integer, default=10)
    hot_topics.add_argument("--timeout", type=positive_integer, default=180)
    hot_topics.set_defaults(handler=hot_topic)
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
    if args.command == "hot":
        return hot_topic(args)
    if args.command == "topic":
        return topic(args)
    if args.command == "trending":
        return trending(args)
    return batch_download(args)


if __name__ == "__main__":
    raise SystemExit(main())
