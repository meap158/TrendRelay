"""The HTTP surface for carousel slides (PM-209).

What the Library's Carousel tool asks: the styles, sizes and limits (so the interface shows the same
ones the server enforces) and a preview, which renders the script into slides and sends them back as
images. Slides are drawn by the pinned FFmpeg in `carousel_slides`; nothing is stored, so a preview
costs nothing to throw away. Reads need membership.
"""

from __future__ import annotations

import base64
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from trendrelay_api import carousel_slides as slides
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import membership, require_role

router = APIRouter(prefix="/api/workspaces/{workspace_id}/carousels", tags=["carousels"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
EDITORS = {"owner", "editor", "approver"}


class SaveBody(BaseModel):
    script: str = Field(min_length=1, max_length=slides.MAX_SLIDES * (slides.MAX_SLIDE_CHARS + 2))
    style: str = Field(default=slides.DEFAULT_STYLE, max_length=20)
    size: str = Field(default=slides.DEFAULT_SIZE, max_length=20)
    title: str = Field(default="Carousel", min_length=1, max_length=120)


class PreviewBody(BaseModel):
    #: Slides are separated by blank lines. The longest script `split_slides` accepts is
    #: MAX_SLIDES blocks of MAX_SLIDE_CHARS characters, so this is that limit plus the gaps.
    script: str = Field(min_length=1, max_length=slides.MAX_SLIDES * (slides.MAX_SLIDE_CHARS + 2))
    style: str = Field(default=slides.DEFAULT_STYLE, max_length=20)
    size: str = Field(default=slides.DEFAULT_SIZE, max_length=20)


@router.get("/rules")
def rules(workspace_id: str, user: AuthenticatedUser, session: DatabaseSession) -> dict[str, Any]:
    """The styles, sizes and limits, so the interface never disagrees with the server."""
    membership(session, workspace_id, user.id)
    return {
        "styles": [
            {"id": name, "background": look.background, "text": look.text}
            for name, look in slides.LOOKS.items()
        ],
        "default_style": slides.DEFAULT_STYLE,
        "sizes": {name: {"width": w, "height": h} for name, (w, h) in slides.SIZES.items()},
        "default_size": slides.DEFAULT_SIZE,
        "max_slides": slides.MAX_SLIDES,
        "max_slide_chars": slides.MAX_SLIDE_CHARS,
    }


@router.post("/preview")
def preview(
    workspace_id: str, body: PreviewBody, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Render the script in a style and size, and return each slide as a PNG data URL."""
    membership(session, workspace_id, user.id)
    try:
        with tempfile.TemporaryDirectory(prefix="trendrelay-carousel-") as scratch:
            paths = slides.render_slides(body.script, Path(scratch), body.style, body.size)
            rendered = [
                {
                    "index": index + 1,
                    "text": text,
                    "image": "data:image/png;base64,"
                    + base64.b64encode(path.read_bytes()).decode(),
                }
                for index, (path, text) in enumerate(
                    zip(paths, slides.split_slides(body.script), strict=True)
                )
            ]
    except slides.CarouselError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    width, height = slides.SIZES[body.size]
    return {
        "style": body.style,
        "size": body.size,
        "width": width,
        "height": height,
        "slides": rendered,
    }


@router.post("/save", status_code=201)
def save(
    workspace_id: str, body: SaveBody, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Render the slides and file each one in the Library as an image."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    from trendrelay_api.config import get_settings
    from trendrelay_api.media_library import create_ingest_job, run_ingest_job
    from trendrelay_api.tool_registry import PROJECT_ROOT

    roots = get_settings().publishing_media_root_list
    if not roots:
        raise HTTPException(status_code=409, detail="No approved media root is configured.")
    root = Path(roots[0])
    folder = (root if root.is_absolute() else PROJECT_ROOT / root) / "carousels" / uuid.uuid4().hex
    try:
        paths = slides.render_slides(body.script, folder, body.style, body.size)
    except slides.CarouselError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    asset_ids: list[str] = []
    for number, path in enumerate(paths, start=1):
        ingest = create_ingest_job(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            path=str(path),
            title=f"{body.title} {number}/{len(paths)}",
            source_type="carousel",
        )
        asset_id = ingest.get("asset_id")
        if not asset_id and ingest.get("id"):
            done = run_ingest_job(ingest["id"])
            asset_id = (done.get("result") or {}).get("asset_id") or done.get("asset_id")
        if not asset_id:
            raise HTTPException(status_code=500, detail=f"Slide {number} could not be filed.")
        asset_ids.append(asset_id)
    return {"asset_ids": asset_ids, "slides": len(asset_ids)}
