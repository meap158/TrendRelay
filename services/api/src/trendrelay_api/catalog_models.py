"""The work layer above the product catalog.

A `Product` row in TrendRelay is a *manifestation* in the publishing industry's
sense — one saleable edition, carrying one ISBN or ASIN. The paperback, the
hardback and the Kindle edition of a book are three products and one book. These
two tables record which products are the same book so that spend and revenue can
be measured against the book.

The mapping is stored rather than derived. Deriving it would mean re-running the
match on every catalog import, and any improvement to the matching rules would
silently redraw last month's numbers. A stored mapping also gives a manual
decision somewhere to live: an override is only durable if it survives the next
import that disagrees with it.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now


class CatalogWork(Base):
    """One book, independent of the formats it is sold in."""

    __tablename__ = "catalog_works"
    __table_args__ = (
        UniqueConstraint("workspace_id", "match_key", name="unique_workspace_work_key"),
        CheckConstraint("origin IN ('matched','manual')", name="valid_work_origin"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("work"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    #: The normalised title-and-author key from `catalog_identifiers.work_key`.
    #: Unique per workspace, which is what stops two imports creating two works
    #: for one book.
    match_key: Mapped[str] = mapped_column(String(400), index=True)
    title: Mapped[str] = mapped_column(String(400))
    author: Mapped[str | None] = mapped_column(String(240), index=True)
    #: 'matched' when the grouping came from the matcher, 'manual' when a person
    #: made it. Manual works are never rewritten by a later import.
    origin: Mapped[str] = mapped_column(String(16), default="matched", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class WorkEdition(Base):
    """One product's membership of one work, plus how that was decided.

    A product belongs to at most one work, so `product_id` is unique. The
    interesting column is `assignment`: 'detached' records that somebody
    deliberately took this edition *out* of a work, and the matcher must respect
    that rather than helpfully putting it back on the next run.
    """

    __tablename__ = "work_editions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "product_id", name="unique_workspace_work_edition"),
        CheckConstraint(
            "assignment IN ('automatic','manual','detached')",
            name="valid_edition_assignment",
        ),
        CheckConstraint(
            "identifier_scheme IN ('isbn13','isbn10','asin','unknown')",
            name="valid_edition_identifier_scheme",
        ),
        CheckConstraint(
            "product_form IN ('hardcover','paperback','ebook','audiobook','unknown')",
            name="valid_edition_product_form",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 100", name="valid_edition_confidence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("edition"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    #: Null exactly when `assignment` is 'detached' — the row then exists only to
    #: remember that this product must be left alone.
    work_id: Mapped[str | None] = mapped_column(
        ForeignKey("catalog_works.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    identifier: Mapped[str | None] = mapped_column(String(64), index=True)
    identifier_scheme: Mapped[str] = mapped_column(String(16), default="unknown")
    product_form: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    assignment: Mapped[str] = mapped_column(String(16), default="automatic", index=True)
    #: How sure the matcher was, 0-100. Exact title-and-author agreement scores
    #: highest; a match that ignored the subtitle scores lower and is offered as
    #: a suggestion rather than applied.
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class AdSpendEntry(Base):
    """One day of advertising spend, recorded against a work.

    Spend is stored at the work rather than the edition because that is the
    level an advertiser actually buys at: a Meta campaign for a book sends
    traffic to whichever format the reader chooses, so splitting its cost across
    editions would be an invention. Revenue rolls up to the same level, which is
    what makes the ratio between them meaningful.

    A day is the grain because that is what every ad platform's insights export
    reports, and re-importing an overlapping window must not double-count: the
    unique key is the source, its own row reference and the date, so a repeated
    import updates the same rows.
    """

    __tablename__ = "ad_spend_entries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source",
            "external_reference",
            "spend_date",
            name="unique_workspace_ad_spend_day",
        ),
        CheckConstraint("spend_cents >= 0", name="valid_ad_spend_amount"),
        CheckConstraint("length(currency) = 3", name="valid_ad_spend_currency"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("adspend"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    work_id: Mapped[str | None] = mapped_column(
        ForeignKey("catalog_works.id", ondelete="CASCADE"), index=True
    )
    #: Kept alongside the work so spend that arrives naming one edition can still
    #: be traced back to the row that produced it.
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), index=True
    )
    #: Which ad platform reported this, e.g. 'meta'.
    source: Mapped[str] = mapped_column(String(40), default="meta", index=True)
    #: The platform's own id for the ad or ad set, so a re-import lands on the
    #: same row instead of adding a second one.
    external_reference: Mapped[str] = mapped_column(String(200))
    #: The campaign this row came from, normalised. Kept so that mapping a
    #: campaign to a book afterwards can correct the spend already imported
    #: under a different answer, rather than only affecting later imports.
    campaign_key: Mapped[str | None] = mapped_column(String(400), index=True)
    campaign_name: Mapped[str | None] = mapped_column(String(400))
    spend_date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)
    spend_cents: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    imported_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class AdCampaignMapping(Base):
    """Which book an ad campaign advertises, once somebody has said so.

    Ad platforms report against their own campaigns and know nothing about
    ISBNs, so most rows resolve by reading an identifier out of the campaign
    name. When the name only hints at the book, the guess is confirmed once and
    recorded here — after which it is a decision, not a guess, and every later
    import of that campaign follows it without asking again.
    """

    __tablename__ = "ad_campaign_mappings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source",
            "campaign_key",
            name="unique_workspace_campaign_mapping",
        ),
        CheckConstraint("origin IN ('matched','manual')", name="valid_mapping_origin"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("admap"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_works.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(40), default="meta", index=True)
    #: The campaign name reduced to lowercase words, so punctuation and casing
    #: changes in the ad account do not orphan the mapping.
    campaign_key: Mapped[str] = mapped_column(String(400), index=True)
    #: The name as it was when mapped, kept only so the interface can show
    #: something a person recognises.
    campaign_name: Mapped[str] = mapped_column(String(400))
    origin: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)
