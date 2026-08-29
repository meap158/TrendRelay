"""Native post performance, collected where a provider can actually report it.

The learning loop needs what the platforms saw - views, comments, likes,
shares - and none of it can be invented. A snapshot is recorded against a
publication execution at fixed windows after it published, timestamped, and
appended rather than overwritten, so a number can be seen moving.

What is deliberately absent: a reader for any current engine. None of the four
publishing engines' adapters exposes a post-metrics read today, and this
module says so instead of synthesising figures - the same shown-and-refused
shape a gated effect uses. When an engine gains a metrics endpoint, its reader
registers here and the collection loop starts filling windows without anything
else changing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.publication_models import PublicationExecution

#: When to look, measured from the moment the provider confirmed the post.
#: Early enough to see a launch, late enough to see the tail.
MEASUREMENT_WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("2h", timedelta(hours=2)),
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
)

#: Snapshots kept per execution. Three windows is the design; the headroom is
#: for a re-read after a provider correction, not for a series.
MAX_SNAPSHOTS = 20

#: The metric names a snapshot may carry. A reader reporting anything else is
#: a reader that changed shape, and the extra is dropped rather than stored as
#: a figure nothing downstream understands.
METRIC_FIELDS = ("views", "likes", "comments", "shares", "saves", "watch_seconds")


def _clean(metrics: dict[str, Any]) -> dict[str, float]:
    """The recognised numbers, as floats. What a snapshot would actually store."""
    return {
        field: float(metrics[field])
        for field in METRIC_FIELDS
        if isinstance(metrics.get(field), (int, float))
    }


def _reads_as_nothing(metrics: dict[str, Any]) -> bool:
    """Whether this read says every figure is zero.

    Not the same as a post that earned nothing. A network that has not
    reported to its scheduler yet is relayed as zeros rather than as silence -
    Buffer documents exactly this, that a metric the network did not supply
    reads 0 - so a read of all zeros is the one shape that means "ask again"
    and "there is nothing to see" equally.
    """
    cleaned = _clean(metrics)
    return bool(cleaned) and not any(cleaned.values())


def _believed_after(index: int) -> timedelta:
    """How long a window waits before an all-zero read is taken at face value.

    The next window's own delay, and for the last window twice its own: a zero
    is believed once there has been at least as long again to hear otherwise.
    Without a bound the retry never ends, and a post that genuinely earned
    nothing would be polled forever against a request budget that publishing
    needs.
    """
    if index + 1 < len(MEASUREMENT_WINDOWS):
        return MEASUREMENT_WINDOWS[index + 1][1]
    return MEASUREMENT_WINDOWS[index][1] * 2

#: One reader per provider id, given the execution and returning metric fields
#: or None when the post cannot be read. Empty on purpose - see the module
#: docstring. Registration is the extension point.
PROVIDER_METRIC_READERS: dict[
    str, Callable[[PublicationExecution], dict[str, Any] | None]
] = {}

#: How a stored provider becomes the engine whose reader can answer for it.
#:
#: An execution stores what its destination stored, which is a *connection* -
#: `zernio-zernio-2` for a second Zernio login - while the readers above are
#: registered per engine. Looked up raw, a second login matched nothing, and
#: every post it published sat at `published` for good: never measured, never
#: retried, and reported as an engine that cannot be read.
#:
#: Injected rather than imported, like the readers, so this module still knows
#: nothing about any engine. Identity until something registers otherwise,
#: which keeps a single-login workspace working with no engine loaded at all.
def _same_provider(provider: str) -> str | None:
    """The default: a workspace with one login per engine resolves to itself."""
    return provider


PROVIDER_ENGINE_RESOLVER: Callable[[str], str | None] = _same_provider


def reader_status() -> dict[str, Any]:
    """Which engines can be measured, said plainly for a screen.

    An empty registry used to be the ordinary state, and the note said so. It
    is now a fault: reading back is a condition of supporting an engine, so
    nothing registered means registration did not run rather than that no engine
    can report. The note says the thing that is actually true, because the
    screen it reaches is the one an operator checks when the figures are missing.
    """
    return {
        "readable_providers": sorted(PROVIDER_METRIC_READERS),
        "note": (
            "No engine is registered for reading post metrics, which should not "
            "happen - every supported engine either reads back or records why it "
            "cannot. Snapshots stay empty until this is repaired."
            if not PROVIDER_METRIC_READERS else None
        ),
    }


def record_snapshot(
    execution: PublicationExecution,
    metrics: dict[str, Any],
    *,
    window: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Append one timestamped observation to an execution.

    Only a provider-confirmed post can be measured: recording a snapshot on a
    failed or uncertain execution would be exactly the "failure becomes a
    positive observation" corruption the learning loop must never allow.
    """
    if execution.state not in ("published", "measured"):
        raise ValueError(
            f"Only a published execution can be measured; this one is {execution.state}."
        )
    moment = at or datetime.now(UTC)
    snapshot = {"at": moment.isoformat(), "window": window, "metrics": _clean(metrics)}
    existing = list(execution.performance_snapshots or [])
    existing.append(snapshot)
    execution.performance_snapshots = existing[-MAX_SNAPSHOTS:]
    execution.state = "measured"
    execution.updated_at = moment
    return snapshot


def _windows_captured(execution: PublicationExecution) -> set[str]:
    return {
        str(snapshot.get("window"))
        for snapshot in execution.performance_snapshots or []
    }


def collect_snapshots(
    session: Session, *, now: datetime | None = None
) -> dict[str, Any]:
    """Fill due measurement windows for every readable published post.

    Cheap when nothing can be read: with no reader registered this reports the
    providers it cannot ask and touches nothing. Failures to read are skipped,
    not recorded - an unread window stays due and is retried on the next pass.

    A read of all zeros is skipped the same way, until there has been long
    enough to hear otherwise. It is not a failure and not an observation: a
    network that has not reported to its scheduler yet is relayed as zeros, so
    recording one froze "not known yet" into the campaign as "earned nothing"
    and closed the window against ever being asked again. Sixty-nine posts
    were reopened by hand for this before the cause was found, and four more
    arrived the same way afterwards.
    """
    moment = now or datetime.now(UTC)
    captured = 0
    unreadable: set[str] = set()
    rows = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.state.in_(("published", "measured")),
            PublicationExecution.published_at.is_not(None),
        )
    ).all()
    for execution in rows:
        engine = PROVIDER_ENGINE_RESOLVER(execution.provider or "") or ""
        reader = PROVIDER_METRIC_READERS.get(engine)
        if reader is None:
            if execution.provider:
                unreadable.add(execution.provider)
            continue
        published_at = execution.published_at
        if published_at is None:
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        done = _windows_captured(execution)
        due = [
            (index, window)
            for index, (window, delay) in enumerate(MEASUREMENT_WINDOWS)
            if window not in done and moment >= published_at + delay
        ]
        if not due:
            continue
        # Once per post, not once per window. A reader answers about the post
        # rather than about the window, so asking again for each due window
        # spent three requests on one answer - against the same budget the
        # Buffer reader has to reserve publishing capacity out of.
        metrics = reader(execution)
        if metrics is None:
            continue
        nothing = _reads_as_nothing(metrics)
        for index, window in due:
            if nothing and moment < published_at + _believed_after(index):
                continue
            record_snapshot(execution, metrics, window=window, at=moment)
            captured += 1
    return {"captured": captured, "unreadable_providers": sorted(unreadable)}


def latest_metrics(execution: PublicationExecution) -> dict[str, float]:
    """The newest snapshot's figures, which is what an aggregate should read.

    Snapshots are cumulative platform counters, so summing windows would count
    the first two hours three times.
    """
    snapshots = execution.performance_snapshots or []
    if not snapshots:
        return {}
    return dict(snapshots[-1].get("metrics") or {})


def destination_engagement(
    session: Session, destination_ids: list[str]
) -> dict[str, dict[str, float]]:
    """Measured engagement per destination, from its posts' latest snapshots.

    Only measured posts count toward `posts_measured` - a published post whose
    provider cannot be read is invisible here rather than a zero, because a
    zero is a claim about the audience and an unread post makes none.
    """
    found: dict[str, dict[str, float]] = {}
    if not destination_ids:
        return found
    rows = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.destination_id.in_(destination_ids),
            PublicationExecution.state == "measured",
        )
    ).all()
    for execution in rows:
        metrics = latest_metrics(execution)
        if not metrics or not execution.destination_id:
            continue
        bucket = found.setdefault(execution.destination_id, {
            "posts_measured": 0.0, "views": 0.0, "likes": 0.0,
            "comments": 0.0, "shares": 0.0, "saves": 0.0,
        })
        bucket["posts_measured"] += 1
        for field in ("views", "likes", "comments", "shares", "saves"):
            bucket[field] += float(metrics.get(field, 0))
    return found
