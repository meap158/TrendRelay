"""Writing a post's copy back - the one kind of write MCP is allowed.

It writes through the same helper the interface's own edit route uses, so a
caption an assistant writes is validated and stored exactly as one a person
types. It sets copy; it never changes a post's state, because approval is not a
write a model may make.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import CampaignQueueItem

_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def _refuse_links(field: str, text: str) -> None:
    """Refuse copy that carries a link, because the campaign carries the link.

    The context an assistant is given names the attached product and its
    affiliate URL - it has to, or the copy would sell something the post does
    not link to. Nothing said the URL was not the assistant's to write, so it
    wrote it in, and the campaign then appended its own: every caption went out
    with the link twice.

    Worse than twice, once the rotation moved on. The link baked into the words
    is whichever product was attached when the copy was written, and the one
    the campaign appends is whichever product's turn it is now - so a post
    described one thing and linked another.

    Refused rather than stripped: an assistant that is told why writes it again
    correctly, and quietly deleting part of what somebody wrote is how copy
    goes out saying something nobody chose.
    """
    found = _URL.search(text or "")
    if not found:
        return
    raise ValueError(
        f"The {field} may not contain a link ({found.group(0)}). The campaign "
        "adds the affiliate link itself, in the place each network allows - in "
        "the caption, in the first comment, or as a profile link - and adds its "
        "disclosure ahead of it. Write the words only; naming the product is "
        "right, pasting its URL is not."
    )


def write_post_copy(
    session: Session,
    workspace_id: str,
    item_id: str,
    *,
    caption: str | None = None,
    first_comment: str | None = None,
    thread: list[str] | None = None,
    hashtags: list[str] | None = None,
    title: str | None = None,
    disclosure: str | None = None,
    bio_hint: str | None = None,
    topic: str | None = None,
    post_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Set any of a post's copy fields, leaving the rest and its state alone.

    A field left as None is not touched, so an assistant filling in a caption
    does not blank a first comment the operator already wrote. Returns the
    post's updated queue view.
    """
    from trendrelay_api.campaign_autopilot_api import (
        QueueItemUpdate,
        _queue_view,
        apply_queue_item_edits,
    )

    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise LookupError(f"No queue item {item_id!r} in this workspace.")

    fields: dict[str, Any] = {}
    if caption is not None:
        if not caption.strip():
            raise ValueError("A caption cannot be empty. Write the copy or leave it unset.")
        _refuse_links("caption", caption)
        fields["body"] = caption
    if hashtags is not None:
        fields["hashtags"] = hashtags
    if first_comment is not None:
        # The same rule, and for the same reason: on a network where the link
        # lives in the first comment, the campaign appends it after these words.
        _refuse_links("first comment", first_comment)
        fields["first_comment"] = first_comment
    if thread is not None:
        for index, reply in enumerate(thread, start=1):
            _refuse_links(f"reply {index}", reply)
        fields["thread"] = thread
    if title is not None:
        fields["title"] = title
    if disclosure is not None:
        # Its own disclosure line for this post; an empty string clears the
        # override and falls the post back to the campaign's.
        fields["disclosure"] = disclosure
    if bio_hint is not None:
        # The words the campaign turns into a profile-bio line. The campaign
        # adds the link there too, so this carries the words only; an empty
        # string clears the override back to the campaign's.
        _refuse_links("bio hint", bio_hint)
        fields["bio_hint"] = bio_hint
    if topic is not None:
        # Threads' topic tag. Validated by the update model's own rule - the
        # same one Publish uses - and delivered only where the engine can
        # attach it; an empty string clears it.
        fields["topic"] = topic
    if post_types is not None:
        # Destination id -> format id. Sparse by design: omitted destinations
        # keep inheriting their campaign default.
        fields["post_type_overrides"] = post_types
    if not fields:
        raise ValueError(
            "Provide at least one of caption, first_comment, thread, hashtags, title, "
            "disclosure, bio_hint, topic or post_types."
        )

    update = QueueItemUpdate(**fields)
    apply_queue_item_edits(session, workspace_id, item.campaign_id, item, update)
    session.commit()
    return _queue_view(item)


def set_post_media(
    session: Session,
    workspace_id: str,
    item_id: str,
    asset_ids: list[str],
    append: bool = False,
    text_only: bool = False,
) -> dict[str, Any]:
    """Attach or replace a draft post's media, from Library assets.

    The other half of writing a post in two visits: `create_campaign_post`
    with no assets drafts the words, an upload brings the clip into the
    Library, and this puts the two together. One video or a set of pictures,
    the queue's own package rule.

    Drafts only. A post in the rotation is one the operator promoted with its
    media in view, and swapping what publishes underneath that decision is
    theirs to do in the app - the same line that keeps approval out of an
    assistant's hands.
    """
    from trendrelay_api.campaign_autopilot_api import (
        QueueItemUpdate,
        _queue_view,
        apply_queue_item_edits,
    )
    from trendrelay_api.integrations.mcp.intake import (
        _and_list,
        _carousel_reach,
        _media_package,
        resolve_post_assets,
    )
    from trendrelay_api.integrations.publishing import MAX_CAROUSEL_IMAGES

    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise LookupError(f"No queue item {item_id!r} in this workspace.")
    if item.state != "draft":
        raise ValueError(
            "Only a draft's media can be set from here. This post is "
            f"{item.state}; ask the operator to change its media in the app."
        )
    if text_only:
        # The deliberate no-media shape: the words are the whole post. An
        # explicit flag rather than an empty list, so "I forgot the assets"
        # and "there should be none" cannot be mistaken for each other.
        if asset_ids or append:
            raise ValueError(
                "text_only carries no assets and nothing to append. Send it "
                "alone to make this a copy-only post."
            )
        update = QueueItemUpdate(video_path="", image_paths=[], text_only=True)
        apply_queue_item_edits(session, workspace_id, item.campaign_id, item, update)
        session.commit()
        view = _queue_view(item)
        view["note"] = (
            "Now a copy-only post: it publishes as words alone. Still a "
            "draft; the operator promotes it in the app."
        )
        return view
    if not asset_ids:
        raise ValueError("Name at least one Library asset to attach.")

    assets = resolve_post_assets(session, workspace_id, asset_ids)
    media = _media_package(assets)
    if append:
        # One more picture onto the carousel, without the caller having to
        # know what is already there. Only pictures gather; a video stands
        # alone, so appending to or with one has no meaning to honour.
        if media.get("video_path"):
            raise ValueError(
                "A video cannot be appended - it stands alone. Send it "
                "without append to replace the post's media."
            )
        if item.video_path:
            raise ValueError(
                "This post holds a video, and a video stands alone. Send "
                "the new package without append to replace it."
            )
        already = list(item.image_paths or [])
        fresh = [path for path in media.get("image_paths", []) if path not in already]
        media = {"image_paths": [*already, *fresh]}

    pictures = media.get("image_paths") or []
    if len(pictures) > MAX_CAROUSEL_IMAGES:
        # Said in words. The queue's schema refuses this too, but as a
        # validation error naming a field and linking to pydantic's website -
        # which tells an assistant nothing it can act on, and is the one
        # refusal in this module that did not read like the others.
        raise ValueError(
            f"A post carries at most {MAX_CAROUSEL_IMAGES} pictures, and this "
            f"would make {len(pictures)}. Send fewer, or replace the package "
            "instead of appending to it."
        )
    update = QueueItemUpdate(
        video_path=media.get("video_path", ""),
        image_paths=media.get("image_paths", []),
        # The lead identity stays with the first picture of the carousel when
        # appending; a replacement takes the new package's own lead.
        asset_id=item.asset_id if append and item.asset_id else assets[0].id,
    )
    apply_queue_item_edits(session, workspace_id, item.campaign_id, item, update)
    session.commit()
    view = _queue_view(item)
    # The same answer `create_campaign_post` gives, because it is the same
    # question. A gallery assembled in one call was told which of the
    # campaign's accounts could carry it; one grown a picture at a time was
    # told nothing, and could pass every network's limit in silence - which is
    # precisely the flow this tool exists to serve.
    reaches, carousel_warnings = (
        _carousel_reach(session, workspace_id, item.campaign_id, len(pictures))
        if pictures
        else ([], [])
    )
    view["carousel_warnings"] = carousel_warnings
    view["note"] = (
        (
            # "cannot take it" rather than "cannot take one this long": an
            # account may be refused because the gallery outgrew its limit or
            # because its engine posts no gallery at all, and both are in this
            # list at once. Naming the wrong cause is worse than naming none.
            f"{len(pictures)} pictures now; they reach {_and_list(reaches)}, and "
            "the rest of this campaign's accounts cannot take it. "
            if carousel_warnings and reaches
            else f"No account in this campaign can take {len(pictures)} pictures, "
            "so this post has nowhere to go as it stands. "
            if carousel_warnings
            else ""
        )
        + "Media attached. The post is still a draft; the operator promotes it "
        "into the rotation in the app."
    )
    return view
