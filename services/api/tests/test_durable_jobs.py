from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.jobs import (
    claim_next_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    heartbeat_job,
    list_job_records,
    request_job_cancellation,
)
from trendrelay_api.models import Base


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
