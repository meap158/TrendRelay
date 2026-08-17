"""Adding a second login from the app, rather than by editing a file.

The registry and the engine wiring are tested beside this. What is tested here
is the way in: that a login can be created, named, filled in and forgotten over
HTTP, and that the guards around it hold - because this endpoint writes to the
file the API keys live in.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import publishing_connections as connections
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

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


def call(method: str, path: str, *, host: str = "127.0.0.1", **kwargs) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=(host, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def api(tmp_path, monkeypatch):
    import os

    from trendrelay_api import env_store

    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_store, "ENV_PATH", path)
    for key in list(os.environ):
        if key.startswith(("BUFFER_", "ZERNIO_", "BUNDLE_SOCIAL_", "WOOPSOCIAL_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(connections.REGISTRY_KEY, raising=False)

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="owner", email="owner@example.com", assurance_level="aal2",
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def workspace() -> str:
    response = call("POST", "/api/workspaces", json={"name": "Lab", "slug": "lab"})
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def base(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/publishing/connections"


# --- what is there to begin with ----------------------------------------------


def test_every_engine_is_listed_before_anything_is_added(workspace) -> None:
    found = call("GET", base(workspace)).json()["connections"]

    assert {row["id"] for row in found} >= {"bundle_social", "zernio", "buffer"}
    assert all(row["is_default"] for row in found)


# --- adding -------------------------------------------------------------------


def test_adding_a_second_login_returns_it_and_the_whole_list(workspace) -> None:
    body = call("POST", base(workspace), json={"provider": "buffer", "label": "Client B"}).json()

    assert body["connection"]["provider"] == "buffer"
    assert body["connection"]["label"] == "Client B"
    assert not body["connection"]["is_default"]
    assert body["connection"]["id"] in {row["id"] for row in body["connections"]}


def test_the_new_login_starts_with_no_key_of_its_own(workspace) -> None:
    """Adding it and filling it in are two steps, and the second needs the first."""
    from trendrelay_api import env_store
    from trendrelay_api.integrations.publishing import PROVIDERS

    added = call("POST", base(workspace), json={"provider": "buffer", "label": "B"}).json()
    row = connections.find(PROVIDERS, added["connection"]["id"])

    assert not env_store.effective_value(row.key_for("BUFFER_API_KEY"))


def test_a_key_saved_for_one_login_leaves_the_other_alone(workspace) -> None:
    """The whole point of the feature, over HTTP."""
    from trendrelay_api import env_store
    from trendrelay_api.integrations.publishing import PROVIDERS

    credentials = f"/api/workspaces/{workspace}/publishing/providers/credentials"
    call("POST", credentials, json={
        "provider": "buffer", "values": {"api_key": "first-key"},
        "confirm_external_action": True,
    })
    added = call("POST", base(workspace), json={"provider": "buffer", "label": "B"}).json()
    second_id = added["connection"]["id"]
    call("POST", credentials, json={
        "provider": second_id, "values": {"api_key": "second-key"},
        "confirm_external_action": True,
    })

    row = connections.find(PROVIDERS, second_id)
    assert env_store.effective_value("BUFFER_API_KEY") == "first-key"
    assert env_store.effective_value(row.key_for("BUFFER_API_KEY")) == "second-key"


def test_an_unknown_engine_is_refused(workspace) -> None:
    response = call("POST", base(workspace), json={"provider": "myspace", "label": "No"})

    assert response.status_code == 422


# --- renaming -----------------------------------------------------------------


def test_a_login_can_be_renamed(workspace) -> None:
    added = call("POST", base(workspace), json={"provider": "buffer", "label": "Old"}).json()
    identifier = added["connection"]["id"]

    body = call("POST", f"{base(workspace)}/{identifier}/rename", json={"label": "New"}).json()

    assert body["connection"]["label"] == "New"


def test_the_first_login_keeps_its_engine_s_name(workspace) -> None:
    response = call("POST", f"{base(workspace)}/buffer/rename", json={"label": "Mine"})

    assert response.status_code == 422


# --- removing -----------------------------------------------------------------


def test_removing_needs_confirming(workspace) -> None:
    # Destinations pointing at it stop resolving; not a button-adjacent action.
    from trendrelay_api.integrations.publishing import PROVIDERS

    added = call("POST", base(workspace), json={"provider": "buffer", "label": "B"}).json()
    identifier = added["connection"]["id"]

    response = call("POST", f"{base(workspace)}/{identifier}/remove",
                    json={"confirm_external_action": False})

    assert response.status_code == 400
    assert connections.find(PROVIDERS, identifier) is not None


def test_removing_forgets_the_login_and_its_key(workspace) -> None:
    from trendrelay_api import env_store
    from trendrelay_api.integrations.publishing import PROVIDERS

    added = call("POST", base(workspace), json={"provider": "buffer", "label": "B"}).json()
    identifier = added["connection"]["id"]
    key = connections.find(PROVIDERS, identifier).key_for("BUFFER_API_KEY")
    env_store.write_env_values({key: "to-be-forgotten"})

    body = call("POST", f"{base(workspace)}/{identifier}/remove",
                json={"confirm_external_action": True}).json()

    assert body["removed"] == identifier
    assert identifier not in {row["id"] for row in body["connections"]}
    assert not env_store.effective_value(key)


def test_an_engine_s_first_login_cannot_be_removed(workspace) -> None:
    response = call("POST", f"{base(workspace)}/buffer/remove",
                    json={"confirm_external_action": True})

    assert response.status_code == 422


def test_removing_something_that_never_existed_says_so(workspace) -> None:
    response = call("POST", f"{base(workspace)}/buffer-imaginary/remove",
                    json={"confirm_external_action": True})

    assert response.status_code == 404


# --- the guards ---------------------------------------------------------------


def test_changing_logins_is_local_machine_only(workspace) -> None:
    # It writes to the file the API keys live in.
    response = call("POST", base(workspace), host="10.1.2.3",
                    json={"provider": "buffer", "label": "Remote"})

    assert response.status_code == 403


def test_reading_the_list_does_not_require_being_local(workspace) -> None:
    response = call("GET", base(workspace), host="10.1.2.3")

    assert response.status_code == 200
