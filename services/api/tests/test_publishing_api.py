import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import publishing_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="owner-user", email="owner@example.com"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def payload(workspace_id: str) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "video_path": "C:/media/clip.mp4",
        "caption": "Launch clip",
        "date": "2099-01-01T12:00:00Z",
        "targets": [{"platform": "tiktok", "integration_id": "account-1"}],
    }


def test_editor_can_preview_but_only_approver_can_submit(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/members",
            json={"user_id": "editor-user", "email": "editor@example.com", "role": "editor"},
        )
    )
    monkeypatch.setattr(
        publishing_api,
        "preview_publish",
        lambda _body: {"operation_id": "abc", "external_action": "create_draft"},
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="editor-user", email="editor@example.com"
    )

    preview = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/publishing/preview",
            json=payload(workspace["id"]),
        )
    )
    submitted = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/publishing/jobs",
            json={**payload(workspace["id"]), "confirm_external_action": True},
        )
    )
    assert preview.status_code == 200
    assert preview.json()["preview"]["external_action"] == "create_draft"
    assert submitted.status_code == 403


async def request_from(host: str, method: str, path: str, **kwargs) -> httpx.Response:
    """The same call from a client that is not this machine."""
    transport = httpx.ASGITransport(app=app, client=(host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def workspace_id() -> str:
    return asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Keys", "slug": "keys"})
    ).json()["workspace"]["id"]


# --- revealing a saved credential ---------------------------------------------
#
# The gates matter more than the value here: this is the one endpoint that hands
# a secret to a browser, so each of them is held to rather than assumed.


def test_revealing_a_credential_is_refused_off_this_machine() -> None:
    workspace = workspace_id()
    response = asyncio.run(request_from(
        "192.168.1.40", "POST",
        f"/api/workspaces/{workspace}/publishing/credentials/BUFFER_API_KEY/reveal",
        json={"confirm_external_action": True},
    ))
    assert response.status_code == 403


def test_revealing_a_credential_needs_confirmation() -> None:
    workspace = workspace_id()
    response = asyncio.run(request(
        "POST",
        f"/api/workspaces/{workspace}/publishing/credentials/BUFFER_API_KEY/reveal",
        json={"confirm_external_action": False},
    ))
    assert response.status_code == 400


def test_only_credential_keys_can_be_revealed() -> None:
    """Otherwise the endpoint reads any environment variable.

    Every secret on the machine would sit behind a control meant for an
    engine's API key.
    """
    workspace = workspace_id()
    for key in ("PATH", "DATABASE_URL", "ATTRIBUTION_HASH_SECRET"):
        response = asyncio.run(request(
            "POST",
            f"/api/workspaces/{workspace}/publishing/credentials/{key}/reveal",
            json={"confirm_external_action": True},
        ))
        assert response.status_code == 404, key
        assert "not a credential" in response.json()["detail"]


def test_a_saved_credential_comes_back_in_full(monkeypatch) -> None:
    from trendrelay_api.integrations import publishing

    monkeypatch.setattr(
        publishing, "effective_value",
        lambda key: "buffer-secret-value" if key == "BUFFER_API_KEY" else "",
    )
    workspace = workspace_id()
    response = asyncio.run(request(
        "POST",
        f"/api/workspaces/{workspace}/publishing/credentials/BUFFER_API_KEY/reveal",
        json={"confirm_external_action": True},
    ))
    assert response.status_code == 200
    assert response.json() == {"key": "BUFFER_API_KEY", "value": "buffer-secret-value"}


def test_a_credential_with_nothing_saved_says_so(monkeypatch) -> None:
    from trendrelay_api.integrations import publishing

    monkeypatch.setattr(publishing, "effective_value", lambda key: "")
    workspace = workspace_id()
    response = asyncio.run(request(
        "POST",
        f"/api/workspaces/{workspace}/publishing/credentials/BUFFER_API_KEY/reveal",
        json={"confirm_external_action": True},
    ))
    assert response.status_code == 404
    assert "no saved value" in response.json()["detail"]


# --- the credit a video's music obliges ------------------------------------------


def _asset_owing_a_credit(workspace: str, asset_id: str, credit: str | None) -> None:
    from trendrelay_api.media_models import MediaAsset

    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace, title="Cut", media_kind="video",
            source_type="autocut", original_path="C:/media/clip.mp4",
            original_sha256=f"{asset_id:0>64}"[:64], mime_type="video/mp4",
            size_bytes=10, created_by="owner-user", attribution=credit,
        ))


def test_a_manual_post_of_a_video_that_owes_a_credit_carries_it(monkeypatch) -> None:
    """The caption somebody typed, plus the line the video's music obliges -
    on the preview and on the submission alike, so what is shown is what is
    sent. Added once: a caption already carrying it is left as written."""
    credit = 'Music: "Upbeat Corporate" by Soundrider (CC BY 3.0)'
    workspace = workspace_id()
    _asset_owing_a_credit(workspace, "cut-1", credit)
    _asset_owing_a_credit(workspace, "own-1", None)
    seen: list = []
    monkeypatch.setattr(
        publishing_api, "preview_publish",
        lambda body: seen.append(body)
        or {"operation_id": "abc", "external_action": "create_draft"},
    )
    monkeypatch.setattr(
        publishing_api, "create_publish_job",
        lambda body: seen.append(body) or {"id": "publish_abc", "status": "queued"},
    )
    monkeypatch.setattr(publishing_api, "run_publish_job", lambda job_id: None)

    previewed = asyncio.run(request(
        "POST", f"/api/workspaces/{workspace}/publishing/preview",
        json={**payload(workspace), "asset_id": "cut-1"},
    ))
    assert previewed.status_code == 200, previewed.text
    assert seen[-1].caption == f"Launch clip\n\n{credit}"

    submitted = asyncio.run(request(
        "POST", f"/api/workspaces/{workspace}/publishing/jobs",
        json={**payload(workspace), "asset_id": "cut-1", "confirm_external_action": True},
    ))
    assert submitted.status_code == 202, submitted.text
    assert seen[-1].caption == f"Launch clip\n\n{credit}"

    # Already written in: not doubled.
    asyncio.run(request(
        "POST", f"/api/workspaces/{workspace}/publishing/preview",
        json={**payload(workspace), "asset_id": "cut-1", "caption": f"Launch clip\n\n{credit}"},
    ))
    assert seen[-1].caption.count(credit) == 1

    # A video owing nothing, and a post naming no asset, are sent as typed.
    asyncio.run(request(
        "POST", f"/api/workspaces/{workspace}/publishing/preview",
        json={**payload(workspace), "asset_id": "own-1"},
    ))
    assert seen[-1].caption == "Launch clip"
    asyncio.run(request(
        "POST", f"/api/workspaces/{workspace}/publishing/preview", json=payload(workspace),
    ))
    assert seen[-1].caption == "Launch clip"
