"""Durable ingestion and local derivation for the TrendRelay media library."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.config import get_settings
from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations.openmontage_runtime import FFMPEG, FFPROBE
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion
from trendrelay_api.models import DurableJob
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "media_ingest"
JOB_SESSION_FACTORY = SessionFactory
LIBRARY_ROOT = PROJECT_ROOT / ".data" / "media"
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SAFE_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES | IMAGE_SUFFIXES
PUBLISHABLE_RIGHTS = {"owned", "licensed", "public-domain"}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approved_source_path(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("Media must be an existing local file.") from error
    roots = [
        (Path(root) if Path(root).is_absolute() else PROJECT_ROOT / root).resolve()
        for root in get_settings().publishing_media_root_list
    ]
    if not any(resolved.is_relative_to(root) for root in roots):
        raise PermissionError(
            "Media must be inside an approved media root: "
            + ", ".join(get_settings().publishing_media_root_list)
        )
    if not resolved.is_file() or resolved.suffix.lower() not in SAFE_SUFFIXES:
        raise ValueError("Media type is not supported by the library.")
    return resolved


def _media_kind(path: Path) -> str:
    if path.suffix.lower() in VIDEO_SUFFIXES:
        return "video"
    if path.suffix.lower() in AUDIO_SUFFIXES:
        return "audio"
    return "image"


def _mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _run(command: list[str], timeout: int = 900) -> None:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env={
            key: value
            for key, value in os.environ.items()
            if key in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Local media command failed.").strip()
        raise RuntimeError(detail[-1500:])


def probe_media(path: Path) -> dict[str, Any]:
    if not FFPROBE.is_file():
        raise RuntimeError("The pinned local ffprobe runtime is missing. Run npm install.")
    completed = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or "ffprobe could not inspect this file.").strip()[-1500:]
        )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        duration = max((float(item.get("duration") or 0) for item in streams), default=0)
    return {
        "duration_ms": round(duration * 1000) if duration > 0 else None,
        "width": (int(video.get("width") or 0) or None) if video else None,
        "height": (int(video.get("height") or 0) or None) if video else None,
        "video_codec": (str(video.get("codec_name") or "") or None) if video else None,
        "audio_codec": (str(audio.get("codec_name") or "") or None) if audio else None,
        "has_audio": audio is not None,
    }


def _copy_original(source: Path, output_root: Path, digest: str) -> Path:
    destination = output_root / f"original{source.suffix.lower()}"
    if destination.is_file():
        if file_sha256(destination) != digest:
            raise RuntimeError("Hash-addressed library original does not match its key.")
        return destination
    temporary = output_root / f"original{source.suffix.lower()}.tmp"
    shutil.copy2(source, temporary)
    if file_sha256(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Original media changed while it was copied.")
    temporary.replace(destination)
    return destination


def _version(path: Path, kind: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_kind": kind,
        "path": str(path),
        "sha256": file_sha256(path),
        "mime_type": _mime(path),
        "size_bytes": path.stat().st_size,
        "duration_ms": metadata.get("duration_ms"),
        "width": metadata.get("width"),
        "height": metadata.get("height"),
    }


def process_media(source: Path, workspace_id: str, digest: str) -> dict[str, Any]:
    if not FFMPEG.is_file():
        raise RuntimeError("The pinned local FFmpeg runtime is missing. Run npm install.")
    output_root = (LIBRARY_ROOT / workspace_id / digest).resolve()
    expected_parent = (LIBRARY_ROOT / workspace_id).resolve()
    if output_root.parent != expected_parent:
        raise RuntimeError("Invalid media-library output location.")
    output_root.mkdir(parents=True, exist_ok=True)
    original = _copy_original(source, output_root, digest)
    metadata = probe_media(original)
    versions = [_version(original, "original", metadata)]
    kind = _media_kind(original)

    if kind in {"video", "image"}:
        thumbnail = output_root / "thumbnail.jpg"
        if not thumbnail.is_file():
            command = [str(FFMPEG), "-y"]
            if kind == "video":
                command.extend(["-ss", "0.5"])
            command.extend(
                [
                    "-i",
                    str(original),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=w='min(720,iw)':h=-2",
                    "-q:v",
                    "3",
                    str(thumbnail),
                ]
            )
            _run(command)
        thumbnail_meta = probe_media(thumbnail)
        versions.append(_version(thumbnail, "thumbnail", thumbnail_meta))

    if kind == "video":
        proxy = output_root / "proxy.mp4"
        if not proxy.is_file():
            _run(
                [
                    str(FFMPEG),
                    "-y",
                    "-i",
                    str(original),
                    "-vf",
                    "scale=w='min(720,iw)':h=-2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "26",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(proxy),
                ]
            )
        versions.append(_version(proxy, "proxy", probe_media(proxy)))

    if kind in {"video", "audio"} and metadata["has_audio"]:
        audio = output_root / "audio.wav"
        if not audio.is_file():
            _run(
                [
                    str(FFMPEG),
                    "-y",
                    "-i",
                    str(original),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(audio),
                ]
            )
        versions.append(_version(audio, "audio", probe_media(audio)))

    return {
        "original": str(original),
        "media_kind": kind,
        "mime_type": _mime(original),
        "size_bytes": original.stat().st_size,
        "metadata": metadata,
        "versions": versions,
    }


def _existing_asset(session: Session, workspace_id: str, digest: str) -> MediaAsset | None:
    return session.scalar(
        select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.original_sha256 == digest,
        )
    )


def create_ingest_job(
    *,
    workspace_id: str,
    actor_user_id: str,
    path: str,
    title: str,
    source_type: str,
    rights_status: str,
    rights_basis: str | None = None,
    source_url: str | None = None,
    platform: str | None = None,
    creator: str | None = None,
    published_at: str | None = None,
    caption: str | None = None,
    hashtags: list[str] | None = None,
    audio_identifier: str | None = None,
    engagement: dict[str, Any] | None = None,
    factory=None,
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    source = approved_source_path(path)
    digest = file_sha256(source)
    with factory() as session:
        existing = _existing_asset(session, workspace_id, digest)
        if existing:
            return {
                "id": None,
                "status": "succeeded",
                "duplicate": True,
                "asset_id": existing.id,
                "sha256": digest,
            }
        job_id = "media_" + hashlib.sha256(f"{workspace_id}:{digest}".encode()).hexdigest()[:24]
        queued = session.get(DurableJob, job_id)
        if queued:
            return get_job_record(job_id, factory=factory)
    payload = {
        "id": job_id,
        "workspace_id": workspace_id,
        "actor_user_id": actor_user_id,
        "source_path": str(source),
        "source_sha256": digest,
        "title": " ".join(title.strip().split())[:300] or source.stem[:300],
        "source_type": source_type[:40],
        "source_url": source_url,
        "platform": platform,
        "creator": creator,
        "published_at": published_at,
        "caption": caption,
        "hashtags": hashtags or [],
        "audio_identifier": audio_identifier,
        "engagement": engagement or {},
        "rights_status": rights_status,
        "rights_basis": rights_basis,
        "created_at": _now(),
    }
    return create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        payload,
        max_attempts=2,
        factory=factory,
    )


def run_ingest_job(
    job_id: str,
    worker_id: str = "media-library-worker",
    *,
    factory=None,
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    claimed = claim_job(job_id, worker_id, lease_seconds=1800, factory=factory)
    payload = dict(claimed["payload"])
    try:
        source = approved_source_path(payload["source_path"])
        if file_sha256(source) != payload["source_sha256"]:
            raise RuntimeError("Source media changed after ingestion was queued.")
        with factory() as session:
            existing = _existing_asset(session, payload["workspace_id"], payload["source_sha256"])
            if existing:
                return complete_job(
                    job_id,
                    worker_id,
                    {
                        "asset_id": existing.id,
                        "duplicate": True,
                        "sha256": existing.original_sha256,
                    },
                    factory=factory,
                )
        processed = process_media(
            source,
            payload["workspace_id"],
            payload["source_sha256"],
        )
        metadata = processed["metadata"]
        with factory.begin() as session:
            item = MediaAsset(
                workspace_id=payload["workspace_id"],
                title=payload["title"],
                media_kind=processed["media_kind"],
                source_type=payload["source_type"],
                source_url=payload.get("source_url"),
                platform=payload.get("platform"),
                creator=payload.get("creator"),
                published_at=(
                    datetime.fromisoformat(payload["published_at"].replace("Z", "+00:00"))
                    if payload.get("published_at")
                    else None
                ),
                caption=payload.get("caption"),
                hashtags=payload.get("hashtags") or [],
                audio_identifier=payload.get("audio_identifier"),
                engagement=payload.get("engagement") or {},
                rights_status=payload["rights_status"],
                rights_basis=payload.get("rights_basis"),
                original_path=processed["original"],
                original_sha256=payload["source_sha256"],
                mime_type=processed["mime_type"],
                size_bytes=processed["size_bytes"],
                duration_ms=metadata.get("duration_ms"),
                width=metadata.get("width"),
                height=metadata.get("height"),
                video_codec=metadata.get("video_codec"),
                audio_codec=metadata.get("audio_codec"),
                has_audio=metadata.get("has_audio", False),
                created_by=payload["actor_user_id"],
            )
            session.add(item)
            session.flush()
            for version in processed["versions"]:
                session.add(
                    MediaAssetVersion(
                        workspace_id=item.workspace_id,
                        asset_id=item.id,
                        **version,
                    )
                )
            asset_id = item.id
        return complete_job(
            job_id,
            worker_id,
            {
                "asset_id": asset_id,
                "duplicate": False,
                "sha256": payload["source_sha256"],
                "versions": len(processed["versions"]),
            },
            factory=factory,
        )
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=factory)
        raise


def list_ingest_jobs(workspace_id: str, limit: int = 30, *, factory=None) -> list[dict[str, Any]]:
    factory = factory or JOB_SESSION_FACTORY
    return list_job_records(workspace_id, JOB_KIND, limit, factory=factory)
