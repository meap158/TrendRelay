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


@pytest.fixture
def original_version(factory):
    from trendrelay_api.media_models import MediaAsset, MediaAssetVersion

    with factory() as session:
        asset = session.get(MediaAsset, "image-one")
        session.add(MediaAssetVersion(
            id="original-one", workspace_id="ws", asset_id=asset.id,
            version_kind="original", path=asset.original_path,
            sha256=asset.original_sha256, mime_type="image/png", size_bytes=asset.size_bytes,
        ))
        session.commit()


def test_streamable_client_browses_library_and_attribution_and_updates_recipe(
    factory, original_version, monkeypatch,
):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from starlette.responses import Response

    monkeypatch.setattr(media_library_api, "face_overlay_sprite", lambda *args: Response(
        b"png", media_type="image/png",
    ))

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
                        resource = await client.call_tool(
                            "read_library_media", {"asset_id": "image-one", "version": "original"}
                        )
                        assert not resource.isError, resource
                        assert resource.content[0].type == "resource"
                        assert resource.content[0].resource.mimeType == "image/png"
                        sprite = await client.call_tool(
                            "read_preview_artifact", {"kind": "overlay", "identifier": "test"}
                        )
                        assert not sprite.isError, sprite
                        assert sprite.content[0].type == "image"
                        assert sprite.content[0].mimeType == "image/png"
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


def test_media_transfer_scope_and_size(factory, original_version, monkeypatch):
    from trendrelay_api.integrations.mcp import media_transfer

    with factory() as session:
        with pytest.raises(HTTPException) as error:
            media_transfer.read_library_media(session, "ws", "foreign-image", "original")
        assert error.value.status_code == 404
        monkeypatch.setattr(media_transfer, "MAX_TRANSFER_BYTES", 1)
        with pytest.raises(ValueError, match="transfer limit"):
            media_transfer.read_library_media(session, "ws", "image-one", "original")


def test_caption_transfer_preserves_gui_path_guards(factory, tmp_path, monkeypatch):
    import base64

    from trendrelay_api import caption_jobs
    from trendrelay_api.integrations.mcp import media_transfer

    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path)
    root = tmp_path / "ws"
    root.mkdir()
    caption = root / "image-one.test.srt"
    caption.write_bytes(b"caption")
    with factory() as session:
        result = media_transfer.read_library_caption(session, "ws", "image-one", str(caption))
        assert base64.b64decode(result.resource.blob) == b"caption"
        for path in (tmp_path / "outside.srt", root / "image-two.test.srt"):
            with pytest.raises(HTTPException) as error:
                media_transfer.read_library_caption(session, "ws", "image-one", str(path))
            assert error.value.status_code == 403


def test_transfer_provider_actions_require_confirmation(factory):
    from trendrelay_api.integrations.mcp import media_transfer

    with factory() as session:
        with pytest.raises(PermissionError, match="confirm_external_action"):
            media_transfer.preview_library_effect(session, "ws", "image-one", [])
        with pytest.raises(PermissionError, match="confirm_external_action"):
            media_transfer.export_shopee_workbook(session, "ws")


def test_effect_transfer_returns_native_image_without_provider(factory, monkeypatch):
    import base64

    from starlette.responses import Response

    from trendrelay_api.integrations.mcp import media_transfer

    def preview(workspace_id, body, actor, session):
        assert workspace_id == "ws"
        assert body.source_path.endswith("image-one.png")
        assert body.at == 0.5
        return Response(b"jpeg", media_type="image/jpeg", headers={"X-Preview-Note": "test"})

    monkeypatch.setattr(media_library_api, "effect_preview_frame", preview)
    with factory() as session:
        result = media_transfer.preview_library_effect(
            session, "ws", "image-one", [], 0.5, confirmed=True,
        )
        assert result[1].type == "image"
        assert base64.b64decode(result[1].data) == b"jpeg"
        with pytest.raises(HTTPException):
            media_transfer.preview_library_effect(
                session, "ws", "foreign-image", [], confirmed=True,
            )


def test_workbook_transfer_uses_confirmed_gui_export(factory, monkeypatch):
    import base64

    from starlette.responses import Response

    from trendrelay_api import attribution_api
    from trendrelay_api.integrations.mcp import media_transfer

    def export(workspace_id, body, request, actor, session):
        assert workspace_id == "ws"
        assert body.confirm_external_action is True
        assert body.limit == 7
        return Response(
            b"xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    monkeypatch.setattr(attribution_api, "export_shopee_offers", export)
    with factory() as session:
        result = media_transfer.export_shopee_workbook(session, "ws", 7, confirmed=True)
        assert base64.b64decode(result.resource.blob) == b"xlsx"


def test_archive_transfer_real_zip_scope_and_limit(factory, tmp_path, monkeypatch):
    import base64
    import io
    import zipfile

    from trendrelay_api import media_archive
    from trendrelay_api.integrations.mcp import media_transfer

    path = tmp_path / "entry.txt"
    path.write_bytes(b"selection")
    token = media_archive.issue("ws", [media_archive.ArchiveEntry(path, "entry.txt")], "test.zip")
    with factory() as session:
        resource = media_transfer.read_library_archive(session, "ws", token)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(resource.resource.blob))) as archive:
            assert archive.read("entry.txt") == b"selection"
        with pytest.raises(HTTPException) as error:
            media_transfer.read_library_archive(session, "foreign", token)
        assert error.value.status_code == 404
        monkeypatch.setattr(media_transfer, "MAX_TRANSFER_BYTES", 2)
        with pytest.raises(ValueError, match="MCP limit"):
            media_transfer.read_library_archive(session, "ws", token)
        with pytest.raises(HTTPException):
            media_transfer.read_library_archive(session, "ws", "expired-or-invalid")


def test_archive_single_file_expiry_and_membership(factory, tmp_path, monkeypatch):
    import base64
    from dataclasses import replace

    from trendrelay_api import media_archive
    from trendrelay_api.integrations.mcp import media_transfer

    path = tmp_path / "single.png"
    path.write_bytes(b"png")
    token = media_archive.issue(
        "ws", [media_archive.ArchiveEntry(path, "single.png")], "single.png", "image/png",
    )
    with factory() as session:
        result = media_transfer.read_library_archive(session, "ws", token)
        assert base64.b64decode(result.resource.blob) == b"png"
        with pytest.raises(HTTPException):
            media_transfer.read_library_archive(session, "no-membership", token)
        monkeypatch.setitem(media_archive._tickets, token, replace(
            media_archive._tickets[token], expires_at=0,
        ))
        with pytest.raises(HTTPException) as error:
            media_transfer.read_library_archive(session, "ws", token)
        assert error.value.status_code == 404


@pytest.mark.parametrize("kind", ["autocut", "storytelling"])
def test_creation_preview_uses_real_job_scope_guard(factory, monkeypatch, kind, tmp_path):
    from trendrelay_api import autocut_api, storytelling_api
    from trendrelay_api.integrations.mcp import media_transfer

    path = tmp_path / "preview.mp4"
    path.write_bytes(b"video")
    if kind == "autocut":
        monkeypatch.setattr(autocut_api, "get_job_record", lambda *a, **kw: {
            "workspace_id": "foreign", "kind": autocut_api.autocut_jobs.JOB_KIND,
            "payload": {"preview": True}, "result": {"output_path": str(path)},
        })
    else:
        def refused(*args):
            raise HTTPException(404, "Not found")
        monkeypatch.setattr(storytelling_api, "_record", refused)
    with factory() as session:
        with pytest.raises(HTTPException) as error:
            media_transfer.read_preview_artifact(session, "ws", kind, "foreign-job")
        assert error.value.status_code == 404


@pytest.mark.parametrize("kind", ["portrait", "blurred", "autocut", "storytelling", "overlay"])
def test_artifact_transfer_delegates_and_returns_native_content(
    factory, monkeypatch, tmp_path, kind,
):
    import base64

    from starlette.responses import FileResponse, Response

    from trendrelay_api import autocut_api, storytelling_api
    from trendrelay_api.integrations.mcp import media_transfer

    path = tmp_path / "result.png"
    path.write_bytes(b"content")
    module, endpoint = {
        "portrait": (media_library_api, "face_swap_face_thumbnail"),
        "blurred": (media_library_api, "face_blur_media"),
        "overlay": (media_library_api, "face_overlay_sprite"),
        "autocut": (autocut_api, "stream_preview"),
        "storytelling": (storytelling_api, "stream_preview"),
    }[kind]

    def response(workspace_id, identifier, actor, session, *args):
        assert workspace_id == "ws"
        assert identifier == "chosen"
        if kind == "overlay":
            assert args == (64,)
            return Response(b"content", media_type="image/png")
        return FileResponse(path, media_type="image/png")

    monkeypatch.setattr(module, endpoint, response)
    with factory() as session:
        result = media_transfer.read_preview_artifact(session, "ws", kind, "chosen", 64)
        data = result.data if kind == "overlay" else result.resource.blob
        assert base64.b64decode(data) == b"content"
        if kind in {"autocut", "storytelling"}:
            assert result.resource.mimeType == "video/mp4"


def test_blur_preview_confirmation_and_scope(factory, monkeypatch):
    from starlette.responses import Response

    from trendrelay_api.integrations.mcp import media_transfer

    def preview(workspace_id, actor, session, path, padding, confidence, at):
        assert path.endswith("image-one.png")
        return Response(b"jpeg", media_type="image/jpeg", headers={"X-Faces-Found": "2"})

    monkeypatch.setattr(media_library_api, "face_blur_frame", preview)
    with factory() as session:
        with pytest.raises(PermissionError):
            media_transfer.preview_library_blur(session, "ws", "image-one")
        with pytest.raises(HTTPException):
            media_transfer.preview_library_blur(session, "ws", "foreign-image", confirmed=True)
        result = media_transfer.preview_library_blur(session, "ws", "image-one", confirmed=True)
        assert "Faces=2" in result[0].text
        assert result[1].type == "image"


def test_effect_preview_read_preserves_file_and_refuses_foreign_job(factory, monkeypatch, tmp_path):
    from trendrelay_api.integrations import effect_render
    from trendrelay_api.integrations.mcp import media_transfer

    root = tmp_path / "ws"
    root.mkdir()
    output = root / "preview.mp4"
    output.write_bytes(b"video")
    job = {"workspace_id": "ws", "status": "succeeded",
           "payload": {"request": {"preview_seconds": 3}}, "result": {"output": str(output)}}
    monkeypatch.setattr(effect_render, "RENDER_ROOT", tmp_path)
    monkeypatch.setattr(effect_render, "get_render_job", lambda _: job)
    with factory() as session:
        result = media_transfer.read_preview_artifact(session, "ws", "effect", "job")
        assert result.resource.mimeType == "video/mp4"
        assert output.is_file()
        job["workspace_id"] = "foreign"
        with pytest.raises(HTTPException) as error:
            media_transfer.read_preview_artifact(session, "ws", "effect", "job")
        assert error.value.status_code == 404
        job["workspace_id"] = "ws"
        job["result"]["output"] = str(tmp_path / "outside.mp4")
        with pytest.raises(HTTPException):
            media_transfer.read_preview_artifact(session, "ws", "effect", "job")
