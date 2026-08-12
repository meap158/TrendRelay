"""Database-backed durable job queue with expiring worker leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from trendrelay_api.database import SessionFactory
from trendrelay_api.models import DurableJob

SessionMaker = sessionmaker[Session]


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def serialize_job(item: DurableJob) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_key,
        "kind": item.kind,
        "status": item.status,
        "payload": item.payload,
        "result": item.result,
        "error": item.last_error,
        "attempt_count": item.attempt_count,
        "max_attempts": item.max_attempts,
        "cancellation_requested": item.cancellation_requested,
        "available_at": item.available_at,
        "lease_owner": item.lease_owner,
        "lease_expires_at": item.lease_expires_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
    }


def create_job_record(
    job_id: str,
    workspace_key: str,
    kind: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
    factory: SessionMaker = SessionFactory,
) -> dict[str, Any]:
    timestamp = now_utc()
    item = DurableJob(
        id=job_id,
        workspace_key=workspace_key,
        kind=kind,
        status="queued",
        payload=payload,
        attempt_count=0,
        max_attempts=max_attempts,
        cancellation_requested=False,
        available_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )
    with factory.begin() as session:
        session.add(item)
    return serialize_job(item)


def get_job_record(job_id: str, *, factory: SessionMaker = SessionFactory) -> dict[str, Any]:
    with factory() as session:
        item = session.get(DurableJob, job_id)
        if not item:
            raise FileNotFoundError(job_id)
        return serialize_job(item)


def list_job_records(
    workspace_key: str,
    kind: str,
    limit: int = 20,
    *,
    factory: SessionMaker = SessionFactory,
) -> list[dict[str, Any]]:
    with factory() as session:
        items = session.scalars(
            select(DurableJob)
            .where(DurableJob.workspace_key == workspace_key, DurableJob.kind == kind)
            .order_by(DurableJob.created_at.desc())
            .limit(limit)
        ).all()
        return [serialize_job(item) for item in items]


#: What an abandoned job's error says. Written once so the worker, the API and
#: the tests all agree on the wording somebody will read on a stuck row.
ABANDONED_ERROR = (
    "The worker handling this stopped before it finished, and no attempts were "
    "left to retry with. Anything already downloaded is on disk; resume to "
    "finish from what is there."
)


def abandon_expired_jobs(
    kind: str,
    limit: int = 50,
    *,
    factory: SessionMaker = SessionFactory,
) -> list[str]:
    """Fail jobs whose worker vanished and which cannot be retried.

    `recoverable_job_ids` requeues a running job once its lease expires, but
    only while it has attempts left. A job that used its last attempt and then
    lost its worker matches neither that query nor anything else, so it stayed
    `running` for as long as the database survived - one download in this
    workspace had been "Downloading now" for four days, with its lease four
    days expired and no error recorded.

    Nothing is retried here. The point is that a job nobody is working on says
    so, which is what puts it in front of somebody who can resume it.
    """
    timestamp = now_utc()
    abandoned: list[str] = []
    with factory.begin() as session:
        items = session.scalars(
            select(DurableJob)
            .where(
                DurableJob.kind == kind,
                DurableJob.status == "running",
                DurableJob.lease_expires_at.is_not(None),
                DurableJob.lease_expires_at <= timestamp,
                DurableJob.attempt_count >= DurableJob.max_attempts,
            )
            .order_by(DurableJob.created_at)
            .limit(limit)
        ).all()
        for item in items:
            item.status = "failed"
            # Only when nothing else explained it. A worker that recorded why it
            # failed and then died knows more than this does.
            if not item.last_error:
                item.last_error = ABANDONED_ERROR
            item.lease_owner = None
            item.lease_expires_at = None
            item.completed_at = timestamp
            item.updated_at = timestamp
            abandoned.append(item.id)
    return abandoned


def recoverable_job_ids(
    kind: str,
    limit: int = 20,
    *,
    factory: SessionMaker = SessionFactory,
) -> list[str]:
    timestamp = now_utc()
    with factory() as session:
        return list(
            session.scalars(
                select(DurableJob.id)
                .where(
                    DurableJob.kind == kind,
                    DurableJob.cancellation_requested.is_(False),
                    DurableJob.attempt_count < DurableJob.max_attempts,
                    or_(
                        and_(
                            DurableJob.status == "queued",
                            DurableJob.available_at <= timestamp,
                        ),
                        and_(
                            DurableJob.status == "running",
                            DurableJob.lease_expires_at.is_not(None),
                            DurableJob.lease_expires_at <= timestamp,
                        ),
                    ),
                )
                .order_by(DurableJob.available_at, DurableJob.created_at)
                .limit(limit)
            )
        )


def claim_job(
    job_id: str,
    worker_id: str,
    *,
    lease_seconds: int = 120,
    factory: SessionMaker = SessionFactory,
) -> dict[str, Any]:
    timestamp = now_utc()
    with factory.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item:
            raise FileNotFoundError(job_id)
        lease_expired = (
            item.status == "running"
            and item.lease_expires_at is not None
            and as_utc(item.lease_expires_at) <= timestamp
        )
        ready = item.status == "queued" and as_utc(item.available_at) <= timestamp
        if item.cancellation_requested or item.attempt_count >= item.max_attempts:
            raise PermissionError("Job cannot be claimed.")
        if not ready and not lease_expired:
            raise PermissionError("Job is not available for claim.")
        item.status = "running"
        item.attempt_count += 1
        item.lease_owner = worker_id
        item.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        item.started_at = item.started_at or timestamp
        item.updated_at = timestamp
        session.flush()
        return serialize_job(item)


def claim_next_job(
    kind: str,
    worker_id: str,
    *,
    lease_seconds: int = 120,
    factory: SessionMaker = SessionFactory,
) -> dict[str, Any] | None:
    timestamp = now_utc()
    with factory.begin() as session:
        item = session.scalar(
            select(DurableJob)
            .where(
                DurableJob.kind == kind,
                DurableJob.cancellation_requested.is_(False),
                DurableJob.attempt_count < DurableJob.max_attempts,
                or_(
                    and_(DurableJob.status == "queued", DurableJob.available_at <= timestamp),
                    and_(
                        DurableJob.status == "running",
                        DurableJob.lease_expires_at.is_not(None),
                        DurableJob.lease_expires_at <= timestamp,
                    ),
                ),
            )
            .order_by(DurableJob.available_at, DurableJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not item:
            return None
        item.status = "running"
        item.attempt_count += 1
        item.lease_owner = worker_id
        item.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        item.started_at = item.started_at or timestamp
        item.updated_at = timestamp
        session.flush()
        return serialize_job(item)


def heartbeat_job(
    job_id: str,
    worker_id: str,
    *,
    lease_seconds: int = 120,
    factory: SessionMaker = SessionFactory,
) -> None:
    timestamp = now_utc()
    with factory.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item or item.status != "running" or item.lease_owner != worker_id:
            raise PermissionError("Worker does not hold this job lease.")
        item.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        item.updated_at = timestamp


def merge_running_result(
    job_id: str,
    worker_id: str,
    patch: dict[str, Any],
    *,
    factory: SessionMaker = SessionFactory,
) -> None:
    """Publish partial results while a job is still running.

    Readers derive live progress from ``result``, which otherwise stays empty
    until completion. List values append so a long job can report work
    incrementally; anything else replaces. A worker that no longer holds the
    lease is ignored rather than raising, because this is progress reporting
    and never the work itself.
    """
    with factory.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item or item.status != "running" or item.lease_owner != worker_id:
            return
        current = dict(item.result or {})
        for key, value in patch.items():
            if isinstance(value, list):
                current[key] = [*(current.get(key) or []), *value]
            else:
                current[key] = value
        item.result = current
        item.updated_at = now_utc()


def complete_job(
    job_id: str,
    worker_id: str,
    result: dict[str, Any],
    *,
    factory: SessionMaker = SessionFactory,
) -> dict[str, Any]:
    timestamp = now_utc()
    with factory.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item or item.status != "running" or item.lease_owner != worker_id:
            raise PermissionError("Worker does not hold this job lease.")
        item.status = "cancelled" if item.cancellation_requested else "succeeded"
        item.result = result
        item.last_error = None
        item.lease_owner = None
        item.lease_expires_at = None
        item.completed_at = timestamp
        item.updated_at = timestamp
        session.flush()
        return serialize_job(item)


def fail_job(
    job_id: str,
    worker_id: str,
    error: str,
    *,
    retry_delay_seconds: int = 30,
    factory: SessionMaker = SessionFactory,
) -> dict[str, Any]:
    timestamp = now_utc()
    with factory.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item or item.status != "running" or item.lease_owner != worker_id:
            raise PermissionError("Worker does not hold this job lease.")
        retry = not item.cancellation_requested and item.attempt_count < item.max_attempts
        item.status = "queued" if retry else (
            "cancelled" if item.cancellation_requested else "failed"
        )
        item.last_error = error[-4000:]
        item.available_at = timestamp + timedelta(seconds=retry_delay_seconds)
        item.lease_owner = None
        item.lease_expires_at = None
        item.completed_at = None if retry else timestamp
        item.updated_at = timestamp
        session.flush()
        return serialize_job(item)


def update_job_payload(
    job_id: str,
    payload: dict[str, Any],
    *,
    factory: SessionMaker = SessionFactory,
) -> dict[str, Any]:
    with factory.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item:
            raise FileNotFoundError(job_id)
        if item.status != "queued" or item.lease_owner:
            raise PermissionError("Only an unclaimed queued job can be updated.")
        item.payload = payload
        item.updated_at = now_utc()
        session.flush()
        return serialize_job(item)


def request_job_cancellation(
    job_id: str, *, factory: SessionMaker = SessionFactory
) -> dict[str, Any]:
    timestamp = now_utc()
    with factory.begin() as session:
        item = session.get(DurableJob, job_id)
        if not item:
            raise FileNotFoundError(job_id)
        if item.status in {"succeeded", "failed", "cancelled"}:
            return serialize_job(item)
        item.cancellation_requested = True
        if item.status == "queued":
            item.status = "cancelled"
            item.completed_at = timestamp
        item.updated_at = timestamp
        session.flush()
        return serialize_job(item)
