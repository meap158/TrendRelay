"""Small adaptive pools for native/subprocess-backed durable jobs.

Python threads are appropriate here because FFmpeg, OpenCV, ONNX Runtime, HTTP
clients, and CTranslate2 do their expensive work outside the GIL. The pool is
bounded deliberately: launching one encoder per selected asset is faster only
until CPU, VRAM, or disk contention turns the machine into a queue of its own.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed


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
) -> int:
    """Run an independent durable batch concurrently without killing its worker."""
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

    if width == 1:
        for job_id in ids:
            run_one(job_id)
        return len(ids)
    with ThreadPoolExecutor(max_workers=width, thread_name_prefix="media-job") as pool:
        futures = [pool.submit(run_one, job_id) for job_id in ids]
        for future in as_completed(futures):
            future.result()
    return len(ids)
