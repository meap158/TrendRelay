import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import openmontage_runtime
from trendrelay_api.jobs import get_job_record
from trendrelay_api.models import Base


@pytest.fixture
def job_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(openmontage_runtime, "JOB_SESSION_FACTORY", factory)
    return factory


def approved_production(source: Path) -> dict[str, object]:
    fingerprint = openmontage_runtime._fingerprint(source)
    return {
        "id": "production_0123456789abcdef",
        "workspace_id": "workspace-1",
        "status": "approved",
        "provider": {"id": "openmontage", "revision": "pinned-revision"},
        "source": {"path": str(source), "sha256": fingerprint},
        "plan": {"budget_cap_usd": 1.0},
        "approval": {"source_sha256": fingerprint},
    }


def test_render_submission_requires_approved_unchanged_source(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"approved-media")
    production = approved_production(source)
    monkeypatch.setattr(openmontage_runtime, "runtime_status", lambda: {"ready": True})
    monkeypatch.setattr(
        openmontage_runtime.openmontage, "get_production", lambda _id: production
    )
    request = openmontage_runtime.RenderRequest(
        workspace_id="workspace-1",
        production_id="production_0123456789abcdef",
        segments=[{"label": "Hook", "start_seconds": 0, "end_seconds": 12}],
        confirm_external_action=True,
    )

    job = openmontage_runtime.create_render_job(request)

    assert job["status"] == "queued"
    assert job["payload"]["budget"]["actual_usd"] == 0
    source.write_bytes(b"changed-media")
    with pytest.raises(ValueError, match="changed"):
        openmontage_runtime.create_render_job(request)


def test_render_worker_records_verified_artifact_provenance(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"approved-media")
    production = approved_production(source)
    monkeypatch.setattr(openmontage_runtime, "runtime_status", lambda: {"ready": True})
    monkeypatch.setattr(
        openmontage_runtime.openmontage, "get_production", lambda _id: production
    )
    output_root = tmp_path / "outputs"
    monkeypatch.setattr(openmontage_runtime, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(openmontage_runtime, "FFMPEG", tmp_path / "ffmpeg.exe")
    monkeypatch.setattr(openmontage_runtime, "FFPROBE", tmp_path / "ffprobe.exe")
    openmontage_runtime.FFMPEG.write_bytes(b"binary")
    openmontage_runtime.FFPROBE.write_bytes(b"binary")
    job = openmontage_runtime.create_render_job(
        openmontage_runtime.RenderRequest(
            workspace_id="workspace-1",
            production_id="production_0123456789abcdef",
            segments=[{"label": "Hook", "start_seconds": 0, "end_seconds": 12}],
            confirm_external_action=True,
        )
    )

    def fake_run(*_args, **kwargs):
        runtime_input = json.loads(kwargs["input"])
        artifact = Path(runtime_input["output_root"]) / "clip-01.mp4"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"verified-output")
        payload = {
            "tool": "OpenMontage VideoTrimmer",
            "artifacts": [
                {
                    "path": str(artifact),
                    "label": "Hook",
                    "size_bytes": artifact.stat().st_size,
                    "media": {"duration_seconds": 12, "streams": ["video"]},
                }
            ],
        }
        return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

    monkeypatch.setattr(openmontage_runtime.subprocess, "run", fake_run)
    completed = openmontage_runtime.run_render_job(job["id"])

    assert completed["status"] == "succeeded"
    result = get_job_record(job["id"], factory=job_factory)["result"]
    assert result["provenance"]["network_used"] is False
    assert result["artifacts"][0]["sha256"]


def test_isolated_upstream_video_trimmer_smoke() -> None:
    source = (
        openmontage_runtime.openmontage.TOOL_ROOT
        / "assets"
        / "signal-from-tomorrow-demo.mp4"
    )
    if not all(
        path.is_file()
        for path in (source, openmontage_runtime.FFMPEG, openmontage_runtime.FFPROBE)
    ):
        pytest.skip("Pinned demo media or static media tools are not installed")
    request = {
        "source": str(source),
        "output_root": str(openmontage_runtime.PROJECT_ROOT / ".data" / "test-openmontage-smoke"),
        "ffmpeg": str(openmontage_runtime.FFMPEG),
        "ffprobe": str(openmontage_runtime.FFPROBE),
        "segments": [{"label": "Smoke", "start_seconds": 0, "end_seconds": 1}],
    }
    environment = {
        "PATH": str(openmontage_runtime.FFMPEG.parent),
        "PYTHONPATH": str(openmontage_runtime.openmontage.TOOL_ROOT),
    }
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        if os.environ.get(name):
            environment[name] = os.environ[name]

    completed = subprocess.run(
        [sys.executable, str(openmontage_runtime.RUNTIME_SCRIPT)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["tool"] == "OpenMontage VideoTrimmer"
    assert result["artifacts"][0]["media"]["duration_seconds"] > 0
