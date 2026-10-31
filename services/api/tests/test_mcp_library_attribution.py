"""Library and Attribution GUI parity in isolated workspaces, with no provider calls."""

import asyncio
import inspect

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from trendrelay_api import media_library_api
from trendrelay_api.integrations.mcp import campaigns, library_attribution, server
from trendrelay_api.models import WorkspaceMember
from trendrelay_api.opportunity_models import Product
from trendrelay_api.product_creative_models import ProductCreativeLink

pytest_plugins = ["test_mcp_campaign_gui"]


def call(factory, name, **arguments):
    with factory() as session:
        return campaigns.invoke(session, "ws", name, arguments)


@pytest.mark.parametrize(
    "module_name", sorted({row[0] for row in library_attribution.SURFACES.values()})
)
def test_each_gui_route_is_exposed_or_has_an_explicit_boundary(module_name):
    from importlib import import_module

    module = import_module("trendrelay_api." + module_name)
    short = module.__name__.rsplit(".", 1)[-1]
    router = getattr(module, "workspace_router", None) or module.router
    routes = {r.endpoint.__name__ for r in router.routes}
    operations = {op.endpoint for op in campaigns.ALL_OPERATIONS.values() if op.module == short}
    excluded = (
        library_attribution.LOCAL_ONLY[short] | library_attribution.BINARY_OR_MULTIPART[short]
    )
    assert operations.isdisjoint(excluded)
    assert routes == operations | excluded


def test_all_writes_and_provider_reads_require_top_level_consent(factory):
    for name, op in {**campaigns.LIBRARY_OPERATIONS, **campaigns.ATTRIBUTION_OPERATIONS}.items():
        if op.mutation or op.external:
            with pytest.raises(PermissionError, match="confirm_external_action"):
                call(factory, name, body={"confirm_external_action": True})


def test_full_library_filter_sort_and_detail_use_gui_values(factory):
    page = call(factory, "library_list_assets", media_kind="image", sort="title", limit=1)
    assert page["total"] == 2
    assert len(page["assets"]) == 1
    assert page["assets"][0]["id"] == "image-one"
    second = call(
        factory, "library_list_assets", media_kind="image", sort="title", limit=1, offset=1
    )
    assert second["assets"][0]["id"] == "image-two"
    detail = call(factory, "library_get_asset", asset_id="image-one")["asset"]
    assert detail["title"] == "image-one"
    assert detail["original_sha256"] == "0" * 64
    with pytest.raises(ValueError):
        call(factory, "library_list_assets", limit=101)


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("library_get_asset", {"asset_id": "foreign-image"}),
        ("library_get_recipe", {"asset_id": "foreign-image"}),
        ("library_enrich_asset", {"asset_id": "foreign-image", "body": {"speech_text": "Private"}}),
        ("attribution_read_product_listing", {"product_id": "foreign-product"}),
        ("creative_read_creative_draft", {"draft_id": "missing-draft"}),
    ],
)
def test_detail_and_mutation_refuse_foreign_or_missing_ids(factory, name, arguments):
    with pytest.raises(HTTPException) as error:
        call(factory, name, confirm_external_action=True, **arguments)
    assert error.value.status_code == 404


def test_enrichment_and_recipe_survive_reread(factory):
    call(
        factory,
        "library_enrich_asset",
        asset_id="image-one",
        body={"language": "vi", "speech_text": "Lời đã duyệt"},
        confirm_external_action=True,
    )
    detail = call(factory, "library_get_asset", asset_id="image-one")["asset"]
    assert "Lời đã duyệt" in str(detail)
    call(
        factory,
        "library_save_recipe",
        asset_id="image-one",
        body={"steps": []},
        confirm_external_action=True,
    )
    assert call(factory, "library_get_recipe", asset_id="image-one")["steps"] == []


def test_product_media_attachment_and_detachment_use_workspace_ids(factory):
    with factory() as session:
        session.add(
            Product(
                id="product",
                workspace_id="ws",
                catalog_key="test",
                name="Set",
                marketplace="shopee",
                created_by="local-admin",
            )
        )
        session.commit()
    call(
        factory,
        "creative_attach_media_products",
        body={
            "asset_ids": ["image-one", "image-two"],
            "product_ids": ["product"],
        },
        confirm_external_action=True,
    )
    with factory() as session:
        assert len(session.scalars(select(ProductCreativeLink)).all()) == 2
    call(
        factory,
        "creative_detach_media_product",
        asset_id="image-one",
        product_id="product",
        confirm_external_action=True,
    )
    with factory() as session:
        assert len(session.scalars(select(ProductCreativeLink)).all()) == 1


def test_editor_permission_is_not_bypassed_by_confirmation(factory):
    with factory() as session:
        session.get(WorkspaceMember, "member-ws").role = "analyst"
        session.commit()
    with pytest.raises(HTTPException) as error:
        call(
            factory,
            "creative_attach_media_products",
            body={
                "asset_ids": ["image-one"],
                "product_ids": ["product"],
            },
            confirm_external_action=True,
        )
    assert error.value.status_code == 403


def test_tracking_creation_and_status_share_gui_validation_and_persist(factory):
    from trendrelay_api.models import Campaign

    with factory() as session:
        session.get(Campaign, "camp-ws").affiliate_url = "https://example.test/product"
        session.commit()
    created = call(
        factory,
        "attribution_create_tracking_link",
        body={
            "campaign_id": "camp-ws",
            "platform": "tiktok",
        },
        confirm_external_action=True,
    )
    links = call(factory, "attribution_list_tracking_links")
    assert len(links["links"]) == 1
    link_id = links["links"][0]["id"]
    assert link_id in str(created)
    call(
        factory,
        "attribution_update_tracking_link_status",
        link_id=link_id,
        body={"status": "disabled"},
        confirm_external_action=True,
    )
    assert call(factory, "attribution_list_tracking_links")["links"][0]["status"] == "disabled"


def test_foreign_product_link_selection_rolls_back_all_pairs(factory):
    with factory() as session:
        session.add(
            Product(
                id="foreign-product",
                workspace_id="foreign",
                catalog_key="outside",
                name="Private",
                marketplace="shopee",
                created_by="local-admin",
            )
        )
        session.commit()
    with pytest.raises(HTTPException) as error:
        call(
            factory,
            "creative_attach_media_products",
            body={
                "asset_ids": ["image-one"],
                "product_ids": ["foreign-product"],
            },
            confirm_external_action=True,
        )
    assert error.value.status_code == 404
    with factory() as session:
        assert session.scalar(select(ProductCreativeLink)) is None


def test_raw_dictionary_confirmation_is_normalized_before_gui_handler(factory, monkeypatch):
    original = media_library_api.submit_render
    seen = []

    def fake(**kwargs):
        seen.append(kwargs["body"])
        return {"job": "queued"}

    fake.__signature__ = inspect.signature(original)
    from typing import get_type_hints

    fake.__annotations__ = get_type_hints(original, include_extras=True)
    monkeypatch.setattr(media_library_api, "submit_render", fake)
    call(
        factory,
        "library_submit_render",
        body={"confirm_external_action": False},
        confirm_external_action=True,
    )
    assert seen == [{"confirm_external_action": True}]


def test_autocut_confirm_field_is_derived_from_top_level_consent(factory, monkeypatch):
    from typing import get_type_hints

    from trendrelay_api import autocut_api

    original = autocut_api.start_render
    seen = []

    def fake(**kwargs):
        seen.append(kwargs["body"].confirm)
        return {"queued": True}

    fake.__signature__ = inspect.signature(original)
    fake.__annotations__ = get_type_hints(original, include_extras=True)
    monkeypatch.setattr(autocut_api, "start_render", fake)
    call(
        factory,
        "autocut_start_render",
        body={"asset_ids": ["image-one"]},
        confirm_external_action=True,
    )
    assert seen == [True]


def test_background_work_runs_after_committed_gui_write(factory, monkeypatch):
    original = media_library_api.run_bulk_action
    seen = []
    from trendrelay_api.models import Campaign

    def fake(**kwargs):
        kwargs["session"].get(Campaign, "camp-ws").name = "Changed"

        def background():
            with factory() as session:
                seen.append(session.get(Campaign, "camp-ws").name)

        kwargs["background_tasks"].add_task(background)
        return {"queued": 1}

    fake.__signature__ = inspect.signature(original)
    from typing import get_type_hints

    fake.__annotations__ = get_type_hints(original, include_extras=True)
    monkeypatch.setattr(media_library_api, "run_bulk_action", fake)
    call(
        factory,
        "library_run_bulk_action",
        body={"action": "unused", "asset_ids": ["image-one"]},
        confirm_external_action=True,
    )
    assert seen == ["Changed"]


def test_streamable_client_browses_library_and_attribution_and_updates_recipe(factory):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def exercise():
        built = server.build_server("ws")
        built.settings.json_response = True
        app = built.streamable_http_app()
        async with built.session_manager.run():  # noqa: SIM117
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                async with streamable_http_client(
                    "http://127.0.0.1:8765/mcp", http_client=http
                ) as (r, w, _):
                    async with ClientSession(r, w) as client:
                        await client.initialize()
                        page = await client.call_tool(
                            "library_list_assets", {"limit": 1, "media_kind": "image"}
                        )
                        assert not page.isError, page
                        assert page.structuredContent["total"] == 2
                        summary = await client.call_tool("attribution_attribution_summary", {})
                        assert not summary.isError, summary
                        denied = await client.call_tool(
                            "library_save_recipe",
                            {
                                "asset_id": "image-one",
                                "body": {"steps": []},
                            },
                        )
                        assert denied.isError
                        saved = await client.call_tool(
                            "library_save_recipe",
                            {
                                "asset_id": "image-one",
                                "body": {"steps": []},
                                "confirm_external_action": True,
                            },
                        )
                        assert not saved.isError, saved

    asyncio.run(exercise())
