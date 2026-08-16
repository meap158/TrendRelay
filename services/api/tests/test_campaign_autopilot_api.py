"""The endpoints a campaign is switched on through."""

from __future__ import annotations

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
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product, ProductOffer

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


@pytest.fixture
def workspace():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")
    body = request(
        "POST", "/api/workspaces", json={"name": "Workspace", "slug": "workspace"}
    ).json()
    workspace_id = body["workspace"]["id"]
    with TestingSession.begin() as session:
        session.add(Product(
            id="prod-1", workspace_id=workspace_id, catalog_key="k", identifier="i",
            name="Coffee espresso maker", brand="B", category="Kitchen", marketplace="amazon",
            product_url="https://example.test/p", image_url=None, created_by="owner-user",
        ))
        session.add(ProductOffer(
            id="offer-1", workspace_id=workspace_id, product_id="prod-1", fingerprint="f",
            network="amazon", merchant="Amazon",
            affiliate_url="https://example.test/aff", price_cents=8_999, currency="USD",
            commission_bps=400, cookie_days=1, availability="available",
            created_by="owner-user",
        ))
    yield workspace_id
    app.dependency_overrides.clear()


def campaign(workspace_id: str) -> str:
    body = request(
        "POST", f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "name": "Launch", "objective": "Sell the thing",
            "audience": "Coffee people", "markets": ["US"], "languages": ["en"],
        },
    )
    assert body.status_code == 201, body.text
    return body.json()["campaign"]["id"]


def test_a_campaign_starts_with_autopilot_off(workspace) -> None:
    """Nothing posts because a campaign was created."""
    campaign_id = campaign(workspace)
    body = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot"
    ).json()
    assert body["autopilot"]["enabled"] is False
    assert body["autopilot"]["delivery"] == "draft"
    assert body["destinations"] == []
    assert body["queue"] == []


def test_switching_it_on_needs_explicit_confirmation(workspace) -> None:
    # Handing an account to a scheduler is an external action like any other.
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"enabled": True},
    )
    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"]


def test_an_offer_without_a_disclosure_cannot_be_saved(workspace) -> None:
    # Refused at the setting as well as at the post: a configuration that cannot
    # produce a lawful post should not be storable.
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"offer_id": "offer-1", "disclosure": "   ", "confirm_external_action": True},
    )
    assert response.status_code == 422
    assert "disclosure" in response.json()["detail"]


def test_settings_round_trip(workspace) -> None:
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={
            "enabled": True, "offer_id": "offer-1", "min_recycle_days": 14,
            "daily_cap_per_account": 3, "delivery": "schedule",
            "confirm_external_action": True,
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()["autopilot"]
    assert saved["enabled"] is True
    assert saved["min_recycle_days"] == 14
    assert saved["delivery"] == "schedule"
    assert saved["offer_mode"] == "manual"


def test_running_autopilot_settings_do_not_require_activation_confirmation(workspace) -> None:
    campaign_id = campaign(workspace)
    url = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot"
    enabled = request(
        "PUT",
        url,
        json={"enabled": True, "confirm_external_action": True},
    )
    assert enabled.status_code == 200, enabled.text

    no_products = request(
        "PUT",
        url,
        json={"enabled": True, "offer_mode": "none"},
    )
    assert no_products.status_code == 200, no_products.text
    assert no_products.json()["autopilot"]["offer_mode"] == "none"

    one_product = request(
        "PUT",
        url,
        json={"enabled": True, "offer_mode": "manual", "offer_id": "offer-1"},
    )
    assert one_product.status_code == 200, one_product.text
    assert one_product.json()["autopilot"]["offer_mode"] == "manual"
    assert one_product.json()["autopilot"]["offer_id"] == "offer-1"


def test_smart_offer_settings_round_trip(workspace) -> None:
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={
            "offer_mode": "smart",
            "candidate_offer_ids": ["offer-1"],
            "max_products_per_post": 3,
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()["autopilot"]
    assert saved["offer_mode"] == "smart"
    assert saved["candidate_offer_ids"] == ["offer-1"]
    assert saved["max_products_per_post"] == 3


def test_campaign_recommendations_explain_content_and_delivery_signals(workspace) -> None:
    campaign_id = campaign(workspace)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={
            "video_path": r"S:\media\espresso.mp4",
            "title": "Portable coffee setup",
            "body": "Make espresso anywhere with this compact coffee kit.",
            "hashtags": ["coffee", "espresso"],
        },
    ).json()["item"]
    response = request(
        "GET",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
        params={"item_id": item["id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matches"][0]["offer_id"] == "offer-1"
    assert body["matches"][0]["confidence"] in {"medium", "high"}
    assert "approved post copy" in body["matches"][0]["evidence_sources"]
    assert body["strategy"]["queue_item_times_posted"] == 0
    assert "posting_slots" in body["strategy"]


def test_weak_matches_are_explicitly_review_only(workspace) -> None:
    campaign_id = campaign(workspace)
    with TestingSession.begin() as session:
        product = session.get(Product, "prod-1")
        product.name = "Unrelated garden hose"
        product.category = "Outdoor plumbing"
        product.brand = "Waterworks"
    body = request(
        "GET",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
    ).json()
    assert body["matches"][0]["confidence"] == "low"
    assert body["strategy"]["recommended_products_per_post"] == 0
    assert "not attached automatically" in body["strategy"]["rotation"]


def test_campaign_recommendations_roll_up_queued_image_evidence(workspace) -> None:
    campaign_id = campaign(workspace)
    with TestingSession.begin() as session:
        session.add(Product(
            id="prod-dress", workspace_id=workspace, catalog_key="dress-key",
            name="Red silk dress", brand="Atelier", category="Fashion",
            marketplace="shop", created_by="owner-user",
        ))
        session.add(ProductOffer(
            id="offer-dress", workspace_id=workspace, product_id="prod-dress",
            fingerprint="dress-offer", network="affiliate", merchant="Atelier",
            affiliate_url="https://example.test/red-dress", currency="USD",
            availability="available", created_by="owner-user",
        ))
        session.add(MediaAsset(
            id="asset-image", workspace_id=workspace, title="Red silk dress outfit",
            media_kind="image", source_type="upload", caption="Styling a red silk dress",
            hashtags=["dress", "fashion"], original_path=r"S:\media\dress.jpg",
            original_sha256="d" * 64, mime_type="image/jpeg", size_bytes=20,
            width=1080, height=1350, created_by="owner-user",
        ))
    queued = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={
            "asset_id": "asset-image", "video_path": r"S:\media\dress.jpg",
            "body": "A timeless outfit for evening events.",
            "hashtags": ["fashion"],
        },
    )
    assert queued.status_code == 201, queued.text
    body = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
    ).json()
    assert body["matches"][0]["offer_id"] == "offer-dress"
    assert "queued post 1 media title" in body["matches"][0]["evidence_sources"]
    assert body["strategy"]["media"]["media_kinds"] == ["image"]
    assert body["strategy"]["media"]["assets_analyzed"] == 1


def test_a_destination_reports_where_its_link_will_go(workspace) -> None:
    """The page can say "link in bio" before anything is posted, not after."""
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations"
    tiktok = request("POST", base, json={
        "provider": "buffer", "integration_id": "acct-1", "platform": "tiktok",
        "label": "brand on TikTok",
    }).json()["destination"]
    youtube = request("POST", base, json={
        "provider": "buffer", "integration_id": "acct-2", "platform": "youtube",
        "label": "brand on YouTube",
    }).json()["destination"]
    assert tiktok["link_placement"] == "bio"
    assert "profile" in tiktok["link_reason"]
    assert youtube["link_placement"] == "caption"


def test_the_same_account_cannot_be_added_twice(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations"
    payload = {
        "provider": "buffer", "integration_id": "acct-1", "platform": "tiktok",
        "label": "brand",
    }
    assert request("POST", base, json=payload).status_code == 201
    assert request("POST", base, json=payload).status_code == 409


def test_a_queued_item_arrives_as_a_draft(workspace) -> None:
    # Nothing enters the rotation because a form was submitted.
    campaign_id = campaign(workspace)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={"video_path": r"S:\media\clip.mp4", "body": "Copy", "hashtags": ["#coffee"]},
    ).json()["item"]
    assert item["state"] == "draft"
    # Hashtags are stored bare and rendered with one hash, however they were typed.
    assert item["hashtags"] == ["coffee"]


def test_approving_an_item_is_an_audited_decision(workspace) -> None:
    campaign_id = campaign(workspace)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={"video_path": r"S:\media\clip.mp4", "body": "Copy"},
    ).json()["item"]
    response = request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item['id']}",
        json={"state": "approved"},
    )
    assert response.status_code == 200
    assert response.json()["item"]["state"] == "approved"


def test_a_draft_queue_item_can_be_deleted(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
    item = request(
        "POST", f"{base}/queue",
        json={"video_path": r"S:\media\remove-me.mp4", "body": "Temporary copy"},
    ).json()["item"]

    response = request("DELETE", f"{base}/queue/{item['id']}")

    assert response.status_code == 200
    assert response.json() == {"removed": item["id"]}
    queue = request("GET", f"{base}/autopilot").json()["queue"]
    assert all(candidate["id"] != item["id"] for candidate in queue)


def test_the_preview_explains_a_campaign_that_would_post_nothing(workspace) -> None:
    """The reason is the point. "Nothing scheduled" explains nothing."""
    campaign_id = campaign(workspace)
    body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/preview"
    ).json()
    assert body["posts"] == []
    assert "not active" in body["note"] or "No destinations" in body["note"]


def test_preview_rows_carry_media_account_and_product_routes(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
    request("POST", f"{base}/destinations", json={
        "provider": "buffer", "integration_id": "acct-1",
        "platform": "youtube", "label": "Coffee channel",
    })
    item = request("POST", f"{base}/queue", json={
        "asset_id": "asset-preview", "video_path": r"S:\media\coffee.mp4",
        "title": "Coffee demo", "body": "Make better espresso.",
        "offer_ids": ["offer-1"],
    }).json()["item"]
    request("PATCH", f"{base}/queue/{item['id']}", json={"state": "approved"})
    upcoming = datetime.now(UTC) + timedelta(hours=1)
    request("POST", f"/api/workspaces/{workspace}/publishing/slots", json={
        "timezone": "UTC",
        "slots": [{"weekday": -1, "time": upcoming.strftime("%H:%M")}],
    })

    response = request("POST", f"{base}/autopilot/preview")

    assert response.status_code == 200, response.text
    assert response.json()["posts"], response.json()
    post = response.json()["posts"][0]
    assert post["title"] == "Coffee demo"
    assert post["asset_id"] == "asset-preview"
    assert post["destination"] == {
        "label": "Coffee channel", "platform": "youtube",
        "provider": "buffer", "post_type": None,
    }
    assert post["product_details"] == [
        {"offer_id": "offer-1", "name": "Coffee espresso maker"}
    ]


def test_deploy_preflights_then_activates_and_enables_campaign(
    workspace, monkeypatch
) -> None:
    from types import SimpleNamespace

    from trendrelay_api import campaign_autopilot_api, campaign_runner

    campaign_id = campaign(workspace)
    destination_body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations",
        json={
            "provider": "buffer", "integration_id": "acct-1",
            "platform": "youtube", "label": "brand",
        },
    ).json()["destination"]
    planned = SimpleNamespace(destination_id=destination_body["id"])
    monkeypatch.setattr(
        campaign_autopilot_api,
        "plan_campaign",
        lambda session, autopilot, **kwargs: ([planned], "Ready."),
    )
    monkeypatch.setattr(
        campaign_autopilot_api, "_would_be_accepted", lambda *args: None
    )
    monkeypatch.setattr(
        campaign_runner,
        "run_campaign",
        lambda session, autopilot, **kwargs: {
            "posts": [{"id": "publish-1"}], "failures": [], "note": "Deployed."
        },
    )

    unconfirmed = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/deploy",
        json={"confirm_external_action": False},
    )
    assert unconfirmed.status_code == 400

    deployed = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/deploy",
        json={"confirm_external_action": True},
    )

    assert deployed.status_code == 200, deployed.text
    assert deployed.json()["campaign_status"] == "active"
    assert deployed.json()["autopilot"]["enabled"] is True
    campaigns = request("GET", f"/api/workspaces/{workspace}/campaigns").json()
    assert campaigns["campaigns"][0]["status"] == "active"


def test_running_a_switched_off_autopilot_is_refused(workspace) -> None:
    campaign_id = campaign(workspace)
    response = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/run"
    )
    assert response.status_code == 409
    assert "switched off" in response.json()["detail"]


def test_an_offer_with_a_plain_http_url_mints_no_tracking_link(workspace) -> None:
    """Checked here, not by the redirector hours later.

    The attribution endpoint refuses a non-HTTPS destination; minting inside the
    autopilot run bypassed that check, so an http:// offer would have produced a
    link that failed at click time, in a different part of the app, long after
    the setting that caused it.
    """
    from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
    from trendrelay_api.campaign_autopilot_api import link_url_for

    campaign_id = campaign(workspace)
    with TestingSession.begin() as session:
        session.execute(
            ProductOffer.__table__.update()
            .where(ProductOffer.id == "offer-1")
            .values(affiliate_url="http://example.test/aff")
        )
    with TestingSession() as session:
        pilot = session.scalar(
            select(CampaignAutopilot).where(
                CampaignAutopilot.campaign_id == campaign_id
            )
        )
        assert pilot is not None
        pilot.offer_id = "offer-1"
        destination = CampaignDestination(
            workspace_id=workspace, campaign_id=campaign_id, provider="buffer",
            integration_id="acct-1", platform="youtube", label="brand",
        )
        session.add(destination)
        session.flush()
        assert link_url_for(session, pilot, destination) is None
        assert destination.tracking_link_id is None


def test_the_preview_reports_what_an_engine_would_refuse(workspace, tmp_path) -> None:
    """A preview that says "this is what will post" must have checked.

    Without this a campaign previews perfectly and then fails on every
    destination at run time - for a caption the disclosure pushed over a limit,
    or a title Reddit requires and the queue item never carried. The check is
    the engine's own `_validate_request`, run locally: nothing is uploaded and
    no engine is contacted, so a preview can afford it on every planned post.
    """
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
    from trendrelay_api.campaign_autopilot_api import _would_be_accepted
    from trendrelay_api.campaign_scheduler import ScheduledPost
    from trendrelay_api.integrations import publishing

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    campaign_id = campaign(workspace)
    pilot = CampaignAutopilot(
        workspace_id=workspace, campaign_id=campaign_id, delivery="draft",
        created_by="owner-user",
    )
    destination = CampaignDestination(
        workspace_id=workspace, campaign_id=campaign_id, provider="buffer",
        integration_id="acct-1", platform="twitter", label="brand on X",
    )

    def planned(caption: str, title: str | None = None) -> ScheduledPost:
        return ScheduledPost(
            campaign_id=campaign_id, destination_id="d1", queue_item_id="q1",
            at=datetime.now(UTC), video_path=str(media), title=title,
            caption=caption, first_comment=None, placement="caption", reason="",
        )

    uploads = CampaignDestination(
        workspace_id=workspace, campaign_id=campaign_id, provider="zernio",
        integration_id="acct-2", platform="twitter", label="brand on X via Zernio",
    )
    original = publishing.get_settings
    publishing.get_settings = lambda: SimpleNamespace(
        publishing_media_root_list=[str(tmp_path)], publishing_provider="zernio")
    # Buffer only needs a public URL when TrendRelay cannot supply one itself.
    # Without pinning this the test reads the developer's own .env and passes or
    # fails depending on whether they happen to have object storage configured.
    original_hosting = publishing.media_hosting.status
    publishing.media_hosting.status = lambda: {"configured": False}
    try:
        # X takes 280 characters. The disclosure leads every caption, so a body
        # that would have fit alone no longer does - exactly the arithmetic an
        # operator cannot be expected to do in their head.
        refused = _would_be_accepted(pilot, planned("word " * 120), uploads)
        assert refused is not None
        assert "280" in refused

        # The finding that matters most, and was invisible until the preview
        # started asking: Buffer has no upload endpoint, so an autopilot feeding
        # it local library files fails on every post until media hosting is set
        # up. Caught here instead of at the first scheduled slot.
        fetch_only = _would_be_accepted(pilot, planned("Short and fine."), destination)
        assert fetch_only is not None
        assert "public" in fetch_only.lower()

        # And a post an engine would take comes back clean, rather than merely
        # unreported.
        assert _would_be_accepted(pilot, planned("Short and fine."), uploads) is None
    finally:
        publishing.get_settings = original
        publishing.media_hosting.status = original_hosting


def test_the_preview_carries_a_verdict_for_every_post(workspace) -> None:
    # Present on every row, so a caller never has to guess whether a missing
    # `problem` means "fine" or "not checked".
    campaign_id = campaign(workspace)
    body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/preview"
    ).json()
    assert "problems" in body
    assert all("problem" in post for post in body["posts"])


def test_an_autopilot_link_carries_sub_ids_too(workspace) -> None:
    """These are the links that matter most and they were getting none.

    The autopilot mints its own rather than going through the attribution
    endpoint, so it missed the assignment entirely - and its posts are the
    unattended ones, whose conversions come back through the network's report
    or not at all.
    """
    from trendrelay_api.attribution_models import TrackingLink
    from trendrelay_api.attribution_subids import link_key
    from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
    from trendrelay_api.campaign_autopilot_api import link_url_for
    from trendrelay_api.opportunity_models import ProductOffer

    campaign_id = campaign(workspace)
    with TestingSession.begin() as session:
        # A network we have a sub-ID contract for; the fixture's offer points at
        # a host we deliberately leave alone.
        session.get(ProductOffer, "offer-1").affiliate_url = "https://shopee.vn/thing-i.1.2"

    request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"enabled": False, "offer_id": "offer-1", "confirm_external_action": True},
    )
    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations",
        json={"provider": "buffer", "integration_id": "acct-1",
              "platform": "instagram", "label": "brand on Instagram"},
    )

    with TestingSession.begin() as session:
        autopilot = session.scalars(select(CampaignAutopilot)).one()
        destination = session.scalars(select(CampaignDestination)).one()
        code = link_url_for(session, autopilot, destination)
        assert code
        link = session.scalars(
            select(TrackingLink).where(TrackingLink.code == code)
        ).one()

        assert link.sub_ids["sub_id1"] == link_key(code)
        assert link.sub_ids["sub_id3"] == "instagram"
        assert link.sub_ids["sub_id4"] == "Launch"
        # One link serves a destination and is reused for every video sent to
        # it, so there is no content to name. The slot stays empty rather than
        # being labelled with whichever video happened to go out first - and the
        # slots after it do not shift up to close the gap, because a network
        # reads them positionally.
        assert "sub_id2" not in link.sub_ids


def test_each_destination_product_pair_keeps_its_own_tracking_link(workspace) -> None:
    from trendrelay_api.autopilot_models import (
        CampaignAutopilot,
        CampaignDestination,
        CampaignDestinationOfferLink,
    )
    from trendrelay_api.campaign_autopilot_api import link_url_for

    campaign_id = campaign(workspace)
    with TestingSession.begin() as session:
        session.add(Product(
            id="prod-2", workspace_id=workspace, catalog_key="k2",
            name="Coffee grinder", marketplace="amazon", created_by="owner-user",
        ))
        session.add(ProductOffer(
            id="offer-2", workspace_id=workspace, product_id="prod-2",
            fingerprint="f2", network="amazon", merchant="Amazon",
            affiliate_url="https://example.test/grinder", currency="USD",
            availability="available", created_by="owner-user",
        ))
    request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"offer_mode": "smart", "confirm_external_action": True},
    )
    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations",
        json={"provider": "buffer", "integration_id": "acct-1",
              "platform": "youtube", "label": "brand"},
    )

    with TestingSession.begin() as session:
        pilot = session.scalars(select(CampaignAutopilot)).one()
        destination = session.scalars(select(CampaignDestination)).one()
        first = link_url_for(session, pilot, destination, "offer-1")
        second = link_url_for(session, pilot, destination, "offer-2")
        repeated = link_url_for(session, pilot, destination, "offer-1")
        mappings = session.scalars(select(CampaignDestinationOfferLink)).all()

        assert first and second and first != second
        assert repeated == first
        assert {mapping.offer_id for mapping in mappings} == {"offer-1", "offer-2"}


def test_a_destination_post_type_is_checked_when_it_is_set(workspace) -> None:
    """Not at every scheduled run, which is where it would otherwise surface.

    A campaign destination is fed by a standing programme, so an unusable post
    type is not one failed post - it fails unattended, on every slot, until
    somebody reads a note explaining why nothing has gone out.
    """
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations"
    response = request("POST", base, json={
        "provider": "buffer", "integration_id": "acct-1", "platform": "tiktok",
        "label": "brand", "post_type": "story",
    })
    assert response.status_code == 422
    assert "does not accept 'story'" in response.json()["detail"]


def test_a_campaign_destination_cannot_be_a_photo_carousel(workspace) -> None:
    """Its queue holds clips, and nothing here could supply images.

    Refused where somebody is watching rather than noted once a slot in
    `last_note`, which is where the failure would land otherwise.
    """
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations"
    response = request("POST", base, json={
        "provider": "zernio", "integration_id": "acct-9", "platform": "tiktok",
        "label": "brand", "post_type": "photo",
    })
    assert response.status_code == 422
    assert "queue of clips" in response.json()["detail"]


def test_a_usable_post_type_is_still_accepted(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations"
    response = request("POST", base, json={
        "provider": "buffer", "integration_id": "acct-2", "platform": "instagram",
        "label": "brand", "post_type": "story",
    })
    assert response.status_code == 201, response.text
    assert response.json()["destination"]["post_type"] == "story"


def test_a_destination_names_an_engine_that_exists(workspace) -> None:
    campaign_id = campaign(workspace)
    response = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations",
        json={"provider": "not-an-engine", "integration_id": "a1",
              "platform": "tiktok", "label": "brand"},
    )
    assert response.status_code == 422
    assert "Unknown publishing provider" in response.json()["detail"]


def test_a_destination_pairs_a_network_its_engine_can_reach(workspace) -> None:
    """Buffer has no Reddit, and a campaign would find that out once a slot.

    The pairing is the thing that has to hold: each is a real name on its own,
    and only together are they a destination nothing can deliver.
    """
    campaign_id = campaign(workspace)
    response = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations",
        json={"provider": "buffer", "integration_id": "a1",
              "platform": "reddit", "label": "brand"},
    )
    assert response.status_code == 422
    assert "does not publish to reddit" in response.json()["detail"]
    # The same network through an engine that has it is fine.
    assert request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations",
        json={"provider": "zernio", "integration_id": "a2",
              "platform": "reddit", "label": "brand"},
    ).status_code == 201
