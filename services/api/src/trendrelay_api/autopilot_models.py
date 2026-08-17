"""A campaign as a standing programme rather than a folder of one-off plans.

Three tables. A campaign gains settings for running unattended; the accounts it
feeds become rows so each one can carry its own tracking link and be measured
separately; and the content it draws from becomes a queue that recycles.

The one non-obvious decision is in `CampaignDestination`: a tracking link per
destination rather than per campaign. Two accounts on two networks sharing one
link cannot be told apart afterwards, so nothing can be ranked and "post where
it converts" has no data to stand on. Splitting them is what makes the rest of
this feature possible at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now


class CampaignAutopilot(Base):
    """How one campaign posts when nobody is watching.

    Separate from `Campaign` rather than more columns on it: a campaign is a
    piece of intent that exists whether or not it ever runs unattended, and most
    of these fields are meaningless until it does.
    """

    __tablename__ = "campaign_autopilot"
    __table_args__ = (
        UniqueConstraint("campaign_id", name="unique_campaign_autopilot"),
        CheckConstraint(
            "min_recycle_days BETWEEN 1 AND 365", name="valid_autopilot_recycle"
        ),
        CheckConstraint(
            "daily_cap_per_account BETWEEN 1 AND 24", name="valid_autopilot_cap"
        ),
        CheckConstraint(
            "delivery IN ('draft','schedule','now')", name="valid_autopilot_delivery"
        ),
        CheckConstraint(
            "offer_mode IN ('smart','manual','none')", name="valid_autopilot_offer_mode"
        ),
        CheckConstraint(
            "authority IN ('assist','auto_draft','run_by_exception','autonomous')",
            name="valid_autopilot_authority",
        ),
        CheckConstraint(
            "priority IN ('reach','discussion','revenue','balanced')",
            name="valid_autopilot_priority",
        ),
        CheckConstraint(
            "max_products_per_post BETWEEN 1 AND 5",
            name="valid_autopilot_product_count",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("autopilot")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    #: Off by default, and off is the only state a new campaign can be created
    #: in. Nothing starts posting because a form was submitted.
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    #: How much this campaign may do alone. `assist` plans and holds every post
    #: for approval; `auto_draft` proceeds but only ever as engine drafts;
    #: `run_by_exception` - the recommended default - proceeds and holds only
    #: what trips a rule; `autonomous` holds nothing but the hard gates. A
    #: low-confidence product is held at every level: quality is not a policy
    #: an authority level can waive.
    authority: Mapped[str] = mapped_column(String(20), default="run_by_exception")
    #: What the campaign optimises for: reach, discussion, revenue, or a
    #: balanced blend. Ranking reads this; the campaign's prose objective is
    #: for people.
    priority: Mapped[str] = mapped_column(String(16), default="balanced")
    #: What the campaign promotes. The tracking links point at this offer's
    #: affiliate URL; without one the campaign still posts, with no link.
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_offers.id", ondelete="SET NULL"), index=True
    )
    #: Smart chooses per queue item from campaign/content evidence. Manual uses
    #: ``offer_id`` everywhere; none leaves posts non-commercial.
    offer_mode: Mapped[str] = mapped_column(String(16), default="smart", index=True)
    #: Optional shortlist for smart mode. Empty means every usable workspace
    #: offer; ids are revalidated at match time, so stale imports are harmless.
    candidate_offer_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Multiple products are useful on link-friendly networks; bio-only
    #: networks deliberately receive one primary product per post.
    max_products_per_post: Mapped[int] = mapped_column(Integer, default=2)
    #: Leads every caption. Required before an offer can be attached; the check
    #: lives in the composer, which refuses rather than posting undisclosed.
    disclosure: Mapped[str] = mapped_column(
        String(500), default="Affiliate link; we may earn a commission."
    )
    #: What the caption says on networks where no link in a post is clickable.
    bio_hint: Mapped[str] = mapped_column(String(120), default="Link in bio")
    #: How long before a queue item may be posted to the same account again.
    #: Reposting identical media too soon is what gets an account flagged.
    min_recycle_days: Mapped[int] = mapped_column(Integer, default=30)
    daily_cap_per_account: Mapped[int] = mapped_column(Integer, default=2)
    #: The whole campaign's ceiling for a rolling week, counted across every
    #: destination. None means the per-account caps are the only limit. This is
    #: the budget shape organic posting actually has - posts, not money.
    weekly_post_cap: Mapped[int | None] = mapped_column(Integer)
    #: Draft by default: the first thing a new autopilot does is fill a queue in
    #: the engine for someone to look at, not publish to a live audience.
    delivery: Mapped[str] = mapped_column(String(16), default="draft")
    #: Counted, not derived, because it drives the exploration cadence and has to
    #: survive a restart.
    posts_scheduled: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why the last tick did nothing, when it did nothing. An autopilot that is
    #: quiet for a reason should be able to say the reason.
    last_note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class CampaignDestination(Base):
    """One connected account this campaign feeds, and its own tracking link."""

    __tablename__ = "campaign_destinations"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "provider", "integration_id", name="unique_campaign_destination"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("dest")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    #: The engine that delivers this account, carried because two engines can
    #: expose different accounts under ids that only look alike.
    provider: Mapped[str] = mapped_column(String(32), index=True)
    integration_id: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(24), index=True)
    label: Mapped[str] = mapped_column(String(200))
    post_type: Mapped[str | None] = mapped_column(String(24))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: Minted once and reused, so every click from this account lands on one
    #: code and the destination can actually be measured.
    tracking_link_id: Mapped[str | None] = mapped_column(
        ForeignKey("tracking_links.id", ondelete="SET NULL")
    )
    last_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class CampaignQueueItem(Base):
    """A piece of content the campaign draws from, in order, and recycles."""

    __tablename__ = "campaign_queue_items"
    __table_args__ = (
        CheckConstraint(
            "state IN ('draft','approved','paused','retired')",
            name="valid_queue_item_state",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("queued")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    #: The approved cut. Resolved through the library at post time so a blurred
    #: version replaces the original without the queue knowing about it.
    asset_id: Mapped[str | None] = mapped_column(String(64), index=True)
    video_path: Mapped[str] = mapped_column(String(1200))
    title: Mapped[str | None] = mapped_column(String(200))
    #: Copy a person wrote. Autopilot never generates it.
    body: Mapped[str] = mapped_column(String(4000))
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Optional operator-authored content after the primary post. Affiliate
    #: links are still routed by the platform policy; these fields are the
    #: campaign's own comment/reply copy and are validated against each engine
    #: in the timeline before deployment.
    first_comment: Mapped[str | None] = mapped_column(String(2000))
    thread: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: A human pin. Empty lets smart mode choose from current evidence.
    offer_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Latest explainable matcher result, shown in Campaigns and retained so a
    #: later catalog change cannot rewrite why an approved item was promoted.
    offer_match: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Draft until approved. Autopilot only ever draws approved items and never
    #: approves one itself.
    state: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    #: Position in the rotation. A posted item is pushed to the back rather than
    #: consumed, which is what makes the campaign keep running.
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    times_posted: Mapped[int] = mapped_column(Integer, default=0)
    last_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Per destination, so an item can be due on one account and too recent on
    #: another. Keyed by destination id.
    last_posted_by_destination: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class CampaignDestinationOfferLink(Base):
    """One measurable affiliate link for a destination/product pairing."""

    __tablename__ = "campaign_destination_offer_links"
    __table_args__ = (
        UniqueConstraint(
            "destination_id", "offer_id", name="unique_destination_offer_link"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("destoffer")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_destinations.id", ondelete="CASCADE"), index=True
    )
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("product_offers.id", ondelete="CASCADE"), index=True
    )
    tracking_link_id: Mapped[str] = mapped_column(
        ForeignKey("tracking_links.id", ondelete="CASCADE"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
