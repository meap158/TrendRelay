"""Durable, workspace-scoped media acquisition through Douyin Downloader."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

JOB_KIND = "douyin_download"
JOB_SESSION_FACTORY = SessionFactory
OUTPUT_ROOT = PROJECT_ROOT / ".data" / "downloads" / "douyin"
DOWNLOAD_SCRIPT = PROJECT_ROOT / "scripts" / "douyin.py"
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
REQUIRED_COOKIE_KEYS = ("ttwid", "odin_tt", "passport_csrf_token")
COOKIE_ENV_KEYS = (
    ("msToken", "DOUYIN_MS_TOKEN"),
    ("ttwid", "DOUYIN_TTWID"),
    ("odin_tt", "DOUYIN_ODIN_TT"),
    ("passport_csrf_token", "DOUYIN_PASSPORT_CSRF_TOKEN"),
    ("sid_guard", "DOUYIN_SID_GUARD"),
)


class DownloadRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=80)
    urls: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["post", "like", "mix", "music"] = "post"
    limit: int = Field(default=20, ge=1, le=100)
    incremental: bool = True
    confirm_external_action: bool = False

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
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"} or not (
                host == "douyin.com"
                or host.endswith(".douyin.com")
                or host == "iesdouyin.com"
                or host.endswith(".iesdouyin.com")
            ):
                raise ValueError("Only HTTPS Douyin URLs are supported by this provider")
            if url not in unique:
                unique.append(url)
        return unique


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
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
        "source": source,
        "missing": missing,
        "cookie_file": str(COOKIE_FILE),
    }


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
    if cookies["ready"]:
        return {
            "state": "connected",
            "message": "Douyin cookies are ready.",
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
        "output_root": str((OUTPUT_ROOT / request.workspace_id / job_id).resolve()),
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


def _collect_artifacts(output_root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.resolve()),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _fingerprint(path),
        }
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    ]


def _queue_library_artifacts(
    payload: dict[str, Any], artifacts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    actor = payload.get("actor_user_id")
    if not actor:
        return [], []
    from trendrelay_api.media_library import create_ingest_job

    source_urls = payload.get("request", {}).get("urls") or []
    source_url = source_urls[0] if len(source_urls) == 1 else None
    queued = []
    errors = []
    for artifact in artifacts:
        try:
            queued.append(
                create_ingest_job(
                    workspace_id=payload["workspace_id"],
                    actor_user_id=actor,
                    path=artifact["path"],
                    title=artifact.get("name") or "Douyin reference",
                    source_type="douyin-download",
                    source_url=source_url,
                    platform="douyin",
                    rights_status="reference-only",
                    rights_basis=(
                        "Acquired for internal creative research; reuse rights not established."
                    ),
                    factory=JOB_SESSION_FACTORY,
                )
            )
        except Exception as error:
            errors.append(str(error)[-500:])
    return queued, errors


def run_download_job(job_id: str, worker_id: str = "douyin-worker") -> dict[str, Any]:
    claimed = claim_job(job_id, worker_id, lease_seconds=3600, factory=JOB_SESSION_FACTORY)
    payload = dict(claimed["payload"])
    try:
        output_root = Path(payload["output_root"]).resolve()
        expected_parent = (OUTPUT_ROOT / payload["workspace_id"]).resolve()
        if output_root.parent != expected_parent:
            raise RuntimeError("Invalid download output location")
        request = payload["request"]
        command = [
            sys.executable,
            str(DOWNLOAD_SCRIPT),
            "batch",
            *request["urls"],
            "--output",
            str(output_root),
            "--mode",
            request["mode"],
            "--limit",
            str(request["limit"]),
        ]
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
            timeout=3600,
        )
        detail = (completed.stderr or completed.stdout or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(detail[-3000:] or "Download failed")
        artifacts = _collect_artifacts(output_root)
        if not artifacts:
            # Defense in depth: never report success for an empty folder.
            message = "Download finished without media files. Connect Douyin in the app and retry."
            if detail:
                message = f"{message}\n{detail[-2500:]}"
            raise RuntimeError(message)
        library_jobs, library_errors = _queue_library_artifacts(payload, artifacts)
        result = {
            **payload,
            "status": "succeeded",
            "updated_at": _now(),
            "completed_at": _now(),
            "artifacts": artifacts,
            "library_jobs": library_jobs,
            "library_errors": library_errors,
            "summary": f"Fetched {len(artifacts)} media file(s)",
        }
        return complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)
        raise


def download_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"download_[a-f0-9]{16}", job_id):
        raise ValueError("Invalid download identifier")
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def list_download_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
