"""Database-backed durable job queue with expiring worker leases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
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
        "progress": item.progress,
        "progress_stage": item.progress_stage,
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


def list_job_records_including_active(
    workspace_key: str,
    kind: str,
    limit: int = 20,
    *,
    factory: SessionMaker = SessionFactory,
) -> list[dict[str, Any]]:
    """Return recent history without ever paging an unfinished job away.

    Notification drawers use a bounded history, but an old slow job remains
    operational state rather than history.  It must survive the limit so a new
    browser session can still show, cancel, and follow it.
    """
    with factory() as session:
        active = session.scalars(
            select(DurableJob).where(
                DurableJob.workspace_key == workspace_key,
                DurableJob.kind == kind,
                DurableJob.status.in_(("queued", "running")),
            )
        ).all()
        active_ids = {item.id for item in active}
        history_limit = max(0, limit - len(active))
        settled = (
            session.scalars(
                select(DurableJob)
                .where(
                    DurableJob.workspace_key == workspace_key,
                    DurableJob.kind == kind,
                    DurableJob.status.not_in(("queued", "running")),
                )
                .order_by(DurableJob.created_at.desc())
                .limit(history_limit)
            ).all()
            if history_limit
            else []
        )
        items = [*active, *(item for item in settled if item.id not in active_ids)]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return [serialize_job(item) for item in items]


#: What an abandoned job's error says. Written once so the worker, the API and
#: the tests all agree on the wording somebody will read on a stuck row.
ABANDONED_ERROR = (
    "The worker handling this stopped before it finished, and no attempts were "
    "left to retry with. Anything already downloaded is on disk; resume to "
    "finish from what is there."
)


def settle_expired_cancellations(
    kind: str,
    limit: int = 50,
    *,
    factory: SessionMaker = SessionFactory,
) -> list[str]:
    """Finish cancellations whose worker disappeared before acknowledging them.

    A running job is cancelled cooperatively: the request sets a flag, and the
    worker removes partial output at its next safe point. If that worker exits,
    recovery correctly refuses to reclaim a cancelled job, but nothing used to
    give the row a terminal state. It therefore stayed ``running`` forever.

    A live lease is left alone so an active worker can still perform its normal
    cleanup. Only queued jobs, expired leases, and legacy running rows with no
    lease are safe to settle here.
    """
    timestamp = now_utc()
    settled: list[str] = []
    with factory.begin() as session:
        items = session.scalars(
            select(DurableJob)
            .where(
                DurableJob.kind == kind,
                DurableJob.cancellation_requested.is_(True),
                DurableJob.status.in_(("queued", "running")),
                or_(
                    DurableJob.status == "queued",
                    DurableJob.lease_expires_at.is_(None),
                    DurableJob.lease_expires_at <= timestamp,
                ),
            )
            .order_by(DurableJob.created_at)
            .limit(limit)
        ).all()
        for item in items:
            item.status = "cancelled"
            item.progress_stage = "Cancelled"
            item.lease_owner = None
            item.lease_expires_at = None
            item.completed_at = timestamp
            item.updated_at = timestamp
            settled.append(item.id)
    return settled


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


def upgrade_active_job_recovery(
    kind: str,
    *,
    max_attempts: int,
    maximum_lease_seconds: int | None = None,
    factory: SessionMaker = SessionFactory,
) -> list[str]:
    """Give legacy unfinished jobs the recovery policy used by new workers.

    Queue policy changes must cover rows already on disk, not only jobs created
    after an upgrade.  This is deliberately limited to non-terminal jobs of one
    explicitly named kind.  A long lease can also be shortened during a policy
    migration: the old effect renderer used a one-hour lease without heartbeats,
    which made a process restart look like an hour-long render that never moved.
    """
    timestamp = now_utc()
    latest_lease = (
        timestamp + timedelta(seconds=maximum_lease_seconds)
        if maximum_lease_seconds is not None
        else None
    )
    changed: list[str] = []
    with factory.begin() as session:
        items = session.scalars(
            select(DurableJob).where(
                DurableJob.kind == kind,
                DurableJob.status.in_(("queued", "running")),
            )
        ).all()
        for item in items:
            dirty = False
            if item.max_attempts < max_attempts:
                item.max_attempts = max_attempts
                dirty = True
            if (
                latest_lease is not None
                and item.status == "running"
                and item.lease_expires_at is not None
                and as_utc(item.lease_expires_at) > latest_lease
            ):
                item.lease_expires_at = latest_lease
                dirty = True
            if dirty:
                item.updated_at = timestamp
                changed.append(item.id)
    return changed


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


def report_progress(
    job_id: str,
    fraction: float,
    stage: str,
    *,
    factory: SessionMaker = SessionFactory,
) -> None:
    """Record how far a running job has got. Best effort, never fatal.

    Called from inside a render loop, so it must not be able to stop one. A
    database that is briefly unavailable costs a stale progress figure; raising
    here would cost the render, which is minutes of work thrown away for a
    cosmetic field.

    Only a running job is updated. A cancelled or finished one keeps whatever it
    ended on rather than being dragged back to a fraction by a worker that has
    not noticed yet.
    """
    try:
        with factory.begin() as session:
            item = session.get(DurableJob, job_id)
            if not item or item.status != "running":
                return
            item.progress = max(0.0, min(1.0, float(fraction)))
            item.progress_stage = stage[:80]
            item.updated_at = now_utc()
    except Exception:  # pragma: no cover - a cosmetic field must not break work
        return


class ProgressReporter:
    """Throttled progress, safe to call on every frame of a render.

    A render loop runs thousands of times and a write per frame would cost more
    than the work being reported on. This keeps the call site simple — one line
    in the loop — and decides for itself when that is worth a round trip.

    Stages are weighted rather than counted. A blur reads the whole clip before
    it draws anything, so a reporter that treated its two passes as halves would
    sit at 50% for as long as it sat at 0%. The caller says how much of the
    total each pass is worth.
    """

    #: A second is finer than the interface polls for and coarse enough that the
    #: writes disappear next to decoding a frame.
    INTERVAL_SECONDS = 1.0

    def __init__(
        self,
        write: Callable[[float, str], None] | None,
        *,
        stage: str = "",
        start: float = 0.0,
        span: float = 1.0,
        clock: list[float] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self._write = write
        self._stage = stage
        self._start = start
        self._span = span
        self._should_cancel = should_cancel
        # A one-cell list rather than a float, so every stage of one render
        # shares it. Copying the value would reset the throttle at each pass
        # boundary, and a recipe of six effects would take six free writes for
        # no new information.
        self._clock = clock if clock is not None else [0.0]

    def stage(self, name: str, start: float, span: float) -> ProgressReporter:
        """A reporter for one pass, mapped onto its slice of the whole."""
        return ProgressReporter(
            self._write,
            stage=name,
            start=start,
            span=span,
            clock=self._clock,
            should_cancel=self._should_cancel,
        )

    def point(self, fraction: float, *, force: bool = False) -> None:
        """Report a pass boundary even when that pass has no frame callback.

        Some effects can describe every decoded frame; ffmpeg-only effects and
        a few model calls are opaque single operations.  Their beginning and
        end still belong on the same progress bar, rather than leaving an
        apparently idle job until it suddenly completes.
        """
        if not self._write and not self._should_cancel:
            return
        fraction = max(0.0, min(1.0, float(fraction)))
        moment = monotonic()
        if not force and fraction < 1.0 and moment - self._clock[0] < self.INTERVAL_SECONDS:
            return
        self._clock[0] = moment
        if self._should_cancel and self._should_cancel():
            raise JobCancellationRequested
        if self._write:
            self._write(self._start + self._span * fraction, self._stage)

    def started(self) -> None:
        self.point(0.0, force=True)

    def finished(self) -> None:
        self.point(1.0, force=True)

    def at(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self.point((done + 1) / total, force=done + 1 >= total)


class JobCancellationRequested(RuntimeError):
    """A cooperative worker reached a safe point after cancellation was requested."""


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
        if item.status == "succeeded":
            item.progress = 1.0
            item.progress_stage = "Complete"
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
