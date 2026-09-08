"""Tests for immediate publishing of campaign queue items.

On its own database, which is the only way these can pass twice.

They used to run against the application's real `SessionFactory` with fixed
ids, so every run wrote a workspace, a campaign and a queue item into the
developer's library - and, once a publish had been attempted, a
`PublicationExecution` that outlived the run. `uncertain` is a holding state,
so the second run met the in-flight guard refusing to publish an item the
first run had already claimed. The suite poisoned the database it was reading,
and could not pass again until somebody deleted the rows by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import LOCAL_ADMIN_ID
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_runner import batch_publish_queue_items, publish_queue_item_now
from trendrelay_api.campaign_scheduler import record_published
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base, Campaign, Workspace, WorkspaceMember
from trendrelay_api.publication_models import PublicationExecution

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with SessionFactory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@pytest.fixture
def workspace_with_campaign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Workspace, campaign, autopilot, connected destination, and sample queue item.

    Built fresh each time. The get-or-create below is left as it was, but it now
    always creates: the schema is dropped and rebuilt per test, so no execution
    survives to hold the queue item against the next one.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    monkeypatch.setenv("TRENDRELAY_MEDIA_ROOTS", str(tmp_path))

    monkeypatch.setattr(
        "trendrelay_api.config.Settings.publishing_media_root_list",
        [str(tmp_path)],
    )

    sample_media = tmp_path / "sample.mp4"
    sample_media.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

    monkeypatch.setattr(
        "trendrelay_api.integrations.publishing.video_fits_platform",
        lambda *args, **kwargs: (True, None),
    )
    monkeypatch.setattr(
        "trendrelay_api.integrations.publishing.delivery_block",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "trendrelay_api.campaign_runner._publish_execution",
        lambda session, autopilot, execution, **kwargs: {
            "id": f"publish_mock_{execution.id}",
            "status": "queued",
        },
    )

    with SessionFactory() as session:
        ws_id = "ws_test_pubnow"
        camp_id = "camp_test_pubnow"

        ws = session.get(Workspace, ws_id)
        if not ws:
            ws = Workspace(
                id=ws_id,
                name="Test WS",
                slug="test-ws-pubnow",
                created_by=LOCAL_ADMIN_ID,
            )
            session.add(ws)
            session.flush()

        existing_member = session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws_id,
                WorkspaceMember.user_id == LOCAL_ADMIN_ID,
            )
        ).scalar_one_or_none()
        if not existing_member:
            session.add(WorkspaceMember(workspace_id=ws_id, user_id=LOCAL_ADMIN_ID, role="owner"))
            session.flush()

        campaign = session.get(Campaign, camp_id)
        if not campaign:
            campaign = Campaign(
                id=camp_id,
                workspace_id=ws_id,
                name="Test Campaign",
                objective="Test fashion content distribution",
                audience="Fashion enthusiasts 18-35",
                status="active",
                created_by=LOCAL_ADMIN_ID,
            )
            session.add(campaign)
            session.flush()

        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == camp_id)
        )
        if not autopilot:
            autopilot = CampaignAutopilot(
                campaign_id=camp_id,
                workspace_id=ws_id,
                enabled=True,
                repeat_posts=False,
                min_recycle_days=30,
                delivery="now",
                created_by=LOCAL_ADMIN_ID,
            )
            session.add(autopilot)
            session.flush()

        dest_id = "dest_tiktok_test"
        destination = session.get(CampaignDestination, dest_id)
        if not destination:
            destination = CampaignDestination(
                id=dest_id,
                campaign_id=camp_id,
                workspace_id=ws_id,
                platform="tiktok",
                provider="zernio",
                integration_id="int_tiktok_123",
                label="TikTok Account",
                enabled=True,
            )
            session.add(destination)
            session.flush()

        item = CampaignQueueItem(
            id="queued_pubnow_test_1",
            workspace_id=ws_id,
            campaign_id=camp_id,
            video_path=str(sample_media),
            title="Test Post 1",
            body="Amazing fashion review post #ootd",
            hashtags=["ootd", "fashion"],
            state="approved",
            position=1,
            times_posted=0,
            last_posted_by_destination={},
            created_by=LOCAL_ADMIN_ID,
        )
        session.merge(item)
        session.commit()

    yield {
        "workspace_id": ws_id,
        "campaign_id": camp_id,
        "destination_id": dest_id,
        "item_id": "queued_pubnow_test_1",
        "sample_media": sample_media,
    }
    app.dependency_overrides.clear()


def test_publish_queue_item_now_reserves_without_counting_before_confirmation(
    workspace_with_campaign: dict[str, Any]
) -> None:
    ws_id = workspace_with_campaign["workspace_id"]
    camp_id = workspace_with_campaign["campaign_id"]
    item_id = workspace_with_campaign["item_id"]
    dest_id = workspace_with_campaign["destination_id"]

    now = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)

    with SessionFactory() as session:
        result = publish_queue_item_now(
            session, ws_id, camp_id, item_id, now=now
        )
        session.commit()

        assert len(result["published"]) == 1
        assert result["published"][0]["destination_id"] == dest_id
        assert len(result["skipped"]) == 0

        item = session.get(CampaignQueueItem, item_id)
        assert item is not None
        assert item.times_posted == 0
        assert dest_id not in item.last_posted_by_destination

        exec_id = result["published"][0]["execution_id"]
        execution = session.get(PublicationExecution, exec_id)
        assert execution is not None
        assert execution.state == "queued"
        assert execution.delivery == "now"
        assert execution.queue_item_id == item_id


def test_repeat_posts_off_skips_already_posted_destination_unless_forced(
    workspace_with_campaign: dict[str, Any]
) -> None:
    ws_id = workspace_with_campaign["workspace_id"]
    camp_id = workspace_with_campaign["campaign_id"]
    item_id = workspace_with_campaign["item_id"]
    dest_id = workspace_with_campaign["destination_id"]

    now1 = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)
    now2 = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)

    with SessionFactory() as session:
        first = publish_queue_item_now(session, ws_id, camp_id, item_id, now=now1)
        execution = session.get(
            PublicationExecution, first["published"][0]["execution_id"]
        )
        assert execution is not None
        record_published(session, execution, now=now1)
        execution.state = "published"
        session.commit()

        with pytest.raises(ValueError, match="Let a post go out more than once.*is Off"):
            publish_queue_item_now(session, ws_id, camp_id, item_id, force=False, now=now2)

        forced_result = publish_queue_item_now(
            session, ws_id, camp_id, item_id, force=True, now=now2
        )
        session.commit()
        assert len(forced_result["published"]) == 1
        assert forced_result["published"][0]["destination_id"] == dest_id


def test_an_inflight_immediate_publish_cannot_be_duplicated(
    workspace_with_campaign: dict[str, Any]
) -> None:
    ws_id = workspace_with_campaign["workspace_id"]
    camp_id = workspace_with_campaign["campaign_id"]
    item_id = workspace_with_campaign["item_id"]
    now = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)

    with SessionFactory() as session:
        publish_queue_item_now(session, ws_id, camp_id, item_id, now=now)
        session.commit()

        with pytest.raises(ValueError, match="already queued or publishing"):
            publish_queue_item_now(
                session,
                ws_id,
                camp_id,
                item_id,
                force=True,
                now=datetime(2026, 8, 30, 10, 1, 0, tzinfo=UTC),
            )


def test_batch_publish_queue_items(workspace_with_campaign: dict[str, Any]) -> None:
    ws_id = workspace_with_campaign["workspace_id"]
    camp_id = workspace_with_campaign["campaign_id"]
    item_id = workspace_with_campaign["item_id"]

    with SessionFactory() as session:
        batch_res = batch_publish_queue_items(
            session, ws_id, camp_id, [item_id], force=True
        )
        session.commit()

        assert batch_res["published_items"] == 1
        assert batch_res["total_jobs"] == 1
        assert len(batch_res["failures"]) == 0


def test_api_publish_single_queue_item(workspace_with_campaign: dict[str, Any]) -> None:
    client = TestClient(app)
    ws_id = workspace_with_campaign["workspace_id"]
    camp_id = workspace_with_campaign["campaign_id"]
    item_id = workspace_with_campaign["item_id"]

    resp = client.post(
        f"/api/workspaces/{ws_id}/campaigns/{camp_id}/queue/{item_id}/publish",
        json={"force": True},
        headers={"x-actor-user-id": LOCAL_ADMIN_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["published"]) == 1
    assert data["item_id"] == item_id
