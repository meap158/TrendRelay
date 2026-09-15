import asyncio
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaign_offer_tags, campaigns_api
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


def test_the_list_reports_how_many_products_each_campaign_may_promote() -> None:
    """The count is what a reader scans the list for; an untagged campaign must
    still say zero rather than omit the number, and a shared product is counted
    by every campaign that promotes it, not shared out between them."""
    workspace_id = create_workspace()
    offer_id = create_offer(workspace_id)
    tagged = create_campaign(workspace_id)
    also_tagged = create_campaign(workspace_id)
    untagged = create_campaign(workspace_id)

    with TestingSession.begin() as session:
        for campaign in (tagged, also_tagged):
            campaign_offer_tags.tag(
                session,
                workspace_id=workspace_id,
                campaign_id=campaign["id"],
                offer_ids=[offer_id],
                user_id="campaign-owner",
            )

    listing = asyncio.run(
        request("GET", f"/api/workspaces/{workspace_id}/campaigns")
    )
    assert listing.status_code == 200
    counts = {c["id"]: c["tagged_products"] for c in listing.json()["campaigns"]}
    # The one product is counted by both campaigns that promote it.
    assert counts[tagged["id"]] == 1
    assert counts[also_tagged["id"]] == 1
    assert counts[untagged["id"]] == 0


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


def test_a_campaign_can_ask_for_telegram_when_it_is_made_and_later(monkeypatch) -> None:
    """The switch is a campaign setting, asked beside the rest of how it
    posts - at creation and in the settings dialog - and off unless asked."""
    from trendrelay_api import approval_notices

    workspace_id = create_workspace()
    plain = create_campaign(workspace_id)["id"]
    assert autopilot_of(plain).approvals_telegram is False

    response = asyncio.run(request(
        "POST", f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "name": "Phone-approved launch", "objective": "Sell", "audience": "People",
            "approvals_telegram": True,
        },
    ))
    assert response.status_code == 201, response.text
    asked = response.json()["campaign"]["id"]
    assert autopilot_of(asked).approvals_telegram is True

    # Switching it on later sends what is already waiting - none here - and
    # the reply says so; saving again with it on sends nothing more.
    announced: list = []
    monkeypatch.setattr(
        approval_notices, "announce_executions",
        lambda session, autopilot, executions: announced.append(list(executions))
        or "Announced 0 posts on Telegram.",
    )
    flipped = update_campaign(workspace_id, plain, approvals_telegram=True)
    assert flipped.status_code == 200, flipped.text
    assert flipped.json()["telegram"] == "Announced 0 posts on Telegram."
    assert autopilot_of(plain).approvals_telegram is True
    again = update_campaign(workspace_id, plain, approvals_telegram=True)
    assert again.json()["telegram"] == ""
    assert announced == [[]]
    # And a correction that says nothing about it leaves it alone.
    update_campaign(workspace_id, plain, audience="Different people")
    assert autopilot_of(plain).approvals_telegram is True


def test_the_list_says_whether_telegram_is_connected_and_as_whom(monkeypatch) -> None:
    from trendrelay_api.integrations import telegram

    workspace_id = create_workspace()
    monkeypatch.setattr(telegram, "connection_summary", lambda: {
        "connected": True, "bot": "@trendrelay_bot", "chat": "Approvals", "reason": "",
    })
    listed = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/campaigns"))
    assert listed.status_code == 200
    assert listed.json()["telegram"] == {
        "connected": True, "bot": "@trendrelay_bot", "chat": "Approvals", "reason": "",
    }


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


def test_a_campaign_that_asks_to_disclose_still_needs_the_words() -> None:
    """The rule the autopilot endpoint enforces, enforced here too.

    Otherwise moving the mode to this dialog would let somebody set it against
    a disclosure that is switched on and blank, from a screen that no longer
    shows one.
    """
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]
    update_campaign(
        workspace_id, campaign_id, offer_mode="none", disclose=True, disclosure=" ",
    )

    refused = update_campaign(workspace_id, campaign_id, offer_mode="smart")

    assert refused.status_code == 422
    assert "disclosure" in refused.json()["detail"]


def test_a_campaign_can_attach_products_and_disclose_nothing() -> None:
    """Off is the default, and it does not block a commercial campaign.

    What it turns off is a legal safeguard - the endorsement guides ask for a
    disclosure near the endorsement, and the networks require paid promotion to
    be marked - so this is the operator's decision, not the program's.
    """
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id)["id"]

    allowed = update_campaign(
        workspace_id, campaign_id, offer_mode="smart", disclosure=" ",
    )

    assert allowed.status_code == 200, allowed.text
    assert autopilot_of(campaign_id).disclose is False


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


def named_campaign(workspace_id: str, name: str) -> str:
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns",
            json={
                "name": name,
                "objective": "Validate purchase intent",
                "audience": "Frequent travelers",
                "languages": ["en"],
            },
        )
    )
    assert response.status_code == 201, response.text
    return response.json()["campaign"]["id"]


def listed_names(workspace_id: str) -> list[str]:
    response = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/campaigns"))
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["campaigns"]]


def test_a_new_campaign_opens_at_the_top_of_the_list() -> None:
    """Where it was when the list was sorted by what had been touched last.

    It is the thing just made and the one about to be worked on, so a list that
    put it ninth would mean scrolling to find what you just created.
    """
    workspace_id = create_workspace()
    for name in ("First", "Second", "Third"):
        named_campaign(workspace_id, name)

    assert listed_names(workspace_id) == ["Third", "Second", "First"]


def test_the_order_holds_when_a_campaign_is_edited() -> None:
    """The bug this column exists for.

    Sorted by `updated_at`, opening a campaign's settings moved it to the top -
    so the list rearranged itself under somebody who had learned where things
    were. Editing changes the campaign, not its place.
    """
    workspace_id = create_workspace()
    first = named_campaign(workspace_id, "First")
    named_campaign(workspace_id, "Second")
    named_campaign(workspace_id, "Third")

    edited = update_campaign(
        workspace_id, first, name="First", objective="Something else entirely",
    )

    assert edited.status_code == 200, edited.text
    assert listed_names(workspace_id) == ["Third", "Second", "First"]


def test_campaigns_are_listed_in_the_order_they_were_dragged_into() -> None:
    workspace_id = create_workspace()
    first = named_campaign(workspace_id, "First")
    second = named_campaign(workspace_id, "Second")
    third = named_campaign(workspace_id, "Third")

    moved = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/order",
            json={"campaign_ids": [first, third, second]},
        )
    )

    assert moved.status_code == 200, moved.text
    assert listed_names(workspace_id) == ["First", "Third", "Second"]
    # And it survives the next read, which is the whole point of storing it.
    assert listed_names(workspace_id) == ["First", "Third", "Second"]


def test_a_partial_order_leaves_the_campaigns_it_did_not_name_behind_it() -> None:
    """A sidebar filtered to the unarchived ones sends only what it showed.

    The archived campaign it never listed cannot be dropped from the order, and
    must not be silently promoted above the ones that were named either.
    """
    workspace_id = create_workspace()
    first = named_campaign(workspace_id, "First")
    second = named_campaign(workspace_id, "Second")
    third = named_campaign(workspace_id, "Third")

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/order",
            json={"campaign_ids": [third, first]},
        )
    )

    assert response.status_code == 200, response.text
    assert listed_names(workspace_id) == ["Third", "First", "Second"]
    assert second in response.json()["campaign_ids"]


def test_a_reorder_naming_a_campaign_from_another_workspace_is_refused() -> None:
    workspace_id = create_workspace()
    mine = named_campaign(workspace_id, "Mine")

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/order",
            json={"campaign_ids": [mine, "campaign_somewhere_else"]},
        )
    )

    assert response.status_code == 404, response.text
    assert "campaign_somewhere_else" in response.json()["detail"]


def test_reordering_does_not_answer_as_a_campaign_called_order() -> None:
    """`/order` is declared above `/{campaign_id}`, and has to stay there.

    Below it, "order" reads as a campaign id and a reorder is answered with a
    404 about a campaign nobody asked about.
    """
    workspace_id = create_workspace()
    campaign_id = named_campaign(workspace_id, "Only one")

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/order",
            json={"campaign_ids": [campaign_id]},
        )
    )

    assert response.status_code == 200, response.text
    assert response.json()["campaign_ids"] == [campaign_id]


def test_a_campaign_named_twice_in_one_order_is_placed_once() -> None:
    """A drag that starts and ends on the same row can send it twice.

    Placed at its first mention and not again, because the alternative is a
    campaign holding two positions and every row after it shifted by one.
    """
    workspace_id = create_workspace()
    first = named_campaign(workspace_id, "First")
    second = named_campaign(workspace_id, "Second")
    third = named_campaign(workspace_id, "Third")

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/order",
            json={"campaign_ids": [third, first, third, second]},
        )
    )

    assert response.status_code == 200, response.text
    assert response.json()["campaign_ids"] == [third, first, second]
    assert listed_names(workspace_id) == ["Third", "First", "Second"]


def test_reordering_is_not_an_edit_of_the_campaigns_it_moves() -> None:
    """`updated_at` is the tie-break the order falls back on, and it is also
    what "when was this last changed" means to everything else. Moving a row in
    a list is neither, so it must leave both alone."""
    workspace_id = create_workspace()
    first = named_campaign(workspace_id, "First")
    second = named_campaign(workspace_id, "Second")

    with TestingSession() as session:
        before = {
            item.id: item.updated_at
            for item in session.scalars(
                select(campaigns_api.Campaign).where(
                    campaigns_api.Campaign.workspace_id == workspace_id
                )
            )
        }

    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/order",
            json={"campaign_ids": [second, first]},
        )
    )

    with TestingSession() as session:
        after = {
            item.id: item.updated_at
            for item in session.scalars(
                select(campaigns_api.Campaign).where(
                    campaigns_api.Campaign.workspace_id == workspace_id
                )
            )
        }

    assert after == before
    assert listed_names(workspace_id) == ["Second", "First"]
