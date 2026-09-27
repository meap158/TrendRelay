"""Recoverable TrendRelay durable-job worker."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.project_storage import sweep_abandoned_scratch  # noqa: E402
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
from trendrelay_api.media_ai import (  # noqa: E402
    ENRICHMENT_LEASE_SECONDS,
    ENRICHMENT_MAX_ATTEMPTS,
    JOB_KIND as ENRICHMENT_JOB_KIND,
    SETUP_JOB_KIND as MEDIA_AI_SETUP_KIND,
    SETUP_LEASE_SECONDS,
    SETUP_MAX_ATTEMPTS,
)
from trendrelay_api.media_ai import run_enrichment_job  # noqa: E402
from trendrelay_api.media_ai import run_setup_job as run_media_ai_setup_job  # noqa: E402
from trendrelay_api.media_library import run_ingest_job  # noqa: E402
from trendrelay_api.shopee_enrichment import run_enrich_job  # noqa: E402
from trendrelay_api.autocut.jobs import JOB_KIND as AUTOCUT_JOB_KIND  # noqa: E402
from trendrelay_api.autocut.jobs import run_render_job as run_autocut_job  # noqa: E402
from trendrelay_api.storytelling.jobs import JOB_KIND as STORY_JOB_KIND  # noqa: E402
from trendrelay_api.storytelling.jobs import run_render_job as run_story_job  # noqa: E402
from trendrelay_api.storytelling.autocreate import JOB_KIND as AUTOCREATE_JOB_KIND  # noqa: E402
from trendrelay_api.storytelling.autocreate import run_autocreate_job  # noqa: E402
from trendrelay_api.campaign_runner import tick as campaign_tick  # noqa: E402
from trendrelay_api.caption_jobs import JOB_KIND as CAPTION_JOB_KIND  # noqa: E402
from trendrelay_api.caption_jobs import run_caption_job  # noqa: E402
from trendrelay_api.voice_jobs import JOB_KIND as VOICE_JOB_KIND  # noqa: E402
from trendrelay_api.voice_jobs import run_voice_job  # noqa: E402
from trendrelay_api.database import SessionFactory  # noqa: E402
from trendrelay_api.jobs import (  # noqa: E402
    abandon_expired_jobs,
    recoverable_job_ids,
    prune_settled_jobs,
    settle_expired_cancellations,
    upgrade_active_job_recovery,
)
from trendrelay_api.worker_pool import run_job_batch  # noqa: E402


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


def start_telegram_approvals() -> None:
    """Listen for approval presses on Telegram, beside the job loop.

    A thread of its own because it waits: each poll holds a request open until
    a button is pressed or the time is up, which is what answers a press within
    a second, and nothing in the job loop should wait on that. Asleep while the
    Telegram tool is not set up, so a machine that never sends never asks.
    """
    from trendrelay_api.approval_notices import poll_forever

    threading.Thread(
        target=poll_forever, args=(SessionFactory,), name="telegram-approvals", daemon=True,
    ).start()


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
    VOICE_JOB_KIND,
    AUTOCUT_JOB_KIND,
    STORY_JOB_KIND,
    AUTOCREATE_JOB_KIND,
)


def drain_downloads() -> None:
    """Run any download that is waiting, wherever the worker is in its pass.

    Called from inside the render pool while it has nothing to report, so a
    download queued during a long batch starts then rather than after it. Each
    id is claimed the same way the main pass claims it, so a download already
    running elsewhere is not started twice.
    """
    for job_id in recoverable_job_ids("douyin_download"):
        print(f"Starting download {job_id} without waiting for the render queue.", flush=True)
        run_download_job(job_id)


def process_available() -> int:
    upgraded = upgrade_active_job_recovery(
        EFFECT_JOB_KIND,
        max_attempts=RENDER_MAX_ATTEMPTS,
        maximum_lease_seconds=RENDER_LEASE_SECONDS,
    )
    for job_id in upgraded:
        print(f"Prepared interrupted effect job {job_id} for recovery.", flush=True)
    for kind, attempts, lease in (
        (MEDIA_AI_SETUP_KIND, SETUP_MAX_ATTEMPTS, SETUP_LEASE_SECONDS),
        (ENRICHMENT_JOB_KIND, ENRICHMENT_MAX_ATTEMPTS, ENRICHMENT_LEASE_SECONDS),
    ):
        for job_id in upgrade_active_job_recovery(
            kind,
            max_attempts=attempts,
            maximum_lease_seconds=lease,
        ):
            print(f"Prepared interrupted {kind} job {job_id} for recovery.", flush=True)
    # Before claiming anything: a job whose worker died with no attempts left
    # is invisible to the recovery below, and stays "running" until somebody
    # notices it never finished. Giving it a terminal state is what puts it in
    # front of them.
    for kind in JOB_KINDS:
        for job_id in settle_expired_cancellations(kind):
            print(f"Finished cancellation for orphaned {kind} job {job_id}.", flush=True)
        for job_id in abandon_expired_jobs(kind):
            print(f"Abandoned {kind} job {job_id}: its worker never came back.", flush=True)

    # Finished jobs nobody reads any more. Nothing removed them until now, so
    # 72,359 of them had accumulated with 317 MB of request and result JSON -
    # about half the database. Bounded per pass, because the first few have
    # tens of thousands to get through and one enormous delete would hold the
    # write lock across everything else the worker is doing.
    forgotten = prune_settled_jobs()
    if forgotten:
        print(f"Forgot {forgotten} settled job(s) past their retention.", flush=True)

    # Listing reads run beside the pass, not at their turn in it. Each is a
    # couple of seconds of polite HTTP - yet in a serial pass a hundred media
    # ingests ahead of them kept four hundred "waiting" with nothing wrong.
    # The lease makes a cross-thread claim safe (a job claimed here is not
    # claimed again by anything else), two workers keep the batch moving
    # while each job's own politeness pause still spaces the requests, and
    # the join at the end keeps the pass's return honest.
    enrich_ids = recoverable_job_ids("shopee_enrich")
    enrich_lane = threading.Thread(
        target=lambda: run_job_batch(
            enrich_ids,
            run_enrich_job,
            label="Shopee listing",
            workers=2,
            refill=lambda: recoverable_job_ids("shopee_enrich"),
        ),
        name="shopee-listing-lane",
        daemon=True,
    )
    if enrich_ids:
        enrich_lane.start()

    download_ids = recoverable_job_ids("douyin_download")
    research_ids = recoverable_job_ids("trend_research")
    publishing_ids = recoverable_job_ids("social_publish")
    render_ids = recoverable_job_ids("openmontage_render")
    media_ids = recoverable_job_ids("media_ingest")
    blur_ids = recoverable_job_ids("media_face_blur")
    effect_ids = recoverable_job_ids(EFFECT_JOB_KIND)
    caption_ids = recoverable_job_ids(CAPTION_JOB_KIND)
    media_ai_setup_ids = recoverable_job_ids(MEDIA_AI_SETUP_KIND)
    enrichment_ids = recoverable_job_ids(ENRICHMENT_JOB_KIND)
    voice_ids = recoverable_job_ids(VOICE_JOB_KIND)
    autocut_ids = recoverable_job_ids(AUTOCUT_JOB_KIND)
    story_ids = recoverable_job_ids(STORY_JOB_KIND)
    autocreate_ids = recoverable_job_ids(AUTOCREATE_JOB_KIND)
    for job_id in download_ids:
        run_download_job(job_id)
    for job_id in research_ids:
        run_job(job_id)
    for job_id in publishing_ids:
        run_publish_job(job_id)
    for job_id in render_ids:
        run_render_job(job_id)
    # Ingests are hash-and-copy, which is what the adaptive pool was sized
    # for - a hundred of them one at a time is hours of a queue that
    # parallelises fine, and everything scheduled after them waited it out.
    run_job_batch(
        media_ids,
        run_ingest_job,
        label="Library ingest",
        refill=lambda: recoverable_job_ids("media_ingest"),
    )
    for job_id in blur_ids:
        run_blur_job(job_id)
    # Renders are the long queue - an effect over a selection is hundreds of
    # them - so this one keeps its pool fed rather than draining twenty and
    # waiting on the slowest before claiming more.
    run_job_batch(
        effect_ids,
        run_effect_render_job,
        label="Effect render",
        refill=lambda: recoverable_job_ids(EFFECT_JOB_KIND),
        # Downloads do not wait for renders. A render is CPU on this machine
        # and a download is a subprocess waiting on Douyin, so they compete for
        # nothing - but the worker runs one kind at a time, and a queue of
        # renders held the pass for hours. Two downloads sat "waiting" behind
        # 205 renders with nothing wrong with either of them.
        on_wait=drain_downloads,
    )
    run_job_batch(caption_ids, run_caption_job, label="Caption render")
    # One image montage is one ffmpeg graph, tens of seconds of CPU; a small
    # pool keeps two operators' renders from waiting on each other.
    run_job_batch(
        autocut_ids, run_autocut_job, label="AutoCut render", workers=2,
        refill=lambda: recoverable_job_ids(AUTOCUT_JOB_KIND),
    )
    # One at a time. A narration render is an AutoCut render with a paid
    # network call in front of it, and the two-wide pool that suits a montage
    # would turn one queued batch into simultaneous generations - the same
    # reason voice generation below keeps its own pool small.
    run_job_batch(
        story_ids, run_story_job, label="Storytelling render", workers=1,
        refill=lambda: recoverable_job_ids(STORY_JOB_KIND),
    )
    # An autonomous build searches stock, imports it, arranges it, and then
    # queues an ordinary storytelling render - which the lane above claims on
    # the next pass. One at a time: it runs its own imports inline, and a burst
    # of parallel Pexels fetches helps nobody.
    run_job_batch(
        autocreate_ids, run_autocreate_job, label="Storytelling auto-build", workers=1,
        refill=lambda: recoverable_job_ids(AUTOCREATE_JOB_KIND),
    )
    # ElevenLabs plans enforce their own concurrency limits. Two requests keep
    # ordinary plans moving without turning a large selection into a burst of
    # paid requests; deterministic job IDs still prevent duplicate billing.
    run_job_batch(
        voice_ids, run_voice_job, label="Voice generation", workers=2
    )
    # Local inference is serialized inside each shared model, while frame/audio
    # extraction and hosted transcription can overlap. This bounded lane gives
    # both paths throughput without loading another model per asset.
    run_job_batch(enrichment_ids, run_enrichment_job, label="Transcription")
    for job_id in media_ai_setup_ids:
        # A download the operator is watching, so a failure belongs on their
        # screen rather than in this console. The job row already carries it.
        try:
            run_media_ai_setup_job(job_id)
        except Exception as error:
            print(f"Media analysis setup {job_id} failed: {error}", flush=True)
    # The listing lane finishes on its own clock; waiting here keeps the
    # pass's count honest and the loop's idle sleep meaningful.
    if enrich_lane.is_alive():
        enrich_lane.join()
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
        + len(voice_ids)
        + len(autocut_ids)
        + len(story_ids)
        + len(autocreate_ids)
    )


def worker_main() -> None:
    print(
        "Durable worker ready: douyin_download, trend_research, social_publish, "
        "openmontage_render, media_ingest, media_face_blur, media_effect_render, "
        "caption_render, media_ai_setup, media_enrichment, voice_render, "
        "campaign_autopilot, telegram_approvals",
        flush=True,
    )
    # Whatever a crashed render or a killed worker left behind last time.
    # Nothing else removes these: `TemporaryDirectory` only cleans up what it
    # made if the process lives long enough to unwind, so two months of them
    # had accumulated to 8.6 GB.
    swept, freed = sweep_abandoned_scratch()
    if swept:
        print(
            f"Swept {swept} abandoned scratch file(s), {freed / 1024 / 1024:.0f} MB.",
            flush=True,
        )
    start_telegram_approvals()
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
