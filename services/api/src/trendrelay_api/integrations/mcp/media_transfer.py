"""Bounded MCP media/file content through the GUI's workspace-scoped readers."""

import base64
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from sqlalchemy.orm import Session

from .access import confirm_external_action, require_access

MAX_TRANSFER_BYTES = 16 * 1024 * 1024


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
