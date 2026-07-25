"""Governed tracking links, privacy-minimized click routing, and revenue attribution."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from secrets import token_urlsafe
from threading import Lock
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.config import get_settings
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.media_models import CreativeAnalysis, MediaAsset
from trendrelay_api.models import Campaign, PublicationPlan, utc_now
from trendrelay_api.opportunity_models import ProductOffer
from trendrelay_api.tool_registry import PROJECT_ROOT

router = APIRouter(tags=["attribution"])
workspace_router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/attribution",
    tags=["attribution"],
)
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
Platform = Literal["tiktok", "instagram", "youtube", "douyin", "other"]
LinkStatus = Literal["active", "disabled", "broken", "expired"]
ConversionStatus = Literal["pending", "approved", "reversed", "refunded"]
_ATTRIBUTION_SECRET_PATH = PROJECT_ROOT / ".data" / "attribution-hash-secret"
_ATTRIBUTION_SECRET_LOCK = Lock()


CSV_REQUIRED = {
    "tracking_code",
    "network",
    "conversion_id",
    "occurred_at",
    "status",
    "currency",
    "commission",
}


def _https_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Attribution destinations must be credential-free HTTPS URLs.")
    if parsed.fragment:
        raise ValueError("Attribution destinations cannot contain URL fragments.")
    return value


class TrackingLinkCreate(BaseModel):
    campaign_id: str = Field(min_length=1, max_length=64)
    plan_id: str | None = Field(default=None, max_length=64)
    offer_id: str | None = Field(default=None, max_length=64)
    platform: Platform
    campaign_parameter: str = Field(default="tr_campaign", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
    platform_parameter: str = Field(default="tr_platform", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
    country_destinations: dict[str, str] = Field(default_factory=dict, max_length=20)
    disclosure: str = Field(default="Affiliate link", min_length=2, max_length=500)
    expires_at: datetime | None = None
    confirm_external_action: bool = False

    @field_validator("country_destinations")
    @classmethod
    def valid_country_destinations(cls, values: dict[str, str]) -> dict[str, str]:
        result = {}
        for country, destination in values.items():
            code = country.strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", code):
                raise ValueError("Country destinations use two-letter country codes.")
            result[code] = _https_url(destination)
        return result

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value and value.tzinfo is None:
            raise ValueError("Expiry must include a timezone.")
        return value


class TrackingLinkStatusUpdate(BaseModel):
    status: LinkStatus
    confirm_external_action: bool = False


class ConversionImport(BaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)
    confirm_external_action: bool = False


def _campaign_record(session: Session, workspace_id: str, campaign_id: str) -> Campaign:
    item = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return item


def _tracking_record(session: Session, workspace_id: str, link_id: str) -> TrackingLink:
    item = session.scalar(
        select(TrackingLink).where(
            TrackingLink.id == link_id,
            TrackingLink.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Tracking link not found.")
    return item


def _public_url(code: str) -> str:
    return f"{get_settings().attribution_public_url.rstrip('/')}/c/{code}"


def _link_view(session: Session, item: TrackingLink) -> dict[str, Any]:
    clicks = (
        session.scalar(
            select(func.count(ClickEvent.id)).where(ClickEvent.tracking_link_id == item.id)
        )
        or 0
    )
    conversions = session.scalars(
        select(Conversion).where(Conversion.tracking_link_id == item.id)
    ).all()
    currency_totals: dict[str, int] = defaultdict(int)
    for conversion in conversions:
        if conversion.status == "approved":
            currency_totals[conversion.currency] += conversion.commission_cents
        elif conversion.status in {"reversed", "refunded"}:
            currency_totals[conversion.currency] -= conversion.commission_cents
    return {
        "id": item.id,
        "code": item.code,
        "url": _public_url(item.code),
        "campaign_id": item.campaign_id,
        "plan_id": item.plan_id,
        "offer_id": item.offer_id,
        "product_id": item.product_id,
        "destination_host": urlsplit(item.destination_url).hostname,
        "country_destinations": {
            country: urlsplit(destination).hostname
            for country, destination in item.country_destinations.items()
        },
        "platform": item.platform,
        "campaign_parameter": item.campaign_parameter,
        "platform_parameter": item.platform_parameter,
        "disclosure": item.disclosure,
        "status": item.status,
        "expires_at": item.expires_at,
        "clicks": clicks,
        "conversions": len(
            [conversion for conversion in conversions if conversion.status == "approved"]
        ),
        "pending_conversions": len(
            [conversion for conversion in conversions if conversion.status == "pending"]
        ),
        "commission_by_currency": dict(currency_totals),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _destination(item: TrackingLink, country_code: str | None) -> str:
    base = item.country_destinations.get(country_code or "", item.destination_url)
    parsed = urlsplit(base)
    query = list(parse_qsl(parsed.query, keep_blank_values=True))
    existing = {key.casefold() for key, _value in query}
    for key, value in (
        (item.campaign_parameter, item.campaign_id),
        (item.platform_parameter, item.platform),
    ):
        if key.casefold() not in existing:
            query.append((key, value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _country(request: Request) -> str | None:
    value = (
        (request.headers.get("cf-ipcountry") or request.headers.get("x-vercel-ip-country") or "")
        .strip()
        .upper()
    )
    return value if re.fullmatch(r"[A-Z]{2}", value) else None


def _referrer_origin(request: Request) -> str | None:
    value = request.headers.get("referer", "")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"[:500]


def _user_agent_family(request: Request) -> str | None:
    value = request.headers.get("user-agent", "").casefold()
    for marker, family in (
        ("tiktok", "TikTok"),
        ("instagram", "Instagram"),
        ("facebook", "Facebook"),
        ("youtube", "YouTube"),
        ("edg/", "Edge"),
        ("chrome/", "Chrome"),
        ("safari/", "Safari"),
        ("firefox/", "Firefox"),
    ):
        if marker in value:
            return family
    return "Other" if value else None


def _hash_secret() -> str:
    settings = get_settings()
    configured = settings.attribution_hash_secret.get_secret_value()
    if configured:
        return configured
    if getattr(settings, "environment", "development") == "production":
        raise RuntimeError("ATTRIBUTION_HASH_SECRET is required in production.")
    with _ATTRIBUTION_SECRET_LOCK:
        if _ATTRIBUTION_SECRET_PATH.is_file():
            return _ATTRIBUTION_SECRET_PATH.read_text(encoding="utf-8").strip()
        _ATTRIBUTION_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        secret = token_urlsafe(48)
        _ATTRIBUTION_SECRET_PATH.write_text(secret, encoding="utf-8")
        if os.name != "nt":
            _ATTRIBUTION_SECRET_PATH.chmod(0o600)
        return secret


def _private_visitor_hash(request: Request, occurred_at: datetime, workspace_id: str) -> str | None:
    host = request.client.host if request.client else ""
    if not host:
        return None
    message = f"{workspace_id}:{occurred_at.date().isoformat()}:{host}".encode()
    return hmac.new(_hash_secret().encode(), message, hashlib.sha256).hexdigest()


def _reference_hash(workspace_id: str, network: str, reference: str) -> str:
    message = f"{workspace_id}:{network.casefold()}:{reference}".encode()
    return hmac.new(_hash_secret().encode(), message, hashlib.sha256).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_datetime(value: str, row_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Row {row_number}: occurred_at must be ISO 8601.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Row {row_number}: occurred_at must include a timezone.")
    return parsed.astimezone(UTC)


def _cents(value: str, field: str, row_number: int, *, optional: bool = False) -> int | None:
    cleaned = value.strip()
    if optional and not cleaned:
        return None
    try:
        decimal = Decimal(cleaned)
    except InvalidOperation as error:
        raise ValueError(f"Row {row_number}: {field} must be a monetary amount.") from error
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(f"Row {row_number}: {field} must be zero or greater.")
    return int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@workspace_router.get("/links")
def list_tracking_links(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    items = session.scalars(
        select(TrackingLink)
        .where(TrackingLink.workspace_id == workspace_id)
        .order_by(TrackingLink.created_at.desc())
    ).all()
    return {"links": [_link_view(session, item) for item in items]}


@workspace_router.post("/links", status_code=201)
def create_tracking_link(
    workspace_id: str,
    body: TrackingLinkCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Tracking-link creation requires confirmation.")
    campaign = _campaign_record(session, workspace_id, body.campaign_id)
    plan = None
    if body.plan_id:
        plan = session.scalar(
            select(PublicationPlan).where(
                PublicationPlan.id == body.plan_id,
                PublicationPlan.workspace_id == workspace_id,
                PublicationPlan.campaign_id == campaign.id,
            )
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Publication plan not found.")
    offer = None
    product_id = None
    if body.offer_id:
        offer = session.scalar(
            select(ProductOffer).where(
                ProductOffer.id == body.offer_id,
                ProductOffer.workspace_id == workspace_id,
            )
        )
        if not offer:
            raise HTTPException(status_code=404, detail="Affiliate offer not found.")
        if offer.availability == "unavailable":
            raise HTTPException(status_code=409, detail="Affiliate offer is unavailable.")
        destination = _https_url(offer.affiliate_url)
        product_id = offer.product_id
    else:
        destination_value = (plan.affiliate_url if plan else None) or campaign.affiliate_url
        if not destination_value:
            raise HTTPException(
                status_code=422,
                detail="Choose an offer or add an affiliate URL to the campaign.",
            )
        try:
            destination = _https_url(destination_value)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    if body.campaign_parameter.casefold() == body.platform_parameter.casefold():
        raise HTTPException(status_code=422, detail="Campaign and platform parameters must differ.")
    destinations = [destination, *body.country_destinations.values()]
    existing_parameters = {
        key.casefold()
        for candidate in destinations
        for key, _value in parse_qsl(urlsplit(candidate).query)
    }
    if body.campaign_parameter.casefold() in existing_parameters:
        raise HTTPException(
            status_code=409,
            detail="Campaign parameter already exists in the affiliate destination.",
        )
    if body.platform_parameter.casefold() in existing_parameters:
        raise HTTPException(
            status_code=409,
            detail="Platform parameter already exists in the affiliate destination.",
        )
    ensure_profile(session, user)
    item = TrackingLink(
        code=token_urlsafe(8),
        workspace_id=workspace_id,
        campaign_id=campaign.id,
        plan_id=plan.id if plan else None,
        offer_id=offer.id if offer else None,
        product_id=product_id,
        destination_url=destination,
        country_destinations=body.country_destinations,
        platform=body.platform,
        campaign_parameter=body.campaign_parameter,
        platform_parameter=body.platform_parameter,
        disclosure=body.disclosure.strip(),
        expires_at=body.expires_at,
        created_by=user.id,
    )
    session.add(item)
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "attribution.tracking_link_created",
        "tracking_link",
        item.id,
        {
            "campaign_id": campaign.id,
            "plan_id": item.plan_id,
            "offer_id": item.offer_id,
            "destination_host": urlsplit(destination).hostname,
        },
    )
    return {"link": _link_view(session, item)}


@workspace_router.post("/links/{link_id}/status")
def update_tracking_link_status(
    workspace_id: str,
    link_id: str,
    body: TrackingLinkStatusUpdate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Tracking-link changes require confirmation.")
    item = _tracking_record(session, workspace_id, link_id)
    previous = item.status
    item.status = body.status
    item.updated_at = utc_now()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "attribution.tracking_link_status_changed",
        "tracking_link",
        item.id,
        {"from": previous, "to": body.status},
    )
    return {"link": _link_view(session, item)}


@workspace_router.post("/conversions/import", status_code=201)
def import_conversions(
    workspace_id: str,
    body: ConversionImport,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "analyst"},
    )
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Conversion import requires confirmation.")
    ensure_profile(session, user)
    reader = csv.DictReader(io.StringIO(body.csv_text.lstrip("\ufeff")))
    fields = {field.strip().casefold() for field in reader.fieldnames or [] if field}
    missing = sorted(CSV_REQUIRED - fields)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing conversion CSV columns: {', '.join(missing)}.",
        )
    created = 0
    updated = 0
    matched_clicks = 0
    try:
        for row_number, raw in enumerate(reader, start=2):
            if row_number > 10_001:
                raise ValueError("Conversion import is limited to 10,000 rows.")
            row = {
                (key or "").strip().casefold(): (value or "").strip() for key, value in raw.items()
            }
            if not any(row.values()):
                continue
            code = row["tracking_code"]
            link = session.scalar(
                select(TrackingLink).where(
                    TrackingLink.code == code,
                    TrackingLink.workspace_id == workspace_id,
                )
            )
            if not link:
                raise ValueError(f"Row {row_number}: tracking_code was not found.")
            network = row["network"][:120].casefold()
            reference = row["conversion_id"]
            if not network or not reference:
                raise ValueError(f"Row {row_number}: network and conversion_id are required.")
            status = row["status"].casefold()
            if status not in {"pending", "approved", "reversed", "refunded"}:
                raise ValueError(f"Row {row_number}: status is invalid.")
            currency = row["currency"].upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError(f"Row {row_number}: currency must be a three-letter code.")
            occurred_at = _parse_datetime(row["occurred_at"], row_number)
            commission = _cents(row["commission"], "commission", row_number)
            order_value = _cents(
                row.get("order_value", ""),
                "order_value",
                row_number,
                optional=True,
            )
            reference_hash = _reference_hash(workspace_id, network, reference)
            existing = session.scalar(
                select(Conversion).where(
                    Conversion.workspace_id == workspace_id,
                    Conversion.network == network,
                    Conversion.external_reference_hash == reference_hash,
                )
            )
            offer = session.get(ProductOffer, link.offer_id) if link.offer_id else None
            window_days = offer.cookie_days if offer and offer.cookie_days else 30
            click = session.scalar(
                select(ClickEvent)
                .where(
                    ClickEvent.tracking_link_id == link.id,
                    ClickEvent.occurred_at <= occurred_at,
                    ClickEvent.occurred_at >= occurred_at - timedelta(days=window_days),
                )
                .order_by(ClickEvent.occurred_at.desc())
                .limit(1)
            )
            if click:
                matched_clicks += 1
            if existing:
                existing.click_event_id = click.id if click else None
                existing.occurred_at = occurred_at
                existing.status = status
                existing.currency = currency
                existing.order_value_cents = order_value
                existing.commission_cents = commission or 0
                existing.updated_at = utc_now()
                updated += 1
            else:
                session.add(
                    Conversion(
                        workspace_id=workspace_id,
                        tracking_link_id=link.id,
                        click_event_id=click.id if click else None,
                        campaign_id=link.campaign_id,
                        plan_id=link.plan_id,
                        offer_id=link.offer_id,
                        product_id=link.product_id,
                        network=network,
                        external_reference_hash=reference_hash,
                        occurred_at=occurred_at,
                        status=status,
                        currency=currency,
                        order_value_cents=order_value,
                        commission_cents=commission or 0,
                        raw_metadata={},
                        imported_by=user.id,
                    )
                )
                created += 1
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "attribution.conversions_imported",
        "conversion_import",
        hashlib.sha256(body.csv_text.encode()).hexdigest()[:24],
        {"created": created, "updated": updated, "matched_clicks": matched_clicks},
    )
    return {"created": created, "updated": updated, "matched_clicks": matched_clicks}


def _conversion_view(item: Conversion, link: TrackingLink) -> dict[str, Any]:
    return {
        "id": item.id,
        "tracking_code": link.code,
        "campaign_id": item.campaign_id,
        "plan_id": item.plan_id,
        "offer_id": item.offer_id,
        "product_id": item.product_id,
        "network": item.network,
        "occurred_at": item.occurred_at,
        "status": item.status,
        "currency": item.currency,
        "order_value_cents": item.order_value_cents,
        "commission_cents": item.commission_cents,
        "click_matched": item.click_event_id is not None,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@workspace_router.get("/conversions")
def list_conversions(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    rows = session.execute(
        select(Conversion, TrackingLink)
        .join(TrackingLink, TrackingLink.id == Conversion.tracking_link_id)
        .where(Conversion.workspace_id == workspace_id)
        .order_by(Conversion.occurred_at.desc())
        .limit(500)
    ).all()
    return {"conversions": [_conversion_view(item, link) for item, link in rows]}


def _net_commission(item: Conversion) -> int:
    if item.status == "approved":
        return item.commission_cents
    if item.status in {"reversed", "refunded"}:
        return -item.commission_cents
    return 0


@workspace_router.get("/summary")
def attribution_summary(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    links = session.scalars(
        select(TrackingLink).where(TrackingLink.workspace_id == workspace_id)
    ).all()
    clicks = session.scalars(
        select(ClickEvent).where(ClickEvent.workspace_id == workspace_id)
    ).all()
    conversions = session.scalars(
        select(Conversion).where(Conversion.workspace_id == workspace_id)
    ).all()
    by_currency: dict[str, dict[str, int | float]] = {}
    for currency in sorted({item.currency for item in conversions}):
        related = [item for item in conversions if item.currency == currency]
        net = sum(_net_commission(item) for item in related)
        approved = len([item for item in related if item.status == "approved"])
        by_currency[currency] = {
            "approved_conversions": approved,
            "pending_conversions": len([item for item in related if item.status == "pending"]),
            "reversals": len([item for item in related if item.status in {"reversed", "refunded"}]),
            "net_commission_cents": net,
            "earnings_per_click_cents": round(net / len(clicks), 2) if clicks else 0,
        }
    campaign_names = {
        item.id: item.name
        for item in session.scalars(
            select(Campaign).where(Campaign.workspace_id == workspace_id)
        ).all()
    }
    campaign_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for item in conversions:
        key = (item.campaign_id, item.currency)
        row = campaign_rows.setdefault(
            key,
            {
                "campaign_id": item.campaign_id,
                "campaign_name": campaign_names.get(item.campaign_id, "Unknown campaign"),
                "currency": item.currency,
                "approved_conversions": 0,
                "net_commission_cents": 0,
            },
        )
        if item.status == "approved":
            row["approved_conversions"] += 1
        row["net_commission_cents"] += _net_commission(item)
    format_rows: dict[tuple[str, str], dict[str, Any]] = {}
    plan_ids = {item.plan_id for item in conversions if item.plan_id}
    plan_hashes = (
        {
            item.id: item.video_sha256
            for item in session.scalars(
                select(PublicationPlan).where(PublicationPlan.id.in_(plan_ids))
            ).all()
        }
        if plan_ids
        else {}
    )
    format_by_plan: dict[str, str] = {}
    for plan_id, digest in plan_hashes.items():
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.original_sha256 == digest,
            )
        )
        analysis = (
            session.scalar(
                select(CreativeAnalysis)
                .where(CreativeAnalysis.asset_id == asset.id)
                .order_by(CreativeAnalysis.version.desc())
                .limit(1)
            )
            if asset
            else None
        )
        format_by_plan[plan_id] = (
            analysis.creative_format if analysis and analysis.creative_format else "Unclassified"
        )
    for item in conversions:
        creative_format = (
            format_by_plan.get(item.plan_id, "Unclassified") if item.plan_id else "Unclassified"
        )
        key = (creative_format, item.currency)
        row = format_rows.setdefault(
            key,
            {
                "creative_format": creative_format,
                "currency": item.currency,
                "approved_conversions": 0,
                "net_commission_cents": 0,
            },
        )
        if item.status == "approved":
            row["approved_conversions"] += 1
        row["net_commission_cents"] += _net_commission(item)
    return {
        "totals": {
            "links": len(links),
            "active_links": len([item for item in links if item.status == "active"]),
            "clicks": len(clicks),
            "unique_visitors": len({item.visitor_hash for item in clicks if item.visitor_hash}),
        },
        "by_currency": by_currency,
        "campaigns": sorted(
            campaign_rows.values(),
            key=lambda item: item["net_commission_cents"],
            reverse=True,
        ),
        "creative_formats": sorted(
            format_rows.values(),
            key=lambda item: item["net_commission_cents"],
            reverse=True,
        ),
        "limitations": [
            "Click-through rate requires platform view metrics, which are not synchronized yet.",
            (
                "Unique visitors use workspace-scoped daily HMAC pseudonyms; "
                "raw IP addresses are not stored."
            ),
            "Conversions are matched to the latest eligible click within the offer cookie window.",
        ],
    }


@router.get("/c/{code}/info")
def tracking_link_info(code: str, session: DatabaseSession) -> dict[str, Any]:
    item = session.scalar(select(TrackingLink).where(TrackingLink.code == code))
    if not item:
        raise HTTPException(status_code=404, detail="Tracking link not found.")
    return {
        "code": item.code,
        "destination_host": urlsplit(item.destination_url).hostname,
        "disclosure": item.disclosure,
        "status": item.status,
        "expires_at": item.expires_at,
    }


@router.get("/c/{code}", response_model=None)
def follow_tracking_link(
    code: str, request: Request, session: DatabaseSession
) -> RedirectResponse | JSONResponse:
    item = session.scalar(select(TrackingLink).where(TrackingLink.code == code))
    if not item:
        return JSONResponse(status_code=404, content={"detail": "Tracking link not found."})
    now = datetime.now(UTC)
    if item.expires_at and _aware(item.expires_at) <= now:
        item.status = "expired"
        item.updated_at = utc_now()
    if item.status != "active":
        return JSONResponse(
            status_code=410,
            content={
                "detail": "Tracking link is unavailable.",
                "status": item.status,
                "disclosure": item.disclosure,
            },
        )
    if item.offer_id:
        offer = session.get(ProductOffer, item.offer_id)
        if not offer or offer.availability == "unavailable":
            item.status = "broken"
            item.updated_at = utc_now()
            return JSONResponse(
                status_code=410,
                content={
                    "detail": "Affiliate offer is unavailable.",
                    "status": "broken",
                    "disclosure": item.disclosure,
                },
            )
    country = _country(request)
    click = ClickEvent(
        workspace_id=item.workspace_id,
        tracking_link_id=item.id,
        campaign_id=item.campaign_id,
        plan_id=item.plan_id,
        offer_id=item.offer_id,
        product_id=item.product_id,
        occurred_at=now,
        country_code=country,
        referrer_origin=_referrer_origin(request),
        user_agent_family=_user_agent_family(request),
        visitor_hash=_private_visitor_hash(request, now, item.workspace_id),
    )
    session.add(click)
    return RedirectResponse(
        _destination(item, country),
        status_code=302,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


router.include_router(workspace_router)
