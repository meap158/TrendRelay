"""Affiliate catalog, evidence-backed opportunity scoring, and campaign conversion."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.integrations.last30days import get_job
from trendrelay_api.models import Campaign
from trendrelay_api.opportunity_models import (
    Opportunity,
    OpportunityCampaign,
    Product,
    ProductOffer,
)

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/opportunities",
    tags=["opportunities"],
)
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
Lifecycle = Literal[
    "emerging",
    "accelerating",
    "peaking",
    "saturated",
    "declining",
    "unknown",
]

POSITIVE_FACTORS = {
    "growth_velocity": ("Growth velocity", Decimal("0.20")),
    "acceleration": ("Acceleration", Decimal("0.15")),
    "cross_platform_confirmation": ("Cross-platform confirmation", Decimal("0.15")),
    "buyer_intent": ("Buyer intent", Decimal("0.15")),
    "affiliate_economics": ("Affiliate economics", Decimal("0.15")),
    "creative_reproducibility": ("Creative reproducibility", Decimal("0.10")),
    "freshness": ("Freshness", Decimal("0.10")),
}
PENALTIES = {
    "competition": ("Competition penalty", Decimal("-0.15")),
    "policy_risk": ("Policy-risk penalty", Decimal("-0.20")),
}
CSV_REQUIRED = {"product_name", "marketplace", "network", "affiliate_url"}
CSV_OPTIONAL = {
    "brand",
    "category",
    "merchant",
    "product_url",
    "image_url",
    "price",
    "currency",
    "commission_percent",
    "commission_flat",
    "cookie_days",
    "availability",
    "restrictions",
}


def _clean(value: str | None, limit: int) -> str | None:
    normalized = " ".join((value or "").strip().split())
    return normalized[:limit] or None


def _key(*values: str | None) -> str:
    normalized = "\x1f".join((value or "").strip().casefold() for value in values)
    return sha256(normalized.encode("utf-8")).hexdigest()


def _money(value: str | None, field: str) -> int | None:
    if not _clean(value, 100):
        return None
    try:
        decimal = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise ValueError(f"{field} must be a decimal number.") from error
    if decimal < 0:
        raise ValueError(f"{field} cannot be negative.")
    return int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _integer(value: str | None, field: str, maximum: int) -> int | None:
    if not _clean(value, 100):
        return None
    try:
        result = int(str(value).strip())
    except ValueError as error:
        raise ValueError(f"{field} must be a whole number.") from error
    if result < 0 or result > maximum:
        raise ValueError(f"{field} must be between 0 and {maximum}.")
    return result


def _commission_bps(value: str | None) -> int | None:
    if not _clean(value, 100):
        return None
    try:
        percent = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise ValueError("commission_percent must be a decimal number.") from error
    if percent < 0 or percent > 100:
        raise ValueError("commission_percent must be between 0 and 100.")
    return int((percent * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class CsvImport(BaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)


class Evidence(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    source: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    source_url: AnyHttpUrl | None = None
    observed_at: datetime | None = None
    metrics: dict[str, float] = Field(default_factory=dict)

    @field_validator("source", "title")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().split())


class ScoreInputs(BaseModel):
    growth_velocity: int = Field(ge=0, le=100)
    acceleration: int = Field(ge=0, le=100)
    buyer_intent: int = Field(ge=0, le=100)
    creative_reproducibility: int = Field(ge=0, le=100)
    freshness: int = Field(ge=0, le=100)
    competition: int = Field(ge=0, le=100)
    policy_risk: int = Field(ge=0, le=100)
    reasons: dict[str, str] = Field(default_factory=dict)

    @field_validator("reasons")
    @classmethod
    def valid_reasons(cls, values: dict[str, str]) -> dict[str, str]:
        allowed = {
            "growth_velocity",
            "acceleration",
            "buyer_intent",
            "creative_reproducibility",
            "freshness",
            "competition",
            "policy_risk",
        }
        result = {}
        for key, value in values.items():
            reason = " ".join(value.strip().split())
            if key not in allowed or not reason or len(reason) > 500:
                raise ValueError(
                    "Score reasons must name a supported factor and stay under 500 characters."
                )
            result[key] = reason
        return result


class OpportunityCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    trend_entity: str = Field(min_length=2, max_length=300)
    summary: str = Field(min_length=2, max_length=2000)
    lifecycle: Lifecycle = "unknown"
    markets: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    inputs: ScoreInputs
    offer_ids: list[str] = Field(default_factory=list, max_length=100)
    selected_offer_id: str | None = Field(default=None, max_length=64)
    source_research_job_id: str | None = Field(default=None, max_length=64)

    @field_validator("name", "trend_entity", "summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("markets", "languages", "offer_ids")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class CampaignFromOpportunity(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    objective: str | None = Field(default=None, max_length=1000)
    audience: str = Field(default="People showing purchase intent", min_length=2, max_length=1000)


def _offer_view(item: ProductOffer, product: Product) -> dict[str, Any]:
    return {
        "id": item.id,
        "product": {
            "id": product.id,
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "marketplace": product.marketplace,
            "product_url": product.product_url,
            "image_url": product.image_url,
        },
        "network": item.network,
        "merchant": item.merchant,
        "affiliate_url": item.affiliate_url,
        "price_cents": item.price_cents,
        "currency": item.currency,
        "commission_bps": item.commission_bps,
        "commission_flat_cents": item.commission_flat_cents,
        "cookie_days": item.cookie_days,
        "availability": item.availability,
        "restrictions": item.restrictions,
    }


def _offer_records(
    session: Session, workspace_id: str, offer_ids: list[str] | None = None
) -> list[tuple[ProductOffer, Product]]:
    query = (
        select(ProductOffer, Product)
        .join(Product, Product.id == ProductOffer.product_id)
        .where(ProductOffer.workspace_id == workspace_id)
    )
    if offer_ids is not None:
        if not offer_ids:
            return []
        query = query.where(ProductOffer.id.in_(offer_ids))
    return list(session.execute(query.order_by(ProductOffer.created_at.desc())).all())


def score_opportunity(
    inputs: ScoreInputs,
    evidence: list[dict[str, Any]],
    offers: list[ProductOffer],
) -> tuple[int, list[dict[str, Any]]]:
    sources = sorted(
        {str(item.get("source", "")).strip().casefold() for item in evidence if item.get("source")}
    )
    cross_platform = min(100, len(sources) * 25)
    available = [
        item for item in offers if item.availability in {"available", "limited", "unknown"}
    ]
    commission_rates = [
        item.commission_bps for item in available if item.commission_bps is not None
    ]
    average_percent = sum(commission_rates) / len(commission_rates) / 100 if commission_rates else 0
    affiliate = min(100, round(len(available) * 15 + average_percent * 2))
    values = {
        "growth_velocity": inputs.growth_velocity,
        "acceleration": inputs.acceleration,
        "cross_platform_confirmation": cross_platform,
        "buyer_intent": inputs.buyer_intent,
        "affiliate_economics": affiliate,
        "creative_reproducibility": inputs.creative_reproducibility,
        "freshness": inputs.freshness,
        "competition": inputs.competition,
        "policy_risk": inputs.policy_risk,
    }
    evidence_ids = [str(item["id"]) for item in evidence]
    default_reasons = {
        "cross_platform_confirmation": (
            f"Evidence spans {len(sources)} source{'s' if len(sources) != 1 else ''}: "
            + (", ".join(sources) if sources else "none")
            + "."
        ),
        "affiliate_economics": (
            f"{len(available)} usable offer{'s' if len(available) != 1 else ''}; "
            f"average percentage commission {average_percent:.2f}%."
        ),
    }
    breakdown: list[dict[str, Any]] = []
    for key, (label, weight) in {**POSITIVE_FACTORS, **PENALTIES}.items():
        value = values[key]
        contribution = (Decimal(value) * weight).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        breakdown.append(
            {
                "factor": key,
                "label": label,
                "value": value,
                "weight": float(weight),
                "contribution": float(contribution),
                "reason": inputs.reasons.get(key)
                or default_reasons.get(key)
                or "Operator assessment.",
                "evidence_ids": (
                    [item.id for item in available]
                    if key == "affiliate_economics"
                    else evidence_ids
                ),
            }
        )
    total = sum(Decimal(str(item["contribution"])) for item in breakdown)
    return max(0, min(100, int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))), breakdown


def _research_evidence(job_id: str, workspace_id: str) -> list[dict[str, Any]]:
    try:
        job = get_job(job_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Research job not found.") from error
    if job.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="Research job not found.")
    if job.get("status") != "succeeded":
        raise HTTPException(
            status_code=409, detail="Research must finish before it becomes evidence."
        )
    result = []
    for index, item in enumerate(job.get("observations", [])[:100]):
        result.append(
            {
                "id": f"research-{index + 1}",
                "source": str(item.get("source") or "unknown"),
                "title": str(item.get("title") or item.get("entity") or "Research evidence")[:500],
                "source_url": (item.get("evidence") or {}).get("source_url") or None,
                "observed_at": item.get("observed_at"),
                "metrics": item.get("metrics") or {},
            }
        )
    return result


def _opportunity_view(item: Opportunity) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "trend_entity": item.trend_entity,
        "summary": item.summary,
        "lifecycle": item.lifecycle,
        "markets": item.markets,
        "languages": item.languages,
        "evidence": item.evidence,
        "inputs": item.inputs,
        "score": item.score,
        "score_version": item.score_version,
        "score_breakdown": item.score_breakdown,
        "offer_ids": item.offer_ids,
        "selected_offer_id": item.selected_offer_id,
        "source_research_job_id": item.source_research_job_id,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.get("/offers")
def list_offers(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {
        "offers": [
            _offer_view(offer, product) for offer, product in _offer_records(session, workspace_id)
        ]
    }


@router.post("/offers/import")
def import_offers(
    workspace_id: str,
    body: CsvImport,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    ensure_profile(session, user)
    reader = csv.DictReader(io.StringIO(body.csv_text.lstrip("\ufeff")))
    fields = {str(item).strip() for item in (reader.fieldnames or [])}
    missing = CSV_REQUIRED - fields
    unknown = fields - CSV_REQUIRED - CSV_OPTIONAL
    if missing:
        raise HTTPException(
            status_code=422,
            detail="CSV is missing required columns: " + ", ".join(sorted(missing)),
        )
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="CSV contains unsupported columns: " + ", ".join(sorted(unknown)),
        )
    created = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        if row_number > 1001:
            errors.append({"row": row_number, "detail": "Import is limited to 1,000 rows."})
            break
        try:
            name = _clean(row.get("product_name"), 240)
            marketplace = _clean(row.get("marketplace"), 80)
            network = _clean(row.get("network"), 120)
            affiliate_url = _clean(row.get("affiliate_url"), 2000)
            if not all((name, marketplace, network, affiliate_url)):
                raise ValueError("Required values cannot be blank.")
            if not re.match(r"^https?://", affiliate_url, re.IGNORECASE):
                raise ValueError("affiliate_url must be an HTTP(S) URL.")
            availability = (_clean(row.get("availability"), 16) or "unknown").lower()
            if availability not in {"available", "limited", "unavailable", "unknown"}:
                raise ValueError(
                    "availability must be available, limited, unavailable, or unknown."
                )
            currency = (_clean(row.get("currency"), 3) or "USD").upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("currency must be a three-letter code.")
            price_cents = _money(row.get("price"), "price")
            commission_bps = _commission_bps(row.get("commission_percent"))
            commission_flat_cents = _money(row.get("commission_flat"), "commission_flat")
            cookie_days = _integer(row.get("cookie_days"), "cookie_days", 3650)
            restrictions = [
                item.strip() for item in (row.get("restrictions") or "").split("|") if item.strip()
            ][:30]
            product_key = _key(marketplace, name, row.get("brand"), row.get("product_url"))
            product = session.scalar(
                select(Product).where(
                    Product.workspace_id == workspace_id,
                    Product.catalog_key == product_key,
                )
            )
            if not product:
                product = Product(
                    workspace_id=workspace_id,
                    catalog_key=product_key,
                    name=name,
                    brand=_clean(row.get("brand"), 160),
                    category=_clean(row.get("category"), 160),
                    marketplace=marketplace,
                    product_url=_clean(row.get("product_url"), 2000),
                    image_url=_clean(row.get("image_url"), 2000),
                    created_by=user.id,
                )
                session.add(product)
                session.flush()
            fingerprint = _key(network, affiliate_url)
            if session.scalar(
                select(ProductOffer.id).where(
                    ProductOffer.workspace_id == workspace_id,
                    ProductOffer.fingerprint == fingerprint,
                )
            ):
                skipped += 1
                continue
            session.add(
                ProductOffer(
                    workspace_id=workspace_id,
                    product_id=product.id,
                    fingerprint=fingerprint,
                    network=network,
                    merchant=_clean(row.get("merchant"), 160),
                    affiliate_url=affiliate_url,
                    price_cents=price_cents,
                    currency=currency,
                    commission_bps=commission_bps,
                    commission_flat_cents=commission_flat_cents,
                    cookie_days=cookie_days,
                    availability=availability,
                    restrictions=restrictions,
                    created_by=user.id,
                )
            )
            created += 1
        except ValueError as error:
            errors.append({"row": row_number, "detail": str(error)})
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "affiliate_offers.imported",
        "product_offer",
        "csv",
        {"created": created, "skipped": skipped, "errors": len(errors)},
    )
    return {"import": {"created": created, "skipped": skipped, "errors": errors}}


@router.get("")
def list_opportunities(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    items = session.scalars(
        select(Opportunity)
        .where(Opportunity.workspace_id == workspace_id)
        .order_by(Opportunity.score.desc(), Opportunity.updated_at.desc())
    ).all()
    return {"opportunities": [_opportunity_view(item) for item in items]}


@router.post("", status_code=201)
def create_opportunity(
    workspace_id: str,
    body: OpportunityCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "analyst"},
    )
    ensure_profile(session, user)
    evidence = [item.model_dump(mode="json") for item in body.evidence]
    if body.source_research_job_id:
        research = _research_evidence(body.source_research_job_id, workspace_id)
        known = {item["id"] for item in evidence}
        evidence.extend(item for item in research if item["id"] not in known)
    if not evidence:
        raise HTTPException(status_code=422, detail="At least one evidence item is required.")
    rows = _offer_records(session, workspace_id, body.offer_ids)
    if len(rows) != len(body.offer_ids):
        raise HTTPException(status_code=422, detail="One or more affiliate offers are unavailable.")
    offers = [row[0] for row in rows]
    if body.selected_offer_id and body.selected_offer_id not in body.offer_ids:
        raise HTTPException(status_code=422, detail="Selected offer must be attached.")
    score, breakdown = score_opportunity(body.inputs, evidence, offers)
    item = Opportunity(
        workspace_id=workspace_id,
        name=body.name,
        trend_entity=body.trend_entity,
        summary=body.summary,
        lifecycle=body.lifecycle,
        markets=body.markets,
        languages=body.languages,
        evidence=evidence,
        inputs=body.inputs.model_dump(),
        score=score,
        score_version="v1",
        score_breakdown=breakdown,
        offer_ids=body.offer_ids,
        selected_offer_id=body.selected_offer_id,
        source_research_job_id=body.source_research_job_id,
        created_by=user.id,
    )
    session.add(item)
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "opportunity.created",
        "opportunity",
        item.id,
        {
            "score": item.score,
            "score_version": item.score_version,
            "evidence_count": len(evidence),
            "offer_count": len(offers),
        },
    )
    return {"opportunity": _opportunity_view(item)}


@router.post("/{opportunity_id}/campaign", status_code=201)
def campaign_from_opportunity(
    workspace_id: str,
    opportunity_id: str,
    body: CampaignFromOpportunity,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    item = session.scalar(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    offer = (
        session.scalar(
            select(ProductOffer).where(
                ProductOffer.id == item.selected_offer_id,
                ProductOffer.workspace_id == workspace_id,
            )
        )
        if item.selected_offer_id
        else None
    )
    campaign = Campaign(
        workspace_id=workspace_id,
        name=_clean(body.name, 160) or item.name,
        objective=_clean(body.objective, 1000)
        or f"Validate and convert the {item.trend_entity} opportunity (score {item.score}/100).",
        audience=" ".join(body.audience.strip().split()),
        markets=item.markets,
        languages=item.languages,
        affiliate_url=offer.affiliate_url if offer else None,
        created_by=user.id,
    )
    session.add(campaign)
    session.flush()
    session.add(
        OpportunityCampaign(
            workspace_id=workspace_id,
            opportunity_id=item.id,
            offer_id=offer.id if offer else None,
            campaign_id=campaign.id,
            created_by=user.id,
        )
    )
    item.status = "shortlisted"
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "opportunity.campaign_created",
        "opportunity",
        item.id,
        {"campaign_id": campaign.id, "offer_id": offer.id if offer else None},
    )
    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "affiliate_url": campaign.affiliate_url,
            "opportunity_id": item.id,
            "offer_id": offer.id if offer else None,
        }
    }
