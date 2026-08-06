"""Work-level catalog grouping and book economics.

The endpoints here answer three questions in order: which products look like the
same book, which of those groupings a person has confirmed, and what each book
earned against what its ads cost.

Grouping is never applied silently on a guess. `/works/suggestions` proposes,
`/works/group` applies, and the split and merge endpoints record a decision that
no later import is allowed to undo.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.ad_attribution import Resolution, campaign_key, resolve
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.catalog_identifiers import (
    ONIX_PRODUCT_FORM,
    PRODUCT_FORM_LABEL,
    parse_identifier,
    work_key,
)
from trendrelay_api.catalog_models import (
    AdCampaignMapping,
    AdSpendEntry,
    CatalogWork,
    WorkEdition,
)
from trendrelay_api.catalog_works import (
    AUTO_GROUP_CONFIDENCE,
    WorkEconomics,
    apply_grouping,
    read_edition,
    suggest_groups,
    work_economics,
)
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.opportunity_models import Product

router = APIRouter(prefix="/api/workspaces/{workspace_id}/catalog", tags=["catalog"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]

EDITORS = {"owner", "editor"}
#: One import call covers a reporting window, not a year of history. A cap keeps
#: a mistyped export from writing tens of thousands of rows in one transaction.
MAX_SPEND_ROWS = 2000


class GroupRequest(BaseModel):
    """Apply groupings. Suggestions stay out unless they are named explicitly."""

    #: Match keys the caller has looked at and accepted. Anything not listed is
    #: applied only if the matcher was confident enough to do so on its own.
    accept_keys: list[str] = Field(default_factory=list, max_length=500)


class MergeRequest(BaseModel):
    """Put these products in one work, and record that a person said so."""

    product_ids: list[str] = Field(min_length=2, max_length=100)
    title: str = Field(min_length=1, max_length=400)
    author: str | None = Field(default=None, max_length=240)


class DetachRequest(BaseModel):
    """Take one product out of its work and keep it out."""

    product_id: str = Field(min_length=1, max_length=64)


class SpendRow(BaseModel):
    """One day of spend as an ad platform's insights export reports it.

    Either name the book directly with an identifier, or supply the ad platform's
    own names and let them be resolved. Real exports only ever have the second,
    which is why the names are what the import is built around.
    """

    #: Any identifier one of the book's editions carries. An ISBN-13, an ISBN-10
    #: or an ASIN all resolve to the same work. Wins over the names when given.
    identifier: str | None = Field(default=None, max_length=64)
    campaign_name: str | None = Field(default=None, max_length=400)
    adset_name: str | None = Field(default=None, max_length=400)
    ad_name: str | None = Field(default=None, max_length=400)
    #: The platform's own id for the ad or ad set. Re-importing the same window
    #: updates these rows rather than adding a second copy of the spend.
    external_reference: str = Field(min_length=1, max_length=200)
    spend_date: date
    currency: str = Field(min_length=3, max_length=3)
    spend_cents: int = Field(ge=0)
    impressions: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    campaign_id: str | None = Field(default=None, max_length=64)

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _needs_something_to_resolve_by(self) -> SpendRow:
        if not (self.identifier or self.campaign_name or self.adset_name or self.ad_name):
            raise ValueError(
                "A spend row needs an identifier or an ad, ad set or campaign name."
            )
        return self

    @property
    def label(self) -> str:
        return self.campaign_name or self.ad_name or self.identifier or self.external_reference


class SpendImport(BaseModel):
    source: str = Field(default="meta", min_length=1, max_length=40)
    rows: list[SpendRow] = Field(min_length=1, max_length=MAX_SPEND_ROWS)
    #: Import rows whose book was guessed from the campaign name. Off by default:
    #: spend attributed to the wrong book is worse than spend left out, because
    #: it flatters one book's return while dragging down another's.
    accept_suggested: bool = False


class MappingRequest(BaseModel):
    """Record that this campaign advertises this book."""

    campaign_name: str = Field(min_length=1, max_length=400)
    work_id: str = Field(min_length=1, max_length=64)
    source: str = Field(default="meta", min_length=1, max_length=40)


def _candidates(session: Session, workspace_id: str) -> list[Any]:
    products = session.execute(
        select(Product).where(Product.workspace_id == workspace_id)
    ).scalars().all()
    return [read_edition(product) for product in products]


def _edition_view(edition: Any) -> dict[str, Any]:
    return {
        "product_id": edition.product_id,
        "title": edition.title,
        "author": edition.author,
        "identifier": edition.identifier,
        "identifier_scheme": edition.identifier_scheme,
        "identifier_valid": edition.identifier_valid,
        "product_form": edition.product_form,
        "product_form_label": PRODUCT_FORM_LABEL[edition.product_form],
        "onix_product_form": ONIX_PRODUCT_FORM[edition.product_form],
    }


def _decimal(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _economics_view(report: WorkEconomics) -> dict[str, Any]:
    return {
        "work_id": report.work_id,
        "title": report.title,
        "author": report.author,
        "edition_count": report.edition_count,
        "editions": report.editions,
        # Kept as a list of per-currency totals rather than one blended figure.
        # A single total would have to pick a currency and convert into it, and
        # a rate nobody supplied is a guess wearing a currency symbol.
        "currencies": [
            {
                "currency": bucket.currency,
                "spend_cents": bucket.spend_cents,
                "gross_revenue_cents": bucket.gross_revenue_cents,
                "royalty_cents": bucket.royalty_cents,
                "attributed_royalty_cents": bucket.attributed_royalty_cents,
                "units": bucket.units,
                "impressions": bucket.impressions,
                "clicks": bucket.clicks,
                "roas": _decimal(bucket.roas),
                "acos": _decimal(bucket.acos),
                "tacos": _decimal(bucket.tacos),
            }
            for bucket in sorted(report.buckets.values(), key=lambda item: item.currency)
        ],
        "mixed_currency": report.mixed_currency,
    }


@router.get("/works")
def list_works(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    since: date | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    """Every grouped book with its economics, one row per book rather than per edition."""
    membership(session, workspace_id, user.id)
    reports = work_economics(session, workspace_id=workspace_id, since=since, until=until)
    return {
        "works": [_economics_view(report) for report in reports],
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
    }


@router.get("/works/suggestions")
def list_suggestions(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Proposed groupings, separated into what will apply itself and what will not."""
    membership(session, workspace_id, user.id)
    grouped = {
        row[0]
        for row in session.execute(
            select(WorkEdition.product_id).where(
                WorkEdition.workspace_id == workspace_id,
                WorkEdition.work_id.is_not(None),
            )
        ).all()
    }

    suggestions = suggest_groups(_candidates(session, workspace_id))
    payload = []
    for suggestion in suggestions:
        if len(suggestion.editions) < 2:
            continue
        pending = [item for item in suggestion.editions if item.product_id not in grouped]
        payload.append(
            {
                "match_key": suggestion.match_key,
                "title": suggestion.title,
                "author": suggestion.author,
                "confidence": suggestion.confidence,
                "reason": suggestion.reason,
                "automatic": suggestion.automatic,
                "already_grouped": not pending,
                "editions": [_edition_view(item) for item in suggestion.editions],
            }
        )
    return {"suggestions": payload, "auto_threshold": AUTO_GROUP_CONFIDENCE}


@router.post("/works/group")
def group_works(
    workspace_id: str,
    body: GroupRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Apply confident groupings, plus any suggestion the caller named."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)

    suggestions = suggest_groups(_candidates(session, workspace_id))
    accepted = set(body.accept_keys)
    confident = [item for item in suggestions if item.automatic]
    chosen = [item for item in suggestions if item.match_key in accepted and not item.automatic]

    summary = apply_grouping(
        session,
        workspace_id=workspace_id,
        user_id=user.id,
        suggestions=confident,
    )
    if chosen:
        extra = apply_grouping(
            session,
            workspace_id=workspace_id,
            user_id=user.id,
            suggestions=chosen,
            include_suggested=True,
        )
        for key, value in extra.items():
            summary[key] += value

    audit(
        session,
        request,
        workspace_id,
        user.id,
        "catalog.works.grouped",
        "catalog_work",
        workspace_id,
        {**summary, "accepted_keys": len(accepted)},
    )
    session.commit()
    return summary


@router.post("/works/merge")
def merge_works(
    workspace_id: str,
    body: MergeRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Group these editions because a person says they are one book.

    Recorded as a manual assignment, which the matcher treats as settled. Without
    that flag the next import would re-derive its own answer and undo this.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)

    products = session.execute(
        select(Product).where(
            Product.workspace_id == workspace_id,
            Product.id.in_(body.product_ids),
        )
    ).scalars().all()
    if len(products) != len(set(body.product_ids)):
        raise HTTPException(status_code=404, detail="One or more products were not found.")

    key = work_key(body.title, body.author) or f"manual:{sorted(body.product_ids)[0]}"
    work = session.execute(
        select(CatalogWork).where(
            CatalogWork.workspace_id == workspace_id,
            CatalogWork.match_key == key,
        )
    ).scalar_one_or_none()
    if work is None:
        work = CatalogWork(
            workspace_id=workspace_id,
            match_key=key,
            title=body.title,
            author=body.author,
            origin="manual",
            created_by=user.id,
        )
        session.add(work)
        session.flush()
    else:
        work.origin = "manual"
        work.title = body.title
        work.author = body.author
        work.updated_at = datetime.now(tz=None)

    for product in products:
        edition = read_edition(product, author=body.author)
        existing = session.execute(
            select(WorkEdition).where(
                WorkEdition.workspace_id == workspace_id,
                WorkEdition.product_id == product.id,
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                WorkEdition(
                    workspace_id=workspace_id,
                    work_id=work.id,
                    product_id=product.id,
                    identifier=edition.identifier or None,
                    identifier_scheme=edition.identifier_scheme,
                    product_form=edition.product_form,
                    assignment="manual",
                    confidence=100,
                    created_by=user.id,
                )
            )
        else:
            existing.work_id = work.id
            existing.assignment = "manual"
            existing.confidence = 100
            existing.updated_at = datetime.now(tz=None)

    audit(
        session,
        request,
        workspace_id,
        user.id,
        "catalog.works.merged",
        "catalog_work",
        work.id,
        {"product_ids": body.product_ids, "title": body.title},
    )
    session.commit()
    return {"work_id": work.id, "edition_count": len(products)}


@router.post("/works/detach")
def detach_edition(
    workspace_id: str,
    body: DetachRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Split one edition out of its work, permanently.

    The membership row survives with no work attached. That is the record which
    stops the matcher from grouping this product again on the next run, so the
    row is kept rather than deleted.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)

    edition = session.execute(
        select(WorkEdition).where(
            WorkEdition.workspace_id == workspace_id,
            WorkEdition.product_id == body.product_id,
        )
    ).scalar_one_or_none()
    if edition is None:
        product = session.get(Product, body.product_id)
        if product is None or product.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="That product was not found.")
        candidate = read_edition(product)
        edition = WorkEdition(
            workspace_id=workspace_id,
            work_id=None,
            product_id=product.id,
            identifier=candidate.identifier or None,
            identifier_scheme=candidate.identifier_scheme,
            product_form=candidate.product_form,
            assignment="detached",
            confidence=0,
            created_by=user.id,
        )
        session.add(edition)
    else:
        edition.work_id = None
        edition.assignment = "detached"
        edition.confidence = 0
        edition.updated_at = datetime.now(tz=None)

    audit(
        session,
        request,
        workspace_id,
        user.id,
        "catalog.works.detached",
        "catalog_work",
        body.product_id,
        {},
    )
    session.commit()
    return {"product_id": body.product_id, "assignment": "detached"}


@router.post("/ad-spend/import")
def import_ad_spend(
    workspace_id: str,
    body: SpendImport,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Record daily ad spend against the book it was spent on.

    Spend is stored against the *work*, never one edition: the campaign was for
    the book, and a reader who clicks an ad buys whichever format suits them, so
    charging the cost to one format would make every format's numbers wrong.

    Rows that cannot be attributed are returned rather than skipped quietly.
    Spend that fails to attach makes every book's return look better than it is,
    which is the one failure here that the numbers themselves would not show.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)

    written = 0
    updated = 0
    skipped: list[dict[str, Any]] = []

    for row in body.rows:
        outcome = _resolve_row(session, workspace_id=workspace_id, source=body.source, row=row)
        if outcome.work_id is None or (not outcome.automatic and not body.accept_suggested):
            skipped.append(_skip_view(row, outcome))
            continue

        existing = session.execute(
            select(AdSpendEntry).where(
                AdSpendEntry.workspace_id == workspace_id,
                AdSpendEntry.source == body.source,
                AdSpendEntry.external_reference == row.external_reference,
                AdSpendEntry.spend_date == row.spend_date,
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                AdSpendEntry(
                    workspace_id=workspace_id,
                    work_id=outcome.work_id,
                    product_id=None,
                    campaign_id=row.campaign_id,
                    source=body.source,
                    external_reference=row.external_reference,
                    campaign_key=outcome.campaign_key or None,
                    campaign_name=row.campaign_name,
                    spend_date=row.spend_date,
                    currency=row.currency,
                    spend_cents=row.spend_cents,
                    impressions=row.impressions,
                    clicks=row.clicks,
                    imported_by=user.id,
                )
            )
            written += 1
        else:
            existing.work_id = outcome.work_id
            existing.campaign_id = row.campaign_id
            existing.campaign_key = outcome.campaign_key or None
            existing.campaign_name = row.campaign_name
            existing.currency = row.currency
            existing.spend_cents = row.spend_cents
            existing.impressions = row.impressions
            existing.clicks = row.clicks
            existing.updated_at = datetime.now(tz=None)
            updated += 1

    audit(
        session,
        request,
        workspace_id,
        user.id,
        "catalog.ad_spend.imported",
        "ad_spend",
        workspace_id,
        {
            "source": body.source,
            "written": written,
            "updated": updated,
            "skipped": len(skipped),
        },
    )
    session.commit()
    return {
        "written": written,
        "updated": updated,
        "skipped": skipped[:100],
        "skipped_count": len(skipped),
        "skipped_spend_cents": _skipped_totals(skipped),
    }


def _resolve_row(
    session: Session, *, workspace_id: str, source: str, row: SpendRow
) -> Resolution:
    """Which book this row belongs to, preferring a stated identifier."""
    if row.identifier:
        identifier = parse_identifier(row.identifier)
        edition = session.execute(
            select(WorkEdition).where(
                WorkEdition.workspace_id == workspace_id,
                WorkEdition.work_id.is_not(None),
                WorkEdition.identifier.in_(
                    [value for value in {identifier.canonical, identifier.value} if value]
                ),
            )
        ).scalars().first()
        if edition is not None:
            return Resolution(
                work_id=edition.work_id,
                method="identifier",
                confidence=100,
                reason=f"Row names {row.identifier}.",
                campaign_key=campaign_key(row.campaign_name),
            )
        return Resolution(
            work_id=None,
            method="unresolved",
            confidence=0,
            reason=f"No grouped book carries {row.identifier}.",
            campaign_key=campaign_key(row.campaign_name),
        )

    return resolve(
        session,
        workspace_id=workspace_id,
        source=source,
        campaign_name=row.campaign_name,
        adset_name=row.adset_name,
        ad_name=row.ad_name,
    )


def _skip_view(row: SpendRow, outcome: Resolution) -> dict[str, Any]:
    return {
        "label": row.label,
        "campaign_name": row.campaign_name,
        "campaign_key": outcome.campaign_key,
        "external_reference": row.external_reference,
        "spend_date": row.spend_date.isoformat(),
        "currency": row.currency,
        "spend_cents": row.spend_cents,
        "method": outcome.method,
        "reason": outcome.reason,
        # Present for a guessed match, so the interface can offer "this campaign
        # is that book" as one click rather than a search.
        "suggested_work_id": outcome.work_id if not outcome.automatic else None,
    }


def _skipped_totals(skipped: list[dict[str, Any]]) -> dict[str, int]:
    """How much spend went unattributed, per currency.

    A count of rows understates this — one campaign can be most of a budget — and
    the amount is what decides whether the ratios above are worth reading.
    """
    totals: dict[str, int] = {}
    for item in skipped:
        totals[item["currency"]] = totals.get(item["currency"], 0) + item["spend_cents"]
    return totals


@router.post("/ad-spend/resolve")
def resolve_ad_spend(
    workspace_id: str,
    body: SpendImport,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Say what each row would attribute to, without writing anything.

    Worth having as its own call: an import that silently drops a third of a
    budget looks identical to one that worked, and this is how that gets seen
    before the numbers are trusted.
    """
    membership(session, workspace_id, user.id)

    rows = []
    for row in body.rows:
        outcome = _resolve_row(session, workspace_id=workspace_id, source=body.source, row=row)
        rows.append(
            {
                **_skip_view(row, outcome),
                "work_id": outcome.work_id,
                "automatic": outcome.automatic,
                "confidence": outcome.confidence,
            }
        )
    return {
        "rows": rows,
        "ready": sum(1 for item in rows if item["automatic"]),
        "needs_confirming": sum(
            1 for item in rows if item["work_id"] and not item["automatic"]
        ),
        "unresolved": sum(1 for item in rows if not item["work_id"]),
    }


@router.get("/ad-spend/mappings")
def list_mappings(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Every campaign whose book somebody has settled."""
    membership(session, workspace_id, user.id)
    rows = session.execute(
        select(AdCampaignMapping, CatalogWork)
        .join(CatalogWork, CatalogWork.id == AdCampaignMapping.work_id)
        .where(AdCampaignMapping.workspace_id == workspace_id)
        .order_by(AdCampaignMapping.campaign_name)
    ).all()
    return {
        "mappings": [
            {
                "id": mapping.id,
                "campaign_name": mapping.campaign_name,
                "campaign_key": mapping.campaign_key,
                "source": mapping.source,
                "work_id": work.id,
                "work_title": work.title,
                "origin": mapping.origin,
            }
            for mapping, work in rows
        ]
    }


@router.post("/ad-spend/mappings")
def create_mapping(
    workspace_id: str,
    body: MappingRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Settle which book a campaign advertises, for this import and every later one."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)

    work = session.get(CatalogWork, body.work_id)
    if work is None or work.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="That book was not found.")

    key = campaign_key(body.campaign_name)
    if not key:
        raise HTTPException(status_code=422, detail="That campaign name has nothing to match on.")

    existing = session.execute(
        select(AdCampaignMapping).where(
            AdCampaignMapping.workspace_id == workspace_id,
            AdCampaignMapping.source == body.source,
            AdCampaignMapping.campaign_key == key,
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = AdCampaignMapping(
            workspace_id=workspace_id,
            work_id=work.id,
            source=body.source,
            campaign_key=key,
            campaign_name=body.campaign_name,
            origin="manual",
            created_by=user.id,
        )
        session.add(existing)
    else:
        existing.work_id = work.id
        existing.campaign_name = body.campaign_name
        existing.origin = "manual"
        existing.updated_at = datetime.now(tz=None)

    # Spend already imported under a different answer is corrected too, so the
    # decision applies to the history rather than only to what comes next.
    moved = 0
    for entry in session.execute(
        select(AdSpendEntry).where(
            AdSpendEntry.workspace_id == workspace_id,
            AdSpendEntry.source == body.source,
            AdSpendEntry.campaign_key == key,
            AdSpendEntry.work_id != work.id,
        )
    ).scalars():
        entry.work_id = work.id
        entry.updated_at = datetime.now(tz=None)
        moved += 1

    audit(
        session,
        request,
        workspace_id,
        user.id,
        "catalog.ad_spend.mapped",
        "ad_campaign_mapping",
        work.id,
        {"campaign_name": body.campaign_name, "moved_entries": moved},
    )
    session.commit()
    return {"work_id": work.id, "campaign_key": key, "moved_entries": moved}
