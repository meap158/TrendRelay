"""The durable worker must own effect renders after the request session ends."""

from __future__ import annotations

import scripts.worker as worker


def test_the_worker_drains_recoverable_effect_renders(monkeypatch) -> None:
    recovered: list[str] = []

    monkeypatch.setattr(worker, "upgrade_active_job_recovery", lambda *a, **k: [])
    monkeypatch.setattr(worker, "abandon_expired_jobs", lambda *a, **k: [])
    monkeypatch.setattr(
        worker,
        "recoverable_job_ids",
        lambda kind: ["edit_interrupted"] if kind == worker.EFFECT_JOB_KIND else [],
    )
    for name in (
        "run_download_job",
        "run_job",
        "run_publish_job",
        "run_render_job",
        "run_ingest_job",
        "run_blur_job",
        "run_enrich_job",
    ):
        monkeypatch.setattr(worker, name, lambda _job_id: None)
    monkeypatch.setattr(
        worker, "run_effect_render_job", lambda job_id: recovered.append(job_id)
    )

    assert worker.process_available() == 1
    assert recovered == ["edit_interrupted"]


def test_the_worker_upgrades_legacy_effect_jobs_before_sweeping(monkeypatch) -> None:
    events: list[tuple] = []

    monkeypatch.setattr(
        worker,
        "upgrade_active_job_recovery",
        lambda kind, **policy: events.append(("upgrade", kind, policy)) or [],
    )
    monkeypatch.setattr(
        worker,
        "abandon_expired_jobs",
        lambda kind: events.append(("abandon", kind)) or [],
    )
    monkeypatch.setattr(worker, "recoverable_job_ids", lambda _kind: [])

    assert worker.process_available() == 0
    assert events[0] == (
        "upgrade",
        worker.EFFECT_JOB_KIND,
        {
            "max_attempts": worker.RENDER_MAX_ATTEMPTS,
            "maximum_lease_seconds": worker.RENDER_LEASE_SECONDS,
        },
    )
    assert ("abandon", worker.EFFECT_JOB_KIND) in events
