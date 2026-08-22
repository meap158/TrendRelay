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
from trendrelay_api.integrations.face_blur import run_blur_job  # noqa: E402
from trendrelay_api.integrations.effect_render import (  # noqa: E402
    JOB_KIND as EFFECT_JOB_KIND,
    RENDER_LEASE_SECONDS,
    RENDER_MAX_ATTEMPTS,
    run_render_job as run_effect_render_job,
)
from trendrelay_api.integrations.last30days import run_job  # noqa: E402
from trendrelay_api.integrations.openmontage_runtime import run_render_job  # noqa: E402
from trendrelay_api.integrations.publishing import run_publish_job  # noqa: E402
from trendrelay_api.media_ai import JOB_KIND as ENRICHMENT_JOB_KIND  # noqa: E402
from trendrelay_api.media_ai import SETUP_JOB_KIND as MEDIA_AI_SETUP_KIND  # noqa: E402
from trendrelay_api.media_ai import run_enrichment_job  # noqa: E402
from trendrelay_api.media_ai import run_setup_job as run_media_ai_setup_job  # noqa: E402
from trendrelay_api.media_library import run_ingest_job  # noqa: E402
from trendrelay_api.shopee_enrichment import run_enrich_job  # noqa: E402
from trendrelay_api.campaign_runner import tick as campaign_tick  # noqa: E402
from trendrelay_api.caption_jobs import JOB_KIND as CAPTION_JOB_KIND  # noqa: E402
from trendrelay_api.caption_jobs import run_caption_job  # noqa: E402
from trendrelay_api.database import SessionFactory  # noqa: E402
from trendrelay_api.jobs import (  # noqa: E402
    abandon_expired_jobs,
    recoverable_job_ids,
    settle_expired_cancellations,
    upgrade_active_job_recovery,
)


#: Campaign autopilot is time-driven rather than queue-driven, so it is asked
#: on a clock instead of waiting for a job to appear. Once a minute is far more
#: often than any posting slot needs and cheap when there is nothing to do.
AUTOPILOT_EVERY_SECONDS = 60
_last_autopilot = 0.0


def tick_autopilot(now: float) -> None:
    """Ask every switched-on campaign whether a slot is due."""
    global _last_autopilot
    if now - _last_autopilot < AUTOPILOT_EVERY_SECONDS:
        return
    _last_autopilot = now
    try:
        campaign_tick(SessionFactory)
    except Exception as error:  # pragma: no cover - the loop must not die here
        print(f"Campaign autopilot tick failed: {error}", flush=True)


#: Every queue this worker drains. Named once so the sweep below cannot drift
#: out of step with the list of things actually processed.
JOB_KINDS = (
    "douyin_download",
    "trend_research",
    "social_publish",
    "openmontage_render",
    "media_ingest",
    "media_face_blur",
    EFFECT_JOB_KIND,
    "shopee_enrich",
    CAPTION_JOB_KIND,
    MEDIA_AI_SETUP_KIND,
    ENRICHMENT_JOB_KIND,
)


def process_available() -> int:
    upgraded = upgrade_active_job_recovery(
        EFFECT_JOB_KIND,
        max_attempts=RENDER_MAX_ATTEMPTS,
        maximum_lease_seconds=RENDER_LEASE_SECONDS,
    )
    for job_id in upgraded:
        print(f"Prepared interrupted effect job {job_id} for recovery.", flush=True)
    # Before claiming anything: a job whose worker died with no attempts left
    # is invisible to the recovery below, and stays "running" until somebody
    # notices it never finished. Giving it a terminal state is what puts it in
    # front of them.
    for kind in JOB_KINDS:
        for job_id in settle_expired_cancellations(kind):
            print(f"Finished cancellation for orphaned {kind} job {job_id}.", flush=True)
        for job_id in abandon_expired_jobs(kind):
            print(f"Abandoned {kind} job {job_id}: its worker never came back.", flush=True)

    download_ids = recoverable_job_ids("douyin_download")
    research_ids = recoverable_job_ids("trend_research")
    publishing_ids = recoverable_job_ids("social_publish")
    render_ids = recoverable_job_ids("openmontage_render")
    media_ids = recoverable_job_ids("media_ingest")
    blur_ids = recoverable_job_ids("media_face_blur")
    effect_ids = recoverable_job_ids(EFFECT_JOB_KIND)
    enrich_ids = recoverable_job_ids("shopee_enrich")
    caption_ids = recoverable_job_ids(CAPTION_JOB_KIND)
    media_ai_setup_ids = recoverable_job_ids(MEDIA_AI_SETUP_KIND)
    enrichment_ids = recoverable_job_ids(ENRICHMENT_JOB_KIND)
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
    for job_id in blur_ids:
        run_blur_job(job_id)
    for job_id in effect_ids:
        run_effect_render_job(job_id)
    for job_id in enrich_ids:
        run_enrich_job(job_id)
    for job_id in caption_ids:
        run_caption_job(job_id)
    for job_id in enrichment_ids:
        # Transcribing a clip fails for ordinary reasons - a provider switched
        # off between queueing and running, a file that moved - and the job row
        # carries the reason to the operator's screen. The loop keeps going.
        try:
            run_enrichment_job(job_id)
        except Exception as error:
            print(f"Transcription {job_id} failed: {error}", flush=True)
    for job_id in media_ai_setup_ids:
        # A download the operator is watching, so a failure belongs on their
        # screen rather than in this console. The job row already carries it.
        try:
            run_media_ai_setup_job(job_id)
        except Exception as error:
            print(f"Media analysis setup {job_id} failed: {error}", flush=True)
    return (
        len(download_ids)
        + len(research_ids)
        + len(publishing_ids)
        + len(render_ids)
        + len(media_ids)
        + len(blur_ids)
        + len(effect_ids)
        + len(enrich_ids)
        + len(caption_ids)
        + len(media_ai_setup_ids)
        + len(enrichment_ids)
    )


def worker_main() -> None:
    print(
        "Durable worker ready: douyin_download, trend_research, social_publish, "
        "openmontage_render, media_ingest, media_face_blur, media_effect_render, "
        "caption_render, media_ai_setup, media_enrichment, "
        "campaign_autopilot",
        flush=True,
    )
    try:
        while True:
            tick_autopilot(time.monotonic())
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


#: Windows reports a live process with this exit code.
_STILL_ACTIVE = 259


def process_is_alive(pid: int) -> bool:
    """Whether a process is still running, without a third-party dependency."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        # Query-only access, so this works against a process we do not own.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, and owned by somebody else.
        return True
    return True


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


def watch_worker(parent_pid: int = 0) -> int:
    """Run the worker, reloading it when its source changes.

    `parent_pid` is the runner that started this. When it is gone this returns,
    and the `finally` below stops the worker it spawned.

    Without that, every hard stop of the runner leaked two processes. The runner
    reclaims its ports on the way back up, which kills a leftover API or dev
    server, but the worker holds no port and so nothing ever noticed it: three
    generations of them were found alive at once, all polling the same SQLite
    database as the API that was being waited on.
    """
    snapshot = source_snapshot()
    process = start_watched_worker()
    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                return return_code or 1
            if parent_pid and not process_is_alive(parent_pid):
                print("Runner is gone; stopping the worker.", flush=True)
                return 0
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
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=0,
        help="exit when this process is gone, so a stopped runner leaves nothing behind",
    )
    args = parser.parse_args()
    if args.once:
        print(f"Processed {process_available()} durable job(s).")
        return 0
    if args.watch:
        return watch_worker(args.parent_pid)
    worker_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
