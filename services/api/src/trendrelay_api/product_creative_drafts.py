"""Pending creatives for one Attribution product, filled through the Library.

Queueing stores the prompt the resolver just produced and nothing else. A
file, a public https URL, or base64 — one of them — is ingested by the same
job the Library uses for any other upload. The product↔asset link is written
only when the draft holds as many assets as it asked for. A refused file, a
failed ingest, or a carousel still short of its count leaves that link unwritten
and the draft pending.

Nothing here publishes, approves, or attaches the creative to a campaign.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.config import get_settings
from trendrelay_api.models import new_id, utc_now
from trendrelay_api.opportunity_models import Product
from trendrelay_api.product_creative_models import (
    ProductCreativeDraft,
    ProductCreativeLink,
)
from trendrelay_api.product_creative_recipes import RECIPES, resolve_prompt
from trendrelay_api.tool_registry import PROJECT_ROOT

DEFAULT_PAGE = 50
MAX_PAGE = 100
#: How many Library pictures one draft may attach. The same ceiling as a
#: thumbnail read, so the assistant that fills the draft can fetch them in
#: one call.
MAX_SUBJECT_IMAGES = 8
_KINDS = frozenset({"image", "carousel", "video"})
_UPLOAD_DIRNAME = "product-creatives"


def _upload_root() -> Path:
    """Where a submitted file sits before the Library copies it.

    Inside the first approved media root, so `approved_source_path` accepts
    it without a new root. Named for how it arrived.
    """
    roots = get_settings().publishing_media_root_list
    if not roots:
        raise RuntimeError("No approved media root is configured.")
    first = Path(roots[0])
    if not first.is_absolute():
        first = PROJECT_ROOT / first
    return first / _UPLOAD_DIRNAME


def _product(session: Session, workspace_id: str, product_id: str) -> Product:
    product = session.get(Product, product_id)
    if product is None or product.workspace_id != workspace_id:
        raise LookupError("Product not found.")
    return product


def _draft(session: Session, workspace_id: str, draft_id: str) -> ProductCreativeDraft:
    draft = session.get(ProductCreativeDraft, draft_id)
    if draft is None or draft.workspace_id != workspace_id:
        raise LookupError("Creative draft not found.")
    return draft


def _product_images(product: Product) -> list[str]:
    from trendrelay_api.integrations.mcp.products import product_images

    return product_images(product)


def _subject_ids(raw: list[str] | None) -> list[str]:
    """The pick, in order, without blanks or repeats."""
    found: list[str] = []
    for item in raw or []:
        text = str(item).strip()
        if text and text not in found:
            found.append(text)
    if len(found) > MAX_SUBJECT_IMAGES:
        raise ValueError(
            f"Choose at most {MAX_SUBJECT_IMAGES} images from the Library."
        )
    return found


def _subject_assets(
    session: Session, workspace_id: str, raw: list[str] | None,
) -> list[dict[str, Any]]:
    """The Library images this ask attaches, or a refusal.

    An id from another workspace, a missing id, or a video is not a subject.
    """
    from trendrelay_api.media_models import MediaAsset

    ids = _subject_ids(raw)
    if not ids:
        return []
    rows = {
        asset.id: asset
        for asset in session.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_(ids),
                MediaAsset.workspace_id == workspace_id,
            )
        ).all()
    }
    resolved: list[dict[str, Any]] = []
    for asset_id in ids:
        asset = rows.get(asset_id)
        if asset is None:
            raise ValueError("That Library image is not in this workspace.")
        if asset.media_kind != "image":
            raise ValueError("Choose images from the Library. Video and audio are not a subject.")
        resolved.append({"asset_id": asset.id, "title": asset.title})
    return resolved


def _stored_subjects(session: Session, draft: ProductCreativeDraft) -> list[dict[str, Any]]:
    """What was stored, including an asset that has since gone."""
    from trendrelay_api.media_models import MediaAsset

    ids = [str(item) for item in (draft.subject_asset_ids or []) if str(item).strip()]
    if not ids:
        return []
    rows = {
        asset.id: asset
        for asset in session.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_(ids),
                MediaAsset.workspace_id == draft.workspace_id,
            )
        ).all()
    }
    stored: list[dict[str, Any]] = []
    for asset_id in ids:
        asset = rows.get(asset_id)
        if asset is None or asset.media_kind != "image":
            stored.append({"asset_id": asset_id, "title": "", "missing": True})
        else:
            stored.append({"asset_id": asset.id, "title": asset.title, "missing": False})
    return stored


def _background(recipe: str, enabled: bool, reference: str | None) -> tuple[bool, str | None]:
    """The background switch and the URL it attaches, or a refusal."""
    spec = RECIPES[recipe]
    required = spec["background"] == "required"
    if required and not enabled:
        raise ValueError(
            "This recipe needs a background image. There is no wording without one."
        )
    text = (reference or "").strip()
    if enabled or required:
        if not text.startswith("https://"):
            raise ValueError("Attach the background as an https URL.")
        return True, text[:2000]
    if text:
        raise ValueError("A background URL needs the background option turned on.")
    return False, None


def _card_count(kind: str, requested: int | None) -> int:
    if kind == "carousel":
        if requested is None:
            raise ValueError("A carousel needs an explicit card count.")
        if not 2 <= int(requested) <= 10:
            raise ValueError("A carousel holds between 2 and 10 cards.")
        return int(requested)
    if requested not in (None, 1):
        raise ValueError("An image or a video is one file.")
    return 1


def _prepare(
    session: Session,
    workspace_id: str,
    *,
    product_id: str,
    kind: str,
    recipe: str,
    variant: str | None,
    background_enabled: bool,
    background_reference: str | None,
    card_count: int | None,
    subject_asset_ids: list[str] | None = None,
    require_subject: bool = True,
) -> dict[str, Any]:
    """Validate an ask and resolve the prompt. Does not write."""
    if kind not in _KINDS:
        raise ValueError("Kind must be image, carousel, or video.")
    spec = RECIPES.get(recipe)
    if spec is None:
        known = ", ".join(sorted(RECIPES))
        raise ValueError(f"Unknown recipe {recipe!r}. Known recipes: {known}.")
    if kind not in spec["kinds"]:
        raise ValueError(f"{recipe} is not a {kind} recipe.")
    if spec["variants"]:
        if variant not in spec["variants"]:
            raise ValueError("Choose female or male for the mirror selfie.")
    elif variant:
        raise ValueError("This recipe has no subject variant.")
    product = _product(session, workspace_id, product_id)
    images = _product_images(product)
    subjects = _subject_assets(session, workspace_id, subject_asset_ids)
    # A Library pick is the subject. Without one, the listing pictures are,
    # and a product that has neither cannot be queued. Preview skips that
    # gate so the prompt can be read before a picture is chosen.
    if require_subject and not subjects and not images:
        raise ValueError("This product has no image to generate from.")
    enabled, reference = _background(recipe, background_enabled, background_reference)
    prompt = resolve_prompt(
        recipe,
        background=enabled,
        variant=variant if spec["variants"] else None,
    )
    return {
        "product_id": product.id,
        "kind": kind,
        "recipe": recipe,
        "variant": variant if spec["variants"] else None,
        "background_enabled": enabled,
        "background_reference": reference,
        "prompt": prompt,
        "card_count": _card_count(kind, card_count),
        "product_images": images,
        "subject_assets": subjects,
    }


def _owed(draft: ProductCreativeDraft) -> int:
    if draft.status == "succeeded":
        return 0
    staged = list(draft.staged_asset_ids or [])
    return max(int(draft.card_count) - len(staged), 0)


def _view(session: Session, draft: ProductCreativeDraft) -> dict[str, Any]:
    product = session.get(Product, draft.product_id)
    images = _product_images(product) if product is not None else []
    staged = [str(item) for item in (draft.staged_asset_ids or [])]
    return {
        "id": draft.id,
        "workspace_id": draft.workspace_id,
        "product_id": draft.product_id,
        "kind": draft.kind,
        "recipe": draft.recipe,
        "variant": draft.variant,
        "background_enabled": bool(draft.background_enabled),
        "background_reference": draft.background_reference,
        "prompt": draft.prompt,
        "card_count": draft.card_count,
        "status": draft.status,
        "product_images": images,
        "subject_assets": _stored_subjects(session, draft),
        "ingested_asset_ids": staged,
        "owed": _owed(draft),
        "linked": draft.status == "succeeded",
    }


def preview(
    session: Session,
    workspace_id: str,
    *,
    product_id: str,
    kind: str,
    recipe: str,
    variant: str | None = None,
    background_enabled: bool = False,
    background_reference: str | None = None,
    card_count: int | None = None,
    subject_asset_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The prompt that would be stored, without storing it."""
    prepared = _prepare(
        session, workspace_id,
        product_id=product_id, kind=kind, recipe=recipe, variant=variant,
        background_enabled=background_enabled,
        background_reference=background_reference, card_count=card_count,
        subject_asset_ids=subject_asset_ids, require_subject=False,
    )
    prepared["status"] = "preview"
    prepared["owed"] = prepared["card_count"]
    prepared["linked"] = False
    prepared["ingested_asset_ids"] = []
    return prepared


def create_draft(
    session: Session,
    workspace_id: str,
    actor_user_id: str,
    *,
    product_id: str,
    kind: str,
    recipe: str,
    variant: str | None = None,
    background_enabled: bool = False,
    background_reference: str | None = None,
    card_count: int | None = None,
    subject_asset_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Store a pending draft. Does not ingest media and does not link anything."""
    prepared = _prepare(
        session, workspace_id,
        product_id=product_id, kind=kind, recipe=recipe, variant=variant,
        background_enabled=background_enabled,
        background_reference=background_reference, card_count=card_count,
        subject_asset_ids=subject_asset_ids,
    )
    draft = ProductCreativeDraft(
        id=new_id("pcreative"),
        workspace_id=workspace_id,
        product_id=prepared["product_id"],
        kind=prepared["kind"],
        recipe=prepared["recipe"],
        variant=prepared["variant"],
        background_enabled=prepared["background_enabled"],
        background_reference=prepared["background_reference"],
        prompt=prepared["prompt"],
        card_count=prepared["card_count"],
        subject_asset_ids=[item["asset_id"] for item in prepared["subject_assets"]],
        status="pending",
        staged_asset_ids=[],
        created_by=actor_user_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(draft)
    # MCP closes the session on the way out and rolls back whatever was only
    # flushed. The id this returns has to still be a row.
    session.commit()
    return _view(session, draft)


def get_draft(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    return _view(session, _draft(session, workspace_id, draft_id))


def list_drafts(
    session: Session,
    workspace_id: str,
    *,
    status: str | None = "pending",
    kind: str | None = None,
    product_id: str | None = None,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """This workspace's drafts, newest first. Pending is the working queue."""
    if status not in (None, "pending", "succeeded"):
        raise ValueError("Status must be pending or succeeded.")
    if kind is not None and kind not in _KINDS:
        raise ValueError("Kind must be image, carousel, or video.")
    limit = min(max(int(limit), 1), MAX_PAGE)
    offset = max(int(offset), 0)
    conditions = [ProductCreativeDraft.workspace_id == workspace_id]
    if status:
        conditions.append(ProductCreativeDraft.status == status)
    if kind:
        conditions.append(ProductCreativeDraft.kind == kind)
    if product_id:
        conditions.append(ProductCreativeDraft.product_id == product_id)
    total = session.scalar(
        select(func.count()).select_from(ProductCreativeDraft).where(*conditions)
    ) or 0
    rows = session.scalars(
        select(ProductCreativeDraft)
        .where(*conditions)
        .order_by(ProductCreativeDraft.updated_at.desc(), ProductCreativeDraft.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "drafts": [_view(session, row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


def _one_source(
    *,
    media: dict[str, Any] | str | None,
    media_url: str | None,
    media_base64: str | None,
    images_only: bool,
) -> tuple[bytes, str]:
    """Bytes from exactly one of a file, an https URL, or base64."""
    from trendrelay_api.integrations.mcp import intake

    provided = [
        name
        for name, value in (
            ("media", media),
            ("media_url", media_url),
            ("media_base64", media_base64),
        )
        if value and (not isinstance(value, str) or value.strip())
    ]
    if len(provided) != 1:
        raise ValueError(
            "Send exactly one source: a file, a public https URL, or base64."
        )
    allowed = dict(intake._IMAGE_TYPES if images_only else intake._VIDEO_TYPES)
    inline = intake._inline_source(media, media_url, media_base64)
    if inline:
        data, content_type = intake._decode_inline(inline, allowed=allowed, what="file")
        return data, allowed[content_type]
    fetch = intake._download if images_only else intake._download_media
    data, _content_type = fetch(intake._attachment_url(media, media_url))
    sniffed = intake._sniff_media_type(data)
    suffix = allowed.get(sniffed or "")
    if not suffix:
        accepted = ", ".join(sorted(allowed))
        raise ValueError(
            "The file's signature matches no type this draft accepts. "
            f"Accepted types are {accepted}."
        )
    limit = intake.MAX_IMAGE_BYTES if images_only else intake.MAX_VIDEO_BYTES
    if len(data) > limit:
        raise ValueError(
            f"The file is larger than {limit // (1024 * 1024)} MB, "
            "which is the most this server accepts."
        )
    return data, suffix


def _ingest(
    workspace_id: str,
    actor_user_id: str,
    path: Path,
    digest: str,
    title: str,
) -> str:
    """Run the Library ingest and return the asset id, or raise."""
    from trendrelay_api.media_library import (
        JOB_SESSION_FACTORY,
        create_ingest_job,
        run_ingest_job,
    )
    from trendrelay_api.media_models import MediaAsset

    job = create_ingest_job(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        path=str(path),
        title=title,
        source_type="product-creative",
        source_sha256=digest,
        factory=JOB_SESSION_FACTORY,
    )
    if not (job.get("status") == "succeeded" and job.get("asset_id")):
        job_id = job.get("id")
        if not job_id or job.get("status") != "queued":
            raise RuntimeError(job.get("error") or "Import did not produce a Library asset.")
        job = run_ingest_job(
            str(job_id),
            worker_id=f"product-creative-{digest[:12]}",
            factory=JOB_SESSION_FACTORY,
        )
    result = job.get("result") or {}
    asset_id = job.get("asset_id") or result.get("asset_id")
    if job.get("status") != "succeeded" or not asset_id:
        raise RuntimeError(job.get("error") or "Import did not produce a Library asset.")
    with JOB_SESSION_FACTORY() as check:
        asset = check.get(MediaAsset, asset_id)
        if asset is None or asset.workspace_id != workspace_id:
            raise RuntimeError("Import did not produce a Library asset in this workspace.")
    return str(asset_id)


def _write_links(session: Session, draft: ProductCreativeDraft, asset_ids: list[str]) -> None:
    """Tie every ingested asset to the product. One association each."""
    for position, asset_id in enumerate(asset_ids):
        existing = session.scalar(
            select(ProductCreativeLink).where(
                ProductCreativeLink.workspace_id == draft.workspace_id,
                ProductCreativeLink.product_id == draft.product_id,
                ProductCreativeLink.asset_id == asset_id,
            )
        )
        if existing is not None:
            continue
        session.add(ProductCreativeLink(
            id=new_id("pclink"),
            workspace_id=draft.workspace_id,
            product_id=draft.product_id,
            asset_id=asset_id,
            draft_id=draft.id,
            position=position,
            created_at=utc_now(),
        ))


def submit_media(
    session: Session,
    workspace_id: str,
    actor_user_id: str,
    draft_id: str,
    *,
    media: dict[str, Any] | str | None = None,
    media_url: str | None = None,
    media_base64: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Ingest one file for this draft. Link the product only when the count is met.

    A failure raises and writes no link. The draft stays pending, including
    when a carousel already holds some earlier files: those stay staged and
    unlinked until the set is complete.
    """
    draft = _draft(session, workspace_id, draft_id)
    if draft.status != "pending":
        raise ValueError("This draft is already filled.")
    if _owed(draft) <= 0:
        raise ValueError("This draft already holds every file it asked for.")
    data, suffix = _one_source(
        media=media,
        media_url=media_url,
        media_base64=media_base64,
        images_only=draft.kind != "video",
    )
    digest = hashlib.sha256(data).hexdigest()
    root = _upload_root()
    root.mkdir(parents=True, exist_ok=True)
    saved = root / f"{digest[:20]}{suffix}"
    saved.write_bytes(data)
    # The ingest opens its own session. Commit the read first so that session
    # is not waiting on this request's transaction.
    session.commit()
    title = (filename or "").strip() or f"{draft.recipe} {draft.kind}"
    try:
        asset_id = _ingest(workspace_id, actor_user_id, saved, digest, title[:300])
    except Exception as error:
        raise ValueError(f"The file was not imported: {error}") from error

    draft = _draft(session, workspace_id, draft_id)
    staged = [str(item) for item in (draft.staged_asset_ids or [])]
    if asset_id not in staged:
        staged.append(asset_id)
        draft.staged_asset_ids = staged
    draft.updated_at = utc_now()
    if len(staged) < draft.card_count:
        view = _view(session, draft)
        view["asset_id"] = asset_id
        # The Library asset is already committed by ingest. The staged id has
        # to be too, or the next card opens a session that never saw this one.
        session.commit()
        return view
    _write_links(session, draft, staged)
    draft.status = "succeeded"
    draft.updated_at = utc_now()
    view = _view(session, draft)
    view["asset_id"] = asset_id
    session.commit()
    return view


def creative_assets_by_product(
    session: Session, workspace_id: str
) -> dict[str, list[dict[str, Any]]]:
    """The filled creatives of each product, for the Attribution product read."""
    rows = session.scalars(
        select(ProductCreativeLink)
        .where(ProductCreativeLink.workspace_id == workspace_id)
        .order_by(ProductCreativeLink.position, ProductCreativeLink.created_at)
    ).all()
    found: dict[str, list[dict[str, Any]]] = {}
    for link in rows:
        found.setdefault(link.product_id, []).append({
            "asset_id": link.asset_id,
            "draft_id": link.draft_id,
            "position": link.position,
        })
    return found


def creative_drafts_by_product(
    session: Session, workspace_id: str
) -> dict[str, list[dict[str, Any]]]:
    """A short reading of each product's drafts, pending ones included."""
    rows = session.scalars(
        select(ProductCreativeDraft)
        .where(ProductCreativeDraft.workspace_id == workspace_id)
        .order_by(ProductCreativeDraft.updated_at.desc())
    ).all()
    found: dict[str, list[dict[str, Any]]] = {}
    for draft in rows:
        found.setdefault(draft.product_id, []).append({
            "id": draft.id,
            "kind": draft.kind,
            "recipe": draft.recipe,
            "status": draft.status,
            "card_count": draft.card_count,
            "owed": _owed(draft),
        })
    return found


def attribution_products_by_asset(
    session: Session, workspace_id: str, asset_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """The Attribution products each Library asset is tied to."""
    if not asset_ids:
        return {}
    rows = session.execute(
        select(ProductCreativeLink, Product.name)
        .join(Product, Product.id == ProductCreativeLink.product_id)
        .where(
            ProductCreativeLink.workspace_id == workspace_id,
            ProductCreativeLink.asset_id.in_(asset_ids),
        )
        .order_by(Product.name)
    ).all()
    found: dict[str, list[dict[str, Any]]] = {}
    for link, name in rows:
        found.setdefault(link.asset_id, []).append({
            "product_id": link.product_id,
            "name": name,
            "draft_id": link.draft_id,
        })
    return found
