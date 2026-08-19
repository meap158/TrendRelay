"""Writing a post's copy back - the one kind of write MCP is allowed.

It writes through the same helper the interface's own edit route uses, so a
caption an assistant writes is validated and stored exactly as one a person
types. It sets copy; it never changes a post's state, because approval is not a
write a model may make.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import CampaignQueueItem


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
        fields["body"] = caption
    if hashtags is not None:
        fields["hashtags"] = hashtags
    if first_comment is not None:
        fields["first_comment"] = first_comment
    if thread is not None:
        fields["thread"] = thread
    if title is not None:
        fields["title"] = title
    if not fields:
        raise ValueError(
            "Provide at least one of caption, first_comment, thread, hashtags or title."
        )

    update = QueueItemUpdate(**fields)
    apply_queue_item_edits(session, workspace_id, item.campaign_id, item, update)
    session.commit()
    return _queue_view(item)
