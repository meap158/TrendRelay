import threading
import time
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


def test_a_slow_job_does_not_idle_the_other_slots() -> None:
    """The idle this removes.

    A pass took twenty renders and ran four at once, then waited for the whole
    batch before claiming more. One long render therefore held three quarters
    of the machine still until it ended - on a queue thirteen hundred deep the
    slots were busy 43% of the time, with gaps between starts of up to
    twenty-one minutes.
    """
    from trendrelay_api.worker_pool import run_job_batch

    started: list[str] = []
    lock = threading.Lock()
    release_slow = threading.Event()

    def runner(job_id: str) -> None:
        with lock:
            started.append(job_id)
        if job_id == "slow":
            # Held until the refilled work has been picked up, which is the
            # whole question: does anything else run while this one does?
            release_slow.wait(timeout=5)

    queue = [f"later-{index}" for index in range(4)]

    def refill() -> list[str]:
        return [queue.pop(0)] if queue else []

    def finish() -> None:
        # Let the long job go once the pool has moved on without it.
        for _ in range(100):
            with lock:
                if len(started) >= 5:
                    break
            time.sleep(0.02)
        release_slow.set()

    watcher = threading.Thread(target=finish)
    watcher.start()
    run_job_batch(["slow", "quick"], runner, label="test", workers=2, refill=refill)
    watcher.join()

    assert "slow" in started
    # The refilled work ran while the slow one was still going, which is what
    # the old shape could not do.
    assert len([job for job in started if job.startswith("later-")]) >= 1


def test_the_pool_never_runs_a_job_twice() -> None:
    """`refill` reads the queue, where a submitted job still reads as queued.

    A render claims its own row on the thread that runs it, so between handing
    an id to the pool and that claim the database still offers it. Handing it
    out again would run the same render twice.
    """
    from trendrelay_api.worker_pool import run_job_batch

    ran: list[str] = []
    lock = threading.Lock()

    def runner(job_id: str) -> None:
        with lock:
            ran.append(job_id)
        time.sleep(0.01)

    # A source that keeps offering what has already been handed out, exactly as
    # an unclaimed queue does between submitting a job and its thread claiming
    # the row. Nothing new is ever offered, so nothing new may run.
    def refill() -> list[str]:
        return ["a", "b"]

    run_job_batch(["a", "b"], runner, label="test", workers=2, refill=refill)

    assert sorted(ran) == ["a", "b"], ran


def test_a_batch_with_no_refill_still_runs_everything() -> None:
    from trendrelay_api.worker_pool import run_job_batch

    ran: list[str] = []
    lock = threading.Lock()

    def runner(job_id: str) -> None:
        with lock:
            ran.append(job_id)

    count = run_job_batch([f"job-{i}" for i in range(7)], runner, label="test", workers=3)

    assert count == 7
    assert sorted(ran) == sorted(f"job-{i}" for i in range(7))
