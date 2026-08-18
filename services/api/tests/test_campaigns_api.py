import asyncio
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaigns_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autopilot_models import CampaignAutopilot
from trendrelay_api.campaign_autopilot import localised_text
from trendrelay_api.database import get_session
from trendrelay_api.integrations import publishing
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product, ProductOffer

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


def create_campaign(workspace_id: str, offer_id: str | None = None) -> dict:
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
                "offer_id": offer_id,
            },
        )
    )
    assert response.status_code == 201
    return response.json()["campaign"]


def create_offer(workspace_id: str) -> str:
    with TestingSession.begin() as session:
        session.add(
            Product(
                id="campaign-product",
                workspace_id=workspace_id,
                catalog_key="campaign-product-key",
                identifier="espresso-1",
                name="Connected espresso maker",
                brand="Relay Coffee",
                category="Kitchen",
                marketplace="shopee",
                product_url="https://example.com/product",
                image_url=None,
                created_by="campaign-owner",
            )
        )
        session.add(
            ProductOffer(
                id="campaign-offer",
                workspace_id=workspace_id,
                product_id="campaign-product",
                fingerprint="campaign-offer-key",
                network="shopee",
                merchant="Shopee",
                affiliate_url="https://example.com/tracked-espresso",
                price_cents=3999,
                currency="USD",
                commission_bps=1000,
                cookie_days=7,
                availability="available",
                created_by="campaign-owner",
            )
        )
    return "campaign-offer"


def test_campaign_plans_cover_every_publish_platform() -> None:
    """A platform exposed by Publish must never be rejected by Campaigns."""
    campaign_platforms = set(get_args(campaigns_api.Platform))
    publish_platforms = set(get_args(publishing.Platform))

    assert publish_platforms <= campaign_platforms
    assert "threads" in campaign_platforms


def test_campaign_plan_keeps_publish_destination_and_attribution_offer(
    tmp_path: Path, monkeypatch
) -> None:
    video = tmp_path / "threads.mp4"
    video.write_bytes(b"fake-mp4")
    monkeypatch.setattr(
        campaigns_api,
        "_approved_media_path",
        lambda value, suffixes: Path(value).resolve(strict=True),
    )
    workspace_id = create_workspace()
    offer_id = create_offer(workspace_id)
    campaign = create_campaign(workspace_id, offer_id)

    autopilot = asyncio.run(request(
        "GET", f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/autopilot"
    ))
    assert autopilot.status_code == 200
    assert autopilot.json()["autopilot"]["offer_id"] == offer_id
    assert autopilot.json()["autopilot"]["offer_mode"] == "manual"

    created = asyncio.run(request(
        "POST",
        f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans",
        json={
            "title": "Connected Threads plan",
            "platform": "threads",
            "provider": "buffer",
            "integration_id": "threads-account-1",
            "destination_label": "TrendRelay Threads",
            "offer_id": offer_id,
            "video_path": str(video),
            "caption": "Prepared from connected sources.",
            "scheduled_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "timezone": "Asia/Bangkok",
        },
    ))
    assert created.status_code == 201, created.text
    plan = created.json()["plan"]
    assert plan["platform"] == "threads"
    assert plan["provider"] == "buffer"
    assert plan["integration_id"] == "threads-account-1"
    assert plan["destination_label"] == "TrendRelay Threads"
    assert plan["offer_id"] == offer_id
    assert plan["affiliate_url"] == "https://example.com/tracked-espresso"


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

    handoff = asyncio.run(request(
        "GET",
        f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/plans/{plan['id']}",
    ))
    assert handoff.status_code == 200
    assert handoff.json()["plan"]["caption"] == "Make espresso anywhere."

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
    with TestingSession.begin() as session:
        from trendrelay_api.autopilot_models import CampaignAutopilot

        pilot = session.query(CampaignAutopilot).filter_by(
            campaign_id=campaign["id"]
        ).one()
        pilot.enabled = True
    archived = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/status",
            json={"status": "archived"},
        )
    )
    assert archived.status_code == 200
    pilot = asyncio.run(request(
        "GET", f"/api/workspaces/{workspace_id}/campaigns/{campaign['id']}/autopilot"
    )).json()["autopilot"]
    assert pilot["enabled"] is False

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


# --- correcting a campaign after it exists --------------------------------------


def update_campaign(workspace_id: str, campaign_id: str, **body) -> httpx.Response:
    return asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign_id}",
            json={
                "name": "Portable espresso launch",
                "objective": "Validate purchase intent",
                "audience": "Frequent travelers",
                "languages": ["en"],
                **body,
            },
        )
    )


def autopilot_of(campaign_id: str) -> CampaignAutopilot:
    with TestingSession() as session:
        found = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
        )
        assert found is not None
        return found


def test_the_goal_and_the_audience_can_be_corrected() -> None:
    """They steer product matching, so a hurried first answer must be fixable."""
    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)

    response = update_campaign(
        workspace_id,
        campaign["id"],
        objective="Move seasonal and promotional stock",
        audience="Students and young professionals",
    )

    assert response.status_code == 200
    updated = response.json()["campaign"]
    assert updated["objective"] == "Move seasonal and promotional stock"
    assert updated["audience"] == "Students and young professionals"


def test_changing_the_language_retranslates_scaffolding_nobody_edited() -> None:
    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)
    assert autopilot_of(campaign["id"]).post_language == "en"

    assert update_campaign(workspace_id, campaign["id"], languages=["vi"]).status_code == 200

    autopilot = autopilot_of(campaign["id"])
    assert autopilot.post_language == "vi"
    assert autopilot.disclosure == localised_text("vi", "disclosure")
    assert autopilot.bio_hint == localised_text("vi", "bio_hint")


def test_a_disclosure_somebody_wrote_survives_a_language_change() -> None:
    """Overwriting it would be the app discarding the operator's own words.

    A disclosure is a legal statement in their voice; it is worth leaving in the
    wrong language rather than silently replacing.
    """
    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)
    with TestingSession.begin() as session:
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign["id"])
        )
        autopilot.disclosure = "Paid partnership. Our own wording."

    assert update_campaign(workspace_id, campaign["id"], languages=["vi"]).status_code == 200

    autopilot = autopilot_of(campaign["id"])
    assert autopilot.post_language == "vi"
    assert autopilot.disclosure == "Paid partnership. Our own wording."
    # The one nobody touched still follows the language.
    assert autopilot.bio_hint == localised_text("vi", "bio_hint")


def test_an_unusable_name_is_refused() -> None:
    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)

    assert update_campaign(workspace_id, campaign["id"], name="x").status_code == 422


def test_updating_a_campaign_in_another_workspace_is_refused() -> None:
    workspace_id = create_workspace()
    campaign = create_campaign(workspace_id)
    other = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Other", "slug": "other-lab"})
    ).json()["workspace"]["id"]

    assert update_campaign(other, campaign["id"], name="Renamed").status_code == 404


# --- the campaign owns how hard it is run ---------------------------------------
#
# The caps, the authority and the ranking axis are set when the campaign is
# described and rarely touched after, so they are asked beside the objective
# and the audience rather than beside the queue somebody works in daily. They
# are still stored on the autopilot, which is what reads them.


def test_campaign_settings_set_the_posting_policy() -> None:
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]

    response = update_campaign(
        workspace_id, campaign_id,
        max_products_per_post=3, min_recycle_days=14,
        daily_cap_per_account=4, weekly_post_cap=20,
        authority="autonomous", priority="revenue",
    )

    assert response.status_code == 200, response.text
    saved = autopilot_of(campaign_id)
    assert saved.max_products_per_post == 3
    assert saved.min_recycle_days == 14
    assert saved.daily_cap_per_account == 4
    assert saved.weekly_post_cap == 20
    assert saved.authority == "autonomous"
    assert saved.priority == "revenue"


def test_correcting_the_audience_does_not_reset_the_policy() -> None:
    """Every policy field is optional for exactly this.

    Somebody fixing a typo in the audience sends the identity fields and
    nothing else, and must not thereby put the caps back to their defaults.
    """
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]
    update_campaign(workspace_id, campaign_id, min_recycle_days=7, authority="assist")

    update_campaign(workspace_id, campaign_id, audience="Frequent travelers and students")

    saved = autopilot_of(campaign_id)
    assert saved.min_recycle_days == 7
    assert saved.authority == "assist"


def test_no_weekly_cap_is_a_setting_rather_than_an_omission() -> None:
    # An empty box means no cap; a field left out means leave it alone. Those
    # are different answers, so the form says which one it means.
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]
    update_campaign(workspace_id, campaign_id, weekly_post_cap=20)

    update_campaign(workspace_id, campaign_id, clear_weekly_cap=True)

    assert autopilot_of(campaign_id).weekly_post_cap is None


def test_the_policy_change_is_named_in_the_audit() -> None:
    # It is written to the autopilot, so a comparison against the campaign row
    # would have asked for an attribute that is not there.
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]

    response = update_campaign(workspace_id, campaign_id, min_recycle_days=21)

    assert response.status_code == 200, response.text
    assert autopilot_of(campaign_id).min_recycle_days == 21


def test_campaign_settings_set_how_products_attach() -> None:
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]

    response = update_campaign(
        workspace_id, campaign_id,
        offer_mode="none", disclosure="Paid partnership.", bio_hint="Link in bio",
    )

    assert response.status_code == 200, response.text
    saved = autopilot_of(campaign_id)
    assert saved.offer_mode == "none"
    assert saved.disclosure == "Paid partnership."
    assert saved.bio_hint == "Link in bio"


def test_a_commercial_campaign_still_needs_a_disclosure() -> None:
    """The rule the autopilot endpoint enforces, enforced here too.

    Otherwise moving the mode to this dialog would let somebody set it against
    an empty disclosure from a screen that no longer shows one.
    """
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]
    update_campaign(workspace_id, campaign_id, offer_mode="none", disclosure=" ")

    refused = update_campaign(workspace_id, campaign_id, offer_mode="smart")

    assert refused.status_code == 422
    assert "disclosure" in refused.json()["detail"]


def test_the_check_reads_what_the_request_would_leave_behind() -> None:
    # Turning products on and writing the disclosure in one submission is a
    # legal campaign, so checking either side alone would refuse it.
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]
    update_campaign(workspace_id, campaign_id, offer_mode="none", disclosure=" ")

    allowed = update_campaign(
        workspace_id, campaign_id, offer_mode="smart", disclosure="#ad",
    )

    assert allowed.status_code == 200, allowed.text
    assert autopilot_of(campaign_id).offer_mode == "smart"


def test_an_explicit_disclosure_survives_a_language_change() -> None:
    # The language pass rewrites the scaffolding it wrote itself. Somebody who
    # typed a disclosure in the same submission meant the one they typed.
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]

    update_campaign(
        workspace_id, campaign_id, languages=["fr"], disclosure="Mon propre texte",
    )

    assert autopilot_of(campaign_id).disclosure == "Mon propre texte"
