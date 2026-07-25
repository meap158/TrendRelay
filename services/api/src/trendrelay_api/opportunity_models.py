"""Workspace-scoped affiliate catalog and explainable opportunity models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "catalog_key",
            name="unique_workspace_product_catalog_key",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("product"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    catalog_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(240))
    brand: Mapped[str | None] = mapped_column(String(160))
    category: Mapped[str | None] = mapped_column(String(160), index=True)
    marketplace: Mapped[str] = mapped_column(String(80), index=True)
    product_url: Mapped[str | None] = mapped_column(String(2000))
    image_url: Mapped[str | None] = mapped_column(String(2000))
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class ProductOffer(Base):
    __tablename__ = "product_offers"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "fingerprint",
            name="unique_workspace_offer_fingerprint",
        ),
        CheckConstraint(
            "availability IN ('available','limited','unavailable','unknown')",
            name="valid_offer_availability",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("offer"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    network: Mapped[str] = mapped_column(String(120), index=True)
    merchant: Mapped[str | None] = mapped_column(String(160))
    affiliate_url: Mapped[str] = mapped_column(String(2000))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    commission_bps: Mapped[int | None] = mapped_column(Integer)
    commission_flat_cents: Mapped[int | None] = mapped_column(Integer)
    cookie_days: Mapped[int | None] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    restrictions: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','shortlisted','dismissed')",
            name="valid_opportunity_status",
        ),
        CheckConstraint(
            "lifecycle IN ('emerging','accelerating','peaking','saturated','declining','unknown')",
            name="valid_opportunity_lifecycle",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("opportunity")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    trend_entity: Mapped[str] = mapped_column(String(300), index=True)
    summary: Mapped[str] = mapped_column(String(2000))
    lifecycle: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    markets: Mapped[list[str]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score: Mapped[int] = mapped_column(Integer, index=True)
    score_version: Mapped[str] = mapped_column(String(20), default="v1")
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    offer_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_offers.id", ondelete="SET NULL"), index=True
    )
    source_research_job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class OpportunityCampaign(Base):
    __tablename__ = "opportunity_campaigns"
    __table_args__ = (
        UniqueConstraint(
            "opportunity_id",
            "campaign_id",
            name="unique_opportunity_campaign",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("oppcampaign")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_offers.id", ondelete="SET NULL"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
