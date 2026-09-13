"""The AutoCut HTTP surface: templates, a plan preview, and a render.

Three questions the Library tab asks. Which templates exist and which fits
this set best (so the interface can pre-select one). What the cut plan looks
like for a chosen template, track and speed (so it can be previewed and
adjusted before committing to a render). And "make it" - which queues the
render and hands back the job to watch in the bell.
"""

from __future__ import annotations

from typing import Annotated, Any

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api import creation_drafts as drafts
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autocut import jobs as autocut_jobs
from trendrelay_api.autocut import templates as autocut_templates
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.jobs import get_job_record
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.media_serving import OPAQUE_MEDIA_TYPE

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
        # The steady cadence in beats-per-cut, so the chooser can animate this
        # template's actual rhythm rather than approximate one from the tempo.
        "cadence": list(template.pattern.loop),
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
    #: A Library track to cut to instead of the template's file - one added
    #: from the music search, say. The beats are read from it, and the credit
    #: it carries goes onto the finished video.
    music_asset_id: str | None = Field(default=None, max_length=64)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    #: The canvas shape - the plan is the same for all, only the render frame
    #: differs, so it rides the render/preview calls, not the plan preview.
    aspect: str = Field(default="portrait", pattern="^(portrait|square|landscape)$")
    #: How off-ratio media meets the canvas: crop to fill, or fit whole over
    #: a blurred copy of itself.
    fill: str = Field(default="cover", pattern="^(cover|blur)$")
    #: A hook line burned over the whole video; empty draws none. Placed top or
    #: bottom. Rides the plan/preview/render calls like the other look choices.
    caption: str = Field(default="", max_length=120)
    caption_position: str = Field(default="bottom", pattern="^(top|bottom)$")


def _known_visuals(
    session: Session, workspace_id: str, asset_ids: list[str]
) -> tuple[list[str], dict[str, str]]:
    """This workspace's images and videos among the ids, in the order asked.

    Returns the surviving ids (order preserved - the operator arranged them)
    and their kinds. Audio is left out: AutoCut cuts visuals to a template's
    music, and a sound file has nothing to show. Filtered rather than trusted,
    so one stray id does not fail the whole set.
    """
    rows = session.execute(
        select(MediaAsset.id, MediaAsset.media_kind).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(asset_ids),
            MediaAsset.media_kind.in_(("image", "video")),
        )
    ).all()
    kinds = {asset_id: kind for asset_id, kind in rows}
    ordered = [asset_id for asset_id in asset_ids if asset_id in kinds]
    return ordered, kinds


def _music(
    session: Session, workspace_id: str, asset_id: str | None,
) -> autocut_jobs.MusicChoice | None:
    """The Library track a request names, or 422 when it is not one of ours.

    Refused rather than quietly falling back to the template's file: somebody
    chose that track, and a plan cut to different music is not the plan they
    asked to see.
    """
    if not asset_id:
        return None
    choice = autocut_jobs.music_from_library(session, workspace_id, asset_id)
    if choice is None:
        raise HTTPException(
            status_code=422, detail="That music is not an audio file in this workspace's Library.",
        )
    return choice


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
    visuals, kinds = _known_visuals(session, workspace_id, body.asset_ids)
    if not visuals:
        raise HTTPException(status_code=422, detail="None of those are this workspace's photos or videos.")
    template_id = body.template_id or autocut_templates.best_template(len(visuals)).id
    choice = _music(session, workspace_id, body.music_asset_id)
    try:
        plan, grid, template, audio = autocut_jobs.build_plan(
            template_id, visuals, music=body.music, speed=body.speed, kinds=kinds,
            music_path=choice.path if choice else None,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "template": _template_view(template, autocut_templates.match_score(template, len(visuals))),
        "plan": autocut_jobs._plan_json(plan),
        "bpm": grid.bpm,
        "beat_synced": plan.beat_synced,
        "music_available": audio is not None,
        "picture_count": len(visuals),
        # Which of the arranged clips survived the filter and in what order,
        # so the timeline can reconcile if a stray id was dropped.
        "asset_ids": visuals,
    }


class RenderRequestBody(PlanRequest):
    title: str | None = Field(default=None, max_length=200)
    confirm: bool = False
    #: The saved draft this render is of, when the dialog reopened one. The
    #: draft is then marked as rendering and settles to rendered on its own;
    #: without it the draft stayed offered to pick up after its video was made.
    draft_id: str | None = Field(default=None, max_length=64)


def _queue(
    session: Session, request: Request, workspace_id: str, user: CurrentUser,
    body: PlanRequest, *, preview: bool, title: str | None = None,
    draft_id: str | None = None,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    visuals, kinds = _known_visuals(session, workspace_id, body.asset_ids)
    if not visuals:
        raise HTTPException(status_code=422, detail="None of those are this workspace's photos or videos.")
    template_id = body.template_id or autocut_templates.best_template(len(visuals)).id
    # Looked up before anything is queued, so a draft that is not there
    # refuses the request rather than leaving a render nobody can find.
    if draft_id:
        try:
            drafts.get_draft(session, workspace_id, draft_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Draft not found.") from error
    try:
        queued = autocut_jobs.enqueue_render(
            workspace_id, user.id,
            template_id=template_id,
            asset_ids=visuals,
            music=body.music,
            speed=body.speed,
            title=title,
            preview=preview,
            kinds=kinds,
            aspect=body.aspect,
            fill=body.fill,
            caption=body.caption.strip(),
            caption_position=body.caption_position,
            music_asset=_music(session, workspace_id, body.music_asset_id),
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if draft_id:
        drafts.begin_render(session, workspace_id, user.id, draft_id, job_id=queued["id"])
    audit(
        session, request, workspace_id, user.id,
        "autocut.preview_queued" if preview else "autocut.render_queued",
        "durable_job", queued["id"],
        {"template": template_id, "clips": len(visuals), "preview": preview, "draft": draft_id},
    )
    session.commit()
    return queued


@router.post("/render", status_code=202)
def start_render(
    workspace_id: str,
    body: RenderRequestBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue the full render. Draws in the background, lands in the Library."""
    return _queue(
        session, request, workspace_id, user, body,
        preview=False, title=body.title, draft_id=body.draft_id,
    )


@router.post("/preview", status_code=202)
def start_preview(
    workspace_id: str,
    body: PlanRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue a fast, half-size render of the same plan, to watch and adjust.

    The rehearsal before the real render: same cuts, same music, same timing
    - just quicker to make and never filed in the Library.

    The preview is drawn in-process, right after this response, rather than
    waiting for the media worker's next tick to claim it - a preview is watched
    live while the operator adjusts, so the seconds a queue pickup would cost
    are the ones that matter most. The worker still runs the same job if it
    claims it first; the lease makes the two safe, so this is a head start, not
    a second render.
    """
    queued = _queue(session, request, workspace_id, user, body, preview=True)
    background_tasks.add_task(autocut_jobs.run_render_job, queued["id"])
    return queued


@router.get("/jobs/{job_id}")
def render_status(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Where a queued render or preview has got to, for the dialog to poll."""
    membership(session, workspace_id, user.id)
    try:
        record = get_job_record(job_id, session=session)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="AutoCut job not found.") from error
    if record.get("workspace_id") != workspace_id or record.get("kind") != autocut_jobs.JOB_KIND:
        raise HTTPException(status_code=404, detail="AutoCut job not found.")
    result = record.get("result") or {}
    return {
        "id": job_id,
        "status": record.get("status"),
        "preview": bool((record.get("payload") or {}).get("preview")),
        "error": record.get("error"),
        # The Library asset, once a full render has been ingested.
        "asset_id": result.get("asset_id"),
        "ready": record.get("status") == "succeeded" and bool(result.get("output_path")),
    }


@router.get("/preview/{job_id}/video")
def stream_preview(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> FileResponse:
    """The finished preview clip, retyped opaque like every other served byte.

    Only a preview is served here - a full render is watched in the Library
    through its asset, and serving its file from a second place would be a
    download route around the Library's own controls.
    """
    membership(session, workspace_id, user.id)
    try:
        record = get_job_record(job_id, session=session)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="AutoCut job not found.") from error
    payload = record.get("payload") or {}
    result = record.get("result") or {}
    if (
        record.get("workspace_id") != workspace_id
        or record.get("kind") != autocut_jobs.JOB_KIND
        or not payload.get("preview")
    ):
        raise HTTPException(status_code=404, detail="AutoCut preview not found.")
    output = result.get("output_path")
    if not output or not Path(output).is_file():
        raise HTTPException(status_code=409, detail="This preview is not ready yet.")
    return FileResponse(
        Path(output), media_type=OPAQUE_MEDIA_TYPE,
        filename=f"{job_id}.mp4", content_disposition_type="inline",
    )
