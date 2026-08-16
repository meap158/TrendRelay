"""What a campaign was started because of, kept rather than summarised away.

Discover finds evidence - a topic climbing in a region, a post doing unusual
numbers, a creator worth watching - and today all of it is thrown away at the
moment it becomes useful. The basket is ephemeral, and creating a campaign from
it persists only the synthesised name, objective, audience, markets and
languages. The source URL, the provider, the region, the metric that made
something look interesting: none of it survives the click.

That loss is why a campaign cannot answer "why this?" a week later, and why a
signal cannot be watched, refreshed, or retired when it fades. So a signal is a
record in its own right, attached to the campaign it argued for.

Two things this is deliberately not. It is not proof of media rights - a
trending post is inspiration, and the media to answer it must still come from
the Library or from a production brief. And it is not a metric store: the
numbers here are what was observed at collection time, kept as evidence of why
a decision looked reasonable, not as a series to chart.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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

#: What kind of thing was observed. A topic is a subject with momentum; a post
#: is one piece of content doing well; a creator is an account worth watching.
SIGNAL_KINDS = ("topic", "post", "creator")

#: How long an observation is worth acting on before it should be looked at
#: again. Trends move; a fortnight-old ranking is a claim about the past.
DEFAULT_FRESHNESS_DAYS = 14


class CampaignSignal(Base):
    """One observation from Discover, kept with the campaign it justifies."""

    __tablename__ = "campaign_signals"
    __table_args__ = (
        # The same evidence collected twice is one signal. Without this a
        # basket re-submitted after an edit would double every row.
        UniqueConstraint(
            "campaign_id", "external_id", name="unique_campaign_signal_external"
        ),
        CheckConstraint(
            "kind IN ('topic','post','creator')", name="valid_campaign_signal_kind"
        ),
        CheckConstraint(
            "status IN ('active','watching','expired','retired')",
            name="valid_campaign_signal_status",
        ),
        Index("ix_campaign_signals_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("signal")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    #: Nullable so a signal can be watched before it has a campaign to belong
    #: to - the "Watch this signal" action in the brief.
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )

    #: The id Discover gave it, which is stable for the same observation and is
    #: what makes re-submitting a basket idempotent.
    external_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(300))

    #: Which board it came from - `tiktok`, `reddit`, `google-trends`. Kept as
    #: free text rather than an enum because the set of sources changes without
    #: this table needing to know.
    provider: Mapped[str | None] = mapped_column(String(80), index=True)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    creator: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(16), index=True)
    language: Mapped[str | None] = mapped_column(String(16))

    #: The sentence Discover showed to justify this, in the words it used. Kept
    #: verbatim so the explanation a person acted on is the one recorded.
    evidence: Mapped[str | None] = mapped_column(String(2000))
    #: Whatever the board reported - rank, views, likes, a search volume. Shapes
    #: differ per provider and are not reconciled here; that would be inventing
    #: comparability that does not exist.
    observed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: "rising", "steady", "fading" where a source says so. Absent is honest.
    trend_shape: Mapped[str | None] = mapped_column(String(24))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Angles proposed from this signal, so a later reader can see what was
    #: suggested as well as what was chosen.
    angles: Mapped[list[str]] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    #: When this stops counting as current. Stored rather than computed so a
    #: source that knows its own shelf life can say so.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


def default_expiry(collected: datetime | None = None) -> datetime:
    """When an observation stops being current, absent a source saying so."""
    return (collected or utc_now()) + timedelta(days=DEFAULT_FRESHNESS_DAYS)


def is_stale(signal: CampaignSignal, *, at: datetime | None = None) -> bool:
    """Whether this evidence has passed its own expiry.

    Read rather than written, so nothing has to sweep the table to keep the
    answer true - the same reasoning as a lapsed worker lease.
    """
    if signal.expires_at is None:
        return False
    moment = at or utc_now()
    expiry = signal.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=moment.tzinfo)
    return expiry <= moment


def describe(signal: CampaignSignal, *, at: datetime | None = None) -> dict[str, Any]:
    """A signal as the interface reads it."""
    return {
        "id": signal.id,
        "campaign_id": signal.campaign_id,
        "external_id": signal.external_id,
        "kind": signal.kind,
        "label": signal.label,
        "provider": signal.provider,
        "source_url": signal.source_url,
        "creator": signal.creator,
        "region": signal.region,
        "language": signal.language,
        "evidence": signal.evidence,
        "observed": signal.observed or {},
        "trend_shape": signal.trend_shape,
        "tags": list(signal.tags or []),
        "angles": list(signal.angles or []),
        "status": signal.status,
        # Derived, never stored: see `is_stale`.
        "stale": is_stale(signal, at=at),
        "collected_at": signal.collected_at,
        "expires_at": signal.expires_at,
    }
