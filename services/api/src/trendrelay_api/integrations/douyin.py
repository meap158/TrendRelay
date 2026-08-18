"""Durable, workspace-scoped media acquisition through Douyin Downloader."""

from __future__ import annotations

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
        unique: list[str] = []
        for value in values:
            url = value.strip()
            if not _supported_source_url(url):
                raise ValueError(
                    "Use a specific Douyin video, profile, collection, music, "
                    "or v.douyin.com share link. Discovery pages are not downloadable."
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
        # `sessionid` exists only after an actual login. Douyin serves an
        # anonymous session one page of a profile (about 20 posts) and returns
        # empty pages after it, so the difference decides whether a profile
        # downloads whole or truncated - worth naming, not just "connected".
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
            "message": (
                "Douyin session is signed in and ready."
                if cookies.get("signed_in")
                else "Douyin session is anonymous. Single links download in "
                "full; a profile fetches its first page (about 20 videos), "
                "which is all Douyin serves an anonymous caller. A connected "
                "account fetches whole profiles and topic search."
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


def start_connection(force_refresh: bool = False) -> dict[str, Any]:
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
        if cookie_status()["ready"] and not force_refresh:
            return connection_status()

        _write_connection_status("starting", "Preparing the isolated Douyin login browser.")
        CONNECTION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with CONNECTION_LOG_FILE.open("a", encoding="utf-8") as log:
            CONNECTION_PROCESS = subprocess.Popen(
                [sys.executable, str(DOWNLOAD_SCRIPT), "connect"],
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


def create_download_job(
    request: DownloadRequest, actor_user_id: str | None = None
) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Download requires explicit confirmation.")
    status = provider_status()
    if not status["installed"] or not status["active"]:
        raise RuntimeError("Install and activate Douyin Downloader before fetching media.")
    if not status["cookies_ready"]:
        raise RuntimeError(
            "Douyin cookies are missing or incomplete. "
            "Use Connect Douyin in the app or set DOUYIN_COOKIE / "
            "DOUYIN_TTWID, DOUYIN_ODIN_TT, and DOUYIN_PASSPORT_CSRF_TOKEN, then retry."
        )
    nonce = f"{request.workspace_id}:{_now()}:{request.model_dump_json()}"
    job_id = f"download_{hashlib.sha256(nonce.encode()).hexdigest()[:16]}"
    output_root = (OUTPUT_ROOT / request.workspace_id / job_id).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": job_id,
        "workspace_id": request.workspace_id,
        "actor_user_id": actor_user_id,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "provider": {
            "id": "douyin-downloader",
            "revision": status["revision"],
        },
        "request": request.model_dump(exclude={"confirm_external_action"}),
        "output_root": str(output_root),
    }
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


def _scan_new_media(output_root: Path, seen_paths: set[str]) -> list[Path]:
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
        if path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        if not _is_file(path):
            continue
        resolved = str(path.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
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
    if request["incremental"]:
        command.append("--incremental")
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
    expected_parent = (OUTPUT_ROOT / workspace_id).resolve()
    return output_root if output_root.parent == expected_parent else None


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
    media_files = [
        path for path in all_files if path.suffix.lower() in MEDIA_SUFFIXES
    ]
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
def _queue_library_artifacts(
    payload: dict[str, Any], artifacts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Queue library imports and report the creator profiles they came from."""
    actor = payload.get("actor_user_id")
    if not actor:
        return [], [], []
    from trendrelay_api.media_library import create_ingest_job

    source_urls = payload.get("request", {}).get("urls") or []
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
            queued.append(
                create_ingest_job(
                    workspace_id=payload["workspace_id"],
                    actor_user_id=actor,
                    path=artifact["path"],
                    title=artifact.get("name") or "Douyin reference",
                    source_type="douyin-download",
                    source_url=source_url,
                    platform="douyin",
                    creator=metadata.get("creator"),
                    published_at=metadata.get("published_at"),
                    caption=metadata.get("caption"),
                    engagement={
                        "download_job_id": payload.get("id"),
                        "download_source_path": artifact["path"],
                        "origin_urls": origin_urls,
                    },
                    source_sha256=artifact.get("sha256"),
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
        expected_parent = (OUTPUT_ROOT / payload["workspace_id"]).resolve()
        if output_root.parent != expected_parent:
            raise RuntimeError("Invalid download output location")
        request = payload["request"]

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

        Prepared = tuple[
            list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]
        ]

        def prepare(paths: list[Path]) -> Prepared:
            """Fingerprint one source's media and hand it to the library."""
            described = _describe_media(paths)
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

            if payload.get("resume_from_disk"):
                adopted = _scan_new_media(output_root, seen_paths)
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
                    code, detail = _download_source(url, output_root, request)
                    last_detail = detail or last_detail
                    if "already downloaded" in detail.lower():
                        # The skip pass found every requested video already held;
                        # this is completion, not an empty or blocked fetch.
                        already_complete += 1
                    elif code == 0 or code == 3 or "without saving any media" in detail.lower():
                        if code != 0:
                            # Nothing new here: already held, or blocked.
                            blocked_sources += 1
                    else:
                        source_errors.append(
                            f"Source {position} of {len(urls)}: {detail[-500:]}"
                        )
                    new_paths = _scan_new_media(output_root, seen_paths)
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
            if blocked_sources and _looks_like_auth_failure(evidence):
                _write_connection_status(
                    "refresh_required",
                    "Douyin rejected the saved session. Refresh the Douyin session and retry.",
                )
                message = (
                    "Douyin refused the request for this session. Refresh the Douyin "
                    "session in TrendRelay, then retry."
                )
            elif blocked_sources:
                message = (
                    "Douyin returned no media for these links. The post may have been "
                    "removed, or the link may name a topic or a page rather than a "
                    "video. The saved session was not the problem."
                )
            else:
                message = (
                    "Download finished without media files. Connect Douyin in the app "
                    "and retry."
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
        # Douyin serves an anonymous caller only the first page of a profile
        # (~20 videos), so name that ceiling rather than letting the count look
        # like the whole list. A connected account fetches the rest.
        if (
            any("/user/" in url for url in request["urls"])
            and not cookie_status().get("signed_in")
        ):
            summary = (
                f"{summary}. Anonymous fetch stops at a profile's first page "
                "(about 20 videos) - Douyin's ceiling without an account. "
                "Connect an account to fetch the whole profile."
            )
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
        if from_saved_files and not progress["files_downloaded"]:
            raise ValueError("No completed media files are available to finish.")
        payload = dict(item.payload or {})
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
