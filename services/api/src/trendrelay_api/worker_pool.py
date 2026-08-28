"""Small adaptive pools for native/subprocess-backed durable jobs.

Python threads are appropriate here because FFmpeg, OpenCV, ONNX Runtime, HTTP
clients, and CTranslate2 do their expensive work outside the GIL. The pool is
bounded deliberately: launching one encoder per selected asset is faster only
until CPU, VRAM, or disk contention turns the machine into a queue of its own.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

#: How long a refilling pass keeps claiming new work before it lets the tick go.
#:
#: The worker runs every kind in turn, so a pass that refills for ever starves
#: captions and voiceovers behind a long queue of renders. Two minutes is long
#: enough that the pool is never idle over a normal render and short enough that
#: nothing else waits noticeably; work already in flight is always finished, so
#: the budget delays the next claim rather than interrupting anything.
REFILL_BUDGET_SECONDS = 120.0


def adaptive_media_workers() -> int:
    """A conservative local-media width, overridable for measured deployments."""
    configured = os.environ.get("TRENDRELAY_MEDIA_WORKERS", "").strip()
    if configured:
        try:
            return max(1, min(8, int(configured)))
        except ValueError:
            pass
    cores = os.cpu_count() or 4
    if cores <= 4:
        return 1
    if cores <= 12:
        return 2
    if cores <= 24:
        return 3
    return 4


def run_job_batch(
    job_ids: Sequence[str],
    runner: Callable[[str], object],
    *,
    label: str,
    workers: int | None = None,
    refill: Callable[[], Iterable[str]] | None = None,
) -> int:
    """Run an independent durable batch concurrently without killing its worker.

    With a `refill` the pool claims more work as slots free, instead of draining
    the batch it was given and waiting for the slowest of it.

    That wait was most of the queue's idle time. A pass takes twenty renders and
    runs four at once, so when nineteen are a minute and the twentieth is
    twenty, three quarters of the machine stands still until the long one ends -
    measured at 43% of the slots busy, with gaps between starts of up to
    twenty-one minutes on a queue thirteen hundred deep. The pool now tops
    itself up, and only stops claiming when the source is empty or the budget is
    spent.
    """
    ids = list(job_ids)
    if not ids:
        return 0
    width = min(len(ids), workers or adaptive_media_workers())

    def run_one(job_id: str) -> None:
        try:
            runner(job_id)
        except Exception as error:  # noqa: BLE001 - one item cannot stop its batch
            # Durable runners record their own failed row before raising. This
            # console line is diagnostic; swallowing here keeps the other 99
            # selected items moving.
            print(f"{label} {job_id} failed: {error}", flush=True)

    if width == 1 and refill is None:
        for job_id in ids:
            run_one(job_id)
        return len(ids)

    pending = list(ids)
    # Every id this pass has already taken. `refill` reads the queue, and a job
    # submitted a moment ago is still `queued` until its thread claims it, so
    # without this the same render would be handed out twice.
    taken = set(pending)
    deadline = time.monotonic() + REFILL_BUDGET_SECONDS
    done = 0
    with ThreadPoolExecutor(max_workers=width, thread_name_prefix="media-job") as pool:
        running: set = set()
        while pending or running:
            while pending and len(running) < width:
                running.add(pool.submit(run_one, pending.pop(0)))
            if not running:
                break
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                future.result()
                done += 1
            if refill and not pending and time.monotonic() < deadline:
                try:
                    fresh = [job_id for job_id in refill() if job_id not in taken]
                except Exception as error:  # noqa: BLE001
                    # Reading the queue is a database call and can lose a race
                    # for the write lock. Failing to find more work must not
                    # abandon the work already running: the pass finishes what
                    # it holds and the next tick claims again a second later.
                    print(f"{label} could not look for more work: {error}", flush=True)
                    fresh = []
                taken.update(fresh)
                pending.extend(fresh)
    return done
