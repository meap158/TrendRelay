"""Bounded MCP media/file content through the GUI's workspace-scoped readers."""

import base64
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from sqlalchemy.orm import Session

from .access import confirm_external_action, require_access

MAX_TRANSFER_BYTES = 16 * 1024 * 1024


def read_preview_artifact(
    session: Session, workspace_id: str, kind: str, identifier: str, width: int = 192,
):
    from trendrelay_api import autocut_api, storytelling_api
    from trendrelay_api import media_library_api as api

    actor = require_access(session, workspace_id, {"owner", "editor", "approver", "analyst"})
    uri = f"trendrelay://library/previews/{kind}"
    if kind == "overlay":
        from mcp.types import ImageContent

        response = api.face_overlay_sprite(workspace_id, identifier, actor, session, width)
        resource = _resource(response.body, uri, response.media_type)
        return ImageContent(type="image", mimeType=response.media_type, data=resource.resource.blob)
    if kind == "portrait":
        response = api.face_swap_face_thumbnail(workspace_id, identifier, actor, session)
    elif kind == "blurred":
        response = api.face_blur_media(workspace_id, identifier, actor, session)
    elif kind == "autocut":
        response = autocut_api.stream_preview(workspace_id, identifier, actor, session)
        response.media_type = "video/mp4"
    elif kind == "storytelling":
        response = storytelling_api.stream_preview(workspace_id, identifier, actor, session)
        response.media_type = "video/mp4"
    elif kind == "effect":
        from fastapi import HTTPException
        from starlette.responses import FileResponse

        from trendrelay_api.integrations.effect_render import RENDER_ROOT, _mime_of, get_render_job

        try:
            job = get_render_job(identifier)
        except FileNotFoundError as error:
            raise HTTPException(404, "Effect preview not found.") from error
        request = job.get("payload", {}).get("request", {})
        if job["workspace_id"] != workspace_id or not request.get("preview_seconds"):
            raise HTTPException(404, "Effect preview not found.")
        if job["status"] != "succeeded":
            raise HTTPException(409, "The effect preview is not ready.")
        output = Path(job.get("result", {}).get("output") or "").resolve()
        root = (RENDER_ROOT / workspace_id).resolve()
        if not output.is_relative_to(root) or not output.is_file():
            raise HTTPException(404, "The effect preview is unavailable.")
        response = FileResponse(output, media_type=_mime_of(output))
    else:
        raise ValueError("Unknown preview kind.")
    return _file(response, uri)


def read_library_archive(session: Session, workspace_id: str, token: str):
    import anyio
    from starlette.responses import FileResponse

    from trendrelay_api import media_library_api as api

    require_access(session, workspace_id, {"owner", "editor", "approver", "analyst"})
    response = api.download_archive(workspace_id, token)
    uri = "trendrelay://library/downloads/selection"
    if isinstance(response, FileResponse):
        return _file(response, uri)

    async def collect():
        data = bytearray()
        iterator = response.body_iterator
        try:
            async for chunk in iterator:
                if len(data) + len(chunk) > MAX_TRANSFER_BYTES:
                    raise ValueError("This archive exceeds the 16 MiB MCP limit. Use the GUI.")
                data.extend(chunk)
        finally:
            await iterator.aclose()
        return _resource(bytes(data), uri, response.media_type)

    return anyio.run(collect)


def preview_library_blur(
    session: Session, workspace_id: str, asset_id: str, padding_ratio: float = 0.08,
    confidence: float = 0.6, at: float | None = None, *, confirmed: bool = False,
):
    from mcp.types import ImageContent, TextContent

    from trendrelay_api import media_library_api as api

    confirm_external_action(confirmed)
    actor = require_access(session, workspace_id, {"owner", "editor", "approver"}, governed=True)
    asset = api._asset_record(session, workspace_id, asset_id)
    response = api.face_blur_frame(
        workspace_id, actor, session, asset.original_path, padding_ratio, confidence, at,
    )
    resource = _resource(response.body, "trendrelay://library/previews/blur", response.media_type)
    return [
        TextContent(type="text", text=(
            f"Faces={response.headers.get('x-faces-found', '')}; "
            f"position={response.headers.get('x-frame-position', '')}"
        )),
        ImageContent(type="image", mimeType=response.media_type, data=resource.resource.blob),
    ]


def _resource(data: bytes, uri: str, mime: str):
    from mcp.types import BlobResourceContents, EmbeddedResource

    if len(data) > MAX_TRANSFER_BYTES:
        raise ValueError("This file exceeds the 16 MiB MCP transfer limit. Use the GUI download.")
    return EmbeddedResource(type="resource", resource=BlobResourceContents(
        uri=uri, mimeType=mime, blob=base64.b64encode(data).decode("ascii"),
    ))


def _file(response, uri: str):
    path = Path(response.path)
    with path.open("rb") as handle:
        data = handle.read(MAX_TRANSFER_BYTES + 1)
    return _resource(data, uri, response.media_type or "application/octet-stream")


def read_library_media(
    session: Session, workspace_id: str, asset_id: str, version: str = "thumbnail",
):
    from trendrelay_api import media_library_api as api

    actor = require_access(session, workspace_id, {"owner", "editor", "approver", "analyst"})
    if version == "edited":
        response = api.asset_preview_stream(workspace_id, asset_id, actor, session, cut="edited")
    elif version in {"original", "proxy", "thumbnail", "audio"}:
        response = api.asset_content(workspace_id, asset_id, version, actor, session)
    else:
        raise ValueError("Unknown Library media version.")
    return _file(response, f"trendrelay://library/assets/{asset_id}/{version}")


def read_library_caption(
    session: Session, workspace_id: str, asset_id: str, path: str,
):
    from trendrelay_api import media_library_api as api

    actor = require_access(session, workspace_id, {"owner", "editor", "approver", "analyst"})
    response = api.download_caption_file(workspace_id, asset_id, path, actor, session)
    return _file(response, f"trendrelay://library/assets/{asset_id}/captions/{Path(path).name}")


def preview_library_effect(
    session: Session, workspace_id: str, asset_id: str,
    steps: list[dict[str, Any]], at: float | None = None, *, confirmed: bool = False,
):
    from mcp.types import ImageContent, TextContent

    from trendrelay_api import media_library_api as api

    confirm_external_action(confirmed)
    actor = require_access(session, workspace_id, {"owner", "editor", "approver"}, governed=True)
    asset = api._asset_record(session, workspace_id, asset_id)
    response = api.effect_preview_frame(workspace_id, api.EffectPreviewRequest(
        source_path=asset.original_path, steps=steps, at=at,
    ), actor, session)
    if len(response.body) > MAX_TRANSFER_BYTES:
        raise ValueError("This preview exceeds the 16 MiB MCP transfer limit.")
    return [
        TextContent(type="text", text=(
            f"Preview of {asset_id}; position={response.headers.get('x-frame-position', '')}; "
            f"{unquote(response.headers.get('x-preview-note', ''))}"
        )),
        ImageContent(type="image", mimeType=response.media_type or "image/jpeg",
                     data=base64.b64encode(response.body).decode("ascii")),
    ]


def export_shopee_workbook(
    session: Session, workspace_id: str, limit: int = 100, *, confirmed: bool = False,
):
    from trendrelay_api import attribution_api as api

    confirm_external_action(confirmed)
    actor = require_access(session, workspace_id, {"owner", "editor", "approver"}, governed=True)
    response = api.export_shopee_offers(workspace_id, api.ShopeeOfferExport(
        limit=limit, confirm_external_action=True,
    ), None, actor, session)
    result = _resource(response.body, "trendrelay://attribution/exports/shopee-offers.xlsx",
                       response.media_type)
    session.commit()
    return result
