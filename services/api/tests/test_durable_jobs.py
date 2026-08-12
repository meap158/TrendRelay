from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.jobs import (
    ABANDONED_ERROR,
    abandon_expired_jobs,
    claim_next_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    heartbeat_job,
    list_job_records,
    now_utc,
    recoverable_job_ids,
    request_job_cancellation,
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
    assert get_job_record(completed["id"], factory=sessions)["result"] == {
        "observations": [1]
    }
    assert list_job_records("workspace-1", "trend_research", factory=sessions)[0][
        "id"
    ] == completed["id"]


def test_queued_job_cancellation_is_terminal_and_unclaimable() -> None:
    sessions = factory()
    create_job_record("production_1234567890abcdef", "local", "production", {}, factory=sessions)

    cancelled = request_job_cancellation(
        "production_1234567890abcdef", factory=sessions
    )

    assert cancelled["status"] == "cancelled"
    assert claim_next_job("production", "worker-a", factory=sessions) is None


def stranded(sessions, *, max_attempts: int = 1, kind: str = "douyin_download") -> str:
    """A job that used its last attempt and then lost its worker."""
    job_id = "download_af3674e83463e58c"
    create_job_record(job_id, "workspace-1", kind, {}, max_attempts=max_attempts, factory=sessions)
    for _ in range(max_attempts):
        claim_next_job(kind, "douyin-worker", factory=sessions)
    with sessions.begin() as session:
        session.get(DurableJob, job_id).lease_expires_at = (
            now_utc().replace(tzinfo=None) - timedelta(hours=2)
        )
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
    create_job_record("download_1111111111111111", "workspace-1", "douyin_download", {},
                      max_attempts=1, factory=sessions)
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
