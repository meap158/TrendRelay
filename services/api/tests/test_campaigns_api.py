import asyncio
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaigns_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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


async def request(
    method: str,
    path: str,
    *,
    client_host: str = "127.0.0.1",
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="campaign-owner",
        email="owner@example.com",
        assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def create_workspace() -> str:
    response = asyncio.run(
        request(
            "POST",
            "/api/workspaces",
            json={"name": "Campaign Lab", "slug": "campaign-lab"},
        )
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def create_campaign(workspace_id: str) -> dict:
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns",
            json={
                "name": "Portable espresso launch",
                "objective": "Validate purchase intent",
                "audience": "Frequent travelers",
                "markets": ["TH", "US"],
                "languages": ["en", "th"],
                "affiliate_url": "https://example.com/espresso",
            },
        )
    )
    assert response.status_code == 201
    return response.json()["campaign"]


def test_campaign_calendar_approval_and_idempotent_manual_package(
    tmp_path: Path, monkeypatch
) -> None:
    video = tmp_path / "approved.mp4"
    video.write_bytes(b"fake-mp4")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"fake-jpg")
    monkeypatch.setattr(
        campaigns_api,
        "_approved_media_path",
        lambda value, suffixes: Path(value).resolve(strict=True),
    )
    monkeypatch.setattr(campaigns_api, "PACKAGE_ROOT", tmp_path / "packages")

    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)
    scheduled = datetime.now(UTC) + timedelta(days=2)
    created = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans",
            json={
                "title": "Travel espresso demonstration",
                "platform": "tiktok",
                "video_path": str(video),
                "cover_path": str(cover),
                "caption": "Make espresso anywhere.",
                "hashtags": ["travel", "#espresso", "travel"],
                "disclosure": "#ad Affiliate link",
                "scheduled_at": scheduled.isoformat(),
                "timezone": "Asia/Bangkok",
            },
        )
    )
    assert created.status_code == 201
    plan = created.json()["plan"]
    assert plan["state"] == "needs_approval"
    assert len(plan["video_sha256"]) == 64
    assert len(plan["cover_sha256"]) == 64
    assert plan["hashtags"] == ["travel", "espresso"]
    assert plan["affiliate_url"] == "https://example.com/espresso"
    assert plan["deep_link"] == "https://www.tiktok.com/upload"

    calendar = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/campaigns/calendar"))
    assert calendar.status_code == 200
    assert calendar.json()["plans"][0]["id"] == plan["id"]

    too_early = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{plan['id']}/manual-package",
            json={"confirm_external_action": True},
        )
    )
    assert too_early.status_code == 409

    approved = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{plan['id']}/decision",
            json={"decision": "approve"},
        )
    )
    assert approved.status_code == 200
    assert approved.json()["plan"]["state"] == "approved"

    repeated = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{plan['id']}/decision",
            json={"decision": "approve"},
        )
    )
    assert repeated.status_code == 409

    unconfirmed = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{plan['id']}/manual-package",
            json={"confirm_external_action": False},
        )
    )
    assert unconfirmed.status_code == 400

    exported = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{plan['id']}/manual-package",
            json={"confirm_external_action": True},
        )
    )
    assert exported.status_code == 200
    package = exported.json()["package"]
    package_path = Path(package["path"])
    assert package_path.is_file()
    with zipfile.ZipFile(package_path) as archive:
        assert set(archive.namelist()) == {
            "approved.mp4",
            "cover.jpg",
            "manifest.json",
            "caption.txt",
        }
        assert "Affiliate link" in archive.read("caption.txt").decode()

    exported_again = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{plan['id']}/manual-package",
            json={"confirm_external_action": True},
        )
    )
    assert exported_again.status_code == 200
    assert exported_again.json()["package"]["sha256"] == package["sha256"]

    video.write_bytes(b"changed-after-approval")
    changed_media = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{plan['id']}/manual-package",
            json={"confirm_external_action": True},
        )
    )
    assert changed_media.status_code == 409
    assert "changed" in changed_media.json()["detail"].lower()

    audit = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/audit-events"))
    actions = {item["action"] for item in audit.json()["events"]}
    assert {
        "campaign.created",
        "publication_plan.created",
        "publication_plan.approved",
        "publication_plan.manual_package_exported",
    }.issubset(actions)


def test_archived_campaign_is_locked(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "approved.mp4"
    video.write_bytes(b"fake-mp4")
    monkeypatch.setattr(
        campaigns_api,
        "_approved_media_path",
        lambda value, suffixes: Path(value).resolve(strict=True),
    )
    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)
    archived = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/status",
            json={"status": "archived"},
        )
    )
    assert archived.status_code == 200

    plan = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans",
            json={
                "title": "Locked plan",
                "platform": "youtube",
                "video_path": str(video),
                "caption": "Locked",
                "scheduled_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "timezone": "UTC",
            },
        )
    )
    assert plan.status_code == 409


def test_manual_package_export_is_local_only(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "approved.mp4"
    video.write_bytes(b"fake-mp4")
    monkeypatch.setattr(
        campaigns_api,
        "_approved_media_path",
        lambda value, suffixes: Path(value).resolve(strict=True),
    )
    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)
    created = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans",
            json={
                "title": "Remote block",
                "platform": "youtube",
                "video_path": str(video),
                "caption": "Blocked remotely",
                "scheduled_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "timezone": "UTC",
            },
        )
    ).json()["plan"]
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{created['id']}/decision",
            json={"decision": "approve"},
        )
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/"
            f"{created['id']}/manual-package",
            client_host="192.0.2.10",
            json={"confirm_external_action": True},
        )
    )
    assert response.status_code == 403
