"""First-party tracking links, privacy-minimized clicks, and affiliate conversions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now


class TrackingLink(Base):
    __tablename__ = "tracking_links"
    __table_args__ = (
        UniqueConstraint("code", name="unique_tracking_link_code"),
        CheckConstraint(
            "status IN ('active','disabled','broken','expired')",
            name="valid_tracking_link_status",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("tracking")
    )
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("publication_plans.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_offers.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    destination_url: Mapped[str] = mapped_column(String(2000))
    country_destinations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    platform: Mapped[str] = mapped_column(String(24), index=True)
    campaign_parameter: Mapped[str] = mapped_column(String(40), default="tr_campaign")
    platform_parameter: Mapped[str] = mapped_column(String(40), default="tr_platform")
    disclosure: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class ClickEvent(Base):
    __tablename__ = "click_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("click"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    tracking_link_id: Mapped[str] = mapped_column(
        ForeignKey("tracking_links.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("publication_plans.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_offers.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    referrer_origin: Mapped[str | None] = mapped_column(String(500))
    user_agent_family: Mapped[str | None] = mapped_column(String(80), index=True)
    visitor_hash: Mapped[str | None] = mapped_column(String(64), index=True)


class Conversion(Base):
    __tablename__ = "conversions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "network",
            "external_reference_hash",
            name="unique_workspace_conversion_reference",
        ),
        CheckConstraint(
            "status IN ('pending','approved','reversed','refunded')",
            name="valid_conversion_status",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("conversion")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    tracking_link_id: Mapped[str] = mapped_column(
        ForeignKey("tracking_links.id", ondelete="CASCADE"), index=True
    )
    click_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("click_events.id", ondelete="SET NULL"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("publication_plans.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_offers.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    network: Mapped[str] = mapped_column(String(120), index=True)
    external_reference_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)
    order_value_cents: Mapped[int | None] = mapped_column(Integer)
    commission_cents: Mapped[int] = mapped_column(Integer)
    raw_metadata: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    imported_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)
