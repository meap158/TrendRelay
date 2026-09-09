"""The store and per-kind adapters behind resumable creation drafts.

``creation_models`` holds a draft; this decides what a draft of each *kind*
means. A kind registers an adapter: a spec schema (so a saved draft is shape-
checked before it is stored), a compact summary (so a list reads without the
whole spec), and a render (so "make it" hands the spec to that feature's own
renderer). AutoCut and Storytelling are the two kinds today; adding a third is
one adapter here and a line in the registry - no new table, no new endpoint.

The lifecycle mirrors how an assistant already picks up a campaign post: a draft
is saved incomplete and edited freely; only *rendering* enforces completeness,
by handing the spec to the feature's ``enqueue_render`` which raises the same
actionable errors the interactive API does. Media that is not a Library asset is
kept by the draft (see ``creation_models.CreationDraftMedia``) so nothing an
input pointed at can vanish between sessions.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.creation_models import CreationDraft, CreationDraftMedia
from trendrelay_api.media_models import MediaAsset

#: Paging defaults, matching the other list surfaces (products, campaign posts).
DEFAULT_PAGE = 50
MAX_PAGE = 200


# --- per-kind spec schemas ----------------------------------------------------
#
# Deliberately lenient on completeness (a draft is saved half-built) and strict
# on shape and range (a bad value is caught before it is stored, not at render).


class AutoCutSpec(BaseModel):
    """What an AutoCut draft holds - the same choices the dialog offers."""

    model_config = {"extra": "forbid"}

    asset_ids: list[str] = Field(default_factory=list, max_length=40)
    template_id: str | None = None
    music: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    aspect: str = Field(default="portrait", pattern="^(portrait|square|landscape)$")
    fill: str = Field(default="cover", pattern="^(cover|blur)$")
    caption: str = Field(default="", max_length=120)
    caption_position: str = Field(default="bottom", pattern="^(top|bottom)$")


class StorySpec(BaseModel):
    """What a Storytelling draft holds - script, media, voice, and pacing."""

    model_config = {"extra": "forbid"}

    body: str = Field(default="", max_length=20_000)
    asset_ids: list[str] = Field(default_factory=list, max_length=200)
    assignments: list[str] = Field(default_factory=list, max_length=2_000)
    template_id: str = "explainer"
    voice_id: str | None = None
    model_id: str | None = None
    language_code: str | None = None
    narration_asset_id: str | None = None
    aspect: str = "16:9"
    fill: str = Field(default="cover", pattern="^(cover|blur)$")
    subtitles: bool = True
    caption_style: str = Field(default="", max_length=40)


# --- media resolution ---------------------------------------------------------


def _visual_kinds(
    session: Session, workspace_id: str, asset_ids: list[str]
) -> tuple[list[str], dict[str, str]]:
    """This workspace's images and videos among the ids, in the order given.

    The same filter the AutoCut and Storytelling APIs apply: a stray id is
    dropped rather than failing the set, audio is left out (a track has nothing
    to show), and the operator's arrangement order is preserved.
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


# --- adapters -----------------------------------------------------------------


@dataclass(frozen=True)
class Adapter:
    kind: str
    spec_model: type[BaseModel]
    #: (session, workspace_id, actor_user_id, spec, *, title, preview) -> job dict
    render: Callable[..., dict[str, Any]]
    #: spec -> a compact dict for a list row
    summarize: Callable[[dict[str, Any]], dict[str, Any]]


def _render_autocut(
    session: Session, workspace_id: str, actor_user_id: str,
    spec: dict[str, Any], *, title: str | None, preview: bool,
) -> dict[str, Any]:
    from trendrelay_api.autocut import jobs as autocut_jobs
    from trendrelay_api.autocut import templates as autocut_templates

    ordered, kinds = _visual_kinds(session, workspace_id, spec.get("asset_ids", []))
    if not ordered:
        raise ValueError("None of the draft's media are this workspace's photos or videos.")
    template_id = spec.get("template_id") or autocut_templates.best_template(len(ordered)).id
    return autocut_jobs.enqueue_render(
        workspace_id, actor_user_id,
        template_id=template_id,
        asset_ids=ordered,
        music=spec.get("music"),
        speed=spec.get("speed", 1.0),
        title=title,
        preview=preview,
        kinds=kinds,
        aspect=spec.get("aspect", "portrait"),
        fill=spec.get("fill", "cover"),
        caption=(spec.get("caption") or "").strip(),
        caption_position=spec.get("caption_position", "bottom"),
    )


def _render_story(
    session: Session, workspace_id: str, actor_user_id: str,
    spec: dict[str, Any], *, title: str | None, preview: bool,
) -> dict[str, Any]:
    from trendrelay_api.storytelling import jobs as story_jobs

    ordered, kinds = _visual_kinds(session, workspace_id, spec.get("asset_ids", []))
    return story_jobs.enqueue_render(
        workspace_id, actor_user_id,
        body=spec.get("body", ""),
        asset_ids=ordered,
        template_id=spec.get("template_id", "explainer"),
        voice_id=spec.get("voice_id"),
        model_id=spec.get("model_id"),
        language_code=spec.get("language_code"),
        narration_asset_id=spec.get("narration_asset_id"),
        title=title,
        preview=preview,
        kinds=kinds,
        assignments=spec.get("assignments") or None,
        aspect=spec.get("aspect", story_jobs.DEFAULT_ASPECT),
        fill=spec.get("fill", "cover"),
        subtitles=spec.get("subtitles", True),
        caption_style=spec.get("caption_style", ""),
    )


def _summarize_autocut(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "clips": len(spec.get("asset_ids", [])),
        "template": spec.get("template_id"),
        "aspect": spec.get("aspect", "portrait"),
        "has_caption": bool((spec.get("caption") or "").strip()),
    }


def _summarize_story(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "script_chars": len(spec.get("body", "")),
        "pictures": len(spec.get("asset_ids", [])),
        "template": spec.get("template_id", "explainer"),
        "voice": spec.get("voice_id"),
        "subtitles": spec.get("subtitles", True),
    }


ADAPTERS: dict[str, Adapter] = {
    "autocut": Adapter("autocut", AutoCutSpec, _render_autocut, _summarize_autocut),
    "storytelling": Adapter("storytelling", StorySpec, _render_story, _summarize_story),
}


def kinds() -> list[str]:
    """The creation kinds a draft may be, for the interface and the schema."""
    return list(ADAPTERS)


def _adapter(kind: str) -> Adapter:
    adapter = ADAPTERS.get(kind)
    if adapter is None:
        raise ValueError(f"Unknown creation kind {kind!r}. Known: {', '.join(ADAPTERS)}.")
    return adapter


def validate_spec(kind: str, spec: dict[str, Any] | None) -> dict[str, Any]:
    """Shape-check a spec for its kind and return it normalised (defaults filled).

    Raises ``ValueError`` with a readable message on a bad shape or range, so the
    caller - a person or an assistant - is told exactly what to fix.
    """
    adapter = _adapter(kind)
    try:
        return adapter.spec_model(**(spec or {})).model_dump()
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ())) or "spec"
        raise ValueError(f"{where}: {first.get('msg', 'invalid')}") from error


# --- self-contained media -----------------------------------------------------
#
# A draft references Library media by asset id. Anything the operator or an
# assistant brought that is not in the Library is kept by the draft: the bytes
# under an approved media root, a row per file, and a ``draft:<id>`` ref in the
# spec's asset list. At render each owned ref is ingested into the Library
# through the same pipeline an import uses, so the feature only ever sees asset
# ids - and the media the draft carried never had to be in the Library to be
# saved and resumed.

#: Suffix and kind by the type the bytes actually are (never a caller's label).
_MEDIA_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-matroska": ".mkv",
}
_MEDIA_KIND = {
    "image/jpeg": "image", "image/png": "image", "image/webp": "image",
    "video/mp4": "video", "video/quicktime": "video", "video/webm": "video",
    "video/x-matroska": "video",
}
DRAFT_REF_PREFIX = "draft:"


def _draft_media_root() -> Path:
    from trendrelay_api.config import get_settings
    from trendrelay_api.tool_registry import PROJECT_ROOT

    roots = get_settings().publishing_media_root_list
    if not roots:
        raise RuntimeError("No approved media root is configured.")
    first = Path(roots[0])
    if not first.is_absolute():
        first = PROJECT_ROOT / first
    return first / "creation-drafts"


def attach_media_bytes(
    session: Session, workspace_id: str, draft_id: str,
    *, data: bytes, original_name: str | None = None,
) -> dict[str, Any]:
    """Keep media the draft needs that is not a Library asset.

    The type is decided by the bytes, not the caller. The file is written under
    an approved media root (so the render-time ingest can read it) named by its
    digest, deduplicated within the draft, and recorded. Returns the ``draft:``
    ref to put in the spec's asset list.
    """
    from trendrelay_api.integrations.mcp.intake import _sniff_media_type

    draft = get_draft(session, workspace_id, draft_id)
    media_type = _sniff_media_type(data)
    if media_type not in _MEDIA_EXT:
        raise ValueError("Only images and videos are accepted, and the bytes are neither.")
    digest = hashlib.sha256(data).hexdigest()
    existing = session.scalars(
        select(CreationDraftMedia).where(
            CreationDraftMedia.draft_id == draft.id, CreationDraftMedia.sha256 == digest
        )
    ).first()
    if existing is not None:
        return _media_view(existing)
    root = _draft_media_root() / draft.id
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}{_MEDIA_EXT[media_type]}"
    if not path.exists():
        path.write_bytes(data)
    row = CreationDraftMedia(
        draft_id=draft.id, media_kind=_MEDIA_KIND[media_type],
        original_name=(original_name or "").strip()[:300] or None,
        stored_path=str(path), sha256=digest, mime_type=media_type, size_bytes=len(data),
    )
    session.add(row)
    session.commit()
    return _media_view(row)


def _media_view(row: CreationDraftMedia) -> dict[str, Any]:
    return {
        "id": row.id,
        "ref": f"{DRAFT_REF_PREFIX}{row.id}",
        "media_kind": row.media_kind,
        "original_name": row.original_name,
        "size_bytes": row.size_bytes,
        "ingested_asset_id": row.ingested_asset_id,
    }


def list_media(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    """The media a draft owns - what its ``draft:`` refs point at."""
    draft = get_draft(session, workspace_id, draft_id)
    rows = session.scalars(
        select(CreationDraftMedia).where(CreationDraftMedia.draft_id == draft.id)
        .order_by(CreationDraftMedia.created_at)
    ).all()
    return {"media": [_media_view(row) for row in rows]}


def _resolve_owned_media(
    session: Session, workspace_id: str, actor_user_id: str, draft: CreationDraft,
) -> dict[str, Any]:
    """A copy of the spec with every ``draft:`` ref turned into a Library asset id.

    Owned media is ingested at render, once, through the standard pipeline; the
    resulting asset id is cached on the media row so a re-render reuses it. A
    spec with no owned refs is returned unchanged.
    """
    spec = dict(draft.spec or {})
    ids = spec.get("asset_ids") or []
    if not any(isinstance(ref, str) and ref.startswith(DRAFT_REF_PREFIX) for ref in ids):
        return spec

    from trendrelay_api.media_library import create_ingest_job, run_ingest_job

    resolved: list[str] = []
    changed = False
    for ref in ids:
        if not (isinstance(ref, str) and ref.startswith(DRAFT_REF_PREFIX)):
            resolved.append(ref)
            continue
        media = session.get(CreationDraftMedia, ref[len(DRAFT_REF_PREFIX):])
        if media is None or media.draft_id != draft.id:
            raise ValueError(f"The draft media {ref} is not on this draft.")
        if media.ingested_asset_id:
            resolved.append(media.ingested_asset_id)
            continue
        ingest = create_ingest_job(
            workspace_id=workspace_id, actor_user_id=actor_user_id,
            path=media.stored_path, title=media.original_name or draft.title,
            source_type="creation-draft", source_sha256=media.sha256,
        )
        asset_id = ingest.get("asset_id")
        if not asset_id and ingest.get("id"):
            done = run_ingest_job(ingest["id"])
            asset_id = (done.get("result") or {}).get("asset_id") or done.get("asset_id")
        if not asset_id:
            raise ValueError("A draft's media could not be ingested for rendering.")
        media.ingested_asset_id = asset_id
        changed = True
        resolved.append(asset_id)
    if changed:
        session.commit()
    spec["asset_ids"] = resolved
    return spec


# --- store --------------------------------------------------------------------


def _view(draft: CreationDraft) -> dict[str, Any]:
    """The full draft, spec and all, for a get or after a write."""
    return {
        "id": draft.id,
        "kind": draft.kind,
        "title": draft.title,
        "status": draft.status,
        "spec": draft.spec,
        "summary": _adapter(draft.kind).summarize(draft.spec or {}),
        "render_job_id": draft.render_job_id,
        "asset_id": draft.asset_id,
        "created_by": draft.created_by,
        "updated_by": draft.updated_by,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


def _row(draft: CreationDraft) -> dict[str, Any]:
    """A compact list row - no full spec, just what a chooser needs."""
    return {
        "id": draft.id,
        "kind": draft.kind,
        "title": draft.title,
        "status": draft.status,
        "summary": _adapter(draft.kind).summarize(draft.spec or {}),
        "asset_id": draft.asset_id,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


def create_draft(
    session: Session, workspace_id: str, actor_user_id: str,
    *, kind: str, title: str | None, spec: dict[str, Any] | None,
) -> dict[str, Any]:
    """Save a new draft of some kind. The spec is shape-checked and normalised."""
    normalised = validate_spec(kind, spec)
    draft = CreationDraft(
        workspace_id=workspace_id,
        kind=kind,
        title=(title or "").strip() or f"Untitled {kind}",
        status="draft",
        spec=normalised,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(draft)
    session.commit()
    return _view(draft)


def get_draft(session: Session, workspace_id: str, draft_id: str) -> CreationDraft:
    draft = session.get(CreationDraft, draft_id)
    if draft is None or draft.workspace_id != workspace_id:
        raise LookupError("Draft not found.")
    return draft


def list_drafts(
    session: Session, workspace_id: str,
    *, kind: str | None = None, status: str | None = None,
    limit: int = DEFAULT_PAGE, offset: int = 0,
) -> dict[str, Any]:
    """A page of drafts, newest-edited first - what a chooser or an agent reads."""
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    where = [CreationDraft.workspace_id == workspace_id]
    if kind:
        where.append(CreationDraft.kind == kind)
    if status:
        where.append(CreationDraft.status == status)
    else:
        # A chooser wants what can be continued; archived drafts hide unless asked.
        where.append(CreationDraft.status != "archived")
    total = session.scalar(
        select(func.count()).select_from(CreationDraft).where(*where)
    ) or 0
    rows = session.scalars(
        select(CreationDraft).where(*where)
        .order_by(CreationDraft.updated_at.desc())
        .limit(limit).offset(offset)
    ).all()
    return {
        "items": [_row(d) for d in rows],
        "total": total,
        "returned": len(rows),
        "limit": limit,
        "offset": offset,
        "more": offset + len(rows) < total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }


def update_draft(
    session: Session, workspace_id: str, actor_user_id: str, draft_id: str,
    *, title: str | None = None, spec: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Edit a draft in place. A render is not undone by an edit; status is only
    set by hand for archiving/reopening, never to fake a render outcome."""
    draft = get_draft(session, workspace_id, draft_id)
    if title is not None:
        draft.title = title.strip() or draft.title
    if spec is not None:
        draft.spec = validate_spec(draft.kind, spec)
    if status is not None:
        if status not in ("draft", "archived"):
            raise ValueError("A draft's status is set by rendering; you may only archive or reopen it.")
        draft.status = status
    draft.updated_by = actor_user_id
    session.commit()
    return _view(draft)


def render_draft(
    session: Session, workspace_id: str, actor_user_id: str, draft_id: str,
    *, preview: bool = False,
) -> dict[str, Any]:
    """Hand the draft's spec to its feature's renderer and record the job.

    Completeness is enforced here, not at save: the adapter's render calls the
    feature's own ``enqueue_render``, which raises the same readable errors the
    interactive API does when the spec is not yet renderable.
    """
    draft = get_draft(session, workspace_id, draft_id)
    adapter = _adapter(draft.kind)
    # Owned (non-Library) media is ingested here and its draft: refs become asset
    # ids, so the feature's renderer only ever sees Library assets.
    spec = _resolve_owned_media(session, workspace_id, actor_user_id, draft)
    queued = adapter.render(
        session, workspace_id, actor_user_id, spec,
        title=draft.title, preview=preview,
    )
    if not preview:
        draft.render_job_id = queued.get("id")
        draft.status = "rendering"
        draft.updated_by = actor_user_id
        session.commit()
    return {"draft_id": draft.id, "preview": preview, "job": queued}


def archive_draft(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    draft = get_draft(session, workspace_id, draft_id)
    draft.status = "archived"
    session.commit()
    return {"id": draft.id, "status": draft.status}
