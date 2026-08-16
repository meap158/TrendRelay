"""The evidence a campaign was started because of, kept rather than summarised.

The idea basket used to be ephemeral: creating a campaign from it persisted the
synthesised name and objective and discarded the source URL, provider, region
and metrics that argued for it. These tests are mostly about what survives the
handoff, because that is the whole point of the record.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.signal_models import (
    CampaignSignal,
    default_expiry,
    describe,
    is_stale,
)

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def api():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="signal-owner", email="owner@example.com", assurance_level="aal2",
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def workspace() -> str:
    response = request("POST", "/api/workspaces", json={"name": "Lab", "slug": "lab"})
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def signal(**changes) -> dict:
    return {
        "external_id": "topic:VN:giaydep",
        "kind": "topic",
        "label": "Giày dép",
        "provider": "google-trends",
        "source_url": "https://trends.google.com/trending",
        "region": "VN",
        "evidence": "Rising for 3 days across two boards",
        "observed": {"rank": 4, "searches": 50000},
        "trend_shape": "rising",
        "tags": ["durable"],
        "angles": ["Show the unboxing"],
        **changes,
    }


def create(workspace_id: str, **body) -> httpx.Response:
    return request(
        "POST",
        f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "name": "Back to school",
            "objective": "Revenue from stationery offers",
            "audience": "Students in Vietnam",
            "markets": ["VN"],
            "languages": ["vi"],
            **body,
        },
    )


# --- what survives the handoff ------------------------------------------------


def test_a_campaign_can_still_be_created_with_no_evidence(workspace) -> None:
    """Starting from a blank form is not a lesser path; it must keep working."""
    response = create(workspace)

    assert response.status_code == 201
    assert response.json()["signals"] == []


def test_the_evidence_is_kept_whole(workspace) -> None:
    """Not summarised into the objective, which is what used to happen."""
    response = create(workspace, signals=[signal()])

    kept = response.json()["signals"]
    assert len(kept) == 1
    assert kept[0]["source_url"] == "https://trends.google.com/trending"
    assert kept[0]["provider"] == "google-trends"
    assert kept[0]["region"] == "VN"
    assert kept[0]["evidence"] == "Rising for 3 days across two boards"
    assert kept[0]["observed"] == {"rank": 4, "searches": 50000}
    assert kept[0]["trend_shape"] == "rising"


def test_a_signal_is_bound_to_the_campaign_it_argued_for(workspace) -> None:
    body = create(workspace, signals=[signal()]).json()

    with TestingSession() as db:
        stored = db.scalars(select(CampaignSignal)).all()

    assert len(stored) == 1
    assert stored[0].campaign_id == body["campaign"]["id"]
    assert stored[0].workspace_id == workspace


def test_several_pieces_of_evidence_are_kept_separately(workspace) -> None:
    """A basket is several observations, not one averaged claim."""
    response = create(workspace, signals=[
        signal(),
        signal(external_id="post:reddit:abc", kind="post", label="A thread",
               provider="reddit", creator="r/vietnam"),
    ])

    kept = response.json()["signals"]
    assert {item["kind"] for item in kept} == {"topic", "post"}
    assert {item["provider"] for item in kept} == {"google-trends", "reddit"}


def test_the_audit_records_that_evidence_was_used(workspace) -> None:
    """A campaign started from evidence and one from a blank form differ."""
    from trendrelay_api.models import AuditEvent

    create(workspace, signals=[signal()])

    with TestingSession() as db:
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "campaign.created")
        ).first()

    assert event is not None and event.detail["signals"] == 1


# --- shapes the boards actually produce ---------------------------------------


def test_a_signal_with_almost_nothing_on_it_is_accepted(workspace) -> None:
    """Boards differ: a search trend has no creator, a post has no volume.

    Demanding a uniform shape would mean inventing fields, and an invented
    field reads exactly like a measured one later.
    """
    response = create(workspace, signals=[
        {"external_id": "topic:US:x", "kind": "topic", "label": "Something"}
    ])

    assert response.status_code == 201
    kept = response.json()["signals"][0]
    assert kept["provider"] is None and kept["observed"] == {}


def test_an_unknown_kind_is_refused(workspace) -> None:
    response = create(workspace, signals=[signal(kind="rumour")])

    assert response.status_code == 422


# --- freshness ----------------------------------------------------------------


def test_evidence_carries_an_expiry_so_it_can_go_stale(workspace) -> None:
    """Trends move; a fortnight-old ranking is a claim about the past."""
    kept = create(workspace, signals=[signal()]).json()["signals"][0]

    assert kept["expires_at"] is not None
    assert kept["stale"] is False


def test_staleness_is_read_rather_than_swept(workspace) -> None:
    """Nothing has to run for the answer to be true."""
    collected = datetime(2026, 1, 1, tzinfo=UTC)
    item = CampaignSignal(
        workspace_id="ws", external_id="x", kind="topic", label="Old",
        collected_at=collected, expires_at=default_expiry(collected),
    )

    assert is_stale(item, at=collected + timedelta(days=1)) is False
    assert is_stale(item, at=collected + timedelta(days=30)) is True


def test_evidence_with_no_expiry_never_goes_stale() -> None:
    item = CampaignSignal(
        workspace_id="ws", external_id="x", kind="topic", label="Forever",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC), expires_at=None,
    )

    assert is_stale(item) is False


def test_describing_a_signal_never_invents_a_field() -> None:
    item = CampaignSignal(
        workspace_id="ws", external_id="x", kind="topic", label="Bare",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    shown = describe(item)

    assert shown["observed"] == {} and shown["tags"] == [] and shown["angles"] == []
    assert shown["provider"] is None
