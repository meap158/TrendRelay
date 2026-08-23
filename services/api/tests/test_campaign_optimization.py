"""The closed loop: measured evidence in, objective-weighted choices out.

The brief's own test matrix: sparse data stays unranked, objective weights
change choices, failures never become positive observations, unavailable
offers are replaced safely, and exploration remains deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.campaign_autopilot import (
    DestinationRank,
    choose_destination,
    rank_destinations,
)
from trendrelay_api.campaign_measurement import (
    PROVIDER_METRIC_READERS,
    collect_snapshots,
    destination_engagement,
    latest_metrics,
    reader_status,
    record_snapshot,
)
from trendrelay_api.models import Base, UserProfile, Workspace
from trendrelay_api.publication_models import PublicationExecution

# Imported for the side effect of registering every table on `Base.metadata`.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def dest(identifier: str) -> dict[str, object]:
    return {"id": identifier, "platform": "youtube"}


REVENUE_LEADER = {
    "a": {"clicks": 100.0, "conversions": 8.0, "net_commission_cents": 4000.0},
    "b": {"clicks": 100.0, "conversions": 6.0, "net_commission_cents": 1000.0},
}
REACH_LEADER = {
    "a": {"posts_measured": 6.0, "views": 600.0, "comments": 12.0},
    "b": {"posts_measured": 6.0, "views": 9000.0, "comments": 300.0},
}


# --- ranking by objective -------------------------------------------------------


def test_sparse_data_stays_unranked_on_every_axis() -> None:
    ranks = rank_destinations(
        [dest("a")],
        {"a": {"clicks": 10, "conversions": 2, "net_commission_cents": 900}},
        engagement={"a": {"posts_measured": 3, "views": 40000}},
        priority="reach",
    )

    [rank] = ranks
    assert rank.ranked is False
    assert "3 measured post(s)" in rank.reason
    assert rank.score is None


def test_objective_weights_change_the_choice() -> None:
    """The same evidence, three different winners by declared priority."""
    destinations = [dest("a"), dest("b")]

    by_revenue = rank_destinations(
        destinations, REVENUE_LEADER, engagement=REACH_LEADER, priority="revenue"
    )
    by_reach = rank_destinations(
        destinations, REVENUE_LEADER, engagement=REACH_LEADER, priority="reach"
    )
    by_discussion = rank_destinations(
        destinations, REVENUE_LEADER, engagement=REACH_LEADER, priority="discussion"
    )

    assert by_revenue[0].destination_id == "a"
    assert by_reach[0].destination_id == "b"
    assert by_discussion[0].destination_id == "b"
    assert "views" in by_reach[0].reason
    assert "comments" in by_discussion[0].reason


def test_balanced_blends_only_the_axes_with_evidence() -> None:
    # `a` qualifies on revenue alone, `b` on reach alone; both rank, and each
    # reason names the axis it stood on. `c` qualifies nowhere and stays out.
    ranks = rank_destinations(
        [dest("a"), dest("b"), dest("c")],
        REVENUE_LEADER | {"c": {"clicks": 2, "conversions": 0, "net_commission_cents": 0}},
        engagement={"b": REACH_LEADER["b"]},
        priority="balanced",
    )

    by_id = {rank.destination_id: rank for rank in ranks}
    assert by_id["a"].ranked and "revenue" in by_id["a"].reason
    assert by_id["b"].ranked and "reach" in by_id["b"].reason
    assert by_id["c"].ranked is False
    assert "No axis has enough evidence" in by_id["c"].reason


def test_the_stop_loss_reads_revenue_whatever_the_objective() -> None:
    """Fifty clicks and nothing settled is a measurement, and a bad one."""
    ranks = rank_destinations(
        [dest("a"), dest("b")],
        {
            "a": {"clicks": 80, "conversions": 6, "net_commission_cents": 0},
            "b": {"clicks": 4, "conversions": 0, "net_commission_cents": 0},
        },
        priority="revenue",
    )

    stopped = next(rank for rank in ranks if rank.destination_id == "a")
    assert stopped.stopped is True
    assert stopped.ranked is False
    assert "Stop-loss" in stopped.reason
    # Measured-to-be-losing sorts below not-yet-measured.
    assert ranks[-1].destination_id == "a"


def test_exploration_remains_deterministic_and_reproducible() -> None:
    ranks = [
        DestinationRank("a", "youtube", 40.0, 8, True, "leader.", score=40.0),
        DestinationRank("b", "youtube", None, 0, False, "unranked."),
        DestinationRank("c", "youtube", None, 0, False, "unranked."),
    ]

    sequence = [
        choose_destination(ranks, posts_so_far=count).destination_id
        for count in range(12)
    ]

    assert sequence == [
        choose_destination(ranks, posts_so_far=count).destination_id
        for count in range(12)
    ], "the same counters must produce the same schedule"
    # Counters 4 and 8 explore; the other ten default to the leader.
    assert sequence.count("a") == 10, "the leader takes the default slots"
    assert set(sequence) == {"a", "b", "c"}, "every destination gets explored"


# --- measurement ----------------------------------------------------------------


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        active.add(UserProfile(id="user-1", email="a@example.test"))
        active.add(Workspace(id="ws", name="W", slug="w", created_by="user-1"))
        active.commit()
        yield active


def execution(session, identifier: str, state: str, **overrides) -> PublicationExecution:
    fields: dict = {
        "workspace_id": "ws", "media_path": "clip.mp4", "provider": "buffer",
        "destination_id": "d1", "published_at": NOW,
    }
    fields.update(overrides)
    row = PublicationExecution(id=identifier, state=state, **fields)
    session.add(row)
    session.commit()
    return row


def test_a_failure_can_never_become_a_positive_observation(session) -> None:
    failed = execution(session, "x1", "failed")

    with pytest.raises(ValueError, match="Only a published execution"):
        record_snapshot(failed, {"views": 100}, window="2h", at=NOW)

    assert failed.performance_snapshots == []
    assert destination_engagement(session, ["d1"]) == {}


def test_collection_says_which_providers_cannot_be_read(session) -> None:
    execution(session, "x1", "published")

    result = collect_snapshots(session, now=NOW + timedelta(days=8))

    assert result == {"captured": 0, "unreadable_providers": ["buffer"]}
    # Zernio exposes a post-metrics read, so the interface no longer says that
    # nothing can be measured: it names the engine that cannot be read - Buffer -
    # while listing the one that can.
    status = reader_status()
    assert status["note"] is None
    assert "zernio" in status["readable_providers"]
    assert "buffer" not in status["readable_providers"]


def test_a_registered_reader_fills_each_window_once(session) -> None:
    row = execution(session, "x1", "published")
    PROVIDER_METRIC_READERS["buffer"] = lambda _execution: {
        "views": 500, "comments": 4, "spam_field": 1,
    }
    try:
        first = collect_snapshots(session, now=NOW + timedelta(hours=3))
        second = collect_snapshots(session, now=NOW + timedelta(hours=3))
        late = collect_snapshots(session, now=NOW + timedelta(days=8))
    finally:
        del PROVIDER_METRIC_READERS["buffer"]

    assert first["captured"] == 1, "the 2h window was due"
    assert second["captured"] == 0, "a captured window is not captured again"
    assert late["captured"] == 2, "24h and 7d windows fill when they fall due"
    assert row.state == "measured"
    assert [snapshot["window"] for snapshot in row.performance_snapshots] == [
        "2h", "24h", "7d",
    ]
    assert "spam_field" not in latest_metrics(row), (
        "a reader that changed shape loses the field, not invents a metric"
    )


def test_engagement_reads_the_latest_snapshot_not_the_sum(session) -> None:
    row = execution(session, "x1", "published")
    record_snapshot(row, {"views": 100, "comments": 2}, window="2h", at=NOW)
    record_snapshot(row, {"views": 900, "comments": 11}, window="24h", at=NOW)
    session.commit()

    found = destination_engagement(session, ["d1"])

    assert found["d1"]["views"] == 900, (
        "platform counters are cumulative; summing windows would count the "
        "first two hours twice"
    )
    assert found["d1"]["posts_measured"] == 1
