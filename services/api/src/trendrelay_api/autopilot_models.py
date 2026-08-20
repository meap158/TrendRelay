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
    #: How much this campaign may do alone.
    #:
    #: Read `_hold_reason` before trusting any summary of this, including the
    #: one that used to be here. Approval before an engine is the pipeline's
    #: rule rather than one level's setting: *every* level below `autonomous`
    #: holds every frozen post for a person, `run_by_exception` included. What
    #: the levels below it change is what is prepared and how - `auto_draft`
    #: only ever hands over engine drafts - not whether a person is asked.
    #:
    #: `autonomous` is the one level that posts without a person, and it is
    #: earned through `graduation_block` rather than chosen. A low-confidence
    #: product is held even there: quality is not a policy any level waives.
    authority: Mapped[str] = mapped_column(String(20), default="run_by_exception")
    #: What the campaign optimises for: reach, discussion, revenue, or a
    #: balanced blend. Ranking reads this; the campaign's prose objective is
    #: for people.
    priority: Mapped[str] = mapped_column(String(16), default="balanced")
    #: The language composed scaffolding speaks - the disclosure default, the
    #: bio hint, product labels. The operator's own copy is always their own;
    #: this governs only what the autopilot writes around it.
    post_language: Mapped[str] = mapped_column(String(16), default="en")
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
    #: Whether a disclosure is added at all.
    #:
    #: Off by default, by the operator's decision. The wording below is still
    #: kept and still leads the caption the moment this is switched on, so
    #: turning it off is not the same as clearing it.
    #:
    #: What it turns off is a legal safeguard, not a preference: the FTC's
    #: endorsement guides ask for a disclosure near the endorsement and no
    #: later than the link, and TikTok, Meta and YouTube each require paid
    #: promotion to be marked in their own terms. Whoever owns the account
    #: carries that, which is why this is a setting and not a default.
    disclose: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Leads every caption when `disclose` is on. Kept whether or not it is:
    #: switching disclosure off should not lose the wording somebody wrote.
    disclosure: Mapped[str] = mapped_column(
        String(500), default="Affiliate link; we may earn a commission."
    )
    #: What the caption says on networks where no link in a post is clickable.
    bio_hint: Mapped[str] = mapped_column(String(120), default="Link in bio")
    #: How long before a queue item may be posted to the same account again.
    #: Reposting identical media too soon is what gets an account flagged.
    #: How long before the same post may return to the same account, when
    #: the campaign allows it to return at all.
    min_recycle_days: Mapped[int] = mapped_column(Integer, default=30)
    #: Whether a post may go out more than once on the same account.
    #:
    #: Off by default. The queue used to be a carousel - every item came back
    #: once it had rested - which is one legitimate way to run a campaign, but
    #: it was the only way and nobody chose it, so the same video and caption
    #: returned to the same audience on a timer nobody set. Recycling a small
    #: library is a real strategy; it is now a decision.
    repeat_posts: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Whether smart matching spreads itself across the tagged products.
    #:
    #: On by default. Ranking is deterministic, so without it the best-fitting
    #: product wins every post in a run and a catalogue of forty promotes two.
    #: Rotation takes the best product that has not had its turn, which is the
    #: same ranking asked a fairer question.
    rotate_products: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_cap_per_account: Mapped[int] = mapped_column(Integer, default=2)
    #: The whole campaign's ceiling for a rolling week, counted across every
    #: destination. None means the per-account caps are the only limit. This is
    #: the budget shape organic posting actually has - posts, not money.
    weekly_post_cap: Mapped[int | None] = mapped_column(Integer)
    #: Draft by default: the first thing a new autopilot does is fill a queue in
    #: the engine for someone to look at, not publish to a live audience.
    #: Scheduled, not drafted.
    #:
    #: Drafting was the cautious default and it made the campaign look broken:
    #: a package is approved, the switch is on, the posting time passes - and
    #: the post sits in the engine waiting for a second approval nobody
    #: mentioned. The confirmation on switching the campaign on already says it
    #: will post to live accounts on its own, so this makes the setting match
    #: the promise. Drafting stays available for anybody who wants a review
    #: step, chosen rather than assumed.
    delivery: Mapped[str] = mapped_column(String(16), default="schedule")
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
        CheckConstraint(
            "link_placement IN ('auto','caption','first_comment','bio')",
            name="valid_destination_link_placement",
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
    #: Where the post lands within the account, for the two networks that ask.
    #: Per destination rather than per package: the same copy goes to different
    #: subreddits on different accounts, and a board belongs to one profile.
    subreddit: Mapped[str | None] = mapped_column(String(80))
    board: Mapped[str | None] = mapped_column(String(120))
    board_name: Mapped[str | None] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(200))
    post_type: Mapped[str | None] = mapped_column(String(24))
    #: Where this destination's affiliate link lives. 'auto' - the default,
    #: and the recommendation - lets the network's own behaviour decide;
    #: the explicit values exist for the operator who knows better, with the
    #: trade-off written on the preview rather than assumed away.
    link_placement: Mapped[str] = mapped_column(String(16), default="auto")
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
    #: The video, where this package is a video. Empty for a photo carousel,
    #: which is the one shape a campaign could not hold: a queue item was one
    #: file, so a network that takes several pictures could only be given one.
    video_path: Mapped[str] = mapped_column(String(1200), default="")
    #: The pictures, in the order they should appear. A carousel is ordered -
    #: the first is the cover - so this is a list rather than a set, and the
    #: order somebody chose in the picker is the order that posts.
    image_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    title: Mapped[str | None] = mapped_column(String(200))
    #: Copy a person wrote. Autopilot never generates it.
    body: Mapped[str] = mapped_column(String(4000))
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Declared, never inferred. Several networks require the disclosure and
    #: penalise a missing one, but saying media is AI-made is the operator's
    #: statement to make - defaulting it on would make it for them.
    made_with_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility: Mapped[str] = mapped_column(String(16), default="public")
    #: Threads is the only network with a topic, and Buffer refuses a field a
    #: network does not declare - so None means "not set", never "empty".
    topic: Mapped[str | None] = mapped_column(String(80))
    youtube_category_id: Mapped[str | None] = mapped_column(String(8))
    #: Optional operator-authored content after the primary post. Affiliate
    #: links are still routed by the platform policy; these fields are the
    #: campaign's own comment/reply copy and are validated against each engine
    #: in the timeline before deployment.
    first_comment: Mapped[str | None] = mapped_column(String(2000))
    thread: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: This post's own disclosure and bio wording, where the campaign's does not
    #: suit it. None - not an empty string - means the campaign's, so clearing
    #: the field falls back rather than posting an endorsement with no
    #: disclosure at all.
    disclosure: Mapped[str | None] = mapped_column(String(300))
    bio_hint: Mapped[str | None] = mapped_column(String(120))
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


class CampaignOffer(Base):
    """A product this campaign is allowed to promote.

    Kept as a link rather than a list on the campaign, because the question is
    asked from both ends and only one of those is cheap against a list. The
    campaign asks "what may I attach"; Attribution asks "which campaigns is
    this product in", and answering that from a JSON column on every autopilot
    means reading every autopilot.

    The tag is also a permission, not a hint: an untagged product is not
    offered to smart matching and cannot be pinned by hand. A campaign with no
    tags attaches nothing, which is a state worth being able to see rather than
    one to be inferred from an empty ranking.
    """

    __tablename__ = "campaign_offers"
    __table_args__ = (
        UniqueConstraint("campaign_id", "offer_id", name="unique_campaign_offer"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("campoffer")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("product_offers.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


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


def disclosure_for(item: CampaignQueueItem, autopilot: CampaignAutopilot) -> str:
    """The disclosure this post carries: its own, the campaign's, or none.

    Read through a function rather than at each call site, because there are
    several and a missed one is an endorsement published without a disclosure -
    or, now that a campaign may switch disclosure off, one published with a
    disclosure the campaign said not to add.

    A post that words its own still says nothing when the campaign discloses
    nothing: the switch is the campaign's, and an override is a wording.
    """
    if not autopilot.disclose:
        return ""
    return (item.disclosure or "").strip() or autopilot.disclosure


def bio_hint_for(item: CampaignQueueItem, autopilot: CampaignAutopilot) -> str:
    """The words pointing at the profile link, this post's or the campaign's."""
    return (item.bio_hint or "").strip() or autopilot.bio_hint
