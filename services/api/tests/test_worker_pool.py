from threading import Barrier, Lock

from trendrelay_api import worker_pool


def test_worker_width_adapts_to_cpu_and_accepts_an_override(monkeypatch) -> None:
    monkeypatch.delenv("TRENDRELAY_MEDIA_WORKERS", raising=False)
    monkeypatch.setattr(worker_pool.os, "cpu_count", lambda: 20)
    assert worker_pool.adaptive_media_workers() == 3

    monkeypatch.setenv("TRENDRELAY_MEDIA_WORKERS", "6")
    assert worker_pool.adaptive_media_workers() == 6


def test_worker_override_is_bounded_and_invalid_values_fall_back(monkeypatch) -> None:
    monkeypatch.setattr(worker_pool.os, "cpu_count", lambda: 4)
    monkeypatch.setenv("TRENDRELAY_MEDIA_WORKERS", "200")
    assert worker_pool.adaptive_media_workers() == 8

    monkeypatch.setenv("TRENDRELAY_MEDIA_WORKERS", "not-a-number")
    assert worker_pool.adaptive_media_workers() == 1


def test_batch_uses_multiple_workers_and_keeps_one_failure_local() -> None:
    gate = Barrier(3)
    lock = Lock()
    active = 0
    peak = 0
    completed: list[str] = []

    def run(job_id: str) -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        gate.wait(timeout=2)
        with lock:
            active -= 1
        if job_id == "two":
            raise RuntimeError("broken item")
        completed.append(job_id)

    count = worker_pool.run_job_batch(
        ["one", "two", "three"], run, label="Render", workers=3
    )

    assert count == 3
    assert peak == 3
    assert sorted(completed) == ["one", "three"]
