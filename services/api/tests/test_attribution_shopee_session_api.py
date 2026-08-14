"""Connecting Shopee, and what the API will say about a stored session.

The session is somebody's own Shopee login, and everything imported with it is
done as them. So these tests are mostly about what leaves the API rather than
what it stores: a cookie value must never appear in a response, in an audit
row, or in a probe result.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import attribution_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.integrations import shopee_session as shopee
from trendrelay_api.main import app
from trendrelay_api.models import AuditEvent, Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

SECRET = "SPC_EC=secret-value-abc123; SPC_U=42; SPC_ST=xyz"


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
        id="shopee-owner", email="owner@example.com", assurance_level="aal2",
    )
    attribution_api.get_settings = lambda: SimpleNamespace(
        attribution_public_url="https://go.example.test",
        attribution_hash_secret=SecretStr("test-attribution-secret"),
    )
    # Never touch the operator's real session from a test.
    monkeypatch.setattr(shopee, "COOKIE_FILE", tmp_path / "cookies.json")
    monkeypatch.delenv(shopee.COOKIE_ENV, raising=False)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def workspace() -> str:
    response = request(
        "POST", "/api/workspaces", json={"name": "Shopee Lab", "slug": "shopee-lab"}
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def connect(workspace_id: str, header: str = SECRET, **extra) -> httpx.Response:
    return request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/shopee/session",
        json={"cookie_header": header, "confirm_external_action": True, **extra},
    )


# --- connecting ---------------------------------------------------------------


def test_a_pasted_cookie_header_connects_the_session(workspace) -> None:
    """Pasted whole, because that is the form it can be copied in."""
    response = connect(workspace)

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert shopee.load_cookies()[0]["SPC_EC"] == "secret-value-abc123"


def test_an_unconnected_workspace_says_so_rather_than_failing(workspace) -> None:
    response = request(
        "GET", f"/api/workspaces/{workspace}/attribution/shopee/session"
    )

    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["missing"] == ["SPC_EC", "SPC_U"]


def test_one_cookie_instead_of_the_header_names_what_is_missing(workspace) -> None:
    # The usual mistake, and "that did not work" would not help anybody fix it.
    response = connect(workspace, "SPC_EC=only-one")

    assert response.status_code == 422
    assert "SPC_U" in response.json()["detail"]


def test_storing_a_session_needs_confirming(workspace) -> None:
    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/session",
        json={"cookie_header": SECRET},
    )

    assert response.status_code == 400
    assert shopee.load_cookies()[0] == {}


def test_disconnecting_removes_the_session(workspace) -> None:
    connect(workspace)

    response = request(
        "POST", f"/api/workspaces/{workspace}/attribution/shopee/session/disconnect"
    )

    assert response.status_code == 204
    assert shopee.load_cookies() == ({}, "none")


# --- what leaves the API ------------------------------------------------------


def test_no_response_ever_carries_a_cookie_value(workspace) -> None:
    """Read by a browser, and logged by whatever sits in front of it."""
    stored = connect(workspace)
    read = request("GET", f"/api/workspaces/{workspace}/attribution/shopee/session")

    for response in (stored, read):
        assert "secret-value-abc123" not in response.text


def test_the_audit_row_counts_cookies_rather_than_keeping_them(workspace) -> None:
    # This row is kept for as long as the workspace is.
    connect(workspace)

    with TestingSession() as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "attribution.shopee_session_connected"
            )
        )
    assert event is not None
    assert event.detail == {"cookies": 3}


# --- the probe ----------------------------------------------------------------


def test_a_probe_reports_which_step_broke(workspace, monkeypatch) -> None:
    """"The import did not work" is not something anybody can act on."""
    connect(workspace)
    monkeypatch.setattr(
        shopee, "probe",
        lambda *_args, **_kwargs: {"ok": False, "reconnect": True, "stages": [
            {"id": "session", "label": "Session stored", "ok": True, "detail": "Found."},
            {"id": "reach", "label": "Product page reachable", "ok": False,
             "detail": "Shopee refused the request as a signed-out visitor."},
        ]},
    )

    response = request(
        "POST", f"/api/workspaces/{workspace}/attribution/shopee/session/probe"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reconnect"] is True
    assert [stage["ok"] for stage in body["stages"]] == [True, False]


def test_probing_without_a_session_stops_at_the_first_failure(workspace) -> None:
    # Reporting "could not parse the product" underneath "not signed in" would
    # bury the cause under its own consequences.
    response = request(
        "POST", f"/api/workspaces/{workspace}/attribution/shopee/session/probe"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert len(body["stages"]) == 1
    assert body["stages"][0]["id"] == "session"


# --- signing in rather than pasting -------------------------------------------


def connect_window(workspace_id: str, host: str = "127.0.0.1", **body) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=(host, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/api/workspaces/{workspace_id}/attribution/shopee/session/connect",
                json={"confirm_external_action": True, **body},
            )
    return asyncio.run(go())


def test_opening_the_window_needs_confirming(workspace, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(shopee, "start_connection", lambda: opened.append(1) or {})

    response = connect_window(workspace, confirm_external_action=False)

    assert response.status_code == 400
    assert opened == [], "no browser should have been launched"


def test_a_remote_caller_is_refused_because_the_window_opens_here(workspace, monkeypatch) -> None:
    """Not policy: it opens a window on whatever machine the API runs on."""
    opened = []
    monkeypatch.setattr(shopee, "start_connection", lambda: opened.append(1) or {})

    response = connect_window(workspace, host="203.0.113.9")

    assert response.status_code == 403
    assert opened == []


def test_opening_the_window_reports_what_it_is_doing(workspace, monkeypatch) -> None:
    monkeypatch.setattr(shopee, "start_connection", lambda: {
        "state": "waiting_for_login", "message": "Sign in to Shopee…", "updated_at": None,
    })

    response = connect_window(workspace)

    assert response.status_code == 202
    assert response.json()["connection"]["state"] == "waiting_for_login"


def test_the_sign_in_is_recorded_as_having_been_opened(workspace, monkeypatch) -> None:
    monkeypatch.setattr(shopee, "start_connection", lambda: {"state": "starting", "message": ""})

    connect_window(workspace)

    with TestingSession() as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "attribution.shopee_sign_in_opened"
            )
        )
    assert event is not None and event.detail == {"state": "starting"}


def test_progress_can_be_polled_while_the_window_is_open(workspace, monkeypatch) -> None:
    monkeypatch.setattr(shopee, "connection_status", lambda: {
        "state": "waiting_for_login", "message": "Sign in to Shopee…", "updated_at": None,
    })

    response = request(
        "GET", f"/api/workspaces/{workspace}/attribution/shopee/session/connect"
    )

    assert response.status_code == 200
    assert response.json()["connection"]["state"] == "waiting_for_login"
    # The session state travels with it, so the moment it connects is one read.
    assert response.json()["session"]["ready"] is False


def test_pasting_nothing_is_still_refused(workspace) -> None:
    """The paste path keeps its own requirement now the model does not."""
    response = connect(workspace, "")

    assert response.status_code == 422
