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
