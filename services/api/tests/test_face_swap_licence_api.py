"""Recording what permits face swapping, through the API.

The gate itself is tested in `test_face_swap`. What matters here is that the
recording is an owner's deliberate act and that the audit row keeps the footing
- not merely that somebody switched something on. If the permission is ever
questioned, that row is the answer.
"""

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.integrations import face_swap
from trendrelay_api.main import app
from trendrelay_api.models import AuditEvent, Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(call(method, path, **kwargs))


@pytest.fixture(autouse=True)
def api(monkeypatch, tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="swap-owner", email="owner@example.com", assurance_level="aal2",
    )
    # Never write the operator's real licence record from a test.
    monkeypatch.setattr(face_swap, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(face_swap, "LICENCE_FILE", tmp_path / "licence.json")
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def workspace() -> str:
    response = request(
        "POST", "/api/workspaces", json={"name": "Swap Lab", "slug": "swap-lab"}
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def path(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/media/library/effects/face-swap/licence"


def record(workspace_id: str, **body) -> httpx.Response:
    return request(
        "POST",
        path(workspace_id),
        json={
            "basis": "research",
            "reference": "Grant 41/2026",
            "confirm_external_action": True,
            **body,
        },
    )


# --- recording ----------------------------------------------------------------


def test_research_use_is_recorded_as_itself(workspace) -> None:
    """Not as a commercial licence nobody bought."""
    response = record(workspace)

    assert response.status_code == 200
    assert response.json()["licence_basis"] == "research"
    assert face_swap.licence_record()["reference"] == "Grant 41/2026"


def test_a_commercial_licence_is_recorded_as_itself_too(workspace) -> None:
    response = record(workspace, basis="commercial", reference="INV-2026-0042")

    assert response.json()["licence_basis"] == "commercial"
    assert "Permits commercial use" in response.json()["licence_terms"]


def test_recording_needs_confirming(workspace) -> None:
    response = record(workspace, confirm_external_action=False)

    assert response.status_code == 400
    assert face_swap.licence_record() is None


def test_a_footing_with_nothing_behind_it_is_refused(workspace) -> None:
    # The record that proves worthless exactly when it is asked for.
    response = record(workspace, reference="")

    assert response.status_code == 422
    assert face_swap.licence_record() is None


def test_an_invented_footing_is_refused(workspace) -> None:
    response = record(workspace, basis="fair-use")

    assert response.status_code == 422


def test_withdrawing_the_footing_closes_the_gate_again(workspace) -> None:
    record(workspace)

    response = record(workspace, licensed=False)

    assert response.json()["licence_recorded"] is False
    assert response.json()["available"] is False


# --- who may, and what is kept ------------------------------------------------


def test_an_editor_cannot_assert_this_on_the_organisations_behalf(workspace) -> None:
    """Owners only: it is a claim about what the company is allowed to do."""
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="an-editor", email="editor@example.com", assurance_level="aal2",
    )

    response = record(workspace)

    assert response.status_code in {403, 404}
    assert face_swap.licence_record() is None


def test_the_audit_row_keeps_the_footing_not_just_the_switch(workspace) -> None:
    record(workspace)

    with TestingSession() as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "media.face_swap_licence_recorded"
            )
        )
    assert event is not None
    assert event.detail["basis"] == "research"
    assert event.detail["reference"] == "Grant 41/2026"
    assert event.actor_user_id == "swap-owner"


# --- reading it back ----------------------------------------------------------


def test_the_status_says_what_is_still_missing(workspace) -> None:
    """A recorded footing is not a model on disk."""
    record(workspace)

    response = request("GET", path(workspace))

    assert response.json()["available"] is False
    assert "No swap model is present" in response.json()["reason"]


def test_the_status_never_points_at_a_mirror(workspace) -> None:
    # Somebody reading this must not come away thinking the fix is to go and
    # find a copy somewhere.
    record(workspace)

    reason = request("GET", path(workspace)).json()["reason"]

    assert "does not download" in reason
    assert "does not ship a mirror" in reason
