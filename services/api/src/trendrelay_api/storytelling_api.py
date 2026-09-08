"""The Storytelling HTTP surface: templates, a render, and the job to watch.

Fewer questions than AutoCut asks, because a narration answers one of them
itself. AutoCut offers a plan preview before committing, since its plan is
knowable the moment a track and a template are chosen. A narration's plan is
not knowable until the voice exists - and once the voice exists the expensive
part is already paid for - so there is nothing to preview *before* rendering,
and the plan comes back on the finished job instead.

The script arrives as typed text. Nothing here writes one, and nothing here
researches one: that stays a person's work, or an assistant's over MCP, which
is a separate surface with its own approvals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.jobs import get_job_record
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.media_serving import OPAQUE_MEDIA_TYPE
from trendrelay_api.storytelling import jobs as story_jobs
from trendrelay_api.storytelling import planner, script

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/storytelling", tags=["storytelling"],
)
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]

#: A narrated piece is longer than a montage - one picture a sentence over ten
#: minutes is a couple of hundred - but not unbounded: past this the renderer's
#: own ceiling is the next thing to hit, and a limit that says so is kinder
#: than a render that fails at the end.
MAX_PICTURES = 200

#: The longest script this will take in one piece. Roughly twenty minutes of
#: speech, and past the point where one ffmpeg graph draws the whole thing.
MAX_SCRIPT_CHARACTERS = 20_000


def _template_view(story: planner.StoryTemplate) -> dict[str, Any]:
    return {
        "id": story.id,
        "name": story.name,
        "description": story.description,
        "transition": story.transition,
        "max_hold_seconds": story.max_hold_seconds,
    }


@router.get("/templates")
def list_templates(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The pacings on offer. Not topics - nothing here knows what a script is
    about, and a template that did would only fit the stories it was named for."""
    membership(session, workspace_id, user.id)
    return {"templates": [_template_view(item) for item in planner.TEMPLATES]}


class ScriptRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_SCRIPT_CHARACTERS)


@router.post("/outline")
def outline_script(
    workspace_id: str,
    body: ScriptRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """How this script splits into shots, before anything is spoken or paid for.

    A dry read of the writing: how many sentences it is, and therefore how many
    pictures it wants. Cheap and offline, so somebody can shape the script and
    gather the pictures before spending a generation on it.
    """
    membership(session, workspace_id, user.id)
    lines = script.split(story_text(body.body))
    return {
        "lines": [line.text for line in lines],
        "count": len(lines),
        # The number worth showing beside a picture picker: too few pictures
        # will repeat, which is honest but rarely what somebody wanted.
        "pictures_wanted": len(lines),
    }


def story_text(body: str) -> str:
    """The script as everything downstream will see it.

    Composed here as well as in the job, so the sentence count somebody is
    shown is the sentence count they get - the two would otherwise disagree on
    any script with a decomposed accent in it.
    """
    from trendrelay_api.storytelling import narration

    return narration.prepare(body)


class RenderBody(ScriptRequest):
    asset_ids: list[str] = Field(default_factory=list, max_length=MAX_PICTURES)
    template_id: str = "explainer"
    #: The ElevenLabs voice to read it, or nothing when a recording is used.
    voice_id: str | None = None
    model_id: str | None = None
    language_code: str | None = None
    #: A Library asset somebody recorded themselves, used instead of a voice.
    narration_asset_id: str | None = None
    aspect: str = story_jobs.DEFAULT_ASPECT
    fill: str = "cover"
    subtitles: bool = True
    title: str | None = None


def _known_visuals(
    session: Session, workspace_id: str, asset_ids: list[str],
) -> list[str]:
    """The pictures that are this workspace's, in the order they were chosen.

    Order is the story. A set has none, and the database returns one.
    """
    rows = session.scalars(
        select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(asset_ids),
            MediaAsset.media_kind.in_(["image", "video"]),
        )
    ).all()
    known = {row.id for row in rows}
    return [asset_id for asset_id in asset_ids if asset_id in known]


def _queue(
    session: Session, request: Request, workspace_id: str, user: CurrentUser,
    body: RenderBody, *, preview: bool,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    visuals = _known_visuals(session, workspace_id, body.asset_ids)
    if not visuals:
        raise HTTPException(
            status_code=422,
            detail="None of those are this workspace's photos or videos.",
        )
    try:
        queued = story_jobs.enqueue_render(
            workspace_id, user.id,
            body=body.body,
            asset_ids=visuals,
            template_id=body.template_id,
            voice_id=body.voice_id,
            model_id=body.model_id,
            language_code=body.language_code,
            narration_asset_id=body.narration_asset_id,
            title=body.title,
            preview=preview,
            aspect=body.aspect,
            fill=body.fill,
            subtitles=body.subtitles,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session, request, workspace_id, user.id,
        "storytelling.preview_queued" if preview else "storytelling.render_queued",
        "durable_job", queued["id"],
        {
            "template": body.template_id,
            "pictures": len(visuals),
            "characters": len(body.body),
            "voice": "recording" if body.narration_asset_id else "generated",
            "preview": preview,
        },
    )
    session.commit()
    return queued


@router.post("/render", status_code=202)
def start_render(
    workspace_id: str,
    body: RenderBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Speak the script, cut the pictures to it, file the result in the Library."""
    return _queue(session, request, workspace_id, user, body, preview=False)


@router.post("/preview", status_code=202)
def start_preview(
    workspace_id: str,
    body: RenderBody,
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The same video at half the frame, to watch before keeping it.

    It speaks the script for real, because a preview of a narration with no
    narration in it would be a preview of nothing - so this costs what the
    render costs, and the saving is only the encode. Worth it while the
    pacing and the pictures are still being chosen; not worth doing twice.
    """
    queued = _queue(session, request, workspace_id, user, body, preview=True)
    background_tasks.add_task(story_jobs.run_render_job, queued["id"])
    return queued


def _record(session: Session, workspace_id: str, job_id: str) -> dict[str, Any]:
    try:
        record = get_job_record(job_id, session=session)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Storytelling job not found.") from error
    if (
        record.get("workspace_id") != workspace_id
        or record.get("kind") != story_jobs.JOB_KIND
    ):
        raise HTTPException(status_code=404, detail="Storytelling job not found.")
    return record


@router.get("/jobs/{job_id}")
def render_status(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Where a queued narration has got to, and what it decided."""
    membership(session, workspace_id, user.id)
    record = _record(session, workspace_id, job_id)
    result = record.get("result") or {}
    plan = result.get("plan") or {}
    return {
        "id": job_id,
        "status": record.get("status"),
        "preview": bool((record.get("payload") or {}).get("preview")),
        "error": record.get("error"),
        "asset_id": result.get("asset_id"),
        "ready": record.get("status") == "succeeded" and bool(result.get("output_path")),
        # What was actually drawn. The plan is not knowable before the voice
        # exists, so this is the first and only place it can be reported.
        "shots": len(plan.get("shots") or []),
        "duration": plan.get("duration"),
        "lines": result.get("lines"),
    }


@router.get("/preview/{job_id}/video")
def stream_preview(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> FileResponse:
    """The finished preview, retyped opaque like every other served byte.

    Only a preview. A kept render is watched in the Library through its asset,
    and serving its file from a second place would be a download route around
    the Library's own controls.
    """
    membership(session, workspace_id, user.id)
    record = _record(session, workspace_id, job_id)
    if not (record.get("payload") or {}).get("preview"):
        raise HTTPException(status_code=404, detail="Storytelling preview not found.")
    output = (record.get("result") or {}).get("output_path")
    if not output or not Path(output).is_file():
        raise HTTPException(status_code=409, detail="This preview is not ready yet.")
    return FileResponse(
        Path(output), media_type=OPAQUE_MEDIA_TYPE,
        filename=f"{job_id}.mp4", content_disposition_type="inline",
    )
