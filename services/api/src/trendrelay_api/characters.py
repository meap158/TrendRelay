"""Characters: the built-in cast, new ones drawn on request, and the draft link.

A character's pictures are Library images with `source_type="character"`, so
the Library lists them and every picker can use them. Listing a workspace's
characters first copies in any built-in one it does not have yet; that is
the whole of the seeding, and it is why a fresh workspace has a cast without
a data migration.

Drawing a new character spends twice on the image provider - a portrait,
then the sheet made from it - or once when the operator starts from a
Library picture, which then is the portrait. It runs on a thread in the API
process, not on the worker, because it is two short requests and the
operator is watching the dialog for it. The row carries its own state; a
row left `generating` by a restart reads as failed after a while, and Try
again draws only the picture that is still missing, so nothing already paid
for is drawn twice.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from secrets import token_hex
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trendrelay_api.character_catalogue import (
    DEFAULT_CHARACTERS,
    default_paths,
    draft_line,
    portrait_prompt,
    sheet_prompt,
)
from trendrelay_api.character_models import Character
from trendrelay_api.models import new_id, utc_now

logger = logging.getLogger(__name__)

SOURCE_TYPE = "character"
#: Longer than two image requests ever take. A row still generating after
#: this was orphaned by a restart.
STALE_AFTER = timedelta(minutes=20)
_SLUG = re.compile(r"[^a-z0-9]+")
_NAME_LIMIT = 80
_APPEARANCE_LIMIT = 1000
_OUTFIT_LIMIT = 500
_MAX_REFERENCE_BYTES = 8_000_000

#: Starts the drawing. A thread in the API; tests run it inline.
Spawn = Callable[[Callable[[], None]], None]


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="character-generation", daemon=True).start()


SPAWN: Spawn = _thread
#: The factory the drawing thread opens its own sessions with. None is the
#: API's; tests point it at theirs.
SESSION_FACTORY: Any = None


def _factory() -> Any:
    if SESSION_FACTORY is not None:
        return SESSION_FACTORY
    from trendrelay_api.database import SessionFactory

    return SessionFactory


def _staging_root() -> Path:
    """Where a picture waits before the Library copies it: an approved root."""
    from trendrelay_api.product_creative_drafts import _upload_root

    return _upload_root().parent / "characters"


def _ingest(workspace_id: str, actor_user_id: str, path: Path, title: str) -> str:
    """Put one picture in the Library and return its asset id.

    The Library dedupes by content, so importing the shipped cast twice -
    two requests racing, or a row recreated - lands on the same asset.
    """
    from trendrelay_api.media_library import (
        JOB_SESSION_FACTORY,
        create_ingest_job,
        run_ingest_job,
    )

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    job = create_ingest_job(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        path=str(path),
        title=title,
        source_type=SOURCE_TYPE,
        hashtags=["character"],
        source_sha256=digest,
        factory=JOB_SESSION_FACTORY,
    )
    if not (job.get("status") == "succeeded" and job.get("asset_id")):
        job_id = job.get("id")
        if not job_id or job.get("status") != "queued":
            raise RuntimeError(job.get("error") or "Import did not produce a Library image.")
        job = run_ingest_job(
            str(job_id), worker_id=f"character-{digest[:12]}", factory=JOB_SESSION_FACTORY,
        )
    asset_id = job.get("asset_id") or (job.get("result") or {}).get("asset_id")
    if job.get("status") != "succeeded" or not asset_id:
        raise RuntimeError(job.get("error") or "Import did not produce a Library image.")
    return str(asset_id)


def _stage_bytes(data: bytes, suffix: str = ".jpg") -> Path:
    root = _staging_root()
    root.mkdir(parents=True, exist_ok=True)
    saved = root / f"{hashlib.sha256(data).hexdigest()[:20]}{suffix}"
    saved.write_bytes(data)
    return saved


def _stage_file(source: Path) -> Path:
    root = _staging_root()
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:20]
    saved = root / f"{digest}{source.suffix.lower()}"
    if not saved.is_file():
        shutil.copyfile(source, saved)
    return saved


def ensure_defaults(session: Session, workspace_id: str, actor_user_id: str) -> int:
    """Copy in each built-in character this workspace has never had.

    A built-in row the operator archived stays archived: the slug is still
    taken, so it is not added back. Returns how many were added.
    """
    existing = {row.slug: row for row in session.scalars(
        select(Character).where(Character.workspace_id == workspace_id)
    ).all()}
    taken = set(existing)
    refreshed = False
    added = 0
    for spec in DEFAULT_CHARACTERS:
        if spec.slug in taken:
            row = existing[spec.slug]
            # Correct the previously invented Linh identity without deleting files
            # or changing the character id referenced by existing drafts.
            if (spec.slug == "linh" and row.builtin and row.archived_at is None
                    and row.appearance != spec.appearance):
                portrait, sheet = default_paths(spec.slug)
                if portrait.is_file() and sheet.is_file():
                    try:
                        portrait_id = _ingest(
                            workspace_id, actor_user_id, _stage_file(portrait),
                            f"{spec.name} - Instagram reference portrait",
                        )
                        sheet_id = _ingest(
                            workspace_id, actor_user_id, _stage_file(sheet),
                            f"{spec.name} - corrected character sheet",
                        )
                    except Exception:  # noqa: BLE001 - retry on the next list
                        logger.exception("Corrected Linh references could not be imported")
                    else:
                        row.portrait_asset_id = portrait_id
                        row.sheet_asset_id = sheet_id
                        row.appearance = spec.appearance
                        row.outfit = spec.outfit
                        row.updated_at = utc_now()
                        refreshed = True
            continue
        portrait, sheet = default_paths(spec.slug)
        if not portrait.is_file() or not sheet.is_file():
            continue
        try:
            portrait_id = _ingest(
                workspace_id, actor_user_id, _stage_file(portrait), f"{spec.name} - portrait",
            )
            sheet_id = _ingest(
                workspace_id, actor_user_id, _stage_file(sheet), f"{spec.name} - character sheet",
            )
        except Exception:  # noqa: BLE001 - one bad file must not hide the rest of the cast
            logger.exception("Built-in character %s could not be imported", spec.slug)
            continue
        now = utc_now()
        try:
            with session.begin_nested():
                session.add(Character(
                    id=new_id("character"),
                    workspace_id=workspace_id,
                    slug=spec.slug,
                    name=spec.name,
                    appearance=spec.appearance,
                    outfit=spec.outfit,
                    builtin=True,
                    status="ready",
                    portrait_asset_id=portrait_id,
                    sheet_asset_id=sheet_id,
                    created_by=None,
                    created_at=now,
                    updated_at=now,
                ))
        except IntegrityError:
            # Another request added it first.
            continue
        added += 1
    if added or refreshed:
        session.commit()
    return added


def _status(row: Character) -> tuple[str, str | None]:
    if row.status == "generating" and row.updated_at and utc_now() - row.updated_at > STALE_AFTER:
        return "failed", "This character stopped before it finished. Try again."
    return row.status, row.error


def view(row: Character) -> dict[str, Any]:
    status, error = _status(row)
    return {
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "appearance": row.appearance,
        "outfit": row.outfit,
        "builtin": bool(row.builtin),
        "status": status,
        "error": error,
        "portrait_asset_id": row.portrait_asset_id,
        "sheet_asset_id": row.sheet_asset_id,
        "archived": row.archived_at is not None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_characters(
    session: Session, workspace_id: str, actor_user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Every character not archived, the built-in cast first in catalogue order."""
    if actor_user_id:
        ensure_defaults(session, workspace_id, actor_user_id)
    rows = session.scalars(
        select(Character).where(
            Character.workspace_id == workspace_id,
            Character.archived_at.is_(None),
        )
    ).all()
    order = {spec.slug: index for index, spec in enumerate(DEFAULT_CHARACTERS)}
    rows = sorted(
        rows,
        key=lambda row: (
            0 if row.builtin else 1,
            order.get(row.slug, 0),
            row.created_at or utc_now(),
        ),
    )
    return [view(row) for row in rows]


def _row(session: Session, workspace_id: str, character_id: str) -> Character:
    row = session.get(Character, character_id)
    if row is None or row.workspace_id != workspace_id:
        raise LookupError("Character not found.")
    return row


def get_character(session: Session, workspace_id: str, character_id: str) -> dict[str, Any]:
    return view(_row(session, workspace_id, character_id))


def _clean(value: str | None, limit: int, label: str, *, required: bool) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"Describe the character's {label}.")
    if len(text) > limit:
        raise ValueError(f"Keep the {label} under {limit} characters.")
    return text


def _reference_asset(session: Session, workspace_id: str, asset_id: str) -> str:
    from trendrelay_api.media_models import MediaAsset

    asset = session.get(MediaAsset, asset_id)
    if asset is None or asset.workspace_id != workspace_id:
        raise ValueError("That Library image is not in this workspace.")
    if asset.media_kind != "image":
        raise ValueError("Choose an image from the Library as the reference.")
    return asset.id


def register_character(
    session: Session, workspace_id: str, actor_user_id: str, *,
    name: str, portrait_asset_id: str, sheet_asset_id: str,
    appearance: str | None = None, outfit: str | None = None,
    replace_character_id: str | None = None,
) -> dict[str, Any]:
    """Register reviewed Library references without another provider call."""
    clean_name = _clean(name, _NAME_LIMIT, "name", required=True)
    clean_appearance = _clean(appearance, _APPEARANCE_LIMIT, "appearance", required=True)
    clean_outfit = _clean(outfit, _OUTFIT_LIMIT, "outfit", required=False)
    portrait = _reference_asset(session, workspace_id, portrait_asset_id)
    sheet = _reference_asset(session, workspace_id, sheet_asset_id)
    if portrait == sheet:
        raise ValueError("Choose a separate portrait and character sheet.")
    existing = session.scalar(select(Character).where(
        Character.workspace_id == workspace_id,
        Character.portrait_asset_id == portrait, Character.sheet_asset_id == sheet,
        Character.archived_at.is_(None),
    ))
    if existing is not None and not replace_character_id:
        return view(existing)
    target = _row(session, workspace_id, replace_character_id) if replace_character_id else None
    if target is not None and target.archived_at is not None:
        raise ValueError("That character was removed. Choose an active character to revise.")
    if existing is not None and target is not None and existing.id != target.id:
        raise ValueError("These references already belong to another character.")
    from trendrelay_api.media_models import MediaAsset

    for asset_id in (portrait, sheet):
        asset = session.get(MediaAsset, asset_id)
        asset.source_type = SOURCE_TYPE
    now = utc_now()
    if target is not None:
        target.name = clean_name
        target.appearance = clean_appearance
        target.outfit = clean_outfit or target.outfit
        target.portrait_asset_id = portrait
        target.sheet_asset_id = sheet
        target.status = "ready"
        target.error = None
        target.updated_at = now
        session.commit()
        return view(target)
    base = _SLUG.sub("-", clean_name.lower()).strip("-")[:40] or "character"
    row = Character(
        id=new_id("character"), workspace_id=workspace_id,
        slug=f"{base}-{token_hex(3)}", name=clean_name,
        appearance=clean_appearance, outfit=clean_outfit or "plain neutral everyday clothes",
        builtin=False, status="ready", error=None,
        portrait_asset_id=portrait, sheet_asset_id=sheet,
        created_by=actor_user_id, created_at=now, updated_at=now,
    )
    session.add(row)
    session.commit()
    return view(row)


def create_character(
    session: Session,
    workspace_id: str,
    actor_user_id: str,
    *,
    name: str,
    appearance: str | None = None,
    outfit: str | None = None,
    reference_asset_id: str | None = None,
) -> dict[str, Any]:
    """Store a new character and start drawing its pictures.

    From a description, both pictures are drawn. From a Library picture,
    that picture is the portrait and only the sheet is drawn - which is the
    character-sheet brief's own premise: the reference is the identity.
    """
    from trendrelay_api.integrations.image_generation import require_ready

    clean_name = _clean(name, _NAME_LIMIT, "name", required=True)
    reference = (reference_asset_id or "").strip() or None
    clean_appearance = _clean(
        appearance, _APPEARANCE_LIMIT, "appearance", required=reference is None,
    )
    clean_outfit = _clean(outfit, _OUTFIT_LIMIT, "outfit", required=False) or (
        "simple, plain everyday clothes in neutral colors"
    )
    portrait_id = _reference_asset(session, workspace_id, reference) if reference else None
    require_ready()
    base = _SLUG.sub("-", clean_name.lower()).strip("-")[:40] or "character"
    now = utc_now()
    row = Character(
        id=new_id("character"),
        workspace_id=workspace_id,
        slug=f"{base}-{token_hex(3)}",
        name=clean_name,
        appearance=clean_appearance or "the person in the reference picture",
        outfit=clean_outfit,
        builtin=False,
        status="generating",
        error=None,
        portrait_asset_id=portrait_id,
        sheet_asset_id=None,
        created_by=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    _start(row.id, workspace_id, actor_user_id)
    return view(row)


def retry_character(
    session: Session, workspace_id: str, actor_user_id: str, character_id: str,
) -> dict[str, Any]:
    """Draw whichever picture a failed character is still missing."""
    from trendrelay_api.integrations.image_generation import require_ready

    row = _row(session, workspace_id, character_id)
    status, _ = _status(row)
    if status != "failed":
        raise ValueError("Only a character that stopped can be tried again.")
    require_ready()
    row.status = "generating"
    row.error = None
    row.updated_at = utc_now()
    session.commit()
    _start(row.id, workspace_id, actor_user_id)
    return view(row)


def archive_character(session: Session, workspace_id: str, character_id: str) -> dict[str, Any]:
    """Hide a character from the picker. Its pictures stay in the Library."""
    row = _row(session, workspace_id, character_id)
    if row.status == "generating" and _status(row)[0] == "generating":
        raise ValueError("Wait for this character to finish before removing it.")
    row.archived_at = utc_now()
    row.updated_at = row.archived_at
    session.commit()
    return view(row)


def _start(character_id: str, workspace_id: str, actor_user_id: str) -> None:
    SPAWN(lambda: run_generation(character_id, workspace_id, actor_user_id))


def _asset_bytes(session: Session, workspace_id: str, asset_id: str) -> bytes:
    from trendrelay_api.media_models import MediaAsset

    asset = session.get(MediaAsset, asset_id)
    if asset is None or asset.workspace_id != workspace_id:
        raise RuntimeError("The portrait is no longer in the Library.")
    data = Path(asset.original_path).read_bytes()
    if len(data) > _MAX_REFERENCE_BYTES:
        raise RuntimeError("The portrait is too large to send.")
    return data


def run_generation(
    character_id: str,
    workspace_id: str,
    actor_user_id: str,
    *,
    transport: Any = None,
) -> None:
    """Draw the portrait if missing, then the sheet. Each is saved as it lands."""
    from trendrelay_api.integrations import image_generation

    factory = _factory()

    def save(**values: Any) -> None:
        with factory() as session:
            row = session.get(Character, character_id)
            if row is None:
                return
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utc_now()
            session.commit()

    try:
        with factory() as session:
            row = session.get(Character, character_id)
            if row is None or row.workspace_id != workspace_id:
                return
            name, appearance, outfit = row.name, row.appearance, row.outfit
            portrait_id, sheet_id = row.portrait_asset_id, row.sheet_asset_id
        if not portrait_id:
            data = image_generation.generate(
                portrait_prompt(appearance, outfit), aspect_ratio="2:3", transport=transport,
            )
            portrait_id = _ingest(
                workspace_id, actor_user_id, _stage_bytes(data), f"{name} - portrait",
            )
            save(portrait_asset_id=portrait_id)
        if not sheet_id:
            with factory() as session:
                portrait = _asset_bytes(session, workspace_id, portrait_id)
            data = image_generation.edit(sheet_prompt(outfit), portrait, transport=transport)
            sheet_id = _ingest(
                workspace_id, actor_user_id, _stage_bytes(data), f"{name} - character sheet",
            )
            save(sheet_asset_id=sheet_id)
        save(status="ready", error=None)
    except image_generation.VideoError as error:
        save(status="failed", error=str(error)[:1000])
    except Exception as error:  # noqa: BLE001 - the row must end in a state the operator can read
        logger.exception("Character %s could not be drawn", character_id)
        save(status="failed", error=f"The character could not be drawn: {error}"[:1000])


def for_draft(
    session: Session, workspace_id: str, character_id: str | None,
) -> dict[str, Any] | None:
    """The character a draft may feature, or a refusal."""
    if not character_id:
        return None
    row = _row(session, workspace_id, character_id)
    if row.archived_at is not None:
        raise ValueError("That character was removed. Choose another one.")
    if _status(row)[0] != "ready" or not row.portrait_asset_id:
        raise ValueError("That character is not ready yet.")
    return {
        "id": row.id,
        "name": row.name,
        "portrait_asset_id": row.portrait_asset_id,
        "sheet_asset_id": row.sheet_asset_id,
        "prompt_line": draft_line(row.name, row.appearance),
    }


def stored_for_draft(session: Session, character_id: str | None) -> dict[str, Any] | None:
    """What a stored draft featured, even if the character was since removed."""
    if not character_id:
        return None
    row = session.get(Character, character_id)
    if row is None:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "portrait_asset_id": row.portrait_asset_id,
        "sheet_asset_id": row.sheet_asset_id,
        "archived": row.archived_at is not None,
    }


__all__ = [
    "SOURCE_TYPE",
    "archive_character",
    "create_character",
    "ensure_defaults",
    "for_draft",
    "get_character",
    "list_characters",
    "retry_character",
    "run_generation",
    "stored_for_draft",
]
