"""The clock a workspace keeps, as a setting rather than a side effect.

A posting slot is stored as a wall time - weekday, hour, minute - and means
nothing until this says where that hour is. It was a column with no way to set
it: `UTC` by default, written only as a side effect of saving posting times. So
a workspace run from Bangkok scheduled its posts in London, and every time in
the interface read seven hours off.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

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


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(go())


@pytest.fixture
def workspace():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")
    body = request(
        "POST", "/api/workspaces", json={"name": "Workspace", "slug": "workspace"},
    ).json()
    yield body["workspace"]["id"]
    app.dependency_overrides.clear()


def test_a_workspace_says_which_clock_it_keeps(workspace) -> None:
    """It travels with the workspace, not with the corner that owns slots.

    Every screen reads times off it, so fetching it from the publishing API
    would make the toolbar depend on a page it has nothing to do with.
    """
    body = request("GET", "/api/workspaces").json()
    found = next(item for item in body["workspaces"] if item["id"] == workspace)

    assert found["timezone"] == "UTC"


def test_the_clock_can_be_set_and_comes_back_set(workspace) -> None:
    response = request(
        "PUT", f"/api/workspaces/{workspace}/timezone",
        json={"timezone": "Asia/Bangkok"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["workspace"]["timezone"] == "Asia/Bangkok"

    listed = request("GET", "/api/workspaces").json()["workspaces"]
    assert next(item for item in listed if item["id"] == workspace)["timezone"] == "Asia/Bangkok"


def test_a_zone_that_is_not_one_is_refused(workspace) -> None:
    # A stored name nobody can resolve would make every time on every screen
    # unreadable, and the failure would surface far from here.
    response = request(
        "PUT", f"/api/workspaces/{workspace}/timezone",
        json={"timezone": "Middle/Earth"},
    )

    assert response.status_code == 422
    assert "IANA" in response.text


def test_an_offset_is_not_accepted_in_place_of_a_zone(workspace) -> None:
    """"+07:00" is not a zone: it cannot say when the clocks change."""
    response = request(
        "PUT", f"/api/workspaces/{workspace}/timezone", json={"timezone": "+07:00"},
    )

    assert response.status_code == 422
