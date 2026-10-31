"""Campaign GUI parity with an isolated database and no provider delivery."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import get_type_hints

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trendrelay_api.main  # noqa: F401 - registers all database models
from trendrelay_api import campaign_autopilot_api, campaigns_api, database, publishing_api
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.integrations import posting_slots
from trendrelay_api.integrations.mcp import campaigns, server
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion
from trendrelay_api.models import Base, Campaign, UserProfile, Workspace, WorkspaceMember


@pytest.fixture
def factory(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(UserProfile(id="local-admin", email="local@example.test"))
        for ws in ("ws", "foreign"):
            session.add(
                Workspace(id=ws, name=ws, slug=ws, timezone="UTC", created_by="local-admin")
            )
            session.add(
                WorkspaceMember(
                    id="member-" + ws, workspace_id=ws, user_id="local-admin", role="owner"
                )
            )
            session.add(
                Campaign(
                    id="camp-" + ws,
                    workspace_id=ws,
                    name=ws,
                    objective="Share stories",
                    audience="Readers",
                    status="active",
                    created_by="local-admin",
                )
            )
            session.add(
                CampaignAutopilot(
                    id="auto-" + ws,
                    workspace_id=ws,
                    campaign_id="camp-" + ws,
                    enabled=False,
                    authority="autonomous",
                    offer_mode="none",
                    created_by="local-admin",
                )
            )
        session.add(
            CampaignDestination(
                id="dest",
                workspace_id="ws",
                campaign_id="camp-ws",
                provider="buffer",
                platform="threads",
                integration_id="account",
                label="Threads",
                enabled=True,
            )
        )
        posting_slots.replace_slots("ws", [{"time": "12:00"}], session=session)
        for i, (asset_id, workspace_id) in enumerate(
            (
                ("image-one", "ws"),
                ("image-two", "ws"),
                ("foreign-image", "foreign"),
            )
        ):
            path = tmp_path / f"{asset_id}.png"
            from PIL import Image

            Image.new("RGB", (20, 20), "white").save(path)
            session.add(
                MediaAsset(
                    id=asset_id,
                    workspace_id=workspace_id,
                    title=asset_id,
                    media_kind="image",
                    source_type="test",
                    original_path=str(path),
                    original_sha256=str(i) * 64,
                    mime_type="image/png",
                    size_bytes=path.stat().st_size,
                    created_by="local-admin",
                )
            )
        session.commit()
    monkeypatch.setattr(database, "SessionFactory", factory)
    monkeypatch.setattr(
        "trendrelay_api.config.Settings.publishing_media_root_list",
        [str(tmp_path)],
    )
    yield factory
    engine.dispose()


def call(factory, operation, **arguments):
    with factory() as session:
        return campaigns.invoke(session, "ws", operation, arguments)


def test_every_campaign_gui_route_has_an_explicit_adapter():
    endpoints = {
        route.endpoint
        for module in (campaign_autopilot_api, campaigns_api)
        for route in module.router.routes
    }
    assert endpoints == {campaigns.endpoint(op) for op in campaigns.OPERATIONS.values()}


def test_every_publish_gui_route_has_a_tool_or_a_named_local_only_boundary():
    covered = {campaigns.endpoint(op) for op in campaigns.PUBLISH_OPERATIONS.values()}
    covered |= {
        getattr(publishing_api, name)
        for name in (
            "publishing_integrations",
            "publishing_integrations_all",
            "publishing_account_boards",
            "preview_publishing",
            "submit_publishing",
            "publishing_jobs",
            "get_publishing_job",
        )
    }
    # These manipulate machine credentials/configuration or return raw HTTP
    # media bytes. MCP supplies bounded Library thumbnails instead of a file server.
    local_or_binary = {
        getattr(publishing_api, name)
        for name in (
            "save_credentials",
            "clear_credentials",
            "add_publishing_connection",
            "rename_publishing_connection",
            "remove_publishing_connection",
            "save_media_hosting_credentials",
            "probe_media_hosting",
            "activate_provider",
            "switch_provider",
            "reveal_publishing_credential",
            "preview_publishing_media",
        )
    }
    assert {route.endpoint for route in publishing_api.router.routes} == covered | local_or_binary
    assert not covered & local_or_binary


def test_every_write_refuses_without_confirmation_before_invoking_any_gui_handler(
    factory,
    monkeypatch,
):
    def unexpected(*args, **kwargs):
        pytest.fail("A mutation ran without consent")

    monkeypatch.setattr(campaigns, "endpoint", unexpected)
    for name, operation in campaigns.ALL_OPERATIONS.items():
        if operation.mutation or operation.external:
            with pytest.raises(PermissionError, match="confirm_external_action"):
                call(factory, name)


def test_confirmed_gui_create_then_pin_and_release(factory):
    result = call(
        factory,
        "campaign_add_queue_item",
        campaign_id="camp-ws",
        body={"body": "A story", "text_only": True},
        confirm_external_action=True,
    )
    item = result["item"]
    assert item["state"] == "approved"  # matches the GUI; authority still decides delivery
    assert item["pinned_slot"] is None
    tomorrow = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    pinned = call(
        factory,
        "campaign_assign_queue_item_slot",
        campaign_id="camp-ws",
        item_id=item["id"],
        body={"day": tomorrow},
        confirm_external_action=True,
    )
    assert pinned["item"]["pinned_slot"] is not None
    call(
        factory,
        "campaign_assign_queue_item_slot",
        campaign_id="camp-ws",
        item_id=item["id"],
        body={"release": True},
        confirm_external_action=True,
    )
    with factory() as session:
        assert session.get(CampaignQueueItem, item["id"]).pinned_slot is None


def test_gui_update_state_and_explicit_null_fields_are_preserved(factory):
    item = call(
        factory,
        "campaign_add_queue_item",
        campaign_id="camp-ws",
        body={"body": "A story", "text_only": True, "first_comment": "Before"},
        confirm_external_action=True,
    )["item"]
    changed = call(
        factory,
        "campaign_update_queue_item",
        campaign_id="camp-ws",
        item_id=item["id"],
        body={"state": "draft", "first_comment": None},
        confirm_external_action=True,
    )["item"]
    assert changed["state"] == "draft" and changed["first_comment"] is None
    assert changed["body"] == "A story"


def test_campaign_create_and_complete_media_edit_accept_ordered_library_ids(factory):
    item = call(
        factory,
        "campaign_add_queue_item",
        campaign_id="camp-ws",
        body={"body": "Two scenes"},
        asset_ids=["image-two", "image-one"],
        confirm_external_action=True,
    )["item"]
    assert item["asset_id"] == "image-two"
    assert [p.replace("\\", "/").rsplit("/", 1)[-1] for p in item["image_paths"]] == [
        "image-two.png",
        "image-one.png",
    ]
    changed = call(
        factory,
        "campaign_update_queue_item",
        campaign_id="camp-ws",
        item_id=item["id"],
        body={},
        asset_ids=["image-one"],
        confirm_external_action=True,
    )["item"]
    assert changed["asset_id"] == "image-one" and len(changed["image_paths"]) == 1


def test_library_ids_cannot_select_foreign_workspace_media(factory):
    with pytest.raises(LookupError):
        call(
            factory,
            "campaign_add_queue_item",
            campaign_id="camp-ws",
            body={"body": "Words"},
            asset_ids=["foreign-image"],
            confirm_external_action=True,
        )
    with factory() as session:
        assert session.scalar(select(CampaignQueueItem)) is None


def test_publish_library_selection_uses_the_same_latest_render_as_library_gui(factory):
    from trendrelay_api.integrations.mcp.publishing import composer_request

    with factory() as session:
        asset = session.get(MediaAsset, "image-one")
        edited_path = asset.original_path.replace("image-one.png", "edited.png")
        session.add(
            MediaAssetVersion(
                id="edited",
                workspace_id="ws",
                asset_id=asset.id,
                version_kind="edited",
                path=edited_path,
                sha256="e" * 64,
                mime_type="image/png",
                size_bytes=20,
            )
        )
        session.commit()
        body = composer_request(
            "ws",
            {
                "caption": "Scenes",
                "date": "2030-01-01T12:00:00Z",
                "targets": [{"platform": "threads", "integration_id": "account"}],
                "asset_ids": ["image-one", "image-two"],
            },
            session=session,
        )
        assert body.image_paths == [edited_path, session.get(MediaAsset, "image-two").original_path]
        assert body.asset_id == "image-one"


def test_publish_rejects_ambiguous_library_and_raw_path_selection(factory):
    from trendrelay_api.integrations.mcp.publishing import composer_request

    with factory() as session, pytest.raises(ValueError, match="not both"):
        composer_request(
            "ws",
            {
                "caption": "Scene",
                "date": "2030-01-01T12:00:00Z",
                "targets": [{"platform": "threads", "integration_id": "account"}],
                "asset_ids": ["image-one"],
                "video_path": "other.mp4",
            },
            session=session,
        )


@pytest.mark.parametrize(
    "name,args",
    [
        ("campaign_read_autopilot", {}),
        ("campaign_add_queue_item", {"body": {"body": "Words", "text_only": True}}),
        ("campaign_preview_autopilot", {}),
    ],
)
def test_campaign_reads_and_writes_cannot_cross_workspace(factory, name, args):
    with pytest.raises(HTTPException) as error:
        call(factory, name, campaign_id="camp-foreign", confirm_external_action=True, **args)
    assert error.value.status_code == 404


def test_invalid_query_bounds_are_enforced_for_direct_and_wire_callers(factory):
    with pytest.raises(ValueError):
        call(factory, "campaign_list_autopilot_executions", campaign_id="camp-ws", limit=999)


def test_confirmation_does_not_override_gui_editor_role(factory):
    with factory() as session:
        session.get(WorkspaceMember, "member-ws").role = "analyst"
        session.commit()
    with pytest.raises(HTTPException) as error:
        call(
            factory,
            "campaign_add_queue_item",
            campaign_id="camp-ws",
            body={"body": "Words", "text_only": True},
            confirm_external_action=True,
        )
    assert error.value.status_code == 403
    with factory() as session:
        assert session.scalar(select(CampaignQueueItem)) is None


def test_gui_handler_failure_rolls_back_the_entire_mutation(factory, monkeypatch):
    def fail_after_write(**kwargs):
        kwargs["session"].get(Campaign, "camp-ws").name = "Uncommitted"
        raise ValueError("failed after writing")

    monkeypatch.setattr(campaign_autopilot_api, "remove_queue_item", fail_after_write)
    # A real handler signature remains the source of the schema.
    import inspect

    fail_after_write.__signature__ = inspect.signature(campaign_autopilot_api.update_queue_item)
    fail_after_write.__annotations__ = get_type_hints(
        campaign_autopilot_api.update_queue_item,
        include_extras=True,
    )
    fail_after_write.__module__ = campaign_autopilot_api.__name__
    with pytest.raises(ValueError, match="failed after writing"):
        call(
            factory,
            "campaign_remove_queue_item",
            campaign_id="camp-ws",
            item_id="q",
            body={},
            confirm_external_action=True,
        )
    with factory() as session:
        assert session.get(Campaign, "camp-ws").name == "ws"


def test_real_streamable_client_lists_context_creates_and_pins_without_provider_send(
    factory,
    monkeypatch,
):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from trendrelay_api.integrations import publishing

    delivered = []
    monkeypatch.setattr(publishing, "JOB_SESSION_FACTORY", factory)
    monkeypatch.setattr(
        publishing,
        "preview_publish",
        lambda request, **kwargs: {
            "provider": "buffer",
            "operation_id": "wire-proof",
        },
    )
    monkeypatch.setattr(
        publishing,
        "_execute_publish",
        lambda request, **kwargs: delivered.append(request) or {"sent": True},
    )

    async def exercise():
        built = server.build_server("ws")
        built.settings.json_response = True
        app = built.streamable_http_app()
        async with built.session_manager.run():  # noqa: SIM117 - transport lifetime is explicit
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                async with streamable_http_client(
                    "http://127.0.0.1:8765/mcp",
                    http_client=client,
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        names = {t.name for t in (await session.list_tools()).tools}
                        assert set(campaigns.OPERATIONS) <= names
                        context = await session.call_tool(
                            "get_campaign_config",
                            {
                                "campaign_id": "camp-ws",
                            },
                        )
                        assert not context.isError
                        refused = await session.call_tool(
                            "campaign_add_queue_item",
                            {
                                "campaign_id": "camp-ws",
                                "body": {"body": "Words", "text_only": True},
                            },
                        )
                        assert refused.isError
                        created = await session.call_tool(
                            "campaign_add_queue_item",
                            {
                                "campaign_id": "camp-ws",
                                "body": {"body": "Words", "text_only": True},
                                "confirm_external_action": True,
                            },
                        )
                        assert not created.isError, created
                        item = created.structuredContent["item"]
                        assert item["pinned_slot"] is None
                        tomorrow = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
                        pinned = await session.call_tool(
                            "pin_post_slot",
                            {
                                "item_id": item["id"],
                                "day": tomorrow,
                                "time": "12:00",
                            },
                        )
                        assert not pinned.isError, pinned
                        media_post = await session.call_tool(
                            "create_campaign_post",
                            {
                                "campaign_id": "camp-ws",
                                "asset_ids": ["image-two", "image-one"],
                                "caption": "A draft from Library media",
                            },
                        )
                        assert not media_post.isError, media_post
                        assert media_post.structuredContent["state"] == "draft"
                        draft_id = media_post.structuredContent["id"]
                        assert media_post.structuredContent["pinned_slot"] is None
                        draft_pin = await session.call_tool(
                            "pin_post_slot",
                            {
                                "item_id": draft_id,
                                "day": (datetime.now(UTC) + timedelta(days=3)).date().isoformat(),
                            },
                        )
                        assert not draft_pin.isError, draft_pin
                        assert draft_pin.structuredContent["state"] == "draft"
                        payload = {
                            "caption": "Standalone",
                            "date": "2030-01-01T12:00:00Z",
                            "delivery": "now",
                            "asset_ids": ["image-one", "image-two"],
                            "targets": [
                                {
                                    "platform": "facebook",
                                    "integration_id": "account",
                                    "post_type": "post",
                                }
                            ],
                        }
                        denied = await session.call_tool("submit_publishing", {"request": payload})
                        assert denied.isError and delivered == []
                        sent = await session.call_tool(
                            "submit_publishing",
                            {
                                "request": payload,
                                "confirm_external_action": True,
                            },
                        )
                        assert not sent.isError, sent
                        assert len(delivered) == 1
                        job = sent.structuredContent["job"]
                        assert job["status"] == "succeeded"
                        reloaded = await session.call_tool(
                            "get_publishing_job", {"job_id": job["id"]}
                        )
                        assert not reloaded.isError
                        assert reloaded.structuredContent["job"]["id"] == job["id"]

    asyncio.run(exercise())
