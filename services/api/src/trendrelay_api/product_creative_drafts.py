"""Pending creatives for Attribution products, filled through the Library.

Queueing stores the prompt the resolver just produced and nothing else. One
product is one draft. Together, when asked, is one draft for every selected
product, and the finished file is linked to each of them. A file, a public
https URL, or base64 — one of them — is ingested by the same job the Library
uses for any other upload. The product↔asset link is written only when the
draft holds as many assets as it asked for. A refused file, a failed ingest,
or a carousel still short of its count leaves that link unwritten and the
draft pending.

Nothing here publishes, approves, or attaches the creative to a campaign.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from trendrelay_api.config import get_settings
from trendrelay_api.models import new_id, utc_now
from trendrelay_api.opportunity_models import Product, ProductOffer
from trendrelay_api.product_creative_models import (
    ProductCreativeDraft,
    ProductCreativeDraftProduct,
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
#: What an operator may attach from the listing, in the order a read returns
#: them. Anything else is a refusal. Price is the offer Attribution shows.
_LISTING_KEYS = ("title", "price", "description", "gallery", "variations")
_DESCRIPTION_LIMIT = 8000
_GALLERY_LIMIT = 60
_KINDS = frozenset({"image", "carousel", "video"})
_UPLOAD_DIRNAME = "product-creatives"
#: Writers that may race on one draft at a time are few: an operator, the
#: assistant, and one video job.
_STAGE_ATTEMPTS = 5


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


def _products_for_ask(
    session: Session,
    workspace_id: str,
    *,
    product_id: str,
    together: bool,
    product_ids: list[str] | None,
) -> list[Product]:
    """The products this ask features. Together keeps the lead first.

    Without together, extra ids are ignored. An older create that names one
    product stays one product even if a list is also sent.
    """
    lead = _product(session, workspace_id, product_id)
    if not together:
        return [lead]
    ordered: list[str] = []
    for item in product_ids or []:
        text = str(item).strip()
        if text and text not in ordered:
            ordered.append(text)
    if not ordered:
        ordered = [lead.id]
    if lead.id not in ordered:
        raise ValueError("The lead product has to be one of the products in the shot.")
    ordered = [lead.id, *[item for item in ordered if item != lead.id]]
    if len(ordered) < 2:
        raise ValueError("Together needs at least two products.")
    if len(ordered) > MAX_SUBJECT_IMAGES:
        raise ValueError(f"Together holds at most {MAX_SUBJECT_IMAGES} products.")
    return [_product(session, workspace_id, item) for item in ordered]


def _draft(session: Session, workspace_id: str, draft_id: str) -> ProductCreativeDraft:
    draft = session.get(ProductCreativeDraft, draft_id)
    if draft is None or draft.workspace_id != workspace_id:
        raise LookupError("Creative draft not found.")
    return draft


def _product_images(product: Product) -> list[str]:
    from trendrelay_api.integrations.mcp.products import product_images

    return product_images(product)


def _image_choices(raw: list[dict[str, Any]] | None) -> dict[str, list[str]] | None:
    """Which listing pictures to keep, or None when the caller left them all.

    An empty list is the same as omitting the field. A product named here
    keeps only the addresses listed. An empty address list keeps none.
    """
    if not raw:
        return None
    chosen: dict[str, list[str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Picture choices have to name a product and its pictures.")
        product_id = str(item.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("Picture choices have to name a product and its pictures.")
        if product_id in chosen:
            raise ValueError("Name each product once when choosing its pictures.")
        # No default: an empty list keeps none, and a forgotten key over MCP
        # must not drop every picture of that product.
        urls_raw = item.get("urls")
        if not isinstance(urls_raw, list):
            raise ValueError("Picture choices have to name a product and its pictures.")
        urls: list[str] = []
        for url in urls_raw:
            text = str(url).strip()
            if text and text not in urls:
                urls.append(text)
        if len(urls) > _GALLERY_LIMIT:
            raise ValueError(f"Keep at most {_GALLERY_LIMIT} pictures for one product.")
        chosen[product_id] = urls
    return chosen


def _kept_images(product: Product, chosen: dict[str, list[str]] | None) -> list[str]:
    """The listing pictures generation will see, in gallery order.

    A product the caller did not name keeps its whole gallery. A named
    product keeps the intersection, so a later read cannot put a skipped
    picture back by reordering the request.
    """
    gallery = _product_images(product)
    if chosen is None or product.id not in chosen:
        return gallery
    allowed = set(gallery)
    if any(url not in allowed for url in chosen[product.id]):
        raise ValueError("That picture is not on this product's listing.")
    keep = set(chosen[product.id])
    return [url for url in gallery if url in keep]


def _stored_images(product: Product | None, raw: Any) -> list[str]:
    """Pictures stored at confirm. Null reads the live gallery again."""
    if isinstance(raw, list):
        found: list[str] = []
        for url in raw:
            text = str(url).strip()
            if text and text not in found:
                found.append(text)
        return found
    return _product_images(product) if product is not None else []


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


def _listing_keys(raw: list[str] | None) -> list[str]:
    """The fields to attach, in a stable order, or a refusal."""
    chosen: list[str] = []
    for item in raw or []:
        text = str(item).strip()
        if text not in _LISTING_KEYS:
            raise ValueError(
                "Listing fields are title, price, description, gallery, and variations."
            )
        if text not in chosen:
            chosen.append(text)
    return [key for key in _LISTING_KEYS if key in chosen]


def _price_offers(session: Session, product_id: str) -> list[dict[str, Any]]:
    """Prices Attribution already shows, one per offer that has one."""
    rows = session.scalars(
        select(ProductOffer)
        .where(ProductOffer.product_id == product_id)
        .order_by(ProductOffer.id)
    ).all()
    return [
        {
            "price_cents": int(offer.price_cents),
            "currency": offer.currency,
            "merchant": offer.merchant,
        }
        for offer in rows
        if offer.price_cents is not None
    ]


def _variations(listing: dict[str, Any]) -> dict[str, Any]:
    """Tiers, model names, and stock, bounded the way the listing itself is."""
    tiers: list[dict[str, Any]] = []
    raw_tiers = listing.get("tier_variations")
    if isinstance(raw_tiers, list):
        for entry in raw_tiers[:5]:
            if not isinstance(entry, dict):
                continue
            options_raw = entry.get("options")
            options = [
                str(option)[:120]
                for option in (options_raw if isinstance(options_raw, list) else [])[:30]
            ]
            name = str(entry.get("name") or "")[:120]
            if name or options:
                tiers.append({"name": name, "options": options})
    models_raw = listing.get("models")
    models = [
        str(model)[:160]
        for model in (models_raw if isinstance(models_raw, list) else [])[:30]
        if str(model).strip()
    ]
    stock = listing.get("stock")
    return {
        "tiers": tiers,
        "models": models,
        "stock": int(stock) if isinstance(stock, int) and not isinstance(stock, bool) else None,
    }


def _listing_snapshot(
    session: Session, product: Product, keys: list[str],
) -> dict[str, Any]:
    """The selected values as they are now. Empty when nothing was selected.

    The prompt stays the recipe. These values travel beside it, and a confirm
    stores this object rather than a promise to read the listing again.
    """
    if not keys:
        return {}
    listing = product.listing if isinstance(product.listing, dict) else {}
    snapshot: dict[str, Any] = {}
    if "title" in keys:
        snapshot["title"] = str(listing.get("title") or product.name or "").strip()[:500]
    if "price" in keys:
        snapshot["price"] = {"offers": _price_offers(session, product.id)}
    if "description" in keys:
        text = str(listing.get("description") or "")
        snapshot["description"] = {
            "text": text[:_DESCRIPTION_LIMIT],
            "truncated": len(text) > _DESCRIPTION_LIMIT,
        }
    if "gallery" in keys:
        snapshot["gallery"] = [
            url[:2000] for url in _product_images(product)[:_GALLERY_LIMIT]
        ]
    if "variations" in keys:
        snapshot["variations"] = _variations(listing)
    return snapshot


def _stored_listing(draft: ProductCreativeDraft) -> dict[str, Any]:
    """What was snapshotted. A missing column-shaped value is nothing sent."""
    raw = draft.listing_fields if isinstance(draft.listing_fields, dict) else {}
    return {key: raw[key] for key in _LISTING_KEYS if key in raw}


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
        if urlsplit(text).scheme != "https" or not urlsplit(text).hostname:
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
    listing_fields: list[str] | None = None,
    require_subject: bool = True,
    together: bool = False,
    product_ids: list[str] | None = None,
    included_images: list[dict[str, Any]] | None = None,
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
    products = _products_for_ask(
        session, workspace_id,
        product_id=product_id, together=together, product_ids=product_ids,
    )
    subjects = _subject_assets(session, workspace_id, subject_asset_ids)
    chosen = _image_choices(included_images)
    if chosen is not None:
        member_ids = {product.id for product in products}
        if any(product_id not in member_ids for product_id in chosen):
            raise ValueError("Picture choices have to name a product in this draft.")
    kept = [_kept_images(product, chosen) for product in products]
    # A Library pick replaces the listing for one product. Together keeps
    # every product's own listing pictures, and a shared pick is extra: it
    # does not stand in for the whole group. A member with neither cannot
    # be queued. Pictures the operator unchecked count as absent. Preview
    # skips that gate so the prompt can be read first.
    if require_subject:
        if len(products) > 1:
            missing = [
                product.name or product.id
                for index, product in enumerate(products)
                if not kept[index] and not subjects
            ]
            if missing:
                raise ValueError(
                    "These products have no image to generate from: "
                    + ", ".join(missing)
                    + "."
                )
        elif not subjects and not kept[0]:
            raise ValueError("This product has no image to generate from.")
    enabled, reference = _background(recipe, background_enabled, background_reference)
    group = len(products) > 1
    prompt = resolve_prompt(
        recipe,
        background=enabled,
        variant=variant if spec["variants"] else None,
        together=group,
    )
    keys = _listing_keys(listing_fields)
    snapshots = [_listing_snapshot(session, product, keys) for product in products]
    return {
        "product_id": products[0].id,
        "kind": kind,
        "recipe": recipe,
        "variant": variant if spec["variants"] else None,
        "background_enabled": enabled,
        "background_reference": reference,
        "prompt": prompt,
        "card_count": _card_count(kind, card_count),
        "product_images": kept[0],
        "subject_assets": subjects,
        "listing_fields": snapshots[0],
        "together": group,
        "product_count": len(products),
        "included_by_product": {
            product.id: kept[index]
            for index, product in enumerate(products)
            if chosen is not None and product.id in chosen
        },
        "products": [
            {
                "product_id": product.id,
                "name": product.name,
                "position": index,
                "product_images": kept[index],
                "listing_fields": snapshots[index],
            }
            for index, product in enumerate(products)
        ],
    }


def _owed(draft: ProductCreativeDraft) -> int:
    if draft.status == "succeeded":
        return 0
    staged = list(draft.staged_asset_ids or [])
    return max(int(draft.card_count) - len(staged), 0)


def _known_listing(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in _LISTING_KEYS if key in raw}


def _member_views(session: Session, draft: ProductCreativeDraft) -> list[dict[str, Any]]:
    """Each product on this draft, lead first. A draft with no member rows
    still reads as its lead, which is an insert that predates membership.
    """
    rows = session.scalars(
        select(ProductCreativeDraftProduct)
        .where(ProductCreativeDraftProduct.draft_id == draft.id)
        .order_by(
            ProductCreativeDraftProduct.position,
            ProductCreativeDraftProduct.id,
        )
    ).all()
    if not rows:
        product = session.get(Product, draft.product_id)
        return [{
            "product_id": draft.product_id,
            "name": product.name if product is not None else "",
            "position": 0,
            "product_images": _product_images(product) if product is not None else [],
            "listing_fields": _stored_listing(draft),
        }]
    views: list[dict[str, Any]] = []
    for row in rows:
        product = session.get(Product, row.product_id)
        views.append({
            "product_id": row.product_id,
            "name": product.name if product is not None else "",
            "position": int(row.position),
            "product_images": _stored_images(product, row.included_images),
            "listing_fields": _known_listing(row.listing_fields),
        })
    return views


def _member_product_ids(session: Session, draft: ProductCreativeDraft) -> list[str]:
    ids = [item["product_id"] for item in _member_views(session, draft)]
    return ids or [draft.product_id]


def _view(session: Session, draft: ProductCreativeDraft) -> dict[str, Any]:
    product = session.get(Product, draft.product_id)
    staged = [str(item) for item in (draft.staged_asset_ids or [])]
    members = _member_views(session, draft)
    images = next(
        (
            item["product_images"]
            for item in members
            if item["product_id"] == draft.product_id
        ),
        _product_images(product) if product is not None else [],
    )
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
        "listing_fields": _stored_listing(draft),
        "together": len(members) > 1,
        "product_count": len(members),
        "products": members,
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
    listing_fields: list[str] | None = None,
    together: bool = False,
    product_ids: list[str] | None = None,
    included_images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The prompt that would be stored, without storing it."""
    prepared = _prepare(
        session, workspace_id,
        product_id=product_id, kind=kind, recipe=recipe, variant=variant,
        background_enabled=background_enabled,
        background_reference=background_reference, card_count=card_count,
        subject_asset_ids=subject_asset_ids, listing_fields=listing_fields,
        require_subject=False, together=together, product_ids=product_ids,
        included_images=included_images,
    )
    prepared.pop("included_by_product", None)
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
    listing_fields: list[str] | None = None,
    together: bool = False,
    product_ids: list[str] | None = None,
    included_images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Store a pending draft. Does not ingest media and does not link anything."""
    prepared = _prepare(
        session, workspace_id,
        product_id=product_id, kind=kind, recipe=recipe, variant=variant,
        background_enabled=background_enabled,
        background_reference=background_reference, card_count=card_count,
        subject_asset_ids=subject_asset_ids, listing_fields=listing_fields,
        together=together, product_ids=product_ids,
        included_images=included_images,
    )
    now = utc_now()
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
        listing_fields=prepared["listing_fields"],
        status="pending",
        staged_asset_ids=[],
        created_by=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(draft)
    included_by_product = prepared.pop("included_by_product")
    for member in prepared["products"]:
        product_id = member["product_id"]
        session.add(ProductCreativeDraftProduct(
            id=new_id("pcmember"),
            workspace_id=workspace_id,
            draft_id=draft.id,
            product_id=product_id,
            position=member["position"],
            listing_fields=member["listing_fields"],
            included_images=(
                included_by_product[product_id]
                if product_id in included_by_product
                else None
            ),
            created_at=now,
        ))
    # MCP closes the session on the way out and rolls back whatever was only
    # flushed. The id this returns has to still be a row, and so do the
    # products it features.
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
        member_of = select(ProductCreativeDraftProduct.draft_id).where(
            ProductCreativeDraftProduct.workspace_id == workspace_id,
            ProductCreativeDraftProduct.product_id == product_id,
        )
        conditions.append(or_(
            ProductCreativeDraft.product_id == product_id,
            ProductCreativeDraft.id.in_(member_of),
        ))
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


def _existing_assets(session: Session, workspace_id: str, asset_ids: list[str]) -> set[str]:
    from trendrelay_api.media_models import MediaAsset

    if not asset_ids:
        return set()
    return set(session.scalars(
        select(MediaAsset.id).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(asset_ids),
        )
    ).all())


def forget_asset(session: Session, workspace_id: str, asset_id: str) -> None:
    """Drop what a deleted Library asset leaves behind in creative drafts.

    The link table declares ON DELETE CASCADE, but SQLite here does not
    enforce foreign keys, so the delete has to say it. A pending carousel
    also stops counting a card that is gone, so it owes that card again.
    Subject ids stay: a stored draft shows a removed subject as missing.
    """
    session.execute(
        delete(ProductCreativeLink).where(
            ProductCreativeLink.workspace_id == workspace_id,
            ProductCreativeLink.asset_id == asset_id,
        )
    )
    pending = session.scalars(
        select(ProductCreativeDraft).where(
            ProductCreativeDraft.workspace_id == workspace_id,
            ProductCreativeDraft.status == "pending",
        )
    ).all()
    for draft in pending:
        staged = [str(item) for item in (draft.staged_asset_ids or [])]
        if asset_id in staged:
            draft.staged_asset_ids = [item for item in staged if item != asset_id]
            draft.updated_at = utc_now()


def _write_links(session: Session, draft: ProductCreativeDraft, asset_ids: list[str]) -> None:
    """Tie every ingested asset to every product in the shot. One association each."""
    now = utc_now()
    for product_id in _member_product_ids(session, draft):
        for position, asset_id in enumerate(asset_ids):
            existing = session.scalar(
                select(ProductCreativeLink).where(
                    ProductCreativeLink.workspace_id == draft.workspace_id,
                    ProductCreativeLink.product_id == product_id,
                    ProductCreativeLink.asset_id == asset_id,
                )
            )
            if existing is not None:
                continue
            session.add(ProductCreativeLink(
                id=new_id("pclink"),
                workspace_id=draft.workspace_id,
                product_id=product_id,
                asset_id=asset_id,
                draft_id=draft.id,
                position=position,
                created_at=now,
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
    for _attempt in range(_STAGE_ATTEMPTS):
        # Another submit can land while this file is ingested, and the session
        # keeps what it read before (expire_on_commit is off). Read the row
        # again so that submit is seen rather than overwritten.
        session.refresh(draft)
        if draft.status != "pending" or _owed(draft) <= 0:
            raise ValueError(
                "This draft was filled while this file was imported. The file is in "
                f"the Library as {asset_id} and is not linked to a product."
            )
        seen = draft.updated_at
        staged = [str(item) for item in (draft.staged_asset_ids or [])]
        if asset_id not in staged:
            staged.append(asset_id)
        # A card deleted from the Library since it was staged is not part of
        # the set any more. Count only what is still there, so the draft owes
        # it again rather than linking a file that does not exist.
        present = _existing_assets(session, workspace_id, staged)
        staged = [item for item in staged if item in present]
        complete = len(staged) >= draft.card_count
        # Write only if nobody else wrote since the read: a second card that
        # finished at the same moment retries on top of this one instead of
        # replacing it, and only one writer can complete the set.
        claimed = session.execute(
            update(ProductCreativeDraft)
            .where(
                ProductCreativeDraft.id == draft.id,
                ProductCreativeDraft.status == "pending",
                ProductCreativeDraft.updated_at == seen,
            )
            .values(
                staged_asset_ids=staged,
                status="succeeded" if complete else "pending",
                updated_at=utc_now(),
            )
            .execution_options(synchronize_session=False)
        ).rowcount
        if claimed == 1:
            break
        session.rollback()
    else:
        raise ValueError(
            "This draft kept changing while this file was imported. The file is in "
            f"the Library as {asset_id} and is not linked yet. Submit it again."
        )
    if complete:
        _write_links(session, draft, staged)
    # The Library asset is already committed by ingest. The staged id has to
    # be too, or the next card opens a session that never saw this one.
    session.commit()
    session.refresh(draft)
    view = _view(session, draft)
    view["asset_id"] = asset_id
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
    """A short reading of each product's drafts, pending ones included.

    A draft that features several products is attached to every member, so
    each row can show it. `product_count` is the size of that group. Names
    and listing snapshots stay on the single-draft read.
    """
    rows = session.scalars(
        select(ProductCreativeDraft)
        .where(ProductCreativeDraft.workspace_id == workspace_id)
        .order_by(ProductCreativeDraft.updated_at.desc())
    ).all()
    membership: dict[str, list[str]] = {}
    for member in session.scalars(
        select(ProductCreativeDraftProduct)
        .where(ProductCreativeDraftProduct.workspace_id == workspace_id)
        .order_by(ProductCreativeDraftProduct.position, ProductCreativeDraftProduct.id)
    ).all():
        bucket = membership.setdefault(member.draft_id, [])
        if member.product_id not in bucket:
            bucket.append(member.product_id)
    # A group shot is named by number, oldest first, so "Group 2" is the same
    # shot on every row, band, and card however the table is filtered or
    # sorted. Drafts are never deleted, so a number does not move.
    group_numbers: dict[str, int] = {}
    for draft in sorted(rows, key=lambda item: (item.created_at, item.id)):
        if len(membership.get(draft.id) or [draft.product_id]) > 1:
            group_numbers[draft.id] = len(group_numbers) + 1
    found: dict[str, list[dict[str, Any]]] = {}
    for draft in rows:
        product_ids = membership.get(draft.id) or [draft.product_id]
        summary = {
            "id": draft.id,
            "kind": draft.kind,
            "recipe": draft.recipe,
            "status": draft.status,
            "card_count": draft.card_count,
            "owed": _owed(draft),
            "product_count": len(product_ids),
            "group_number": group_numbers.get(draft.id),
        }
        for product_id in product_ids:
            found.setdefault(product_id, []).append(summary)
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
