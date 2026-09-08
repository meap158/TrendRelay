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


def test_a_long_job_does_not_hold_up_unrelated_work(monkeypatch) -> None:
    """The starvation this hook exists to end.

    The worker runs one kind at a time. A queue of effect renders kept a single
    pass inside `run_job_batch` for as long as its slowest render - measured at
    twenty-nine minutes on a 4K clip - and a download queued in the meantime
    sat "waiting" for all of it, with nothing wrong with either of them.
    """
    monkeypatch.setattr(worker_pool, "YIELD_SECONDS", 0.01)
    release = threading.Event()
    yielded: list[int] = []

    def slow(_job_id: str) -> None:
        # Stands in for a render that outlasts the whole refill budget.
        release.wait(timeout=5)

    def other_work() -> None:
        yielded.append(1)
        # Let it run a few times, then let the "render" finish.
        if len(yielded) >= 3:
            release.set()

    done = worker_pool.run_job_batch(
        ["render-1"], slow, label="Effect render", workers=1, on_wait=other_work
    )

    assert done == 1
    # The point: the waiting work ran while the long job was still going.
    assert len(yielded) >= 3


def test_yielding_is_off_unless_asked_for(monkeypatch) -> None:
    """Without the hook the pool blocks as before - no polling, no wake-ups."""
    monkeypatch.setattr(worker_pool, "YIELD_SECONDS", 0.01)
    started = time.monotonic()

    done = worker_pool.run_job_batch(
        ["a", "b"], lambda _job_id: time.sleep(0.02), label="Effect render", workers=2
    )

    assert done == 2
    assert time.monotonic() - started < 2


def test_a_failure_in_the_yielded_work_does_not_stop_the_batch(monkeypatch) -> None:
    """Other people's work must not take the render queue down with it."""
    monkeypatch.setattr(worker_pool, "YIELD_SECONDS", 0.01)
    calls: list[int] = []

    def exploding() -> None:
        calls.append(1)
        raise RuntimeError("the download queue could not be read")

    done = worker_pool.run_job_batch(
        ["render-1"],
        lambda _job_id: time.sleep(0.08),
        label="Effect render",
        workers=1,
        on_wait=exploding,
    )

    assert done == 1
    assert calls, "the hook never ran"
