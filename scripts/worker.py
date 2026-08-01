"""Recoverable TrendRelay durable-job worker."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.integrations.douyin import run_download_job  # noqa: E402
from trendrelay_api.integrations.last30days import run_job  # noqa: E402
from trendrelay_api.integrations.openmontage_runtime import run_render_job  # noqa: E402
from trendrelay_api.integrations.postiz import run_publish_job  # noqa: E402
from trendrelay_api.media_library import run_ingest_job  # noqa: E402
from trendrelay_api.jobs import recoverable_job_ids  # noqa: E402


def process_available() -> int:
    download_ids = recoverable_job_ids("douyin_download")
    research_ids = recoverable_job_ids("trend_research")
    publishing_ids = recoverable_job_ids("social_publish")
    render_ids = recoverable_job_ids("openmontage_render")
    media_ids = recoverable_job_ids("media_ingest")
    for job_id in download_ids:
        run_download_job(job_id)
    for job_id in research_ids:
        run_job(job_id)
    for job_id in publishing_ids:
        run_publish_job(job_id)
    for job_id in render_ids:
        run_render_job(job_id)
    for job_id in media_ids:
        run_ingest_job(job_id)
    return (
        len(download_ids)
        + len(research_ids)
        + len(publishing_ids)
        + len(render_ids)
        + len(media_ids)
    )


def worker_main() -> None:
    print(
        "Durable worker ready: douyin_download, trend_research, social_publish, openmontage_render, media_ingest",
        flush=True,
    )
    try:
        while True:
            if process_available() == 0:
                time.sleep(1)
    except KeyboardInterrupt:
        print("Durable worker stopped.", flush=True)


def source_snapshot() -> tuple[tuple[str, int, int], ...]:
    files: list[tuple[str, int, int]] = []
    for root in (ROOT / "scripts", API_SOURCE):
        for path in root.rglob("*.py"):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(files))


def start_watched_worker() -> subprocess.Popen[bytes]:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=ROOT,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )


def stop_watched_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def watch_worker() -> int:
    snapshot = source_snapshot()
    process = start_watched_worker()
    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                return return_code or 1
            time.sleep(0.5)
            updated_snapshot = source_snapshot()
            if updated_snapshot == snapshot:
                continue
            print("Worker source changed; reloading...", flush=True)
            stop_watched_worker(process)
            snapshot = updated_snapshot
            process = start_watched_worker()
    except KeyboardInterrupt:
        return 0
    finally:
        stop_watched_worker(process)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="process currently available work"
    )
    parser.add_argument(
        "--watch", action="store_true", help="reload the worker after code changes"
    )
    args = parser.parse_args()
    if args.once:
        print(f"Processed {process_available()} durable job(s).")
        return 0
    if args.watch:
        return watch_worker()
    worker_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
