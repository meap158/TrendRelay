"""The context an assistant needs to write a post's copy, read from the app.

Everything here is a read. It reuses the interface's own serializers - the queue
view, the destination view, the campaign status - so what the assistant sees is
what the operator sees, and the two cannot drift. Nothing here writes.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
    disclosure_for,
)
from trendrelay_api.models import Campaign
from trendrelay_api.opportunity_models import Product, ProductOffer

DEFAULT_COPY_PAGE_SIZE = 50
MAX_COPY_PAGE_SIZE = 250


def _placeholder_body() -> str:
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY

    return PLACEHOLDER_BODY


def _needs_copy(item: CampaignQueueItem) -> bool:
    return item.body == _placeholder_body()


def _asset_for(session: Session, item: CampaignQueueItem) -> Any | None:
    """The Library asset this post is made from, if it is still in the Library.

    A post can outlive its asset - the file is what gets published, and the
    Library row is what describes it - so this answers None rather than raising
    when the description is gone.
    """
    if not item.asset_id:
        return None
    from trendrelay_api.media_models import MediaAsset

    return session.scalar(
        select(MediaAsset).where(
            MediaAsset.id == item.asset_id,
            MediaAsset.workspace_id == item.workspace_id,
        )
    )


def _attached_media(session: Session, item: CampaignQueueItem) -> list[dict[str, Any]]:
    """This post's own attached files, in posting order, by Library id.

    A carousel is built one card at a time, and each new card has to carry on
    from the ones already on the post - the same faces, the same room, the
    same props. The cards that do that are attached to this very post and were
    checked before they landed, so they are the only pictures worth generating
    the next one beside; another post's card is another post's cast, and a
    generation that came out wrong is not evidence of anything. Until now
    nothing could name them: the queue stores file paths, and the ids
    `get_asset_thumbnails` needs live in the Library.

    So the paths are resolved back to their assets here. `asset_id` is null
    for a file that has since left the Library - the post still publishes it,
    but there is nothing to look at - and the order is the swipe order, which
    makes the last entry the card the next scene follows.
    """
    from pathlib import Path

    from trendrelay_api.media_models import MediaAsset

    paths = (
        [item.video_path] if item.video_path else list(item.image_paths or [])
    )
    paths = [path for path in paths if path]
    if not paths:
        return []
    assets = session.scalars(
        select(MediaAsset).where(
            MediaAsset.workspace_id == item.workspace_id,
            MediaAsset.original_path.in_(paths),
        )
    ).all()
    by_path = {asset.original_path: asset for asset in assets}
    attached = []
    for position, path in enumerate(paths, start=1):
        asset = by_path.get(path)
        entry: dict[str, Any] = {
            "position": position,
            "asset_id": asset.id if asset else None,
            "kind": asset.media_kind if asset else ("video" if item.video_path else "image"),
            "title": (asset.title if asset else None) or Path(path).name,
        }
        if not asset:
            entry["note"] = (
                "This file is no longer in the Library, so it cannot be "
                "fetched with get_asset_thumbnails. The post still publishes it."
            )
        attached.append(entry)
    return attached


def _asset_index(session: Session, items: list[CampaignQueueItem]) -> dict[str, Any]:
    """Every asset behind a page of posts, in one query.

    The title lookup below only reached the Library when a queue item had no
    title of its own, so most cards cost nothing. Duration always needs the
    asset, and a hundred posts needing copy would otherwise be a hundred
    queries to draw one list.
    """
    ids = {item.asset_id for item in items if item.asset_id}
    if not ids:
        return {}
    from trendrelay_api.media_models import MediaAsset

    found = session.scalars(
        select(MediaAsset).where(
            MediaAsset.id.in_(ids),
            MediaAsset.workspace_id == items[0].workspace_id,
        )
    ).all()
    return {asset.id: asset for asset in found}


def _duration_seconds(asset: Any | None) -> float | None:
    """How long the clip runs, or None when nothing knows.

    None rather than zero: a post whose asset has left the Library and a clip
    that is genuinely empty are different answers, and rounding the first to
    "0 seconds" would have an assistant write for a length nobody measured.

    Seconds rather than milliseconds because it is read by something deciding
    how much copy fits, and a tenth is finer than that decision needs.
    """
    if asset is None or asset.duration_ms is None:
        return None
    return round(asset.duration_ms / 1000, 1)


def _asset_title(
    session: Session, item: CampaignQueueItem, asset: Any | None = None
) -> str | None:
    """The clip's own name, for the assistant to describe what it is writing for.

    Prefers the queue item's title, then the Library asset's title, then the
    file's own name - a path is not a name, so the extension is dropped.

    `asset` is passed in by callers that have already loaded it, so a page of
    cards does not look the same row up twice.
    """
    candidate = ""
    if item.title and item.title.strip():
        candidate = item.title.strip()
    elif item.asset_id:
        if asset is None:
            asset = _asset_for(session, item)
        if asset and (asset.title or "").strip():
            candidate = asset.title.strip()
    if not candidate:
        path = item.video_path or (item.image_paths[0] if item.image_paths else "")
        candidate = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
    if not candidate:
        return None
    for suffix in (".mp4", ".mov", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"):
        if candidate.lower().endswith(suffix):
            candidate = candidate[: -len(suffix)]
            break
    return candidate or None


def _offer_ids_for(item: CampaignQueueItem) -> list[str]:
    """Which offers this post would carry: the operator's pins, else the
    matcher's own selection cached beside the item."""
    if item.offer_ids:
        return list(dict.fromkeys(item.offer_ids))
    match = item.offer_match or {}
    selected = match.get("selected_offer_ids") or []
    if selected:
        return list(dict.fromkeys(selected))
    # What the matcher resolved for this post, rather than the top of the
    # ranking it resolved from: taking three off the front ignored both the
    # campaign's ceiling on products per post and its rotation, so every post
    # in a batch described the same product to the assistant.
    chosen = match.get("chosen_offer_ids") or []
    if chosen:
        return list(dict.fromkeys(chosen))
    return [m.get("offer_id") for m in (match.get("matches") or []) if m.get("offer_id")][:3]


def _money(cents: int | None, currency: str | None) -> str | None:
    if cents is None:
        return None
    return f"{cents / 100:.2f} {currency or 'USD'}"


def _commission(offer: ProductOffer) -> str | None:
    parts: list[str] = []
    if offer.commission_bps is not None:
        parts.append(f"{offer.commission_bps / 100:.1f}%")
    if offer.commission_flat_cents:
        flat = _money(offer.commission_flat_cents, offer.currency)
        if flat:
            parts.append(f"+{flat}")
    return " ".join(parts) or None


def _resolve_products(session: Session, item: CampaignQueueItem) -> list[dict[str, Any]]:
    # The link the post would carry, so the assistant writes a caption that
    # earns on the product it names rather than one that mentions a different
    # thing than the one being sold.
    from trendrelay_api.campaign_autopilot_api import offer_link_url

    offer_ids = _offer_ids_for(item)
    if not offer_ids:
        return []
    offers = session.scalars(
        select(ProductOffer).where(
            ProductOffer.workspace_id == item.workspace_id,
            ProductOffer.id.in_(offer_ids),
        )
    ).all()
    by_id = {offer.id: offer for offer in offers}
    product_ids = {offer.product_id for offer in offers}
    products = session.scalars(
        select(Product).where(Product.id.in_(product_ids))
    ).all() if product_ids else []
    names = {product.id: product for product in products}
    resolved: list[dict[str, Any]] = []
    from trendrelay_api.integrations.mcp.products import product_summary

    for offer_id in offer_ids:
        offer = by_id.get(offer_id)
        if not offer:
            continue
        product = names.get(offer.product_id)
        product_context = product_summary(product) if product else {}
        resolved.append({
            **product_context,
            "offer_id": offer.id,
            "product_name": product.name if product else None,
            "brand": product.brand if product else None,
            "category": product.category if product else None,
            "network": offer.network,
            "merchant": offer.merchant,
            "price": _money(offer.price_cents, offer.currency),
            "commission": _commission(offer),
            "affiliate_link": offer_link_url(session, offer.id),
            "availability": offer.availability,
            "pinned": bool(item.offer_ids),
            "details_tool": (
                "Call get_product_details with product_id for the complete listing, "
                "gallery, variants, vouchers, campaign links and attribution."
                if product else None
            ),
        })
    return resolved


def _destinations(session: Session, campaign_id: str, workspace_id: str) -> list[Any]:
    return list(session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == campaign_id,
            CampaignDestination.workspace_id == workspace_id,
            CampaignDestination.enabled.is_(True),
        )
    ).all())


def _follow_up_landing(destinations: list[Any]) -> dict[str, Any]:
    """Where a first comment / thread reply would actually land, per destination.

    A follow-up delivers on some networks and through some engines. When an account
    is configured with link_placement='none' (No affiliate link), first comments are
    optional and explicitly accepted to carry supplementary information (e.g. styling, sizing,
    product details, care instructions, or engagement prompts — not affiliate links)
    and are preserved with the post.
    """
    from trendrelay_api.integrations.publishing import (
        first_comment_deliverable,
        follow_up_kind,
    )

    landings: list[dict[str, Any]] = []
    any_deliverable = False
    for dest in destinations:
        is_none = getattr(dest, "link_placement", None) == "none"
        engine_deliverable = first_comment_deliverable(dest.provider, dest.platform)
        deliverable = engine_deliverable or is_none
        any_deliverable = any_deliverable or deliverable
        landing: dict[str, Any] = {
            "platform": dest.platform,
            "provider": dest.provider,
            "follow_up_kind": follow_up_kind(dest.platform),
            "deliverable": deliverable,
            "accepts_first_comment": is_none or engine_deliverable,
            "first_comment_optional": is_none,
        }
        if is_none:
            landing["link_placement"] = "none"
            landing["note"] = (
                "Link placement is set to 'No affiliate link' ('none'). First comment is "
                "optional and explicitly accepted for supplementary information (e.g. sizing, "
                "styling tips, product notes, or engagement prompts — not affiliate links) "
                "and is stored with the post."
            )
        landings.append(landing)
    return {"any_deliverable": any_deliverable, "per_destination": landings}


_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def _posting_schedule(session: Session, workspace_id: str) -> list[str]:
    """When this workspace posts, as readable phrases like 'Every day 09:00'.

    The times are the workspace's, not the post's - a queued post is recycled
    content that goes out on the next open slot - but they are what an assistant
    needs to know the campaign is daily at nine rather than a one-off.
    """
    from trendrelay_api.models import PublishingSlot

    slots = session.scalars(
        select(PublishingSlot)
        .where(PublishingSlot.workspace_id == workspace_id)
        .order_by(PublishingSlot.hour, PublishingSlot.minute)
    ).all()
    phrases: list[str] = []
    for slot in slots:
        day = "Every day" if slot.weekday < 0 else _WEEKDAYS[slot.weekday]
        phrases.append(f"{day} {slot.hour:02d}:{slot.minute:02d}")
    return phrases


#: How each resolved link placement reads in a sentence, so the composed
#: "where it posts" line says where the affiliate link actually goes.
_LINK_PLACEMENT_PHRASE = {
    "first_comment": "link in the first comment",
    "caption": "link in the caption",
    "bio": "link in bio",
    "description": "link in the description",
    "none": "no clickable link",
}


def _destination_summary(view: dict[str, Any]) -> str:
    """One line, like 'Facebook · Video · link in the first comment'.

    The raw pieces are all in the destination view; this composes them so the
    assistant does not have to, and so 'where it would go' reads the same way
    the interface says it.
    """
    parts = [str(view.get("provider_label") or view.get("platform") or "a platform")]
    post_type = view.get("effective_post_type") or view.get("resolved_post_type")
    if post_type:
        parts.append(str(post_type).replace("_", " ").title())
    placement = view.get("link_placement")
    if placement:
        parts.append(_LINK_PLACEMENT_PHRASE.get(placement, f"link: {placement}"))
    return " · ".join(parts)


def list_campaigns(session: Session, workspace_id: str) -> list[dict[str, Any]]:
    """Every campaign in the workspace, with how many posts still need copy.

    And whether it has anywhere to put pictures. A campaign is chosen by name,
    and a name does not say whether its accounts can carry a gallery - so an
    assistant asked to file images somewhere would otherwise learn the answer
    by uploading them first and being refused.
    """
    from trendrelay_api.autopilot_models import CampaignDestination
    from trendrelay_api.integrations.publishing import (  # noqa: PLC0415
        carousel_fits_destination,
    )

    campaigns = session.scalars(
        select(Campaign).where(Campaign.workspace_id == workspace_id)
    ).all()
    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.workspace_id == workspace_id,
            CampaignDestination.enabled.is_(True),
        )
    ).all()
    # One picture, because this answers "anywhere at all" rather than "all
    # twenty of these" - the count is a property of the post, and the post does
    # not exist yet.
    carries: dict[str, bool] = {}
    for destination in destinations:
        fits, _why = carousel_fits_destination(
            destination.provider, destination.platform, 1
        )
        carries[destination.campaign_id] = carries.get(
            destination.campaign_id, False
        ) or fits
    placeholder = _placeholder_body()
    result: list[dict[str, Any]] = []
    for campaign in campaigns:
        needs = session.scalars(
            select(CampaignQueueItem.id).where(
                CampaignQueueItem.campaign_id == campaign.id,
                CampaignQueueItem.workspace_id == workspace_id,
                CampaignQueueItem.body == placeholder,
            )
        ).all()
        result.append({
            "campaign_id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "objective": campaign.objective,
            "posts_needing_copy": len(needs),
            # False for a campaign with no accounts yet too: it has nowhere to
            # post anything, and saying "yes" of a campaign pointed at nothing
            # would be a promise about accounts nobody has connected.
            "accepts_carousel": carries.get(campaign.id, False),
        })
    return result


def posts_with_notes(session: Session, item_ids: list[str]) -> set[str]:
    """Which of these posts carry working notes, in one query - see the queue's
    own copy of this question, which this is."""
    from trendrelay_api.campaign_autopilot_api import (  # noqa: PLC0415
        posts_with_notes as _asked,
    )

    return _asked(session, item_ids)


def _post_summary(
    session: Session,
    item: CampaignQueueItem,
    campaign: Campaign | None,
    asset: Any | None = None,
    *,
    has_context: bool = False,
) -> dict[str, Any]:
    products = _resolve_products(session, item)
    return {
        "item_id": item.id,
        "campaign_id": item.campaign_id,
        "campaign_name": campaign.name if campaign else None,
        "media_kind": _item_media_kind(item),
        # What it holds and what it is waiting for. `media_kind` is the shape,
        # and a carousel is a carousel at its third card as at its eighth, so
        # the count is what says whether the set is finished. `media_target`
        # is null where the post names no number - there anything attached
        # finishes it, the rule that predates targets.
        "media_count": item.media_count,
        "media_target": item.media_target,
        "media_complete": item.media_is_complete,
        "video_title": _asset_title(session, item, asset),
        # How long there is to say it. A seven-second cut wants its hook in the
        # first word and a minute-long one can breathe, and the assistant was
        # being asked to write for a clip whose length it could not learn.
        "duration_seconds": _duration_seconds(asset),
        "products": [
            {"product_name": p["product_name"], "commission": p["commission"]}
            for p in products
        ],
        "has_caption": not _needs_copy(item),
        "has_first_comment": bool(item.first_comment),
        "thread_replies": len(item.thread or []),
        # Whether a previous pass left working notes on this post, so a listing
        # can be scanned for the ones that carry reasoning without opening each
        # in turn. `get_post_context` reads what they say. Passed in rather than
        # read off the item: the column is deferred, and a listing that touched
        # it would fetch paragraphs a row at a time.
        "has_context": has_context,
    }


def list_posts_needing_copy(
    session: Session,
    workspace_id: str,
    campaign_id: str | None = None,
    *,
    limit: int = DEFAULT_COPY_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """The posts an assistant should help with: queued, but no caption written.

    A bounded page of compact cards - what each clip is and what it sells - so
    the assistant can choose work without filling its context window, then call
    `get_post_context` for the full picture. Pagination metadata says exactly
    whether and where another page begins.
    """
    if not 1 <= limit <= MAX_COPY_PAGE_SIZE:
        raise ValueError(
            f"limit must be between 1 and {MAX_COPY_PAGE_SIZE}; received {limit}."
        )
    if offset < 0:
        raise ValueError(f"offset must be zero or greater; received {offset}.")

    placeholder = _placeholder_body()
    conditions = (
        CampaignQueueItem.workspace_id == workspace_id,
        CampaignQueueItem.body == placeholder,
    )
    if campaign_id:
        conditions += (CampaignQueueItem.campaign_id == campaign_id,)
    total = session.scalar(
        select(func.count(CampaignQueueItem.id)).where(*conditions)
    ) or 0
    items = list(session.scalars(
        select(CampaignQueueItem)
        .where(*conditions)
        # Position is only unique within a campaign. The stable tie-breakers
        # keep page boundaries from moving between otherwise identical calls.
        .order_by(
            CampaignQueueItem.campaign_id,
            CampaignQueueItem.position,
            CampaignQueueItem.id,
        )
        .offset(offset)
        .limit(limit)
    ).all())
    campaign_ids = {item.campaign_id for item in items}
    campaigns = {
        c.id: c for c in session.scalars(
            select(Campaign).where(
                Campaign.workspace_id == workspace_id,
                Campaign.id.in_(campaign_ids),
            )
        ).all()
    } if campaign_ids else {}
    assets = _asset_index(session, list(items))
    noted = posts_with_notes(session, [item.id for item in items])
    posts = [
        _post_summary(
            session, item, campaigns.get(item.campaign_id),
            assets.get(item.asset_id) if item.asset_id else None,
            has_context=item.id in noted,
        )
        for item in items
    ]
    more = offset + len(posts) < total
    return {
        "posts": posts,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(posts),
        "more": more,
        "next_offset": offset + len(posts) if more else None,
    }


POST_STATES = ("draft", "approved", "paused", "retired")
POST_MEDIA_KINDS = ("video", "carousel", "text only", "none yet")
#: What `media` may narrow by. The shapes above, plus the backlog itself.
#:
#: "none yet" is literal - nothing attached - and stopped being the whole
#: backlog once a post could say how much media it is waiting for: a carousel
#: briefed as eight cards and holding three is a `carousel` by shape and still
#: unpublishable. "unfinished" is that question asked directly, and is the
#: filter a pass filling media should work from.
POST_MEDIA_FILTERS = (*POST_MEDIA_KINDS, "unfinished")


def _item_media_kind(item: CampaignQueueItem) -> str:
    return (
        "carousel" if item.image_paths
        else "video" if item.video_path
        else "text only" if item.text_only
        else "none yet"
    )


def list_campaign_posts(
    session: Session,
    workspace_id: str,
    campaign_id: str | None = None,
    *,
    state: str | None = None,
    media: str | None = None,
    search: str | None = None,
    limit: int = DEFAULT_COPY_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """Every post in the queue, whatever its state - so none is ever lost.

    The listing that makes ids recoverable: a post written words-first in an
    earlier conversation can be found again by its caption and given media,
    instead of being reachable only while the id from its create call is still
    at hand. Same compact cards and pagination as `list_posts_needing_copy`,
    plus each post's state, a caption excerpt to recognise it by, and the slot
    it is locked to if any.

    `state` and `media` narrow on different things, and reading one as the
    other is the mistake this docstring exists to head off: `approved` says a
    person accepted the words, not that the post has media. A post drafted
    words-first is routinely approved while `media_kind` is still `none yet` -
    there was no media to decide on - and the scheduler passes over it every
    tick exactly as it passes over an empty draft. The queue of posts waiting
    for media is therefore `media="none yet"` on its own; narrowing it by
    `state="draft"` as well hides the approved half of the same backlog.
    """
    if not 1 <= limit <= MAX_COPY_PAGE_SIZE:
        raise ValueError(
            f"limit must be between 1 and {MAX_COPY_PAGE_SIZE}; received {limit}."
        )
    if offset < 0:
        raise ValueError(f"offset must be zero or greater; received {offset}.")
    if state is not None and state not in POST_STATES:
        raise ValueError(
            f"state must be one of {', '.join(POST_STATES)}; received {state!r}."
        )
    if media is not None and media not in POST_MEDIA_FILTERS:
        raise ValueError(
            f"media must be one of {', '.join(POST_MEDIA_FILTERS)}; "
            f"received {media!r}."
        )

    conditions = (CampaignQueueItem.workspace_id == workspace_id,)
    if campaign_id:
        conditions += (CampaignQueueItem.campaign_id == campaign_id,)
    if state:
        conditions += (CampaignQueueItem.state == state,)
    items = list(session.scalars(
        select(CampaignQueueItem)
        .where(*conditions)
        .order_by(
            CampaignQueueItem.campaign_id,
            CampaignQueueItem.position,
            CampaignQueueItem.id,
        )
    ).all())
    # Media shape and caption text live in JSON and free text, so these two
    # narrow in Python; the page and its total describe the narrowed list.
    if media == "unfinished":
        items = [item for item in items if not item.media_is_complete]
    elif media:
        items = [item for item in items if _item_media_kind(item) == media]
    if search and search.strip():
        needle = search.strip().lower()
        placeholder = _placeholder_body()
        items = [
            item for item in items
            if needle in (item.body if item.body != placeholder else "").lower()
            or needle in (item.title or "").lower()
        ]
    total = len(items)
    page = items[offset:offset + limit]

    campaign_ids = {item.campaign_id for item in page}
    campaigns = {
        c.id: c for c in session.scalars(
            select(Campaign).where(
                Campaign.workspace_id == workspace_id,
                Campaign.id.in_(campaign_ids),
            )
        ).all()
    } if campaign_ids else {}
    assets = _asset_index(session, list(page))
    noted = posts_with_notes(session, [item.id for item in page])
    placeholder = _placeholder_body()
    posts = []
    for item in page:
        summary = _post_summary(
            session, item, campaigns.get(item.campaign_id),
            assets.get(item.asset_id) if item.asset_id else None,
            has_context=item.id in noted,
        )
        summary["state"] = item.state
        caption = item.body if item.body != placeholder else ""
        summary["caption_preview"] = (
            caption[:157] + "..." if len(caption) > 160 else caption
        ) or None
        summary["locked_slot"] = (
            # Stamped UTC: SQLite returns the stored moment naive, and a bare
            # ISO string reads as local time to whoever parses it.
            (
                item.pinned_slot.replace(tzinfo=UTC)
                if item.pinned_slot.tzinfo is None
                else item.pinned_slot
            ).isoformat()
            if item.pinned_slot
            else None
        )
        posts.append(summary)
    more = offset + len(posts) < total
    return {
        "posts": posts,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(posts),
        "more": more,
        "next_offset": offset + len(posts) if more else None,
    }


def get_campaign_config(session: Session, workspace_id: str, campaign_id: str) -> dict[str, Any]:
    """The campaign's own brief and posting configuration, for tone and rules.

    Objective, audience, markets and languages come from the campaign; the
    disclosure line, product mode, link placement default and cadence come from
    its autopilot - the same status the interface shows.
    """
    campaign = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id, Campaign.workspace_id == workspace_id
        )
    )
    if not campaign:
        raise LookupError(f"No campaign {campaign_id!r} in this workspace.")
    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
    )
    config: dict[str, Any] = {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "objective": campaign.objective,
        "audience": campaign.audience,
        "markets": list(campaign.markets or []),
        "languages": list(campaign.languages or []),
    }
    if autopilot is not None:
        from trendrelay_api.campaign_scheduler import campaign_status

        status = campaign_status(session, autopilot)
        for key in (
            "post_language", "disclosure", "bio_hint", "offer_mode",
            "max_products_per_post", "authority", "delivery",
        ):
            config[key] = status.get(key)
    return config


def get_post_context(session: Session, workspace_id: str, item_id: str) -> dict[str, Any]:
    """Everything needed to write one post's copy, in one call.

    The clip and what it is, the attached product and what it pays, every
    destination the post will reach and where a follow-up lands there, the
    campaign's brief, and whatever copy already exists - so the assistant writes
    into the gaps rather than over the operator.
    """
    from types import SimpleNamespace

    from trendrelay_api.campaign_autopilot_api import _destination_view, _queue_view
    from trendrelay_api.campaign_runner import _post_type_for

    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise LookupError(f"No queue item {item_id!r} in this workspace.")
    campaign = session.scalar(
        select(Campaign).where(Campaign.id == item.campaign_id)
    )
    asset = _asset_for(session, item)
    destinations = _destinations(session, item.campaign_id, workspace_id)
    follow_up = _follow_up_landing(destinations)
    # Composed here so every destination carries a one-line "where it posts"
    # alongside its raw fields.
    destination_views = [_destination_view(session, d) for d in destinations]
    for view in destination_views:
        configured = (item.post_type_overrides or {}).get(
            str(view["id"]), view.get("resolved_post_type")
        )
        view["effective_post_type"] = _post_type_for(SimpleNamespace(
            media_path=item.video_path,
            image_paths=item.image_paths,
            platform=view["platform"],
            post_type=configured,
        ))
        view["posts_to"] = _destination_summary(view)
    # The disclosure this post actually carries - its own override, or the
    # campaign's - resolved once so a caption is not written without it.
    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == item.campaign_id)
    )
    effective_disclosure = (
        disclosure_for(item, autopilot) if autopilot else (item.disclosure or None)
    )
    from trendrelay_api.integrations.publishing import topic_deliverable

    # Which of this post's destinations can carry a Threads topic tag: one per
    # post, no leading #, up to 50 characters. Said here so an assistant can
    # offer one where it lands and not where it would be dropped.
    topic_reach = sorted({
        d.platform for d in destinations if topic_deliverable(d.provider, d.platform)
    })
    has_none_link_placement = any(
        getattr(d, "link_placement", None) == "none" for d in destinations
    )
    engine_delivers_first_comment = any(
        d["deliverable"] and not d.get("first_comment_optional")
        for d in follow_up["per_destination"]
    )
    missing = {
        "caption": _needs_copy(item),
        "first_comment": item.first_comment is None and engine_delivers_first_comment,
        "thread": not (item.thread or []) and any(
            d["follow_up_kind"] == "reply in the thread" and d["deliverable"]
            for d in follow_up["per_destination"]
        ),
        # The media, for a post drafted words-first over MCP: attach it with
        # set_post_media once its upload lands. A copy-only post is whole
        # without any - text_only was its author's decision - and a post that
        # says how many files it is waiting for is still missing them at three
        # of eight, which is the same wait this line has always described.
        "media": not item.media_is_complete,
        "topic": item.topic is None and bool(topic_reach),
    }
    return {
        "item_id": item.id,
        "campaign": get_campaign_config(session, workspace_id, item.campaign_id)
        if campaign else None,
        "media_kind": _item_media_kind(item),
        # The brief's number, where the post carries one, beside what it holds
        # now - so a pass picking this post up knows whether it is filling an
        # empty post or finishing a part-filled one.
        "media_count": item.media_count,
        "media_target": item.media_target,
        "media_complete": item.media_is_complete,
        # What this post already holds, in posting order and by Library id, so
        # the next card can be made beside the cards it has to match. Pass any
        # of these ids to get_asset_thumbnails to look at them; the last is
        # the one the next scene follows.
        "attached_media": _attached_media(session, item),
        "video_title": _asset_title(session, item, asset),
        #: None when the asset has left the Library or never carried a
        #: duration - which is a different answer from a clip of no length.
        "duration_seconds": _duration_seconds(asset),
        "products": _resolve_products(session, item),
        "topic": item.topic,
        "topic_deliverable_on": topic_reach,
        # The lock, when one is set: this post waits for exactly this moment
        # instead of flowing with the rotation. Set or released with
        # pin_post_slot; read the day's openings with get_day_slots.
        "locked_slot": (
            # Stamped UTC: SQLite returns the stored moment naive, and a bare
            # ISO string reads as local time to whoever parses it.
            (
                item.pinned_slot.replace(tzinfo=UTC)
                if item.pinned_slot.tzinfo is None
                else item.pinned_slot
            ).isoformat()
            if item.pinned_slot
            else None
        ),
        "destinations": destination_views,
        "follow_up_landing": follow_up,
        # The workspace's posting times, so the assistant knows the cadence the
        # copy is written for.
        "schedule": _posting_schedule(session, workspace_id),
        # What this post carries, resolved once. Not for the caption: the
        # campaign puts the disclosure at the head of it and the link where
        # the network allows one, so copy that includes either duplicates it.
        "effective_disclosure": effective_disclosure,
        "added_by_the_campaign": {
            "disclosure": effective_disclosure,
            "product_links": [
                product["affiliate_link"]
                for product in _resolve_products(session, item)
                if product.get("affiliate_link")
            ],
            "note": (
                "Written into the post by the campaign, per network. Do not put "
                "these in the caption, first comment or replies - name the "
                "product in words instead. Copy containing a link is refused."
            ),
            "first_comment_guidance": (
                "Link placement is set to 'No affiliate link' ('none'). First comment is "
                "optional and explicitly accepted for supplementary information (styling tips, "
                "sizing, fabric/material details, care instructions, or engagement prompts). "
                "Do not include URLs."
                if has_none_link_placement else None
            ),
        },
        "accepts_first_comment": follow_up["any_deliverable"],
        "first_comment_optional": has_none_link_placement,
        "current_copy": {
            "caption": None if _needs_copy(item) else item.body,
            "hashtags": list(item.hashtags or []),
            "first_comment": item.first_comment,
            "thread": list(item.thread or []),
            "title": item.title,
        },
        "needs": missing,
        "queue_item": _queue_view(item, with_context=True),
    }


#: What "interactions" means when a caller does not say.
#:
#: Deliberately not views. A view is what the network chose to show the post to;
#: likes, comments, shares and saves are what a person did about it, and an
#: assistant looking for a post worth imitating wants the second. Views remain
#: sortable by name for anyone who wants reach instead.
INTERACTION_FIELDS = ("likes", "comments", "shares", "saves")

#: Every figure a post can carry, so a caller can sort by any of them.
SORTABLE_METRICS = ("interactions", "views", *INTERACTION_FIELDS, "watch_seconds")


def _is_measurable(provider: str) -> bool:
    """Whether the engine behind a stored connection id can report engagement.

    Resolved through the same hook the collector uses, so this cannot claim a
    post is pending measurement that no pass will ever measure. An id that
    resolves to nothing is reported as unmeasurable, which is the truthful
    answer for a connection that has been removed.
    """
    # The registry is filled by the publishing module at import time, so asking
    # it before that module has loaded answers "nothing can be measured" for
    # every engine. Importing it here makes the answer independent of whatever
    # else this process happened to touch first - the same import-order
    # assumption, left implicit, is what once had a whole engine's posts
    # skipped silently.
    from trendrelay_api.campaign_measurement import (
        PROVIDER_ENGINE_RESOLVER,
        PROVIDER_METRIC_READERS,
    )
    from trendrelay_api.integrations import publishing  # noqa: F401

    return (PROVIDER_ENGINE_RESOLVER(provider) or "") in PROVIDER_METRIC_READERS


def _interactions(metrics: dict[str, float]) -> float:
    return sum(float(metrics.get(field) or 0) for field in INTERACTION_FIELDS)


def list_published_posts(
    session: Session,
    workspace_id: str,
    campaign_id: str | None = None,
    platform: str | None = None,
    sort_by: str = "interactions",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Posts that went out, what they got, and the copy that got it.

    The reason this exists: an assistant asked to write a caption has the brief
    and the product, and no idea which of five hundred posts already worked.
    "Write another like the ones that did well" is the request, and nothing in
    this catalogue could answer it.

    So each entry carries the copy as it went out - caption, first comment,
    thread, hashtags, the products it linked - beside the figures it earned, and
    a permalink to read the real thing.

    Measurement is reported rather than assumed, in three states rather than
    two. A post nobody has read back yet says `measured: false` and carries no
    figures at all, instead of zeros that would sort it alongside a post that
    genuinely got nothing. A post whose engine cannot report engagement at all
    additionally says `measurable: false`, because "not yet" and "not ever" lead
    to different conclusions: an assistant told the first may reasonably wait
    and ask again, and one told the second should stop treating the silence as
    a pending answer - or as evidence the post did badly.
    """
    from trendrelay_api.campaign_measurement import latest_metrics
    from trendrelay_api.publication_models import PublicationExecution

    if sort_by not in SORTABLE_METRICS:
        raise ValueError(
            f"Sort by one of {', '.join(SORTABLE_METRICS)}; got {sort_by!r}."
        )
    query = select(PublicationExecution).where(
        PublicationExecution.workspace_id == workspace_id,
        PublicationExecution.state.in_(("published", "measured")),
    )
    if campaign_id:
        query = query.where(PublicationExecution.campaign_id == campaign_id)
    if platform:
        query = query.where(PublicationExecution.platform == platform)

    names = {
        row.id: row.name
        for row in session.scalars(
            select(Campaign).where(Campaign.workspace_id == workspace_id)
        ).all()
    }
    offers = _offer_names(session, workspace_id)

    executions = session.scalars(query).all()
    clips = _clip_index(session, workspace_id, executions)

    posts: list[dict[str, Any]] = []
    for execution in executions:
        metrics = latest_metrics(execution)
        measured = bool(metrics)
        posts.append({
            "execution_id": execution.id,
            "campaign": names.get(execution.campaign_id or ""),
            "campaign_id": execution.campaign_id,
            "platform": execution.platform,
            # The account in words. The stored provider is a connection id and
            # reads as one; nobody named their login `zernio-zernio-2`.
            "account": execution.destination_label,
            "post_type": execution.post_type,
            "published_at": (
                execution.published_at.isoformat() if execution.published_at else None
            ),
            "permalink": next(iter(execution.permalinks or []), None),
            # The copy exactly as it went out, which is the point of the tool.
            "caption": execution.caption,
            "first_comment": execution.first_comment,
            "thread": list(execution.thread or []),
            "link_placement": execution.placement,
            "products": [offers.get(offer_id, offer_id) for offer_id in execution.offer_ids or []],
            "media_kind": "images" if execution.image_paths else "video",
            # Which clip earned this. Without it the whole list is unusable for
            # the thing it exists for: an assistant told a post took 1,133
            # views can read the copy that earned them and has no way to say
            # which video it was, or to find it again.
            #
            # The Library id as well as the name, because the name is for a
            # person to recognise and the id is what `list_library_assets`
            # and `create_campaign_post` take.
            **_clip_of(execution, clips),
            "measured": measured,
            # Whether figures could ever arrive for this post, which is a fact
            # about the engine that published it rather than about the post.
            "measurable": _is_measurable(execution.provider or ""),
            "interactions": _interactions(metrics) if measured else None,
            "metrics": metrics or None,
        })

    # Unmeasured posts sort last whatever the key, because "not read yet" is not
    # a score of zero and must never outrank a post that earned something.
    posts.sort(
        key=lambda post: (
            post["measured"],
            _interactions(post["metrics"] or {}) if sort_by == "interactions"
            else float((post["metrics"] or {}).get(sort_by) or 0),
            post["published_at"] or "",
        ),
        reverse=True,
    )
    return posts[:max(1, min(limit, 100))]


def _clip_index(
    session: Session, workspace_id: str, executions: list[Any]
) -> dict[str, Any]:
    """Every Library asset behind a page of published posts, in one query.

    A post can outlive its asset - the file is what went out and the Library
    row is what describes it - so a missing row is a name this cannot give
    rather than an error.
    """
    ids = {execution.asset_id for execution in executions if execution.asset_id}
    if not ids:
        return {}
    from trendrelay_api.media_models import MediaAsset

    found = session.scalars(
        select(MediaAsset).where(
            MediaAsset.id.in_(ids), MediaAsset.workspace_id == workspace_id
        )
    ).all()
    return {asset.id: asset for asset in found}


def _clip_of(execution: Any, clips: dict[str, Any]) -> dict[str, Any]:
    """What this post was made of, named so somebody can find it again."""
    asset = clips.get(execution.asset_id or "")
    name = ""
    if asset is not None and (asset.title or "").strip():
        name = asset.title.strip()
    else:
        # The file's own name when the Library no longer describes it: a post
        # outlives its asset, and the path is still there.
        path = execution.media_path or (
            execution.image_paths[0] if execution.image_paths else ""
        )
        name = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
    # Trimmed however it was found, which is what `_asset_title` does for a
    # post that still needs copy. Two tools describing one clip have to name it
    # the same way, or an assistant reading both cannot tell it is one clip.
    for suffix in (".mp4", ".mov", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return {
        "video_title": name or None,
        "asset_id": execution.asset_id,
        "duration_seconds": _duration_seconds(asset),
        # How many pictures a carousel carried, and nothing for a video.
        "image_count": len(execution.image_paths) if execution.image_paths else None,
    }


#: Thumbnails are the small JPEGs the Library cards use; a real one is tens of
#: kilobytes. The cap guards against a mislabelled row handing an original to
#: a channel where every byte is base64 inside somebody's context window.
THUMBNAIL_BYTES_LIMIT = 2 * 1024 * 1024
#: Each still is an inline image in the conversation. A handful shows the
#: posts being studied; a bigger batch is the flood the per-asset design
#: exists to avoid, so it is refused with the number rather than served.
MAX_THUMBNAILS_PER_CALL = 8


def get_asset_thumbnails(
    session: Session, workspace_id: str, asset_ids: list[str]
) -> list[dict[str, Any]]:
    """Library thumbnails for a handful of assets, misses named in words.

    The companion to every listing that names an `asset_id` - the ranked
    published posts, the Library listing, the needs-copy queue. Those stay
    compact text on purpose; this fetches the stills for the assets actually
    being studied, in one call for a top-three and never more than
    MAX_THUMBNAILS_PER_CALL. One entry per requested id, in the order asked;
    an id with nothing to show carries a `note` saying why instead of failing
    the ids beside it. For a video the still is a representative frame; for a
    picture, a small copy.
    """
    from pathlib import Path

    from trendrelay_api.media_models import MediaAsset, MediaAssetVersion

    ids = list(dict.fromkeys(value.strip() for value in asset_ids if value.strip()))
    if not ids:
        raise ValueError("Name at least one asset_id.")
    if len(ids) > MAX_THUMBNAILS_PER_CALL:
        raise ValueError(
            f"Ask for at most {MAX_THUMBNAILS_PER_CALL} thumbnails per call - "
            "each is an inline image, and a bigger batch floods the "
            "conversation this is read in."
        )

    assets = {
        asset.id: asset
        for asset in session.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_(ids), MediaAsset.workspace_id == workspace_id
            )
        ).all()
    }
    # Newest still per asset, chosen here: the version table may hold an older
    # regenerated still beside the current one.
    stills: dict[str, Any] = {}
    for version in session.scalars(
        select(MediaAssetVersion).where(
            MediaAssetVersion.asset_id.in_(ids),
            MediaAssetVersion.version_kind == "thumbnail",
        )
    ).all():
        held = stills.get(version.asset_id)
        if held is None or version.created_at > held.created_at:
            stills[version.asset_id] = version

    entries: list[dict[str, Any]] = []
    for asset_id in ids:
        asset = assets.get(asset_id)
        entry: dict[str, Any] = {
            "asset_id": asset_id,
            "title": (asset.title or "").strip() if asset else "",
            "data": None,
            "mime": "",
            "note": None,
        }
        version = stills.get(asset_id)
        if not asset:
            entry["note"] = f"No asset {asset_id!r} in this workspace."
        elif not version:
            entry["note"] = (
                "No thumbnail still yet - the media worker makes one shortly "
                "after import. Ask again in a moment."
            )
        else:
            try:
                path = Path(version.path).resolve(strict=True)
                oversized = path.stat().st_size > THUMBNAIL_BYTES_LIMIT
            except OSError:
                entry["note"] = "The thumbnail file is unavailable."
            else:
                if oversized:
                    entry["note"] = (
                        "This still is larger than a thumbnail should be and "
                        "is not sent inline. View it in the Library instead."
                    )
                else:
                    entry["data"] = path.read_bytes()
                    entry["mime"] = version.mime_type or "image/jpeg"
        entries.append(entry)
    return entries


def _offer_names(session: Session, workspace_id: str) -> dict[str, str]:
    """Offer ids to product names, so a post says what it sold."""

    rows = session.execute(
        select(ProductOffer.id, Product.name)
        .join(Product, Product.id == ProductOffer.product_id)
        .where(ProductOffer.workspace_id == workspace_id)
    ).all()
    return {offer_id: name for offer_id, name in rows}
