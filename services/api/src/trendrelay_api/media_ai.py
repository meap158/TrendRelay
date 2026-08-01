"""Isolated local transcription and OCR providers for Media Library drafts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from trendrelay_api.config import get_settings
from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations.openmontage_runtime import FFMPEG
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import DurableJob
from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

JOB_KIND = "media_enrichment"
JOB_SESSION_FACTORY = SessionFactory
RUNTIME_ROOT = PROJECT_ROOT / ".tools" / "media-ai" / "runtime"
MODEL_ROOT = PROJECT_ROOT / ".data" / "media-ai" / "models"
WORK_ROOT = PROJECT_ROOT / ".data" / "media-ai" / "work"
SPEECH_PACKAGE = "faster-whisper"
SPEECH_VERSION = "1.2.1"
OCR_PACKAGE = "rapidocr"
OCR_VERSION = "3.9.2"
ONNX_VERSION = "1.28.0"
Mode = Literal["speech", "ocr"]


def _runtime_path() -> None:
    value = str(RUNTIME_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)


def _module_present(name: str) -> bool:
    _runtime_path()
    return importlib.util.find_spec(name) is not None


def _active_tools() -> dict[str, bool]:
    return {item["id"]: bool(item["active"]) for item in list_tools()}


def provider_status() -> dict[str, Any]:
    active = _active_tools()
    model = get_settings().media_ai_speech_model
    model_root = MODEL_ROOT / "faster-whisper"
    model_cached = model_root.is_dir() and any(model_root.rglob("model.bin"))
    speech_runtime = _module_present("faster_whisper")
    ocr_runtime = _module_present("rapidocr") and _module_present("onnxruntime")
    return {
        "speech": {
            "provider": f"faster-whisper {SPEECH_VERSION}",
            "source_active": active.get("faster-whisper", False),
            "runtime_ready": speech_runtime,
            "model": model,
            "model_cached": model_cached,
            "ready": bool(
                active.get("faster-whisper", False) and speech_runtime and model_cached
            ),
            "network_during_analysis": False,
        },
        "ocr": {
            "provider": f"RapidOCR {OCR_VERSION} / ONNX Runtime {ONNX_VERSION}",
            "source_active": active.get("rapidocr", False),
            "runtime_ready": ocr_runtime,
            "ready": bool(active.get("rapidocr", False) and ocr_runtime),
            "network_during_analysis": False,
        },
        "review_required": True,
        "runtime_root": str(RUNTIME_ROOT),
    }


def _version_path(session: Any, asset_id: str, kind: str) -> Path | None:
    item = session.scalar(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind == kind,
        )
        .order_by(MediaAssetVersion.created_at.desc())
        .limit(1)
    )
    if not item:
        return None
    try:
        path = Path(item.path).resolve(strict=True)
    except OSError:
        return None
    return path if path.is_file() else None


def _speech_draft(path: Path, language: str | None) -> dict[str, Any]:
    _runtime_path()
    from faster_whisper import WhisperModel

    settings = get_settings()
    model_root = MODEL_ROOT / "faster-whisper"
    if not model_root.is_dir() or not any(model_root.rglob("model.bin")):
        raise RuntimeError(
            "The configured faster-whisper model is not prepared. "
            "Open Tools and run Prepare speech runtime."
        )
    model = WhisperModel(
        settings.media_ai_speech_model,
        device=settings.media_ai_device,
        compute_type=settings.media_ai_compute_type,
        download_root=str(model_root),
        local_files_only=True,
    )
    segments, info = model.transcribe(
        str(path),
        language=None if not language or language == "auto" else language,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )
    records = []
    text_parts = []
    for segment in segments:
        text = " ".join(str(segment.text).strip().split())
        if not text:
            continue
        text_parts.append(text)
        records.append(
            {
                "start_ms": round(float(segment.start) * 1000),
                "end_ms": round(float(segment.end) * 1000),
                "text": text,
                "avg_logprob": round(float(segment.avg_logprob), 4),
                "no_speech_prob": round(float(segment.no_speech_prob), 4),
                "words": [
                    {
                        "start_ms": round(float(word.start) * 1000),
                        "end_ms": round(float(word.end) * 1000),
                        "text": str(word.word),
                        "probability": round(float(word.probability), 4),
                    }
                    for word in (segment.words or [])
                ],
            }
        )
    text = " ".join(text_parts).strip()
    if not text:
        raise RuntimeError("The speech provider found no spoken text.")
    return {
        "language": str(info.language or language or "und"),
        "text": text[:100_000],
        "segments": records,
        "provider": f"faster-whisper@{SPEECH_VERSION}:{settings.media_ai_speech_model}",
    }


def _extract_ocr_frames(asset: MediaAsset, source: Path, work: Path) -> list[Path]:
    if asset.media_kind == "image":
        return [source]
    if not FFMPEG.is_file():
        raise RuntimeError("The pinned local FFmpeg runtime is missing. Run npm install.")
    work.mkdir(parents=True, exist_ok=True)
    interval = get_settings().media_ai_ocr_interval_seconds
    pattern = work / "frame-%04d.jpg"
    completed = subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(source),
            "-vf",
            f"fps=1/{interval},scale=w='min(1280,iw)':h=-2",
            "-frames:v",
            str(get_settings().media_ai_max_ocr_frames),
            str(pattern),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Frame extraction failed.").strip()
        raise RuntimeError(detail[-1500:])
    frames = sorted(work.glob("frame-*.jpg"))
    if not frames:
        raise RuntimeError("No frames were available for OCR.")
    return frames


def _rapidocr_text(result: Any) -> tuple[list[str], list[float]]:
    texts = list(getattr(result, "txts", []) or [])
    scores = [float(value) for value in (getattr(result, "scores", []) or [])]
    if texts:
        return [str(value) for value in texts], scores
    payload = result.to_json() if hasattr(result, "to_json") else result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if isinstance(payload, dict):
        texts = list(payload.get("txts") or payload.get("texts") or [])
        scores = [float(value) for value in (payload.get("scores") or [])]
    return [str(value) for value in texts], scores


def _ocr_draft(asset: MediaAsset, source: Path, work: Path) -> dict[str, Any]:
    _runtime_path()
    from rapidocr import RapidOCR

    engine = RapidOCR()
    frames = _extract_ocr_frames(asset, source, work)
    records = []
    unique: list[str] = []
    seen: set[str] = set()
    interval_ms = round(get_settings().media_ai_ocr_interval_seconds * 1000)
    for index, frame in enumerate(frames):
        result = engine(str(frame))
        texts, scores = _rapidocr_text(result)
        kept = []
        for text, score in zip(texts, scores or [1.0] * len(texts), strict=False):
            normalized = " ".join(text.strip().split())
            if not normalized or score < 0.45:
                continue
            key = normalized.casefold()
            kept.append({"text": normalized, "confidence": round(score, 4)})
            if key not in seen:
                seen.add(key)
                unique.append(normalized)
        if kept:
            records.append(
                {
                    "timestamp_ms": 0 if asset.media_kind == "image" else index * interval_ms,
                    "lines": kept,
                }
            )
    text = "\n".join(unique).strip()
    if not text:
        raise RuntimeError("The OCR provider found no on-screen text.")
    return {
        "language": "und",
        "text": text[:100_000],
        "segments": records,
        "provider": f"rapidocr@{OCR_VERSION}:onnxruntime@{ONNX_VERSION}",
    }


SPEECH_RUNNER = _speech_draft
OCR_RUNNER = _ocr_draft


def create_enrichment_job(
    *,
    workspace_id: str,
    asset_id: str,
    actor_user_id: str,
    modes: list[Mode],
    language: str | None,
    factory=None,
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    normalized_modes = sorted(set(modes))
    if not normalized_modes:
        raise ValueError("Select speech transcription, OCR, or both.")
    with factory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id,
                MediaAsset.workspace_id == workspace_id,
            )
        )
        if not asset:
            raise ValueError("Media asset was not found.")
        if "speech" in normalized_modes and not asset.has_audio:
            raise ValueError("This asset has no audio track to transcribe.")
        if "ocr" in normalized_modes and asset.media_kind not in {"video", "image"}:
            raise ValueError("OCR requires a video or image asset.")
        signature = ":".join(
            [
                workspace_id,
                asset_id,
                asset.original_sha256,
                ",".join(normalized_modes),
                language or "auto",
                get_settings().media_ai_speech_model,
                str(get_settings().media_ai_ocr_interval_seconds),
                SPEECH_VERSION,
                OCR_VERSION,
            ]
        )
        job_id = "mediaai_" + hashlib.sha256(signature.encode()).hexdigest()[:24]
        existing = session.get(DurableJob, job_id)
        if existing:
            return get_job_record(job_id, factory=factory)
    return create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "id": job_id,
            "workspace_id": workspace_id,
            "asset_id": asset_id,
            "actor_user_id": actor_user_id,
            "modes": normalized_modes,
            "language": language or "auto",
            "speech_provider": f"faster-whisper@{SPEECH_VERSION}",
            "ocr_provider": f"rapidocr@{OCR_VERSION}",
        },
        max_attempts=2,
        factory=factory,
    )


def run_enrichment_job(
    job_id: str,
    worker_id: str = "media-ai-worker",
    *,
    factory=None,
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    claimed = claim_job(job_id, worker_id, lease_seconds=3600, factory=factory)
    payload = dict(claimed["payload"])
    work = WORK_ROOT / job_id
    try:
        status = provider_status()
        for mode in payload["modes"]:
            if not status[mode]["ready"]:
                raise RuntimeError(
                    f"{status[mode]['provider']} is not ready. Complete its Tools setup."
                )
        with factory() as session:
            asset = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.id == payload["asset_id"],
                    MediaAsset.workspace_id == payload["workspace_id"],
                )
            )
            if not asset:
                raise RuntimeError("Media asset was removed before analysis.")
            drafts: list[tuple[Mode, dict[str, Any]]] = []
            if "speech" in payload["modes"]:
                audio = _version_path(session, asset.id, "audio")
                if not audio:
                    audio = _version_path(session, asset.id, "original")
                if not audio:
                    raise RuntimeError("The audio version is unavailable.")
                drafts.append(("speech", SPEECH_RUNNER(audio, payload.get("language"))))
            if "ocr" in payload["modes"]:
                source = _version_path(session, asset.id, "original")
                if not source:
                    raise RuntimeError("The original version is unavailable.")
                drafts.append(("ocr", OCR_RUNNER(asset, source, work)))
        transcript_ids = []
        with factory.begin() as session:
            for kind, draft in drafts:
                existing = session.scalar(
                    select(MediaTranscript).where(
                        MediaTranscript.asset_id == payload["asset_id"],
                        MediaTranscript.job_id == job_id,
                        MediaTranscript.kind == kind,
                    )
                )
                if existing:
                    transcript_ids.append(existing.id)
                    continue
                transcript = MediaTranscript(
                    workspace_id=payload["workspace_id"],
                    asset_id=payload["asset_id"],
                    kind=kind,
                    language=draft["language"],
                    provider=draft["provider"],
                    status="machine",
                    text=draft["text"],
                    segments=draft["segments"],
                    job_id=job_id,
                    created_by=payload["actor_user_id"],
                )
                session.add(transcript)
                session.flush()
                transcript_ids.append(transcript.id)
        return complete_job(
            job_id,
            worker_id,
            {
                "asset_id": payload["asset_id"],
                "transcript_ids": transcript_ids,
                "review_required": True,
            },
            factory=factory,
        )
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=factory)
        raise
    finally:
        if work.is_dir():
            shutil.rmtree(work, ignore_errors=True)


def list_enrichment_jobs(
    workspace_id: str, limit: int = 30, *, factory=None
) -> list[dict[str, Any]]:
    factory = factory or JOB_SESSION_FACTORY
    return list_job_records(workspace_id, JOB_KIND, limit, factory=factory)
