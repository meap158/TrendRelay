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
from trendrelay_api.autopilot_models import CampaignAutopilot
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base, Campaign
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


def tag_offer(workspace_id: str, campaign_id: str, offer_id: str = "offer-1") -> None:
    """Let this campaign promote this product.

    A campaign matches against the products tagged to it and nothing else, so
    a test about matching has to say which product is on the table. Written
    out per test rather than folded into `campaign()`, because "everything in
    the workspace is available" is the assumption the tag exists to remove.
    """
    from trendrelay_api.autopilot_models import CampaignOffer

    with TestingSession.begin() as session:
        session.add(CampaignOffer(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            offer_id=offer_id,
            created_by="owner-user",
        ))


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


def _held(workspace_id: str, campaign_id: str, identifier: str, **overrides):
    """A frozen post waiting in the inbox, ready to be approved."""
    from trendrelay_api.publication_models import PublicationExecution

    fields: dict = {
        "state": "proposed",
        "delivery": "schedule",
        "platform": "youtube",
        "provider": "buffer",
        "integration_id": "acct-1",
        "destination_label": "youtube account",
        "caption": "Three ways to pull a better espresso.",
        "media_path": r"S:\media\clip.mp4",
        "image_paths": [],
        "thread": [],
        "offer_ids": [],
        "tracking_links": [],
        "held_reason": "Waiting for approval.",
    }
    fields.update(overrides)
    with TestingSession.begin() as session:
        session.add(PublicationExecution(
            id=identifier, workspace_id=workspace_id, campaign_id=campaign_id,
            created_by="owner-user", **fields,
        ))
    return identifier


def test_approving_a_batch_needs_the_same_confirmation_one_post_does(workspace) -> None:
    campaign_id = campaign(workspace)
    _held(workspace, campaign_id, "exec-1")

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/executions/approve",
        json={"execution_ids": ["exec-1"]},
    )

    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"]


def test_one_unfinished_post_does_not_sink_the_batch(workspace, monkeypatch) -> None:
    """The whole point of the batch, and the thing that would make it useless.

    Failing all of them because one was unfinished would leave the operator
    finding which one and doing the others again - more work than approving
    them singly, which is what the batch exists to replace.
    """
    from trendrelay_api import campaign_runner

    campaign_id = campaign(workspace)
    _held(workspace, campaign_id, "exec-good")
    # The placeholder is what `finalization_problems` refuses, and it refuses
    # before anything is mutated.
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY
    _held(workspace, campaign_id, "exec-unwritten", caption=PLACEHOLDER_BODY)

    # The delivery itself is stubbed: this is about which posts get that far,
    # not about what an engine does with them.
    monkeypatch.setattr(
        campaign_runner, "_publish_execution",
        lambda session, autopilot, execution, **kwargs: {"id": f"job-{execution.id}"},
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/executions/approve",
        json={
            "execution_ids": ["exec-unwritten", "exec-good"],
            "confirm_external_action": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approved"] == 1
    assert body["refused"] == 1
    # Reported per post, in the order asked: "1 approved" over a list of two is
    # not an answer to which one is still waiting.
    assert [row["execution_id"] for row in body["results"]] == [
        "exec-unwritten", "exec-good",
    ]
    assert body["results"][0]["approved"] is False
    assert "not finished" in body["results"][0]["problem"]
    assert body["results"][1]["approved"] is True


def test_the_same_post_twice_in_one_request_is_one_post(workspace, monkeypatch) -> None:
    from trendrelay_api import campaign_runner

    campaign_id = campaign(workspace)
    _held(workspace, campaign_id, "exec-1")
    monkeypatch.setattr(
        campaign_runner, "_publish_execution",
        lambda session, autopilot, execution, **kwargs: {"id": f"job-{execution.id}"},
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/executions/approve",
        json={
            "execution_ids": ["exec-1", "exec-1"],
            "confirm_external_action": True,
        },
    )

    body = response.json()
    assert body["approved"] == 1
    assert len(body["results"]) == 1


def test_a_campaign_starts_with_autopilot_off(workspace) -> None:
    """Nothing posts because a campaign was created."""
    campaign_id = campaign(workspace)
    body = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot"
    ).json()
    assert body["autopilot"]["enabled"] is False
    # Scheduled, not drafted: the switch is off, so nothing posts either way,
    # and when it is switched on an approved package should go out rather than
    # wait for a second approval nobody asked for.
    assert body["autopilot"]["delivery"] == "schedule"
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
    tag_offer(workspace, campaign_id)
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
    tag_offer(workspace, campaign_id)
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
    tag_offer(workspace, campaign_id, "offer-dress")
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


def test_a_queued_item_arrives_ready(workspace) -> None:
    # Approval lives at the execution layer - the authority dial and its
    # exception inbox - not on a second per-item gate in front of it.
    campaign_id = campaign(workspace)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={"video_path": r"S:\media\clip.mp4", "body": "Copy", "hashtags": ["#coffee"]},
    ).json()["item"]
    assert item["state"] == "approved"
    # Hashtags are stored bare and rendered with one hash, however they were typed.
    assert item["hashtags"] == ["coffee"]


def test_an_item_can_be_parked_and_resumed(workspace) -> None:
    # 'draft' remains as the operator's parking brake.
    campaign_id = campaign(workspace)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={"video_path": r"S:\media\clip.mp4", "body": "Copy"},
    ).json()["item"]
    parked = request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item['id']}",
        json={"state": "draft"},
    )
    assert parked.status_code == 200
    assert parked.json()["item"]["state"] == "draft"
    resumed = request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item['id']}",
        json={"state": "approved"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["item"]["state"] == "approved"


def test_switching_on_activates_the_campaign_and_runs_it(workspace) -> None:
    """The switch is the deploy: no second ceremony to find.

    An empty queue arms rather than errors - the run records its note and
    content added later posts on the next tick.
    """
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"enabled": True, "confirm_external_action": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["autopilot"]["enabled"] is True
    with TestingSession() as session:
        row = session.get(Campaign, campaign_id)
        assert row is not None and row.status == "active"
        pilot = session.scalars(select(CampaignAutopilot).where(
            CampaignAutopilot.campaign_id == campaign_id
        )).one()
        assert pilot.last_run_at is not None, "the first run happened at the switch"


def test_queue_items_persist_an_editable_post_package(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
    item = request("POST", f"{base}/queue", json={
        "video_path": r"S:\media\package.mp4",
        "title": "Original title",
        "body": "Primary post",
        "hashtags": ["launch"],
        "first_comment": "Opening comment",
        "thread": ["Reply one"],
    }).json()["item"]

    assert item["first_comment"] == "Opening comment"
    assert item["thread"] == ["Reply one"]

    updated = request("PATCH", f"{base}/queue/{item['id']}", json={
        "title": "Updated title",
        "first_comment": "",
        "thread": ["Reply one", "  ", "Reply two"],
    })

    assert updated.status_code == 200, updated.text
    package = updated.json()["item"]
    assert package["title"] == "Updated title"
    assert package["first_comment"] is None
    assert package["thread"] == ["Reply one", "Reply two"]


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
    assert any(reason in body["note"] for reason in (
        "not active", "No destinations", "switched off",
    )), body["note"]


def test_preview_rows_carry_media_account_and_product_routes(workspace) -> None:
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
    request("POST", f"{base}/destinations", json={
        "provider": "buffer", "integration_id": "acct-1",
        "platform": "youtube", "label": "Coffee channel",
    })
    # Switched on before anything is queued. Switching on runs the campaign
    # there and then, and a run reserves what it posts - so enabling after
    # approving would consume the very item this test wants to see forecast.
    request("PUT", f"{base}/autopilot",
            json={"enabled": True, "confirm_external_action": True})
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
    # The outlook forecasts nothing while the switch is off, so a test about
    # what a running campaign would post has to switch it on.
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
    # The rate travels with the name. A row that lists which products are
    # attached without saying what any of them pays cannot answer the question
    # the row exists for, and the offer's own commission is already to hand.
    assert post["product_details"] == [
        {
            "offer_id": "offer-1",
            "name": "Coffee espresso maker",
            "commission_bps": 400,
            "commission_flat_cents": None,
            "currency": "USD",
        }
    ]


def test_preview_reconnects_committed_publish_jobs_to_the_timeline(workspace) -> None:
    from trendrelay_api.models import DurableJob

    campaign_id = campaign(workspace)
    at = datetime.now(UTC) + timedelta(hours=2)
    with TestingSession.begin() as session:
        session.add(DurableJob(
            id="publish_campaign_timeline",
            workspace_key=workspace,
            kind="social_publish",
            status="queued",
            payload={"request": {
                "workspace_id": workspace,
                "campaign_id": campaign_id,
                "queue_item_id": "queued-1",
                "destination_id": "destination-1",
                "date": at.isoformat(),
                "title": "Durable campaign post",
                "caption": "Primary content",
                "first_comment": "Follow-up",
                "thread": ["Reply"],
                "delivery": "schedule",
                "targets": [{
                    "platform": "threads", "integration_id": "threads-account",
                    "provider": "buffer", "post_type": "post",
                }],
            }},
            attempt_count=0,
            max_attempts=1,
            cancellation_requested=False,
        ))

    response = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/preview"
    )

    assert response.status_code == 200, response.text
    deployed = response.json()["deployed"]
    assert deployed[0]["id"] == "publish_campaign_timeline"
    assert deployed[0]["caption"] == "Primary content"
    assert deployed[0]["first_comment"] == "Follow-up"
    assert deployed[0]["thread"] == ["Reply"]


def test_committed_jobs_link_to_the_post_or_the_page(workspace) -> None:
    """A succeeded row needs somewhere to point.

    The post itself when the engine reported where it lives, the account's
    own page otherwise - and a retry's second job for the same post does not
    double the list; the newest one tells its story.
    """
    from trendrelay_api.models import DurableJob

    campaign_id = campaign(workspace)
    at = datetime.now(UTC) - timedelta(hours=2)

    def job(id_: str, created: datetime, result: dict | None = None) -> DurableJob:
        return DurableJob(
            id=id_, workspace_key=workspace, kind="social_publish",
            status="succeeded",
            payload={"request": {
                "workspace_id": workspace, "campaign_id": campaign_id,
                "destination_id": "destination-1", "date": at.isoformat(),
                "title": "Same post", "caption": "Same content",
                "delivery": "schedule",
                "targets": [{
                    "platform": "threads",
                    "integration_id": "halcyonbooks.official",
                    "provider": "buffer", "post_type": "post",
                }],
            }},
            result=result, attempt_count=1, max_attempts=3,
            cancellation_requested=False, created_at=created,
        )

    with TestingSession.begin() as session:
        session.add(job("job-older", at))
        session.add(job(
            "job-newer", at + timedelta(minutes=5),
            result={"deliveries": [{
                "post_ids": ["123"],
                "permalink": "https://www.threads.net/@halcyonbooks.official/post/abc",
            }]},
        ))

    response = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/preview"
    )

    assert response.status_code == 200, response.text
    deployed = response.json()["deployed"]
    assert [item["id"] for item in deployed] == ["job-newer"], (
        "one entry per post, told by the newest job"
    )
    assert deployed[0]["post_url"] == (
        "https://www.threads.net/@halcyonbooks.official/post/abc"
    )
    assert deployed[0]["page_url"] == "https://www.threads.net/@halcyonbooks.official"


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


def test_an_offer_with_a_plain_http_url_is_refused_a_place_in_a_post(workspace) -> None:
    """Checked where the link is chosen, not discovered in a published caption.

    The post carries the offer's URL verbatim now (ADR 0022), so an http:// or
    credential-bearing URL would go out to readers exactly as stored. Refusing
    it here keeps the same standard the attribution endpoint always applied.
    """
    from trendrelay_api.campaign_autopilot_api import offer_link_url

    campaign(workspace)
    with TestingSession.begin() as session:
        session.execute(
            ProductOffer.__table__.update()
            .where(ProductOffer.id == "offer-1")
            .values(affiliate_url="http://example.test/aff")
        )
    with TestingSession() as session:
        assert offer_link_url(session, "offer-1") is None


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


def test_a_post_carries_the_offers_own_short_link_and_mints_nothing(workspace) -> None:
    """The Shopee short link is what earns the commission, so it goes out whole.

    Tracking lives in the network's own report now (ADR 0022): the link that
    lands in a caption is the affiliate URL exactly as imported, and no
    TrackingLink row is created behind it.
    """
    from trendrelay_api.attribution_models import TrackingLink
    from trendrelay_api.campaign_autopilot_api import offer_link_url
    from trendrelay_api.opportunity_models import ProductOffer

    campaign(workspace)
    with TestingSession.begin() as session:
        session.get(ProductOffer, "offer-1").affiliate_url = (
            "https://s.shopee.vn/2gAN9f0Ef6"
        )

    with TestingSession.begin() as session:
        assert offer_link_url(session, "offer-1") == "https://s.shopee.vn/2gAN9f0Ef6"
        assert session.scalars(select(TrackingLink)).all() == []


def test_each_offer_brings_its_own_link(workspace) -> None:
    """Two products in one post link to two different places - their own."""
    from trendrelay_api.campaign_autopilot_api import offer_link_url

    campaign(workspace)
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

    with TestingSession() as session:
        first = offer_link_url(session, "offer-1")
        second = offer_link_url(session, "offer-2")
        assert first and second and first != second
        assert second == "https://example.test/grinder"
        assert offer_link_url(session, "missing-offer") is None


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


# --- a package can be pictures, and can arrive before its copy ----------------


def test_a_package_can_be_a_photo_carousel(workspace) -> None:
    """The one shape a campaign could not hold. Publish has sent them for a
    while; the queue had nowhere to put the second picture."""
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"

    response = request("POST", f"{base}/queue", json={
        "image_paths": [r"S:\media\one.jpg", r"S:\media\two.jpg"],
        "body": "Three ways to fold a shirt.",
    })

    assert response.status_code == 201, response.text
    item = response.json()["item"]
    assert item["image_paths"] == [r"S:\media\one.jpg", r"S:\media\two.jpg"]
    assert item["video_path"] == ""


def test_the_picture_order_is_the_order_it_posts(workspace) -> None:
    # A carousel is ordered - the first picture is the cover - so this is a
    # list, and the order chosen in the picker is the order stored.
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
    paths = [rf"S:\media\{n}.jpg" for n in range(5)]

    item = request("POST", f"{base}/queue",
                   json={"image_paths": paths, "body": "Ordered"}).json()["item"]

    assert item["image_paths"] == paths


def test_a_package_is_a_video_or_pictures_but_not_both(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"

    response = request("POST", f"{base}/queue", json={
        "video_path": r"S:\media\clip.mp4",
        "image_paths": [r"S:\media\one.jpg"],
        "body": "Both at once",
    })

    assert response.status_code == 422


def test_a_package_needs_some_media(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"

    response = request("POST", f"{base}/queue", json={"body": "Words only"})

    assert response.status_code == 422


def test_media_can_be_picked_before_the_copy_is_written(workspace) -> None:
    """Picking and writing no longer have to happen in one sitting.

    A network refuses a post with no caption at all, so the package carries a
    stand-in rather than an empty string - and says it is one.
    """
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"

    item = request("POST", f"{base}/queue",
                   json={"video_path": r"S:\media\clip.mp4"}).json()["item"]

    assert item["needs_copy"] is True
    assert item["body"]


def test_copy_somebody_wrote_is_not_marked_as_needing_writing(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}"

    item = request("POST", f"{base}/queue", json={
        "video_path": r"S:\media\clip.mp4", "body": "Real copy.",
    }).json()["item"]

    assert item["needs_copy"] is False
    assert item["body"] == "Real copy."


# --- matching before anything is queued ----------------------------------------
#
# The composer shows the fitting products per row while media is being chosen,
# so somebody sees what would attach before approving rather than after.


def _draft_asset(workspace, asset_id, title, caption, hashtags):
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace, title=title,
            media_kind="video", source_type="upload", caption=caption,
            hashtags=hashtags, original_path=rf"S:\media\{asset_id}.mp4",
            original_sha256=asset_id.ljust(64, "0")[:64], mime_type="video/mp4",
            size_bytes=20, created_by="owner-user",
        ))


def test_a_clip_is_matched_before_it_reaches_the_queue(workspace) -> None:
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)
    _draft_asset(
        workspace, "asset-draft", "Portable espresso setup",
        "Make espresso anywhere with this compact coffee kit", ["coffee"],
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
        "/offer-recommendations/draft",
        json={"asset_ids": ["asset-draft"]},
    )

    assert response.status_code == 200, response.text
    found = response.json()["assets"]["asset-draft"]
    assert found["matches"][0]["offer_id"] == "offer-1"
    # Scored off the asset, which is where the strongest signals live.
    assert any(
        "caption" in source or "media title" in source
        for source in found["matches"][0]["evidence_sources"]
    )


def test_matching_a_draft_never_leaves_a_queue_item_behind(workspace) -> None:
    """The risk in scoring against an item that was built and not saved.

    Reusing the real matcher means handing it a `CampaignQueueItem`, and one
    that reached a flush would become a queue row nobody asked for - media
    would appear in the campaign merely because somebody looked at it.
    """
    campaign_id = campaign(workspace)
    _draft_asset(workspace, "asset-ghost", "Ghost clip", "A clip", [])

    request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
        "/offer-recommendations/draft",
        json={"asset_ids": ["asset-ghost"]},
    )

    queue = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot"
    ).json()["queue"]
    assert queue == []


def test_one_stale_id_does_not_cost_the_rest_their_suggestions(workspace) -> None:
    # A selection of a hundred should not lose every suggestion because one
    # asset was deleted between picking and composing.
    campaign_id = campaign(workspace)
    _draft_asset(workspace, "asset-real", "Real clip", "Espresso coffee kit", [])

    body = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
        "/offer-recommendations/draft",
        json={"asset_ids": ["asset-real", "asset-gone"]},
    ).json()

    assert "asset-real" in body["assets"]
    assert "asset-gone" not in body["assets"]


def test_the_whole_selection_is_matched_in_one_request(workspace) -> None:
    # One call for the row set: a hundred rows as a hundred requests would
    # re-read the same campaign and the same offer catalogue each time.
    campaign_id = campaign(workspace)
    for index in range(3):
        _draft_asset(
            workspace, f"asset-many-{index}", f"Clip {index}", "Espresso kit", [],
        )

    body = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
        "/offer-recommendations/draft",
        json={"asset_ids": [f"asset-many-{index}" for index in range(3)]},
    ).json()

    assert sorted(body["assets"]) == ["asset-many-0", "asset-many-1", "asset-many-2"]


def test_a_selection_larger_than_the_cap_is_refused(workspace) -> None:
    # Each id costs a scoring pass, so the cap is what stops one request
    # becoming a hundred sequential matches inside a single handler.
    campaign_id = campaign(workspace)

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
        "/offer-recommendations/draft",
        json={"asset_ids": [f"asset-{index}" for index in range(101)]},
    )

    assert response.status_code == 422


def _second_product(workspace: str, campaign_id: str) -> None:
    """A second tagged product, so "which one" is a real question.

    Named to share a token with the first and lose to it: a rotation is only
    visible when the ranking has an opinion for it to take turns against.
    """
    with TestingSession.begin() as session:
        session.add(Product(
            id="prod-2", workspace_id=workspace, catalog_key="k2", identifier="i2",
            name="Coffee grinder", brand="B", category="Kitchen",
            marketplace="amazon", product_url="https://example.test/p2",
            created_by="owner-user",
        ))
        session.add(ProductOffer(
            id="offer-2", workspace_id=workspace, product_id="prod-2",
            fingerprint="f2", network="amazon", merchant="Amazon",
            affiliate_url="https://example.test/aff2", price_cents=4_999,
            currency="USD", commission_bps=400, cookie_days=1,
            availability="available", created_by="owner-user",
        ))
    tag_offer(workspace, campaign_id, "offer-2")


def _campaign_setting(workspace: str, campaign_id: str, **settings) -> None:
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"disclosure": "#ad", "confirm_external_action": True, **settings},
    )
    assert response.status_code == 200, response.text


def _drafted(workspace: str, campaign_id: str, asset_ids: list[str]) -> dict:
    body = request(
        "POST",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}"
        "/offer-recommendations/draft",
        json={"asset_ids": asset_ids},
    )
    assert body.status_code == 200, body.text
    return body.json()["assets"]


def test_the_preview_carries_no_more_products_than_a_post_will(workspace) -> None:
    """A preview of a post that could not happen is worse than no preview.

    This showed the top of the raw ranking, and the composer asked for two of
    it - so a campaign set to one product per post previewed two, and the
    setting looked ignored.
    """
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)
    _second_product(workspace, campaign_id)
    _campaign_setting(workspace, campaign_id, max_products_per_post=1)
    _draft_asset(
        workspace, "asset-cap", "Portable espresso setup",
        "Make espresso anywhere with this compact coffee kit", ["coffee"],
    )

    found = _drafted(workspace, campaign_id, ["asset-cap"])

    assert len(found["asset-cap"]["matches"]) == 1


def test_a_batch_spreads_across_products_rather_than_repeating_one(workspace) -> None:
    """Best fit first, then whoever has not had a turn.

    Every row was scored on its own, and the ranking does not change between
    them - so twenty clips all took the same leading product. Each row really
    did get its best match, which is why it went unnoticed until the posts
    promoted two products out of forty.
    """
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)
    _second_product(workspace, campaign_id)
    _campaign_setting(workspace, campaign_id, max_products_per_post=1)
    for index in range(2):
        _draft_asset(
            workspace, f"asset-turn-{index}", "Portable espresso setup",
            "Make espresso anywhere with this compact coffee kit", ["coffee"],
        )

    found = _drafted(
        workspace, campaign_id, ["asset-turn-0", "asset-turn-1"]
    )

    picked = [
        found[f"asset-turn-{index}"]["matches"][0]["offer_id"] for index in range(2)
    ]
    assert picked == ["offer-1", "offer-2"]


def test_rotation_turned_off_previews_the_same_best_fit_every_row(workspace) -> None:
    """The preview follows the setting either way, including off."""
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)
    _second_product(workspace, campaign_id)
    _campaign_setting(
        workspace, campaign_id, max_products_per_post=1, rotate_products=False
    )
    for index in range(2):
        _draft_asset(
            workspace, f"asset-same-{index}", "Portable espresso setup",
            "Make espresso anywhere with this compact coffee kit", ["coffee"],
        )

    found = _drafted(workspace, campaign_id, ["asset-same-0", "asset-same-1"])

    picked = [
        found[f"asset-same-{index}"]["matches"][0]["offer_id"] for index in range(2)
    ]
    assert picked == ["offer-1", "offer-1"]


# --- what would actually attach, not just what fits ----------------------------


def test_review_says_which_matches_would_be_posted(workspace) -> None:
    """The ranking answers "what fits"; only the resolver answers "what posts".

    They differ by the confidence floor, the per-post ceiling, and every pin or
    campaign mode that outranks the scores - so the panel asks the API rather
    than reapplying the rule and drifting from the scheduler.
    """
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={
            "video_path": r"S:\media\espresso.mp4",
            "title": "Portable coffee setup",
            "body": "Make espresso anywhere with this compact coffee kit.",
            "hashtags": ["coffee", "espresso"],
        },
    ).json()["item"]

    body = request(
        "GET",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
        params={"item_id": item["id"]},
    ).json()

    assert body["chosen_offer_ids"] == ["offer-1"]
    # A forecast of the ranking, not the whole of it.
    assert len(body["chosen_offer_ids"]) <= len(body["matches"])


def test_a_pin_is_what_would_post_whatever_the_scores_say(workspace) -> None:
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={"video_path": r"S:\media\x.mp4", "body": "Anything at all."},
    ).json()["item"]
    request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item['id']}",
        json={"offer_ids": ["offer-1"]},
    )

    body = request(
        "GET",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
        params={"item_id": item["id"]},
    ).json()

    assert body["chosen_offer_ids"] == ["offer-1"]


def test_asking_about_the_campaign_forecasts_nothing(workspace) -> None:
    # Without an item there is no post to attach anything to, so the ranking
    # stands alone rather than pretending to predict one.
    campaign_id = campaign(workspace)
    tag_offer(workspace, campaign_id)

    body = request(
        "GET",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
    ).json()

    assert body["chosen_offer_ids"] == []
    assert body["matches"]
