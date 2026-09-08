"""Starting a download when there is more than one service to start it against.

The endpoint works the service out from the links rather than asking for it,
and refuses a batch that spans two. The refusal is the part worth pinning down:
it has to be distinguishable from "that is not a link at all", because the two
need different things done about them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import media_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

DOUYIN = "https://www.douyin.com/video/7666087611615019749"
TIKTOK = "https://www.tiktok.com/@tiktok/video/7681695065927912735"
TIKTOK_TWO = "https://www.tiktok.com/@tiktok/video/7681414892942839071"


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


@pytest.fixture(autouse=True)
def workspace(monkeypatch) -> Any:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="download-owner", email="owner@example.com", assurance_level="aal2",
    )
    made = asyncio.run(
        call("POST", "/api/workspaces", json={"name": "Vault", "slug": "vault"})
    )
    assert made.status_code == 201, made.text
    yield made.json()["workspace"]["id"]
    app.dependency_overrides.clear()


def submit(workspace_id: str, urls: list[str], **extra) -> httpx.Response:
    return asyncio.run(
        call(
            "POST",
            f"/api/workspaces/{workspace_id}/media/downloads",
            json={
                "workspace_id": workspace_id,
                "urls": urls,
                "confirm_external_action": True,
                **extra,
            },
        )
    )


def test_a_batch_spanning_two_services_is_refused_and_named(workspace) -> None:
    """The rule the whole provider table exists to enforce.

    409 rather than 422: nothing is malformed. Each link is perfectly valid and
    they simply cannot travel together in one job.
    """
    response = submit(workspace, [DOUYIN, TIKTOK, TIKTOK_TWO])

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "TikTok (2 links)" in detail
    assert "Douyin (1 link)" in detail
    assert "one service at a time" in detail


def test_no_link_at_all_is_a_different_answer(workspace) -> None:
    """Paste a link, versus split the ones you have - two remedies, two codes."""
    response = submit(workspace, ["hello", "https://example.com/watch"])

    assert response.status_code == 422, response.text
    # Refused by the request model before the endpoint runs, which is earlier
    # and says the same thing: it names the services a link may come from.
    detail = json.dumps(response.json()["detail"])
    assert "Douyin" in detail and "TikTok" in detail
    assert "Discovery pages are not downloadable" in detail


def test_a_mode_the_service_cannot_deliver_is_refused_with_what_it_offers(
    workspace,
) -> None:
    """TikTok keeps likes private, so "liked videos" is not a fetch it can do.

    Refused at submission with the list of what it can, rather than accepted
    and failed twenty minutes later in a worker.
    """
    response = submit(workspace, [TIKTOK], mode="like")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "TikTok cannot fetch" in detail
    assert "post" in detail and "mix" in detail


def test_the_service_is_worked_out_from_the_links(workspace, monkeypatch) -> None:
    """Nobody is asked which service they just pasted a link from."""
    seen: dict[str, Any] = {}

    def fake_create(request, actor_user_id=None, *, service="douyin", **kwargs):
        seen["service"] = service
        seen["urls"] = list(request.urls)
        return {"id": "download_test", "status": "queued"}

    monkeypatch.setattr(media_api, "create_download_job", fake_create)

    response = submit(workspace, [TIKTOK, TIKTOK_TWO])

    assert response.status_code == 202, response.text
    assert seen["service"] == "tiktok"
    assert seen["urls"] == [TIKTOK, TIKTOK_TWO]


def test_prose_pasted_with_a_link_is_still_refused_by_the_request(workspace) -> None:
    """Unchanged from before there were two services.

    The box on screen pulls the links out of a pasted share message and submits
    only those; the endpoint stays strict about what it is handed, so a caller
    that is not the interface cannot smuggle something past it.
    """
    response = submit(workspace, [TIKTOK, "some pasted prose"])

    assert response.status_code == 422, response.text


def test_the_catalogue_says_which_services_can_run_right_now(workspace) -> None:
    """One table, read by the interface, so it keeps no second copy of it."""
    response = asyncio.run(
        call("GET", f"/api/workspaces/{workspace}/media/download-providers")
    )

    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()["providers"]}
    assert set(rows) == {"douyin", "tiktok"}
    for row in rows.values():
        # Whether it can run, and if not, a sentence saying what to do.
        assert "ready" in row
        assert row["ready"] or row["reason"]
        assert row["modes"] and row["kinds"]
    assert "tiktok.com" in rows["tiktok"]["hosts"]
