"""The AutoCut HTTP surface: templates, a plan preview, and a render.

Three questions the Library tab asks. Which templates exist and which fits
this set best (so the interface can pre-select one). What the cut plan looks
like for a chosen template, track and speed (so it can be previewed and
adjusted before committing to a render). And "make it" - which queues the
render and hands back the job to watch in the bell.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autocut import jobs as autocut_jobs
from trendrelay_api.autocut import templates as autocut_templates
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.media_models import MediaAsset

router = APIRouter(prefix="/api/workspaces/{workspace_id}/autocut", tags=["autocut"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]

#: A short-form montage is a handful to a few dozen pictures; beyond this it is
#: a different kind of video and the templates stop describing it.
MAX_PICTURES = 40


def _template_view(template: autocut_templates.Template, score: float | None = None) -> dict[str, Any]:
    view = {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "mood": template.mood,
        "transition": template.transition,
        "music": template.music,
        "designed_bpm": template.designed_bpm,
        "ideal_pictures": list(template.ideal_pictures),
        "music_available": autocut_jobs.resolve_audio(template.music) is not None,
    }
    if score is not None:
        view["match"] = round(score, 3)
    return view


@router.get("/templates")
def list_templates(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    pictures: int = 0,
) -> dict[str, Any]:
    """Every template, ranked for this many pictures when a count is given."""
    membership(session, workspace_id, user.id)
    if pictures > 0:
        ranked = autocut_templates.rank_templates(pictures)
        return {
            "templates": [_template_view(t, score) for t, score in ranked],
            "recommended": ranked[0][0].id,
        }
    return {"templates": [_template_view(t) for t in autocut_templates.TEMPLATES]}


class PlanRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_PICTURES)
    template_id: str | None = None
    music: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


def _known_images(session: Session, workspace_id: str, asset_ids: list[str]) -> list[str]:
    """The asset ids that are this workspace's own images, in the order asked.

    Filtered rather than trusted: an AutoCut cannot cut a video or a clip that
    belongs to another workspace, and saying which survived is clearer than
    failing the whole set for one bad id.
    """
    rows = session.scalars(
        select(MediaAsset.id).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(asset_ids),
            MediaAsset.media_kind == "image",
        )
    ).all()
    allowed = set(rows)
    return [asset_id for asset_id in asset_ids if asset_id in allowed]


@router.post("/plan")
def preview_plan(
    workspace_id: str,
    body: PlanRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The shot list for these pictures, without rendering anything.

    The template defaults to the best fit for the count, so a caller can send
    only pictures and get a sensible plan back; naming one overrides it.
    """
    membership(session, workspace_id, user.id)
    images = _known_images(session, workspace_id, body.asset_ids)
    if not images:
        raise HTTPException(status_code=422, detail="None of those are this workspace's images.")
    template_id = body.template_id or autocut_templates.best_template(len(images)).id
    try:
        plan, grid, template, audio = autocut_jobs.build_plan(
            template_id, images, music=body.music, speed=body.speed,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "template": _template_view(template, autocut_templates.match_score(template, len(images))),
        "plan": autocut_jobs._plan_json(plan),
        "bpm": grid.bpm,
        "beat_synced": plan.beat_synced,
        "music_available": audio is not None,
        "picture_count": len(images),
    }


class RenderRequestBody(PlanRequest):
    title: str | None = Field(default=None, max_length=200)
    confirm: bool = False


@router.post("/render", status_code=202)
def start_render(
    workspace_id: str,
    body: RenderRequestBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue the render. Draws in the background, lands in the Library."""
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    images = _known_images(session, workspace_id, body.asset_ids)
    if not images:
        raise HTTPException(status_code=422, detail="None of those are this workspace's images.")
    template_id = body.template_id or autocut_templates.best_template(len(images)).id
    try:
        queued = autocut_jobs.enqueue_render(
            workspace_id, user.id,
            template_id=template_id,
            asset_ids=images,
            music=body.music,
            speed=body.speed,
            title=body.title,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session, request, workspace_id, user.id,
        "autocut.render_queued", "durable_job", queued["id"],
        {"template": template_id, "pictures": len(images)},
    )
    session.commit()
    return queued
