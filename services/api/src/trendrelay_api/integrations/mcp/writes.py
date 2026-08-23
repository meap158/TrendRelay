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
    if not fields:
        raise ValueError(
            "Provide at least one of caption, first_comment, thread, hashtags, title, "
            "disclosure or bio_hint."
        )

    update = QueueItemUpdate(**fields)
    apply_queue_item_edits(session, workspace_id, item.campaign_id, item, update)
    session.commit()
    return _queue_view(item)
