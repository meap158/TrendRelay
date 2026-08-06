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
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.catalog_identifiers import (
    ONIX_PRODUCT_FORM,
    PRODUCT_FORM_LABEL,
    parse_identifier,
    work_key,
)
from trendrelay_api.catalog_models import AdSpendEntry, CatalogWork, WorkEdition
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
    """One day of spend as an ad platform's insights export reports it."""

    #: The book this spend was for, named by any identifier one of its editions
    #: carries. An ISBN-13, an ISBN-10 or an ASIN all resolve to the same work.
    identifier: str = Field(min_length=1, max_length=64)
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


class SpendImport(BaseModel):
    source: str = Field(default="meta", min_length=1, max_length=40)
    rows: list[SpendRow] = Field(min_length=1, max_length=MAX_SPEND_ROWS)


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

    Rows name an edition by its identifier and are stored against that edition's
    *work*, because the campaign was for the book: a reader who clicks an ad buys
    whichever format suits them, so charging the cost to one format would make
    every format's numbers wrong.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)

    editions = session.execute(
        select(WorkEdition).where(
            WorkEdition.workspace_id == workspace_id,
            WorkEdition.work_id.is_not(None),
        )
    ).scalars().all()
    by_identifier: dict[str, WorkEdition] = {}
    for edition in editions:
        if edition.identifier:
            by_identifier[edition.identifier] = edition

    written = 0
    updated = 0
    unmatched: list[str] = []

    for row in body.rows:
        identifier = parse_identifier(row.identifier)
        edition = by_identifier.get(identifier.canonical) or by_identifier.get(identifier.value)
        if edition is None:
            # Reported rather than dropped silently: unattributed spend makes
            # every ratio look better than it is, so it has to be visible.
            unmatched.append(row.identifier)
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
                    work_id=edition.work_id,
                    product_id=edition.product_id,
                    campaign_id=row.campaign_id,
                    source=body.source,
                    external_reference=row.external_reference,
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
            existing.work_id = edition.work_id
            existing.product_id = edition.product_id
            existing.campaign_id = row.campaign_id
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
            "unmatched": len(unmatched),
        },
    )
    session.commit()
    return {
        "written": written,
        "updated": updated,
        "unmatched": unmatched[:50],
        "unmatched_count": len(unmatched),
    }
