"""Durable, workspace-scoped media acquisition through Douyin Downloader."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations import longpath
from trendrelay_api.jobs import (
    cancellation_requested,
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    heartbeat_job,
    list_job_records,
    merge_running_result,
    request_job_cancellation,
)
from trendrelay_api.models import DurableJob
from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

JOB_KIND = "douyin_download"
JOB_SESSION_FACTORY = SessionFactory
OUTPUT_ROOT = PROJECT_ROOT / ".data" / "downloads" / "douyin"
#: Where TikTok's media lands. Douyin keeps `OUTPUT_ROOT`, the folder it has
#: always used, so a job queued before services existed still resolves to the
#: path recorded in its payload.
TIKTOK_OUTPUT_ROOT = PROJECT_ROOT / ".data" / "downloads" / "tiktok"


def service_roots() -> dict[str, Path]:
    """Every service's download folder, read when asked rather than at import.

    A function, not a dictionary built once: `OUTPUT_ROOT` is a module global
    that tests redirect to a temporary folder, and a dictionary literal would
    have captured the real path at import time and quietly ignored them.
    """
    return {"douyin": OUTPUT_ROOT, "tiktok": TIKTOK_OUTPUT_ROOT}


def output_root_for(service: str) -> Path:
    return service_roots().get(service, OUTPUT_ROOT)
DOWNLOAD_SCRIPT = PROJECT_ROOT / "scripts" / "douyin.py"
# One source at a time, so a slow or blocked link cannot stall the whole batch.
SOURCE_TIMEOUT_SECONDS = 1800
COOKIE_FILE = PROJECT_ROOT / ".data" / "douyin" / "cookies.json"
CONNECTION_STATUS_FILE = PROJECT_ROOT / ".data" / "douyin" / "connection-status.json"
CONNECTION_LOG_FILE = PROJECT_ROOT / ".data" / "douyin" / "connection.log"
CONNECTION_PROCESS: subprocess.Popen[str] | None = None
CONNECTION_LOCK = threading.Lock()
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
VIDEO_SUFFIXES = {".mkv", ".mov", ".mp4", ".webm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav"}
REQUIRED_COOKIE_KEYS = ("ttwid", "odin_tt", "passport_csrf_token")
COOKIE_ENV_KEYS = (
    ("msToken", "DOUYIN_MS_TOKEN"),
    ("ttwid", "DOUYIN_TTWID"),
    ("odin_tt", "DOUYIN_ODIN_TT"),
    ("passport_csrf_token", "DOUYIN_PASSPORT_CSRF_TOKEN"),
    ("sid_guard", "DOUYIN_SID_GUARD"),
)
SUPPORTED_CONTENT_PATHS = ("/video/", "/note/", "/user/", "/mix/", "/music/")

#: The provider's own dedupe database, which records every fetched post with
#: its author - the source for "how many of this profile do we hold".
DY_DATABASE = PROJECT_ROOT / ".data" / "douyin" / "dy_downloader.db"

#: How long the declared-total lookup may take for a whole batch of profile
#: URLs. One resolve plus one profile read each; generous, not open-ended.
PROFILE_STATS_TIMEOUT_SECONDS = 240

#: What a capped profile fetch means, said on the job rather than left to be
#: discovered from the count. Kept neutral and short: the history of when
#: Douyin changed this behaviour lives in docs/third-party, not in product
#: copy. Every run still takes everything the session is offered, so deeper
#: listings are fetched automatically whenever Douyin serves them.
ANONYMOUS_PROFILE_NOTE = (
    "This profile download is incomplete: Douyin returned only part of the "
    "profile to this session. Use Fetch missing to check for more available posts."
)


def _session_signed_in() -> bool:
    """Whether the saved Douyin session belongs to an actual account.

    `sessionid` is the one cookie only a login sets - the same marker the
    capture script waits for - so its absence is what makes a session
    anonymous, however complete its other cookies are.
    """
    try:
        cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(cookies, dict) and cookies.get("sessionid"))


def _is_profile_source(url: str) -> bool:
    """Whether this link is a whole channel rather than one post.

    Two spellings, because the two services spell it differently: Douyin puts
    a profile under `/user/`, TikTok under `/@handle`. Everything that follows
    from "is this a profile" - the coverage badge, the shortfall flag, the note
    about an incomplete listing - was answering no for every TikTok channel.
    """
    if "/user/" in urlparse(url).path.lower():
        return True
    from trendrelay_api.integrations import tiktok  # noqa: PLC0415 - cycle

    return tiktok.is_profile_source(url)


def _profile_stats(urls: list[str]) -> list[dict[str, Any]]:
    """Each URL's declared post total, from the provider's signed client.

    A read, not a download: `profile-stats` resolves short links, and for
    profile URLs asks the user-info endpoint - which answers a signed-out
    session - for the author's declared 作品 count. Failures degrade to an
    empty list rather than failing the job: coverage is an annotation on a
    download, never a precondition for one.
    """
    try:
        completed = subprocess.run(
            [
                sys.executable, str(DOWNLOAD_SCRIPT), "profile-stats",
                *urls, "--timeout", str(PROFILE_STATS_TIMEOUT_SECONDS - 30),
            ],
            cwd=PROJECT_ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=PROFILE_STATS_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            return []
        parsed = json.loads(completed.stdout.strip() or "[]")
        return parsed if isinstance(parsed, list) else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _held_counts(sec_uids: list[str]) -> dict[str, int]:
    """How many posts of each author the provider's database already holds.

    Read from a backup copy: the database is SQLite in WAL mode and may be
    mid-write by the download itself, and the backup API is the one read that
    sees a consistent snapshot.
    """
    if not sec_uids or not DY_DATABASE.is_file():
        return {}
    import sqlite3  # noqa: PLC0415 - the one function that reads this file
    import tempfile  # noqa: PLC0415

    snapshot = Path(tempfile.gettempdir()) / f"dy_coverage_{os.getpid()}.db"
    try:
        source = sqlite3.connect(str(DY_DATABASE))
        try:
            copy = sqlite3.connect(str(snapshot))
            try:
                source.backup(copy)
                marks = ",".join("?" for _ in sec_uids)
                rows = copy.execute(
                    "select author_sec_uid, count(*) from aweme "
                    f"where author_sec_uid in ({marks}) group by author_sec_uid",
                    sec_uids,
                ).fetchall()
                return {str(uid): int(count) for uid, count in rows}
            finally:
                copy.close()
        finally:
            source.close()
    except sqlite3.Error:
        return {}
    finally:
        with contextlib.suppress(OSError):
            snapshot.unlink()


def _coverage_stats(
    urls: list[str], *, service: str = "douyin", workspace_id: str = ""
) -> list[dict[str, Any]]:
    """Per-source coverage: what the profile declares vs what we hold.

    Both services answer in the same shape because one badge draws both; where
    the two halves come from is each service's own business, so the TikTok
    answer is built by the TikTok module rather than by a branch in here.
    """
    if service == "tiktok":
        from trendrelay_api.integrations import tiktok  # noqa: PLC0415 - cycle

        return tiktok.coverage_stats(urls, workspace_id=workspace_id)
    if not any(_is_profile_source(url) or urlparse(url).hostname == "v.douyin.com"
               for url in urls):
        return []
    stats = _profile_stats(urls)
    sec_uids = [str(s["sec_uid"]) for s in stats if s.get("sec_uid")]
    held = _held_counts(sec_uids)
    for entry in stats:
        uid = str(entry.get("sec_uid") or "")
        if uid:
            entry["held"] = held.get(uid, 0)
    return stats


def _coverage_pct(held: int, total: int) -> str:
    """Whole-number share, honest at the edges.

    100% only when actually complete and 0% only when actually empty; anything
    in between is clamped to 1-99 so rounding never overstates either end.
    """
    if total <= 0 or held <= 0:
        return "0%"
    if held >= total:
        return "100%"
    return f"{min(99, max(1, round(held * 100 / total)))}%"


def _coverage_line(stats: list[dict[str, Any]]) -> str:
    """One compact sentence of profile coverage, or nothing to say."""
    profiles = [
        entry for entry in stats
        if entry.get("kind") == "profile" and entry.get("declared_total")
    ]
    if not profiles:
        return ""
    parts = [
        f"{entry.get('nickname') or 'profile'} "
        f"{int(entry.get('held') or 0)}/{int(entry['declared_total'])} "
        f"({_coverage_pct(int(entry.get('held') or 0), int(entry['declared_total']))})"
        for entry in profiles[:3]
    ]
    rest = len(profiles) - 3
    listing = ", ".join(parts) + (f", and {rest} more" if rest > 0 else "")
    if len(profiles) == 1:
        return f"Profile coverage: {listing}."
    held_sum = sum(int(entry.get("held") or 0) for entry in profiles)
    total_sum = sum(int(entry["declared_total"]) for entry in profiles)
    return (
        f"Profile coverage: {held_sum}/{total_sum} posts held "
        f"({_coverage_pct(held_sum, total_sum)}) - {listing}."
    )


def _supported_source_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if host == "v.douyin.com":
        return parsed.path not in {"", "/"}
    if not (
        host == "douyin.com"
        or host.endswith(".douyin.com")
        or host == "iesdouyin.com"
        or host.endswith(".iesdouyin.com")
    ):
        return False
    return any(marker in parsed.path.lower() for marker in SUPPORTED_CONTENT_PATHS)


class DownloadRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=80)
    # Room for a whole profile's worth of per-video links pasted at once - the
    # reliable no-login way to fetch a large profile is to load it in a real
    # browser, copy every video link, and hand them all here to download one by
    # one (which an anonymous session is allowed to do).
    urls: list[str] = Field(min_length=1, max_length=400)
    mode: Literal["post", "like", "mix", "music"] = "post"
    limit: int = Field(default=0, ge=0, le=100)
    incremental: bool = True
    #: Which kinds to fetch. The video itself is always fetched, since a Douyin
    #: post is a video; the cover image and the audio track are extras the
    #: downloader only requests when asked, so leaving one out saves the
    #: bandwidth rather than downloading and discarding it. The default is video
    #: alone: the extras are two more files per post and a library full of
    #: covers is what most of this workspace turned out to be.
    media_kinds: list[Literal["video", "image", "audio"]] = Field(
        default_factory=lambda: ["video"], min_length=1, max_length=3
    )
    confirm_external_action: bool = False

    @field_validator("media_kinds")
    @classmethod
    def video_is_always_fetched(
        cls, value: list[str]
    ) -> list[str]:
        # Refusing video would leave nothing to download from a video platform,
        # so it is stated rather than silently added back.
        if "video" not in value:
            raise ValueError(
                "Video is always downloaded; choose which extras to add alongside it."
            )
        # Deduped but not reordered: a default is not passed through a
        # validator, so sorting here would give the same choice two orderings.
        return list(dict.fromkeys(value))

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("workspace_id contains unsupported characters")
        return value

    @field_validator("urls")
    @classmethod
    def valid_urls(cls, values: list[str]) -> list[str]:
        """Every link must be a source some supported service claims.

        Was Douyin-or-nothing, which refused a TikTok link before anything
        looking at services got to see it. The check is now "does any provider
        claim this", and which provider is settled afterwards - by the caller,
        which is also where a batch spanning two of them is refused.
        """
        from trendrelay_api.integrations.download_providers import (  # noqa: PLC0415
            PROVIDERS,
            provider_for,
        )

        unique: list[str] = []
        for value in values:
            url = value.strip()
            # The legacy Douyin check stays as an accepted shape of its own, so
            # nothing that worked before this stops working now.
            if not (_supported_source_url(url) or provider_for(url)):
                raise ValueError(
                    "Use a link to a specific video, profile or collection from "
                    + " or ".join(provider.label for provider in PROVIDERS)
                    + ". Discovery pages are not downloadable."
                )
            if url not in unique:
                unique.append(url)
        return unique


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_file(path: Path) -> bool:
    """`is_file`, able to see past 260 characters.

    The plain call answers False for an over-long path. That is not "no", it is
    "I could not look", and reading it as no is how over-long downloads became
    invisible: never counted as progress, never scanned, never shortened.
    """
    return os.path.isfile(longpath.extended(path))


def _size_of(path: Path) -> int:
    return os.path.getsize(longpath.extended(path))


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with open(longpath.extended(path), "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_cookie_header(header: str) -> dict[str, str]:
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


def _load_cookie_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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


def cookie_status() -> dict[str, Any]:
    header = os.getenv("DOUYIN_COOKIE", "").strip()
    cookies: dict[str, str] = {}
    source = "none"
    if header:
        cookies = _parse_cookie_header(header)
        source = "DOUYIN_COOKIE"
    if not cookies:
        cookies = {
            cookie_key: os.getenv(env_key, "").strip()
            for cookie_key, env_key in COOKIE_ENV_KEYS
            if os.getenv(env_key, "").strip()
        }
        if cookies:
            source = "DOUYIN_* env"
    if not cookies:
        cookies = _load_cookie_file(COOKIE_FILE)
        if cookies:
            source = str(COOKIE_FILE)
    missing = [key for key in REQUIRED_COOKIE_KEYS if not cookies.get(key)]
    return {
        "ready": not missing,
        # `sessionid` exists only after an actual login. Anonymous cookies can
        # fetch single links and Douyin's first profile window, but current
        # cursored profile responses are refused server-side.
        "signed_in": bool(cookies.get("sessionid")),
        "source": source,
        "missing": missing,
        "cookie_file": str(COOKIE_FILE),
    }


#: Words the provider uses when a request was refused for who we are, rather
#: than for what we asked. Only these justify telling an operator their session
#: is finished.
AUTH_FAILURE_MARKERS = (
    "cookie",
    "login",
    "sign in",
    "unauthor",
    "forbidden",
    "403",
    "401",
    "risk",
    "verify",
    "captcha",
    "anti-bot",
    "slider",
)


def _looks_like_auth_failure(detail: str) -> bool:
    """Whether the provider's own words point at the session rather than the link."""
    text = (detail or "").casefold()
    return any(marker in text for marker in AUTH_FAILURE_MARKERS)


def _write_connection_status(state: str, message: str) -> dict[str, str]:
    payload = {"state": state, "message": message, "updated_at": _now()}
    CONNECTION_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONNECTION_STATUS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(CONNECTION_STATUS_FILE)
    return payload


def connection_status() -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    try:
        loaded = json.loads(CONNECTION_STATUS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    except (OSError, ValueError):
        pass

    process = CONNECTION_PROCESS
    if (
        process is not None
        and process.poll() is None
        and payload
        and payload.get("state")
        in {
            "starting",
            "installing",
            "opening_browser",
            "waiting_for_login",
        }
    ):
        return {
            "state": str(payload["state"]),
            "message": str(payload.get("message", "Connect Douyin to continue.")),
            "updated_at": payload.get("updated_at"),
        }

    cookies = cookie_status()
    if payload and payload.get("state") == "refresh_required":
        return {
            "state": "refresh_required",
            "message": str(payload.get("message", "Refresh the Douyin session.")),
            "updated_at": payload.get("updated_at"),
        }
    if cookies["ready"]:
        return {
            "state": "connected",
            # A line, and the reasoning behind it kept separate.
            #
            # The short one is what is true and what to do about it. The long
            # one answers "but I just tried it signed out and Douyin stopped
            # me", which is a real and reasonable objection - and a paragraph
            # nobody asked for, sitting permanently in a callout, is a paragraph
            # that stops being read.
            "message": (
                "Signed in. Everything is available."
                if cookies.get("signed_in")
                else "Single links and recent profile posts download. Sign in for full profiles."
            ),
            "detail": (
                None
                if cookies.get("signed_in")
                else "Douyin currently refuses deeper profile pages to signed-out "
                "sessions. TrendRelay keeps the recent files it can fetch, then "
                "marks the batch incomplete instead of calling it fully downloaded."
            ),
            "updated_at": None,
        }
    if payload is None:
        return {
            "state": "disconnected",
            "message": "Connect Douyin to capture login cookies.",
            "updated_at": None,
        }
    if (
        process is not None
        and process.poll() is not None
        and payload.get("state")
        in {"starting", "installing", "opening_browser", "waiting_for_login"}
    ):
        return _write_connection_status(
            "failed", "Douyin connection process exited before login completed."
        )
    return {
        "state": str(payload.get("state", "disconnected")),
        "message": str(payload.get("message", "Connect Douyin to continue.")),
        "updated_at": payload.get("updated_at"),
    }


def start_connection(force_refresh: bool = False, *, require_login: bool = False) -> dict[str, Any]:
    global CONNECTION_PROCESS
    with CONNECTION_LOCK:
        current = connection_status()
        if current["state"] in {
            "starting",
            "installing",
            "opening_browser",
            "waiting_for_login",
        }:
            return current
        if cookie_status()["ready"] and not force_refresh and not require_login:
            return connection_status()

        _write_connection_status("starting", "Preparing the isolated Douyin login browser.")
        CONNECTION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with CONNECTION_LOG_FILE.open("a", encoding="utf-8") as log:
            CONNECTION_PROCESS = subprocess.Popen(
                [sys.executable, str(DOWNLOAD_SCRIPT), "connect",
                 *(["--require-login"] if require_login else [])],
                cwd=PROJECT_ROOT,
                env=_environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        return connection_status()


def provider_status() -> dict[str, Any]:
    tool = next(item for item in list_tools() if item["id"] == "douyin-downloader")
    cookies = cookie_status()
    return {
        "installed": tool["installed"],
        "active": tool["active"],
        "revision": tool["revision"],
        "output_root": str(OUTPUT_ROOT),
        "cookies_ready": cookies["ready"],
        "cookies": cookies,
        "connection": connection_status(),
    }


def source_group_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep manual top-ups attached to the original profile/source batch."""
    return payload.get("source_group_request") or payload.get("request") or {}


class CapturedLinksRequest(BaseModel):
    # Far above any real profile: a full capture of an 887-post page must not
    # be refused for outgrowing one download batch. The 400-per-job limit is
    # DownloadRequest's, and the import honours it by splitting instead.
    urls: list[str] = Field(min_length=1, max_length=4000)
    confirm_external_action: bool = False

    @field_validator("urls")
    @classmethod
    def canonical_video_urls(cls, values: list[str]) -> list[str]:
        urls = []
        for value in values:
            # Accept only direct video links a provider's bookmarklet can
            # produce. Never pass credentials, arbitrary hosts or redirect
            # endpoints onward. TikTok calls these posts ``video`` (and
            # ``photo`` for carousels), while Douyin uses ``video``.
            token = value.strip()
            douyin = re.fullmatch(
                r"https://(?:www\.)?douyin\.com/video/([0-9]{6,})(?:[?#][^\s]*)?",
                token,
            )
            tiktok = re.fullmatch(
                r"https://(?:www\.)?tiktok\.com/@[\w.-]+/(?:video|photo)/([0-9]{6,})(?:[?#][^\s]*)?",
                token,
                re.IGNORECASE,
            )
            if len(token) > 2048 or (not douyin and not tiktok):
                raise ValueError(
                    "Paste direct Douyin or TikTok video links only, one per line."
                )
            if douyin:
                urls.append(f"https://www.douyin.com/video/{douyin.group(1)}")
            else:
                # Keep the creator handle and the post kind: yt-dlp uses both
                # to route TikTok posts correctly.
                parsed = urlparse(token)
                urls.append(f"https://www.tiktok.com{parsed.path}")
        return list(dict.fromkeys(urls))


def import_captured_links(
    job_id: str, workspace_id: str, body: CapturedLinksRequest, actor_user_id: str
) -> dict[str, Any]:
    """Queue captured links against their parent batch, split as needed.

    A download job takes at most 400 urls, but a full profile capture is as
    long as the profile - so an oversize import becomes several child jobs,
    each carrying the parent's source group, and the grouped Downloads row
    folds them together like any other re-run. The caller gets the first job
    back, with its siblings named, so nothing changes shape for a small
    import.
    """
    if not body.confirm_external_action:
        raise PermissionError("Importing links requires confirmation.")
    with JOB_SESSION_FACTORY() as session:
        parent = session.get(DurableJob, job_id)
        if not parent or parent.workspace_key != workspace_id or parent.kind != JOB_KIND:
            raise FileNotFoundError(job_id)
        parent_payload = dict(parent.payload or {})
        original = dict(source_group_request(parent_payload))
        service = str(parent_payload.get("service") or "douyin")
    if service not in {"douyin", "tiktok"}:
        service = "douyin"
    from trendrelay_api.integrations import download_providers as registry
    provider, matched, _ignored = registry.detect(list(body.urls))
    if provider is None or provider.id != service or len(matched) != len(body.urls):
        raise ValueError(
            f"Imported links must belong to the {service.title()} batch and be direct video links."
        )
    batch_limit = 400
    jobs = []
    for start in range(0, len(body.urls), batch_limit):
        request = DownloadRequest(
            workspace_id=workspace_id, urls=body.urls[start : start + batch_limit],
            mode="post", limit=0,
            media_kinds=original.get("media_kinds") or ["video"],
            incremental=True, confirm_external_action=True,
        )
        kwargs = {"source_group": original, "parent_job_id": job_id}
        if service != "douyin":
            kwargs["service"] = service
        jobs.append(create_download_job(request, actor_user_id, **kwargs))
    first = dict(jobs[0])
    if len(jobs) > 1:
        first["sibling_jobs"] = [job["id"] for job in jobs[1:]]
        first["batch_count"] = len(jobs)
    return first


def create_download_job(
    request: DownloadRequest, actor_user_id: str | None = None,
    *, source_group: dict[str, Any] | None = None, parent_job_id: str | None = None,
    service: str = "douyin",
) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Download requires explicit confirmation.")
    if service == "tiktok":
        from trendrelay_api.integrations import tiktok  # noqa: PLC0415

        status = tiktok.provider_status()
        if not status["installed"]:
            raise RuntimeError(status["reason"])
        if not status["ready"]:
            # Its own sentence. TikTok reads a link happily and then refuses
            # the fetch, so "the download failed" would send somebody to look
            # at their link rather than at the install.
            raise RuntimeError(status["reason"])
    else:
        status = provider_status()
        if not status["installed"] or not status["active"]:
            raise RuntimeError("Install and activate Douyin Downloader before fetching media.")
        if not status["cookies_ready"]:
            raise RuntimeError(
                "Douyin cookies are missing or incomplete. "
                "Use Connect Douyin in the app or set DOUYIN_COOKIE / "
                "DOUYIN_TTWID, DOUYIN_ODIN_TT, and DOUYIN_PASSPORT_CSRF_TOKEN, then retry."
            )
    nonce = f"{service}:{request.workspace_id}:{_now()}:{request.model_dump_json()}"
    job_id = f"download_{hashlib.sha256(nonce.encode()).hexdigest()[:16]}"
    output_root = (output_root_for(service) / request.workspace_id / job_id).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": job_id,
        "workspace_id": request.workspace_id,
        "actor_user_id": actor_user_id,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        # Which service this job is for, and which tool fetches it. The
        # service is what the runner dispatches on and what the interface
        # labels the job with; the tool is what to blame when it breaks.
        "service": service,
        "provider": {
            "id": "yt-dlp" if service == "tiktok" else "douyin-downloader",
            "revision": status["revision"],
        },
        "request": request.model_dump(exclude={"confirm_external_action"}),
        "output_root": str(output_root),
    }
    if source_group:
        payload["source_group_request"] = source_group
        payload["parent_job_id"] = parent_job_id
    return create_job_record(
        job_id,
        request.workspace_id,
        JOB_KIND,
        payload,
        max_attempts=2,
        factory=JOB_SESSION_FACTORY,
    )


def _environment() -> dict[str, str]:
    allowed = {name: value for name, value in os.environ.items() if name.startswith("DOUYIN_")}
    for name in (
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "PATH",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    ):
        if os.environ.get(name):
            allowed[name] = os.environ[name]
    allowed["PYTHONIOENCODING"] = "utf-8"
    allowed["PYTHONUTF8"] = "1"
    return allowed


def _clean_metadata_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _douyin_artifact_metadata(path: Path, output_root: Path | None) -> dict[str, str]:
    """Read downloader-owned sidecar metadata without making another provider call."""
    metadata: dict[str, str] = {}
    candidates = sorted(path.parent.glob("*_data.json"))
    matching = [
        candidate
        for candidate in candidates
        if path.stem == candidate.stem.removesuffix("_data")
        or path.stem.startswith(candidate.stem.removesuffix("_data") + "_")
    ]
    sidecar = matching[0] if matching else candidates[0] if len(candidates) == 1 else None
    if sidecar:
        try:
            if sidecar.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Douyin metadata sidecar is too large")
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Douyin metadata sidecar must contain an object")
            author = payload.get("author")
            creator = _clean_metadata_text(
                author.get("nickname") if isinstance(author, dict) else None,
                limit=200,
            )
            caption = _clean_metadata_text(
                payload.get("desc") or payload.get("item_title"), limit=5000
            )
            share_url = _clean_metadata_text(payload.get("share_url"), limit=2000)
            sec_uid = _clean_metadata_text(
                author.get("sec_uid") if isinstance(author, dict) else None, limit=200
            )
            if sec_uid and re.fullmatch(r"[A-Za-z0-9_-]{16,120}", sec_uid):
                creator_url = f"https://www.douyin.com/user/{sec_uid}"
                if _supported_source_url(creator_url):
                    metadata["creator_url"] = creator_url
            if creator:
                metadata["creator"] = creator
            if caption:
                metadata["caption"] = caption
            if share_url and _supported_source_url(share_url):
                metadata["source_url"] = share_url
            created_at = payload.get("create_time")
            if isinstance(created_at, (int, float)) and created_at > 0:
                metadata["published_at"] = datetime.fromtimestamp(
                    created_at, tz=UTC
                ).isoformat()
        except (OSError, OverflowError, TypeError, ValueError):
            pass

    if "creator" not in metadata and output_root:
        try:
            relative = path.resolve().relative_to(output_root.resolve())
            if len(relative.parts) >= 3 and relative.parts[1].lower() in {
                "post",
                "like",
                "collect",
                "mix",
                "music",
            }:
                creator = _clean_metadata_text(relative.parts[0], limit=200)
                if creator:
                    metadata["creator"] = creator
        except ValueError:
            pass
    return metadata


def _compact_library_job(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in ("id", "status", "duplicate", "asset_id", "sha256")
        if item.get(key) is not None
    }


def _requested_media_kinds(request: dict[str, Any]) -> set[str]:
    """Return the media kinds this job may expose to the Library.

    Old queued jobs predate ``media_kinds`` and intentionally retain their
    original all-media behaviour. New jobs always carry the explicit choice.
    """
    raw = request.get("media_kinds")
    return set(raw) if isinstance(raw, list) and raw else {"video", "image", "audio"}


def _media_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return None


def _scan_new_media(
    output_root: Path,
    seen_paths: set[str],
    media_kinds: set[str] | None = None,
) -> list[Path]:
    """Record media files this job has not seen yet.

    Deliberately cheap: it walks the folder and nothing more, so the next
    source can start downloading without waiting on hashing. Callers must only
    invoke it from the download thread, which keeps ``seen_paths`` single-owner
    and makes the de-duplication exact without a lock.
    """
    discovered: list[Path] = []
    for path in sorted(output_root.rglob("*")):
        # `is_file()` through the prefix: on a path past 260 the plain call
        # answers False, meaning "I cannot look", not "not a file". Reading it
        # as "no" skipped the over-long downloads entirely - so the files that
        # most needed shortening were the ones never seen.
        kind = _media_kind(path)
        if kind is None:
            continue
        if not _is_file(path):
            continue
        resolved = str(path.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        # A provider can emit a gallery as the post's primary media even when
        # optional covers are disabled. The operator's media-kind selection is
        # authoritative at this boundary: such a file is neither imported nor
        # repeatedly rediscovered on every source scan.
        if media_kinds is not None and kind not in media_kinds:
            continue
        discovered.append(path)

    # The provider writes through the extended-length prefix, so a file may
    # exist at a path Windows will not open without it. Brought back under the
    # limit here, once, at the only point where every new file is in hand: from
    # this line on the rest of the system - ffmpeg, OpenCV, the library, the
    # database - deals only in ordinary paths.
    discovered, _renamed = longpath.shorten_all(discovered, output_root)
    # Marked under the name it now has. The pre-rename path is already in the
    # set and stays there harmlessly - nothing is at it any more - but without
    # this the next scan would find the renamed file and call it new.
    for path in discovered:
        seen_paths.add(str(path.resolve()))
    return discovered


def _describe_media(paths: list[Path]) -> list[dict[str, Any]]:
    """Fingerprint finished files. Slow, so it runs off the download path."""
    described: list[dict[str, Any]] = []
    for path in paths:
        described.append(
            {
                "path": str(path),
                "name": path.name,
                "size_bytes": _size_of(path),
                "sha256": _fingerprint(path),
            }
        )
    return described


def _related_output_roots(payload: dict[str, Any]) -> list[Path]:
    """Earlier run folders for the exact same source set.

    A provider run can finish writing media and fail before its artifact
    manifest is committed. The provider database then correctly skips those
    files on retry, which used to strand them outside Library forever. A retry
    therefore adopts retained files from its sibling runs before fetching.
    """
    request = source_group_request(payload)
    signature = tuple(sorted(str(url) for url in request.get("urls") or []))
    if not signature:
        return []
    current_id = str(payload.get("id") or "")
    workspace_id = str(payload.get("workspace_id") or "")
    roots: list[Path] = []
    with JOB_SESSION_FACTORY() as session:
        jobs = session.scalars(
            select(DurableJob).where(
                DurableJob.workspace_key == workspace_id,
                DurableJob.kind == JOB_KIND,
                DurableJob.id != current_id,
            )
        ).all()
        for item in jobs:
            other_payload = item.payload or {}
            other_request = source_group_request(other_payload)
            other_signature = tuple(
                sorted(str(url) for url in other_request.get("urls") or [])
            )
            if other_signature != signature:
                continue
            root = _job_output_root(
                {"workspace_id": item.workspace_key, "payload": other_payload}
            )
            if root is not None and root.is_dir():
                roots.append(root)
    return roots


def _fetch_source(
    payload: dict[str, Any], url: str, output_root: Path, request: dict[str, Any]
) -> tuple[int, str]:
    """Fetch one source with whichever downloader this job's service uses.

    The only step that differs between services. Everything around it - walking
    the output folder, fingerprinting, de-duplicating, handing files to the
    Library, stopping between sources when a cancel arrives - is the same work
    whoever the media came from, so it is shared rather than written once per
    service.

    Imported here rather than at module scope: the provider table imports this
    module to resolve the Douyin fetch, and naming it at the top would close
    the circle.
    """
    if payload.get("service") == "tiktok":
        from trendrelay_api.integrations.tiktok import (  # noqa: PLC0415
            download_source as tiktok_download,
        )

        return tiktok_download(url, output_root, request)
    return _download_source(url, output_root, request)


def _download_source(url: str, output_root: Path, request: dict[str, Any]) -> tuple[int, str]:
    """Run the pinned downloader for a single source and return (code, detail)."""
    command = [
        sys.executable,
        str(DOWNLOAD_SCRIPT),
        "batch",
        url,
        "--output",
        str(output_root),
        "--mode",
        request["mode"],
        "--limit",
        str(request["limit"]),
    ]
    # A payload without the field predates it and was queued when everything was
    # fetched, so it keeps that behaviour rather than being changed after the
    # fact. New requests default to video alone.
    kinds = request.get("media_kinds") or ["video", "image", "audio"]
    if "image" in kinds:
        command.append("--covers")
    if "audio" in kinds:
        command.append("--music")
    if request["incremental"] and not (
        _is_profile_source(url) and not _session_signed_in()
    ):
        # Incremental keeps only items newer than the newest already
        # downloaded. For a signed-out profile fetch the listing is one fixed
        # window - the newest ~40, nothing deeper - so that filter silently
        # drops the *older* half of the window on exactly the run that could
        # have fetched it (a 20-post first grab left the next 24 invisible to
        # every later incremental run). The provider already skips media it
        # holds, by aweme id and by file, so a full pass costs one listing.
        command.append("--incremental")
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=SOURCE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # A profile large enough to outrun the half-hour cap used to escape as
        # `TimeoutExpired`, which only the job's outer catch-all saw: the job
        # failed with Python's own wording and the whole command line in it, and
        # the files already on disk were never scanned into the Library because
        # the scan runs after this returns.
        #
        # Reported as a source error instead. The loop then does what it does
        # for any other failed source - keeps what downloaded, ingests it, and
        # moves on - so a run that ran out of time is a resumable stop rather
        # than a crash, and `--incremental` means resuming starts where this
        # left off.
        minutes = SOURCE_TIMEOUT_SECONDS // 60
        return 1, (
            f"This source ran past the {minutes}-minute limit for one fetch. "
            "Everything downloaded so far is kept - resume the job to carry on "
            "from where it stopped."
        )
    return completed.returncode, (completed.stderr or completed.stdout or "").strip()


def _job_output_root(job: dict[str, Any]) -> Path | None:
    payload = job.get("payload") or {}
    workspace_id = str(
        job.get("workspace_id") or payload.get("workspace_id") or ""
    )
    raw_path = payload.get("output_root")
    if not workspace_id or not raw_path:
        return None
    output_root = Path(str(raw_path)).resolve()
    # Against every service's root, not only Douyin's.
    #
    # This is a guard against a payload pointing somewhere it should not, and
    # it was written when there was one place a download could land. A TikTok
    # job lives under `downloads/tiktok/...`, so it failed the check and this
    # returned None - which reads downstream as "no files": its progress showed
    # zero however much had been fetched, and resuming refused with "No
    # completed media files are available to finish" for a folder full of them.
    #
    # The job's own service is preferred, and the rest are still accepted so a
    # payload written before `service` existed keeps resolving.
    roots = service_roots()
    service = str(payload.get("service") or "")
    candidates = [roots[service]] if service in roots else []
    candidates += [root for root in roots.values() if root not in candidates]
    for root in candidates:
        if output_root.parent == (root / workspace_id).resolve():
            return output_root
    return None


def _download_progress(job: dict[str, Any]) -> dict[str, Any]:
    output_root = _job_output_root(job)
    if output_root is None or not output_root.is_dir():
        return {
            "folder_exists": False,
            "files_downloaded": 0,
            "videos_downloaded": 0,
            "images_downloaded": 0,
            "audio_downloaded": 0,
            "bytes_downloaded": 0,
            "has_files_on_disk": False,
        }
    all_files = [path for path in output_root.rglob("*") if _is_file(path)]
    payload = job.get("payload") or {}
    request = payload.get("request") or {}
    requested = _requested_media_kinds(request)
    media_files = [path for path in all_files if _media_kind(path) in requested]
    return {
        "folder_exists": True,
        "files_downloaded": len(media_files),
        "videos_downloaded": sum(
            path.suffix.lower() in VIDEO_SUFFIXES for path in media_files
        ),
        "images_downloaded": sum(
            path.suffix.lower() in IMAGE_SUFFIXES for path in media_files
        ),
        "audio_downloaded": sum(
            path.suffix.lower() in AUDIO_SUFFIXES for path in media_files
        ),
        "bytes_downloaded": sum(_size_of(path) for path in media_files),
        "has_files_on_disk": bool(all_files),
    }


def _with_download_progress(job: dict[str, Any]) -> dict[str, Any]:
    return {
        **job,
        "progress": _download_progress(job),
        "library_progress": _library_progress(job),
    }


def _library_progress(job: dict[str, Any]) -> dict[str, int]:
    entries = (job.get("result") or {}).get("library_jobs") or []
    statuses = ("queued", "running", "succeeded", "failed", "cancelled")
    counts = {status: 0 for status in statuses}
    identifiers = [str(entry["id"]) for entry in entries if entry.get("id")]
    current_status: dict[str, str] = {}
    if identifiers:
        with JOB_SESSION_FACTORY() as session:
            for start in range(0, len(identifiers), 400):
                rows = session.execute(
                    select(DurableJob.id, DurableJob.status).where(
                        DurableJob.id.in_(identifiers[start : start + 400])
                    )
                ).all()
                current_status.update({job_id: status for job_id, status in rows})
    for entry in entries:
        status = current_status.get(
            str(entry.get("id")), str(entry.get("status") or "failed")
        )
        counts[status if status in counts else "failed"] += 1
    return {
        "total": len(entries),
        **counts,
        "active": counts["queued"] + counts["running"],
    }
def _source_platform(urls: list[str]) -> str:
    """The platform a file actually came from, read off its URLs.

    TikTok posts ride this pipeline now (yt-dlp routes them), and stamping
    them `douyin` filed them under the wrong network everywhere platform is
    read - the Library card, the detail panel, the filters. The stamp
    follows the evidence; douyin remains the default this downloader is.
    """
    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        if host == "tiktok.com" or host.endswith(".tiktok.com"):
            return "tiktok"
    return "douyin"


def _queue_library_artifacts(
    payload: dict[str, Any], artifacts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Queue library imports and report the creator profiles they came from."""
    actor = payload.get("actor_user_id")
    if not actor:
        return [], [], []
    from trendrelay_api.media_library import create_ingest_job

    source_urls = list(dict.fromkeys([
        *(payload.get("request", {}).get("urls") or []),
        *(source_group_request(payload).get("urls") or []),
    ]))
    output_root_value = payload.get("output_root")
    output_root = Path(str(output_root_value)) if output_root_value else None
    queued = []
    errors = []
    creator_urls: list[str] = []
    for artifact in artifacts:
        try:
            artifact_path = Path(artifact["path"])
            metadata = _douyin_artifact_metadata(artifact_path, output_root)
            artifact_source_url = metadata.get("source_url")
            creator_url = metadata.get("creator_url")
            if creator_url:
                creator_urls.append(creator_url)
            origin_urls = list(
                dict.fromkeys(
                    url for url in [artifact_source_url, *source_urls] if url
                )
            )
            source_url = artifact_source_url or (
                source_urls[0] if len(source_urls) == 1 else None
            )
            platform = _source_platform(origin_urls)
            queued.append(
                create_ingest_job(
                    workspace_id=payload["workspace_id"],
                    actor_user_id=actor,
                    path=artifact["path"],
                    title=artifact.get("name")
                        or ("TikTok reference" if platform == "tiktok" else "Douyin reference"),
                    source_type="douyin-download",
                    source_url=source_url,
                    platform=platform,
                    creator=metadata.get("creator"),
                    published_at=metadata.get("published_at"),
                    caption=metadata.get("caption"),
                    engagement={
                        "download_job_id": payload.get("id"),
                        "download_job_ids": [payload["id"]] if payload.get("id") else [],
                        "download_source_path": artifact["path"],
                        "origin_urls": origin_urls,
                    },
                    source_sha256=artifact.get("sha256"),
                    # One download run is one notification card, however many
                    # files it streams into the Library across its sources.
                    batch={"id": payload.get("id"), "total": 0},
                    factory=JOB_SESSION_FACTORY,
                )
            )
        except Exception as error:
            errors.append(str(error)[-500:])
    return queued, errors, list(dict.fromkeys(creator_urls))


def _remove_missing_library_assets(
    workspace_id: str, missing_hashes: set[str]
) -> list[str]:
    if not missing_hashes:
        return []
    from trendrelay_api.media_library import LIBRARY_ROOT
    from trendrelay_api.media_models import MediaAsset

    removed: list[str] = []
    directories: list[Path] = []
    workspace_root = (LIBRARY_ROOT / workspace_id).resolve()
    with JOB_SESSION_FACTORY.begin() as session:
        items = session.scalars(
            select(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.source_type == "douyin-download",
                MediaAsset.original_sha256.in_(missing_hashes),
            )
        ).all()
        for item in items:
            directory = Path(item.original_path).resolve().parent
            if directory.parent == workspace_root:
                directories.append(directory)
            removed.append(item.id)
            session.delete(item)
    for directory in directories:
        shutil.rmtree(directory, ignore_errors=True)
    return removed


def reconcile_downloads_to_library(workspace_id: str, actor_user_id: str) -> dict[str, Any]:
    """Mirror available downloads into Library and remove deleted download media."""
    queued: list[dict[str, Any]] = []
    errors: list[str] = []
    missing_hashes: set[str] = set()
    available_hashes: set[str] = set()
    scanned_downloads = 0
    for job in list_download_jobs(workspace_id, limit=200):
        result = job.get("result") or {}
        artifacts = result.get("artifacts") or []
        if job.get("status") != "succeeded" or not artifacts:
            continue
        scanned_downloads += 1
        available_artifacts = []
        for artifact in artifacts:
            digest = str(artifact.get("sha256") or "")
            if Path(str(artifact.get("path") or "")).is_file():
                available_artifacts.append(artifact)
                if digest:
                    available_hashes.add(digest)
            elif digest:
                missing_hashes.add(digest)
        payload = dict(job.get("payload") or {})
        payload["workspace_id"] = workspace_id
        payload["actor_user_id"] = actor_user_id
        batch, batch_errors, _creators = _queue_library_artifacts(payload, available_artifacts)
        queued.extend(batch)
        errors.extend(batch_errors)
    removed_asset_ids = _remove_missing_library_assets(
        workspace_id, missing_hashes - available_hashes
    )
    return {
        "scanned_downloads": scanned_downloads,
        "queued": queued,
        "errors": errors,
        "removed_asset_ids": removed_asset_ids,
    }


def run_download_job(job_id: str, worker_id: str = "douyin-worker") -> dict[str, Any]:
    claimed = claim_job(job_id, worker_id, lease_seconds=3600, factory=JOB_SESSION_FACTORY)
    payload = dict(claimed["payload"])
    try:
        output_root = Path(payload["output_root"]).resolve()
        expected_parent = (
            output_root_for(payload.get("service", "douyin")) / payload["workspace_id"]
        ).resolve()
        if output_root.parent != expected_parent:
            raise RuntimeError("Invalid download output location")
        request = payload["request"]
        service_label = "TikTok" if payload.get("service") == "tiktok" else "Douyin"

        # Every path a file can reach us by is recorded once, on the download
        # thread only, so a file already handed to the library is never
        # collected or ingested a second time.
        seen_paths: set[str] = set()
        artifacts: list[dict[str, Any]] = []
        library_jobs: list[dict[str, Any]] = []
        library_errors: list[str] = []
        source_errors: list[str] = []
        creator_urls: list[str] = []
        blocked_sources = 0
        already_complete = 0
        last_detail = ""
        cancelled = False
        seen_hashes: set[str] = set()

        Prepared = tuple[
            list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]
        ]

        def prepare(paths: list[Path]) -> Prepared:
            """Fingerprint one source's media and hand it to the library."""
            described = []
            for artifact in _describe_media(paths):
                digest = str(artifact.get("sha256") or "")
                if digest and digest in seen_hashes:
                    continue
                if digest:
                    seen_hashes.add(digest)
                described.append(artifact)
            queued, errors, creators = _queue_library_artifacts(payload, described)
            merge_running_result(
                job_id,
                worker_id,
                {
                    "library_jobs": [_compact_library_job(item) for item in queued],
                    "creator_urls": creators,
                },
                factory=JOB_SESSION_FACTORY,
            )
            return described, queued, errors, creators

        # A single worker keeps library preparation ordered and never
        # concurrent with itself, while still overlapping the next download.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-ingest") as ingest:
            pending: list[Future] = []

            requested_kinds = _requested_media_kinds(request)
            # Recover completed files from an earlier failed/retried run of the
            # same sources. Provider-level dedupe means downloading again is
            # neither necessary nor reliable; finalizing what is already on
            # disk is both faster and lossless.
            retained: list[Path] = []
            for related_root in _related_output_roots(payload):
                retained.extend(_scan_new_media(related_root, seen_paths, requested_kinds))
            if retained:
                pending.append(ingest.submit(prepare, retained))
            if payload.get("resume_from_disk"):
                adopted = _scan_new_media(output_root, seen_paths, requested_kinds)
                if adopted:
                    pending.append(ingest.submit(prepare, adopted))
            else:
                urls = list(request["urls"])
                for position, url in enumerate(urls, start=1):
                    # Stop between sources when a cancel was asked for. A source
                    # is one provider run and cannot be interrupted mid-file, so
                    # this is the finest a stop can be honoured; whatever already
                    # downloaded is kept and ingested below.
                    if cancellation_requested(job_id, factory=JOB_SESSION_FACTORY):
                        cancelled = True
                        break
                    code, detail = _fetch_source(payload, url, output_root, request)
                    last_detail = detail or last_detail
                    new_paths = _scan_new_media(output_root, seen_paths, requested_kinds)
                    if "already downloaded" in detail.lower():
                        # The skip pass found every requested video already held;
                        # this is completion, not an empty or blocked fetch.
                        already_complete += 1
                    else:
                        if code != 0 and (code == 3 or "without saving any media" in detail.lower()):
                            # An empty response is not evidence of completion.
                            # Preserve its reason even when another source saved
                            # files, so mixed batches don't silently read as done.
                            blocked_sources += 1
                        if code != 0 or not new_paths:
                            reason = detail[-500:] or "No new media was saved and completion could not be verified."
                            source_errors.append(f"Source {position} of {len(urls)}: {reason}")
                    if new_paths:
                        # Hash and register in the background; the next source
                        # starts downloading immediately.
                        pending.append(ingest.submit(prepare, new_paths))
                    try:
                        heartbeat_job(
                            job_id, worker_id, lease_seconds=3600, factory=JOB_SESSION_FACTORY
                        )
                    except PermissionError:
                        # The lease was taken from us; stop rather than double-download.
                        break

            for future in pending:
                described, queued, errors, creators = future.result()
                artifacts.extend(described)
                library_jobs.extend(queued)
                library_errors.extend(errors)
                creator_urls.extend(creators)

        if cancelled or cancellation_requested(job_id, factory=JOB_SESSION_FACTORY):
            # Stopped on request. Keep whatever finished - complete_job marks the
            # record cancelled because the flag is set - rather than failing it,
            # so the partial media stays in the library and the row reads
            # "cancelled", not "failed".
            summary = (
                f"Stopped after {len(artifacts)} media file(s)."
                if artifacts
                else "Stopped before any media was saved."
            )
            result = {
                **payload,
                "status": "cancelled",
                "updated_at": _now(),
                "completed_at": _now(),
                "artifacts": artifacts,
                "library_jobs": [_compact_library_job(item) for item in library_jobs],
                "library_errors": library_errors,
                "creator_urls": list(dict.fromkeys(creator_urls)),
                "source_errors": source_errors,
                "summary": summary,
            }
            return complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)

        if not artifacts and already_complete and not source_errors and not blocked_sources:
            # Every requested source was already downloaded. That is a finished
            # job with nothing to add, not the empty-folder failure below.
            result = {
                **payload,
                "status": "succeeded",
                "updated_at": _now(),
                "completed_at": _now(),
                "artifacts": [],
                "library_jobs": [],
                "library_errors": [],
                "creator_urls": [],
                "source_errors": [],
                "summary": "Everything requested was already downloaded.",
            }
            return complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)

        if not artifacts:
            details = source_errors or ([last_detail] if last_detail else [])
            evidence = '\n'.join(details)
            # A source that saved nothing is not proof the session is finished.
            # It is also what a removed post, a link that is not a video, and a
            # clip already held all look like. Only the provider actually saying
            # so marks the connection broken - anything else had the operator
            # re-authenticating over and over against a session that was fine.
            if payload.get("service") != "tiktok" and blocked_sources and _looks_like_auth_failure(evidence):
                _write_connection_status(
                    "refresh_required",
                    f"{service_label} rejected the saved session. Refresh the {service_label} session and retry.",
                )
                message = (
                    f"{service_label} refused the request for this session. Refresh the "
                    f"{service_label} session in TrendRelay, then retry."
                )
            elif blocked_sources:
                message = (
                    f"{service_label} returned no media for these links. The post may have been "
                    "removed, or the link may name a topic or a page rather than a "
                    "video. The saved session was not the problem."
                )
            else:
                message = (
                    f"Download finished without media files. Check the {service_label} "
                    "connection and retry."
                )
            # The provider's own words survive whichever branch runs. They were
            # dropped exactly when something unexpected happened, which is when
            # they are worth the most.
            if evidence:
                message = message + '\n' + evidence[-2500:]
            raise RuntimeError(message)

        summary = f"Fetched {len(artifacts)} media file(s)"
        if source_errors:
            summary = f"{summary}; {len(source_errors)} source(s) failed"
        # Coverage, measured after the downloads so "held" includes them: the
        # profile's declared 作品 total against what the provider's database
        # holds for that author across every run - the honest answer to "do we
        # have this profile", which one run's count alone cannot give.
        source_stats = (
            [] if payload.get("resume_from_disk")
            else _coverage_stats(
                list(source_group_request(payload).get("urls", [])),
                service=str(payload.get("service") or "douyin"),
                workspace_id=str(payload.get("workspace_id") or ""),
            )
        )
        coverage = _coverage_line(source_stats)
        if coverage:
            summary = f"{summary}. {coverage}"
        incomplete_profiles = any(
            entry.get("kind") == "profile"
            and entry.get("declared_total")
            and int(entry.get("held") or 0) < int(entry["declared_total"])
            for entry in source_stats
        )
        wall_worth_naming = incomplete_profiles or (
            not source_stats
            and any(_is_profile_source(url) for url in request.get("urls", []))
        )
        # Douyin's note, about Douyin's signed-out window. TikTok is fetched
        # by a different tool with no session of ours to be signed out of, so
        # attaching this to its summary would explain a shortfall by a cause
        # that cannot apply.
        if (
            payload.get("service") != "tiktok"
            and wall_worth_naming
            and not _session_signed_in()
        ):
            summary = f"{summary} {ANONYMOUS_PROFILE_NOTE}"
        requested_all = int(request.get("limit") or 0) == 0
        incomplete_requested_profile = requested_all and incomplete_profiles
        result = {
            **payload,
            "status": "succeeded",
            "updated_at": _now(),
            "completed_at": _now(),
            "artifacts": artifacts,
            "library_jobs": [_compact_library_job(item) for item in library_jobs],
            "library_errors": library_errors,
            "creator_urls": list(dict.fromkeys(creator_urls)),
            "source_errors": source_errors,
            "source_stats": source_stats,
            # A provider process exiting cleanly does not prove that an `All`
            # profile request reached the declared end. Preserve and import the
            # returned media, but make the shortfall first-class so the UI does
            # not present a recent anonymous window as a completed catalogue.
            "incomplete_profile": incomplete_requested_profile,
            # A short listing alone does not establish a login requirement.
            # Signed-out browsers may be offered more posts than this API session.
            "summary": summary,
        }
        return complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)
        raise


def download_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"download_[a-f0-9]{16}", job_id):
        raise ValueError("Invalid download identifier")
    return _with_download_progress(
        get_job_record(job_id, factory=JOB_SESSION_FACTORY)
    )


def cancel_download_job(job_id: str, workspace_id: str) -> dict[str, Any]:
    """Ask a queued or running download to stop.

    A queued job is cancelled at once; a running one is flagged and stops at its
    next source boundary, keeping whatever already downloaded. Scoped to the
    workspace so one cannot stop another's download by guessing an id.
    """
    if not re.fullmatch(r"download_[a-f0-9]{16}", job_id):
        raise ValueError("Invalid download identifier")
    with JOB_SESSION_FACTORY() as session:
        item = session.get(DurableJob, job_id)
        if not item or item.workspace_key != workspace_id or item.kind != JOB_KIND:
            raise FileNotFoundError(job_id)
    request_job_cancellation(job_id, factory=JOB_SESSION_FACTORY)
    return download_job(job_id)


def list_download_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return [
        _with_download_progress(job)
        for job in list_job_records(
            workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY
        )
    ]


def resume_download_job(
    job_id: str, workspace_id: str, *, from_saved_files: bool = False
) -> dict[str, Any]:
    if not re.fullmatch(r"download_[a-f0-9]{16}", job_id):
        raise ValueError("Invalid download identifier")
    timestamp = datetime.now(UTC)
    with JOB_SESSION_FACTORY.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item or item.workspace_key != workspace_id or item.kind != JOB_KIND:
            raise FileNotFoundError(job_id)
        if item.status == "running":
            raise RuntimeError("This download is already running.")
        if item.status == "succeeded":
            raise ValueError("This download has already completed.")
        progress = _download_progress(
            {"workspace_id": item.workspace_key, "payload": item.payload}
        )
        payload = dict(item.payload or {})
        request = payload.get("request") or {}
        requested_kinds = _requested_media_kinds(request)
        related_has_media = any(
            any(
                _media_kind(path) in requested_kinds and _is_file(path)
                for path in root.rglob("*")
            )
            for root in _related_output_roots(payload)
        )
        if from_saved_files and not progress["files_downloaded"] and not related_has_media:
            raise ValueError("No completed media files are available to finish.")
        payload["resume_from_disk"] = from_saved_files
        item.payload = payload
        item.status = "queued"
        item.result = None
        item.last_error = None
        item.attempt_count = 0
        item.cancellation_requested = False
        item.available_at = timestamp
        item.lease_owner = None
        item.lease_expires_at = None
        item.started_at = None
        item.completed_at = None
        item.updated_at = timestamp
    return download_job(job_id)


def clear_download_history(workspace_id: str) -> dict[str, list[str]]:
    removed: list[str] = []
    preserved_active: list[str] = []
    preserved_on_disk: list[str] = []
    empty_directories: list[Path] = []
    with JOB_SESSION_FACTORY.begin() as session:
        jobs = session.scalars(
            select(DurableJob).where(
                DurableJob.workspace_key == workspace_id,
                DurableJob.kind == JOB_KIND,
            )
        ).all()
        for item in jobs:
            if item.status in {"queued", "running"}:
                preserved_active.append(item.id)
                continue
            progress = _download_progress(
                {
                    "workspace_id": item.workspace_key,
                    "payload": item.payload,
                }
            )
            if progress["has_files_on_disk"]:
                preserved_on_disk.append(item.id)
                continue
            output_root = _job_output_root(
                {"workspace_id": item.workspace_key, "payload": item.payload}
            )
            if output_root is not None and output_root.is_dir():
                empty_directories.append(output_root)
            removed.append(item.id)
            session.delete(item)
    for directory in empty_directories:
        shutil.rmtree(directory, ignore_errors=True)
    return {
        "removed_job_ids": removed,
        "preserved_active_job_ids": preserved_active,
        "preserved_on_disk_job_ids": preserved_on_disk,
    }
