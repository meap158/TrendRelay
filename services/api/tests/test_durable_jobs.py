from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.jobs import (
    ABANDONED_ERROR,
    abandon_expired_jobs,
    claim_job,
    claim_next_job,
    clear_settled_jobs,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    heartbeat_job,
    list_job_records,
    list_job_records_including_active,
    now_utc,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    recoverable_job_ids,
    retry_delay_for,
    request_job_cancellation,
    requeue_terminal_job,
    settle_expired_cancellations,
    upgrade_active_job_recovery,
)
from trendrelay_api.models import Base, DurableJob


def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_durable_job_retries_and_requires_the_active_lease_owner() -> None:
    sessions = factory()
    created = create_job_record(
        "research_1234567890abcdef",
        "workspace-1",
        "trend_research",
        {"topic": "espresso"},
        max_attempts=2,
        factory=sessions,
    )
    assert created["status"] == "queued"

    claimed = claim_next_job("trend_research", "worker-a", factory=sessions)
    assert claimed and claimed["attempt_count"] == 1
    heartbeat_job(claimed["id"], "worker-a", lease_seconds=60, factory=sessions)

    retried = fail_job(
        claimed["id"],
        "worker-a",
        "temporary provider failure",
        retry_delay_seconds=0,
        factory=sessions,
    )
    assert retried["status"] == "queued"
    assert recoverable_job_ids("trend_research", factory=sessions) == [claimed["id"]]
    claimed_again = claim_next_job("trend_research", "worker-b", factory=sessions)
    assert claimed_again and claimed_again["attempt_count"] == 2

    completed = complete_job(
        claimed_again["id"], "worker-b", {"observations": [1]}, factory=sessions
    )
    assert completed["status"] == "succeeded"
    assert get_job_record(completed["id"], factory=sessions)["result"] == {"observations": [1]}
    assert (
        list_job_records("workspace-1", "trend_research", factory=sessions)[0]["id"]
        == completed["id"]
    )


def test_a_definite_failure_can_end_without_spending_recovery_attempts() -> None:
    """Process-loss recovery and provider-error retry are separate policies."""
    sessions = factory()
    create_job_record(
        "setup_1234567890abcdef",
        "local-machine",
        "media_ai_setup",
        {},
        max_attempts=3,
        factory=sessions,
    )
    claim_job("setup_1234567890abcdef", "worker", factory=sessions)

    failed = fail_job(
        "setup_1234567890abcdef",
        "worker",
        "The access token is expired.",
        retry_allowed=False,
        factory=sessions,
    )

    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1
    assert recoverable_job_ids("media_ai_setup", factory=sessions) == []


def test_a_terminal_content_addressed_job_can_be_queued_again() -> None:
    sessions = factory()
    job_id = "mediaai_1234567890abcdef"
    create_job_record(job_id, "workspace-1", "media_enrichment", {}, factory=sessions)
    claim_job(job_id, "worker", factory=sessions)
    fail_job(job_id, "worker", "Provider was off.", retry_allowed=False, factory=sessions)

    resumed = requeue_terminal_job(job_id, factory=sessions)

    assert resumed["status"] == "queued"
    assert resumed["attempt_count"] == 0
    assert resumed["error"] is None
    assert resumed["completed_at"] is None
    assert recoverable_job_ids("media_enrichment", factory=sessions) == [job_id]


def test_queued_job_cancellation_is_terminal_and_unclaimable() -> None:
    sessions = factory()
    create_job_record("production_1234567890abcdef", "local", "production", {}, factory=sessions)

    cancelled = request_job_cancellation("production_1234567890abcdef", factory=sessions)

    assert cancelled["status"] == "cancelled"
    assert claim_next_job("production", "worker-a", factory=sessions) is None


def test_an_expired_cancelled_job_stops_saying_it_is_running() -> None:
    sessions = factory()
    job_id = "edit_cancelled_worker_gone"
    create_job_record(
        job_id,
        "workspace-1",
        "media_effect_render",
        {},
        max_attempts=3,
        factory=sessions,
    )
    claim_job(job_id, "effect-worker", factory=sessions)
    request_job_cancellation(job_id, factory=sessions)
    with sessions.begin() as session:
        session.get(DurableJob, job_id).lease_expires_at = now_utc().replace(
            tzinfo=None
        ) - timedelta(minutes=1)

    assert recoverable_job_ids("media_effect_render", factory=sessions) == []
    assert settle_expired_cancellations("media_effect_render", factory=sessions) == [job_id]

    settled = get_job_record(job_id, factory=sessions)
    assert settled["status"] == "cancelled"
    assert settled["progress_stage"] == "Cancelled"
    assert settled["lease_owner"] is None
    assert settled["completed_at"] is not None


def test_a_cancelled_job_with_a_live_worker_is_left_to_stop_safely() -> None:
    sessions = factory()
    job_id = "edit_cancelling_live"
    create_job_record(job_id, "workspace-1", "media_effect_render", {}, factory=sessions)
    claim_job(job_id, "effect-worker", lease_seconds=120, factory=sessions)
    request_job_cancellation(job_id, factory=sessions)

    assert settle_expired_cancellations("media_effect_render", factory=sessions) == []
    assert get_job_record(job_id, factory=sessions)["status"] == "running"


def stranded(sessions, *, max_attempts: int = 1, kind: str = "douyin_download") -> str:
    """A job that used its last attempt and then lost its worker."""
    job_id = "download_af3674e83463e58c"
    create_job_record(job_id, "workspace-1", kind, {}, max_attempts=max_attempts, factory=sessions)
    for _ in range(max_attempts):
        claim_next_job(kind, "douyin-worker", factory=sessions)
    with sessions.begin() as session:
        session.get(DurableJob, job_id).lease_expires_at = now_utc().replace(
            tzinfo=None
        ) - timedelta(hours=2)
    return job_id


def test_a_job_whose_worker_never_came_back_stops_saying_it_is_running() -> None:
    """It had been "Downloading now" for four days.

    Recovery only requeues a running job while it has attempts left, so one that
    spent its last attempt and then lost its worker matched nothing at all and
    kept that status until somebody edited the database.
    """
    sessions = factory()
    job_id = stranded(sessions)

    assert abandon_expired_jobs("douyin_download", factory=sessions) == [job_id]

    job = get_job_record(job_id, factory=sessions)
    assert job["status"] == "failed"
    assert job["error"] == ABANDONED_ERROR


def test_an_abandoned_job_is_not_retried() -> None:
    # The point is a terminal state, not another attempt: the attempts are what
    # ran out. Retrying here would loop on whatever killed the worker.
    sessions = factory()
    stranded(sessions)

    abandon_expired_jobs("douyin_download", factory=sessions)

    assert recoverable_job_ids("douyin_download", factory=sessions) == []
    assert claim_next_job("douyin_download", "douyin-worker", factory=sessions) is None


def test_a_job_that_can_still_retry_is_left_for_recovery() -> None:
    """Abandoning it would throw away an attempt it is entitled to."""
    sessions = factory()
    job_id = stranded(sessions, max_attempts=2)

    assert abandon_expired_jobs("douyin_download", factory=sessions) == []
    assert recoverable_job_ids("douyin_download", factory=sessions) == [job_id]


def test_a_live_lease_is_never_touched() -> None:
    # A worker still holding its lease is working, however long it has taken.
    sessions = factory()
    create_job_record(
        "download_1111111111111111",
        "workspace-1",
        "douyin_download",
        {},
        max_attempts=1,
        factory=sessions,
    )
    claim_next_job("douyin_download", "douyin-worker", factory=sessions)

    assert abandon_expired_jobs("douyin_download", factory=sessions) == []
    assert get_job_record("download_1111111111111111", factory=sessions)["status"] == "running"


def test_an_error_the_worker_recorded_is_kept() -> None:
    """A worker that said why it failed and then died knows more than this does."""
    sessions = factory()
    job_id = stranded(sessions)
    with sessions.begin() as session:
        session.get(DurableJob, job_id).last_error = "Douyin refused the session."

    abandon_expired_jobs("douyin_download", factory=sessions)

    assert get_job_record(job_id, factory=sessions)["error"] == "Douyin refused the session."


def test_only_the_kind_asked_for_is_swept() -> None:
    sessions = factory()
    stranded(sessions, kind="douyin_download")

    assert abandon_expired_jobs("media_face_blur", factory=sessions) == []


def test_legacy_active_jobs_receive_the_new_retry_and_lease_policy() -> None:
    sessions = factory()
    job_id = "edit_legacy1234567890"
    create_job_record(
        job_id,
        "workspace-1",
        "media_effect_render",
        {},
        max_attempts=1,
        factory=sessions,
    )
    claim_job(job_id, "old-session", lease_seconds=3600, factory=sessions)
    before = get_job_record(job_id, factory=sessions)

    assert upgrade_active_job_recovery(
        "media_effect_render",
        max_attempts=3,
        maximum_lease_seconds=120,
        factory=sessions,
    ) == [job_id]

    upgraded = get_job_record(job_id, factory=sessions)
    assert upgraded["max_attempts"] == 3
    assert upgraded["lease_expires_at"] < before["lease_expires_at"]
    assert upgraded["status"] == "running"


def test_recovery_policy_never_reopens_a_finished_job() -> None:
    sessions = factory()
    job_id = "edit_complete12345678"
    create_job_record(
        job_id,
        "workspace-1",
        "media_effect_render",
        {},
        max_attempts=1,
        factory=sessions,
    )
    claim_job(job_id, "worker", factory=sessions)
    complete_job(job_id, "worker", {}, factory=sessions)

    assert (
        upgrade_active_job_recovery(
            "media_effect_render",
            max_attempts=3,
            maximum_lease_seconds=120,
            factory=sessions,
        )
        == []
    )
    assert get_job_record(job_id, factory=sessions)["max_attempts"] == 1


def test_active_jobs_are_not_paged_out_of_notification_history() -> None:
    sessions = factory()
    create_job_record(
        "edit_old_active",
        "workspace-1",
        "media_effect_render",
        {},
        factory=sessions,
    )
    for index in range(4):
        job_id = f"edit_new_done_{index}"
        create_job_record(
            job_id,
            "workspace-1",
            "media_effect_render",
            {},
            factory=sessions,
        )
        claim_job(job_id, "worker", factory=sessions)
        complete_job(job_id, "worker", {}, factory=sessions)

    visible = list_job_records_including_active(
        "workspace-1",
        "media_effect_render",
        limit=2,
        factory=sessions,
    )

    assert len(visible) == 2
    assert "edit_old_active" in {job["id"] for job in visible}


def test_a_running_job_whose_worker_vanished_reads_as_stalled() -> None:
    """The row keeps saying "running" because only a worker writes a status.

    Every sweep that would correct it - retry, abandon - also runs inside the
    worker, so while that process is down the record stays frozen mid-render.
    One face-overlay job in this workspace showed "Applying 63%" for ten hours.
    Readers derive it from the lease instead of waiting to be told.
    """
    sessions = factory()
    create_job_record("edit_stalled", "workspace-1", "media_effect_render", {}, factory=sessions)
    claim_job("edit_stalled", "worker-gone", lease_seconds=120, factory=sessions)

    assert get_job_record("edit_stalled", factory=sessions)["stalled"] is False

    with sessions.begin() as session:
        item = session.get(DurableJob, "edit_stalled")
        item.lease_expires_at = now_utc() - timedelta(seconds=1)

    record = get_job_record("edit_stalled", factory=sessions)
    assert record["status"] == "running"
    assert record["stalled"] is True


def test_a_worker_coming_back_makes_the_job_read_as_running_again() -> None:
    # Derived on read rather than written, so recovery needs no second sweep:
    # the next heartbeat is enough.
    sessions = factory()
    create_job_record("edit_back", "workspace-1", "media_effect_render", {}, factory=sessions)
    claim_job("edit_back", "worker-a", lease_seconds=120, factory=sessions)
    with sessions.begin() as session:
        session.get(DurableJob, "edit_back").lease_expires_at = now_utc() - timedelta(seconds=1)
    assert get_job_record("edit_back", factory=sessions)["stalled"] is True

    heartbeat_job("edit_back", "worker-a", lease_seconds=120, factory=sessions)

    assert get_job_record("edit_back", factory=sessions)["stalled"] is False


def test_a_settled_job_is_never_stalled() -> None:
    # Only a job claiming to be running can be lying about it. A finished one
    # keeps its lease columns cleared, and a queued one holds no lease at all.
    sessions = factory()
    create_job_record("edit_done", "workspace-1", "media_effect_render", {}, factory=sessions)
    assert get_job_record("edit_done", factory=sessions)["stalled"] is False

    claim_job("edit_done", "worker", factory=sessions)
    complete_job("edit_done", "worker", {}, factory=sessions)

    assert get_job_record("edit_done", factory=sessions)["stalled"] is False


def test_the_stall_flag_reaches_the_list_the_drawer_reads() -> None:
    # Notifications, the thumbnail overlay and the detail panel all read this
    # one list; a flag only `get_job_record` carried would fix none of them.
    sessions = factory()
    create_job_record("edit_listed", "workspace-1", "media_effect_render", {}, factory=sessions)
    claim_job("edit_listed", "worker-gone", lease_seconds=120, factory=sessions)
    with sessions.begin() as session:
        session.get(DurableJob, "edit_listed").lease_expires_at = now_utc() - timedelta(seconds=1)

    listed = list_job_records_including_active(
        "workspace-1", "media_effect_render", factory=sessions
    )

    assert [job["stalled"] for job in listed] == [True]


def test_clearing_history_forgets_finished_jobs_only() -> None:
    """An activity log nobody can empty becomes the panel rather than part of it.

    Work in flight is never deleted: the row is what a worker holds a lease on
    and the only way an operator can cancel it.
    """
    sessions = factory()
    for job_id in ("edit_done", "edit_running", "edit_queued"):
        create_job_record(job_id, "workspace-1", "media_effect_render", {}, factory=sessions)
    # One attempt, so the failure is terminal rather than a requeue.
    create_job_record(
        "edit_failed",
        "workspace-1",
        "media_effect_render",
        {},
        max_attempts=1,
        factory=sessions,
    )
    claim_job("edit_done", "worker", factory=sessions)
    complete_job("edit_done", "worker", {}, factory=sessions)
    claim_job("edit_failed", "worker", factory=sessions)
    fail_job("edit_failed", "worker", "no faces", factory=sessions)
    claim_job("edit_running", "worker", factory=sessions)

    assert clear_settled_jobs("workspace-1", "media_effect_render", factory=sessions) == 2

    left = list_job_records_including_active("workspace-1", "media_effect_render", factory=sessions)
    assert {job["id"] for job in left} == {"edit_running", "edit_queued"}


def test_clearing_history_stops_at_the_workspace_and_the_kind() -> None:
    sessions = factory()
    for workspace, kind in (
        ("workspace-1", "media_effect_render"),
        ("workspace-2", "media_effect_render"),
        ("workspace-1", "media_face_blur"),
    ):
        job_id = f"job_{workspace}_{kind}"
        create_job_record(job_id, workspace, kind, {}, factory=sessions)
        claim_job(job_id, "worker", factory=sessions)
        complete_job(job_id, "worker", {}, factory=sessions)

    assert clear_settled_jobs("workspace-1", "media_effect_render", factory=sessions) == 1

    assert list_job_records("workspace-2", "media_effect_render", factory=sessions)
    assert list_job_records("workspace-1", "media_face_blur", factory=sessions)


def test_keep_spares_the_rows_it_names() -> None:
    # How one asset's log is cleared without touching the rest of the workspace.
    sessions = factory()
    for job_id, asset in (("edit_a", "asset-1"), ("edit_b", "asset-2")):
        create_job_record(
            job_id,
            "workspace-1",
            "media_effect_render",
            {"asset_id": asset},
            factory=sessions,
        )
        claim_job(job_id, "worker", factory=sessions)
        complete_job(job_id, "worker", {}, factory=sessions)

    removed = clear_settled_jobs(
        "workspace-1",
        "media_effect_render",
        keep=lambda item: item.payload.get("asset_id") != "asset-1",
        factory=sessions,
    )

    assert removed == 1
    assert [
        job["id"]
        for job in list_job_records("workspace-1", "media_effect_render", factory=sessions)
    ] == ["edit_b"]


def test_each_retry_waits_longer_than_the_one_before() -> None:
    delays = [retry_delay_for("edit_abc123", attempt) for attempt in range(1, 6)]

    assert delays == sorted(delays), "a retry must not come back sooner than the last"
    # Doubling, within the spread each one is allowed.
    for attempt, delay in enumerate(delays, start=1):
        step = min(RETRY_BASE_SECONDS * 2 ** (attempt - 1), RETRY_MAX_SECONDS)
        assert 0.75 * step <= delay <= 1.25 * step


def test_the_wait_is_capped_however_many_attempts_are_allowed() -> None:
    # A job configured with many attempts must not end up waiting a day.
    assert retry_delay_for("edit_abc123", 40) <= RETRY_MAX_SECONDS * 1.25


def test_a_batch_failing_on_one_cause_does_not_come_back_in_lockstep() -> None:
    """The spread, and why it is there.

    Failures in a batch are usually one cause - a full disk, a missing
    encoder - so without a spread the whole batch fails together, waits the
    same interval, and returns together to fail against the same cause again.
    """
    delays = {retry_delay_for(f"edit_{index:04d}aaaa", 1) for index in range(40)}

    assert len(delays) > 5, "forty jobs should not share a handful of wake-up times"


def test_a_job_that_failed_is_tried_again_after_the_work_never_attempted() -> None:
    """The point of pushing a failure into the future.

    The queue is claimed by `available_at`, so a failed job waiting its backoff
    sits behind everything still untouched. A batch finishes what it has never
    tried before coming back to what has already refused once - which is what
    keeps one bad clip from being retried ahead of forty good ones.
    """
    sessions = factory()
    for name in ("first", "second", "third"):
        create_job_record(name, "ws", "media_effect_render", {}, max_attempts=3, factory=sessions)

    claimed = claim_next_job("media_effect_render", "worker", factory=sessions)
    assert claimed["id"] == "first"
    fail_job("first", "worker", "ffmpeg said no", factory=sessions)

    # The two that have never been tried come first, in order.
    assert claim_next_job("media_effect_render", "worker", factory=sessions)["id"] == "second"
    assert claim_next_job("media_effect_render", "worker", factory=sessions)["id"] == "third"
    # And the failure is not available yet at all.
    assert claim_next_job("media_effect_render", "worker", factory=sessions) is None

    requeued = get_job_record("first", factory=sessions)
    assert requeued["status"] == "queued"
    assert requeued["attempt_count"] == 1


def test_a_caller_that_names_a_delay_still_gets_it() -> None:
    # One caller schedules its own retry against a provider's window; the
    # schedule is a default, not a policy imposed on work that knows better.
    sessions = factory()
    create_job_record("job", "ws", "media_effect_render", {}, max_attempts=3, factory=sessions)
    claim_next_job("media_effect_render", "worker", factory=sessions)

    failed = fail_job("job", "worker", "later", retry_delay_seconds=5, factory=sessions)

    waited = failed["available_at"] - now_utc()
    assert waited.total_seconds() <= 6
