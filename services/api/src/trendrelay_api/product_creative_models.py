"""Pending creatives for an Attribution product, and the link once they land.

A draft is the prompt and the ask — image, carousel, or video — bound to one
product. It holds no pixels. Bytes arrive later, through the Library's own
ingest, and only then does `ProductCreativeLink` tie the asset to the product
from both sides. A carousel remembers files it has already ingested and stays
pending, with no link, until it holds its card count.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now


class ProductCreativeDraft(Base):
    __tablename__ = "product_creative_drafts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('image','carousel','video')",
            name="valid_product_creative_kind",
        ),
        CheckConstraint(
            "status IN ('pending','succeeded')",
            name="valid_product_creative_status",
        ),
        CheckConstraint(
            "recipe IN ('bed_flat_lay','mannequin_transition','mirror_selfie')",
            name="valid_product_creative_recipe",
        ),
        CheckConstraint(
            "variant IS NULL OR variant IN ('female','male')",
            name="valid_product_creative_variant",
        ),
        CheckConstraint(
            "card_count >= 1 AND card_count <= 10",
            name="valid_product_creative_card_count",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("pcreative")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), index=True)
    recipe: Mapped[str] = mapped_column(String(40))
    variant: Mapped[str | None] = mapped_column(String(16))
    background_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    background_reference: Mapped[str | None] = mapped_column(String(2000))
    #: The prompt the operator reviewed. Stored as resolved, so a later read
    #: returns this text rather than whatever the resolver would say now.
    prompt: Mapped[str] = mapped_column(String(4000))
    #: Library image ids the operator picked as the subject, in pick order.
    #: Empty means the product's own listing pictures are the subject, which
    #: is the path an older draft and an MCP call without a pick still take.
    subject_asset_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    #: Listing fields the operator chose to attach, snapshotted at confirm.
    #: Keys are title, price, description, gallery, and variations. An empty
    #: object means none of them were sent, which is what an older draft and
    #: a create that omits the field still do. The values are the product's
    #: at that moment, so a later listing refresh does not rewrite the ask.
    listing_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    card_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    #: Library asset ids already ingested for this draft, in order. They are
    #: not the product link: that is written once, when the count is met.
    staged_asset_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class ProductCreativeLink(Base):
    """One asset tied to one product. Visible from either side."""

    __tablename__ = "product_creative_links"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "asset_id",
            name="unique_product_creative_asset",
        ),
        UniqueConstraint(
            "draft_id",
            "position",
            name="unique_product_creative_position",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("pclink")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("product_creative_drafts.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
