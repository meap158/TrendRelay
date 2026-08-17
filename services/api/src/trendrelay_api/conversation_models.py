"""Real audience comments, collected into an inbox rather than answered by one.

A brand-owned reply planned with the post is content; a reply to a real person
is a conversation, and TrendRelay does not hold one unattended. Comments are
ingested where a provider can read them, deduplicated, classed for escalation
by rules a person can read, and put in front of an operator with room for a
suggested answer - which nothing here writes today, because no reviewed
generation provider exists and a template pretending to understand a complaint
is worse than silence.

What is deliberately absent: any way to send. There is no reply endpoint, no
reply function, and no queue that could grow one by accident. Auto-replies
stay out until the relevant provider offers authenticated comment reading,
moderation, rate limits, deduplication and identity-safe reply endpoints, all
covered by integration tests - the bar the build brief sets.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now

#: Where a message sits in the operator's queue. `escalated` is terminal until
#: a person acts: nothing automated moves a message out of it.
MESSAGE_STATES = ("new", "suggested", "answered", "dismissed", "escalated")

#: Why a message must reach a person. The classes come from the build brief's
#: always-escalate list; `none` means the rules found nothing.
ESCALATION_CLASSES = (
    "complaint", "refund", "privacy", "harassment", "regulated", "legal", "none",
)


class ConversationMessage(Base):
    """One audience comment, as the provider reported it."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        # The same comment fetched twice is one message.
        UniqueConstraint(
            "provider", "remote_comment_id", name="unique_conversation_remote"
        ),
        CheckConstraint(
            "state IN ({})".format(",".join(f"'{s}'" for s in MESSAGE_STATES)),
            name="valid_conversation_state",
        ),
        CheckConstraint(
            "escalation_class IN ({})".format(
                ",".join(f"'{c}'" for c in ESCALATION_CLASSES)
            ),
            name="valid_conversation_escalation",
        ),
        Index("ix_conversation_campaign_state", "campaign_id", "state"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("comment")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    #: The publication this comment answered, when the reader could say.
    execution_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    platform: Mapped[str | None] = mapped_column(String(24), index=True)
    remote_post_id: Mapped[str | None] = mapped_column(String(200))
    remote_comment_id: Mapped[str] = mapped_column(String(200))
    #: The commenter as the platform names them publicly. Never resolved
    #: further; this is a public handle, not an identity.
    author_handle: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(String(4000))
    language: Mapped[str | None] = mapped_column(String(16))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    state: Mapped[str] = mapped_column(String(16), default="new", index=True)
    escalation_class: Mapped[str] = mapped_column(String(16), default="none")
    #: Which rule escalated it, verbatim, so the routing can be checked.
    escalation_reason: Mapped[str | None] = mapped_column(String(300))
    #: Room for a reviewed suggestion. Nothing fills it today - see the module
    #: docstring - and a person sending any reply does so on the platform.
    suggested_reply: Mapped[str | None] = mapped_column(String(2000))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    collected_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)
