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

#: How many sentences an arrangement can name. A script at the character
#: ceiling written entirely in very short sentences, with room over.
MAX_SENTENCES = 2_000


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
    #: One picture per sentence - the matcher's suggestion, or what somebody
    #: moved it to. Empty means the order the pictures were chosen in.
    assignments: list[str] = Field(default_factory=list, max_length=MAX_SENTENCES)
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
            # Filtered against the same set the pictures were: an assignment
            # naming something this workspace does not own would otherwise
            # reach the planner as a picture that cannot be drawn.
            assignments=[
                asset_id if asset_id in set(visuals) else "" for asset_id in body.assignments
            ],
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


# --------------------------------------------------------------------------- #
# B-roll
#
# A script about something nobody filmed has no pictures in the Library to cut
# to. Searching for them is part of writing the video, so it lives beside the
# script rather than in a separate tab somebody has to leave and come back from.
# --------------------------------------------------------------------------- #


@router.get("/broll/status")
def broll_status(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Whether b-roll can be searched, and what to do when it cannot."""
    from trendrelay_api.integrations import pexels

    membership(session, workspace_id, user.id)
    return pexels.provider_status()


@router.get("/broll/search")
def search_broll(
    workspace_id: str,
    q: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    kind: str = "image",
    page: int = 1,
    orientation: str = "",
    locale: str = "",
) -> dict[str, Any]:
    """Candidates for one search. Nothing is downloaded by asking."""
    from trendrelay_api.integrations import pexels

    membership(session, workspace_id, user.id)
    try:
        found = pexels.search(
            q, kind="video" if kind == "video" else "image",
            page=page, orientation=orientation, locale=locale,
        )
    except pexels.PexelsUnavailable as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        **found,
        "results": [
            {
                "id": item.id,
                "kind": item.kind,
                "preview_url": item.preview_url,
                "width": item.width,
                "height": item.height,
                "duration_seconds": item.duration_seconds,
                # Shown on the tile, not just stored: the licence asks for the
                # credit wherever the media is, and a picker is one of the
                # places the media is.
                "credit": item.credit,
                "photographer": item.photographer,
                "photographer_url": item.photographer_url,
                "page_url": item.page_url,
            }
            for item in found["results"]
        ],
    }


class BrollImport(BaseModel):
    """Which result to bring in - and nothing about where it lives.

    The file URL is resolved from the id on this side. A search hands back a
    preview and a credit and no download link precisely so that an import
    carries no url for anybody to swap for one of their own, which is the
    difference between fetching a chosen photo and fetching whatever a caller
    names.
    """

    id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")
    kind: str = "image"
    query: str = ""


@router.post("/broll/import", status_code=202)
def import_broll(
    workspace_id: str,
    body: BrollImport,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Bring one chosen result into the Library, credit and all.

    Through the Library's own ingest rather than rendered from a URL: b-roll is
    then hashed, de-duplicated, thumbnailed and searchable like everything else,
    and a narration plans over Library assets whatever they came from.
    """
    from trendrelay_api.integrations import pexels

    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    try:
        candidate = pexels.lookup(body.id, "video" if body.kind == "video" else "image")
        queued = pexels.import_candidate(
            candidate, workspace_id=workspace_id, actor_user_id=user.id, query=body.query,
        )
    except pexels.PexelsUnavailable as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    audit(
        session, request, workspace_id, user.id,
        "storytelling.broll_imported", "media_asset", queued.get("asset_id") or body.id,
        {"pexels_id": body.id, "kind": body.kind, "query": body.query},
    )
    session.commit()
    return {"job": queued, "credit": candidate.credit}


# --------------------------------------------------------------------------- #
# Arranging: which picture belongs to which sentence.
# --------------------------------------------------------------------------- #


class ArrangeBody(ScriptRequest):
    asset_ids: list[str] = Field(default_factory=list, max_length=MAX_PICTURES)


@router.post("/arrange")
def arrange_pictures(
    workspace_id: str,
    body: ArrangeBody,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Suggest a picture for each sentence, and say what it was suggested on.

    Offline and free: no voice is generated, nothing is written. It answers
    the question somebody would otherwise answer by dragging twenty tiles into
    order, and it is a suggestion - the arrangement that comes back is sent on
    to `/render` only after somebody has looked at it, changed what they
    disagree with, and pressed the button.

    No durations, because there is no audio yet. The clip-too-short rule
    therefore does not fire here; it is the one thing this cannot know before
    the voice exists, and guessing at reading speed would apply it wrongly in
    whichever language the guess was not calibrated for.
    """
    from trendrelay_api.media_models import MediaTranscript
    from trendrelay_api.storytelling import match

    membership(session, workspace_id, user.id)
    lines = [line.text for line in script.split(story_text(body.body))]
    ordered = _known_visuals(session, workspace_id, body.asset_ids)
    if not lines or not ordered:
        return {"lines": lines, "assignments": []}

    assets = {
        asset.id: asset
        for asset in session.scalars(
            select(MediaAsset).where(MediaAsset.id.in_(ordered))
        ).all()
    }
    readings: dict[str, list[Any]] = {}
    for transcript in session.scalars(
        select(MediaTranscript)
        .where(MediaTranscript.asset_id.in_(ordered))
        .order_by(
            (MediaTranscript.status == "reviewed").desc(),
            MediaTranscript.created_at.desc(),
        )
    ).all():
        readings.setdefault(transcript.asset_id, []).append(transcript)

    candidates = []
    for asset_id in ordered:
        asset = assets.get(asset_id)
        if asset is None:
            continue
        evidence, machine = match.evidence_for(asset, readings.get(asset_id, []))
        candidates.append(match.Candidate(
            asset_id=asset_id,
            media_kind=asset.media_kind or "image",
            duration_seconds=(asset.duration_ms / 1000.0) if asset.duration_ms else None,
            evidence=evidence,
            machine=machine,
        ))

    found = match.arrange(lines, candidates)
    return {
        "lines": lines,
        "assignments": [
            {
                "line": item.line,
                "asset_id": item.asset_id,
                "score": item.score,
                # The words it was matched on. Shown, because a suggestion
                # nobody can see the reason for is one nobody trusts twice.
                "matched": list(item.matched),
            }
            for item in found
        ],
    }


# --------------------------------------------------------------------------- #
# Voices. The ones on the key, and the ones that could be.
# --------------------------------------------------------------------------- #


@router.get("/voices/shared")
def shared_voices(
    workspace_id: str,
    language: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Voices in ElevenLabs' library that read this language.

    A read, and the answer to a question the picker could not otherwise
    answer. `/v2/voices` reports what is on the key, which for most accounts
    is the premade set - verified in English and a handful of others. Asking
    for a Vietnamese narration finds nothing there and reads as "Vietnamese is
    not supported", when what is true is that twelve Vietnamese voices exist
    and none of them have been added to this account.
    """
    from trendrelay_api.integrations import elevenlabs

    membership(session, workspace_id, user.id)
    return {"voices": elevenlabs.shared_voices(language), "language": language}


class AddVoice(BaseModel):
    public_owner_id: str = Field(min_length=1, max_length=128)
    voice_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)


@router.post("/voices/add", status_code=201)
def add_voice(
    workspace_id: str,
    body: AddVoice,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Add one shared voice to this key's ElevenLabs library.

    Deliberately its own endpoint and its own button rather than something
    choosing a language quietly does. It changes what the operator's
    ElevenLabs account holds, it counts against that account's voice slots,
    and it is theirs - so it happens when they ask for it, once, and is
    written down in the audit log like every other outward change.
    """
    from trendrelay_api.integrations import elevenlabs

    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    try:
        added = elevenlabs.add_shared_voice(body.public_owner_id, body.voice_id, body.name)
    except elevenlabs.ElevenLabsUnavailable as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    audit(
        session, request, workspace_id, user.id,
        "storytelling.voice_added", "elevenlabs_voice", added,
        {"name": body.name, "from_library": body.voice_id},
    )
    session.commit()
    # The id it is listed under is not the id it has once added, so the picker
    # is told the new one rather than left addressing a voice this key has not
    # got.
    return {"voice_id": added, "name": body.name}
