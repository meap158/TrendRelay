"""Grouping editions into works, and reporting economics at the work level.

Two jobs live here. The first decides which products are the same book and
records that decision. The second answers what a book earned against what it
cost to advertise — the question the grouping exists to make answerable.

Both are deliberately conservative. Grouping applies itself only when title and
author agree exactly, and anything looser is offered as a suggestion for a
person to confirm; a wrong merge pools two books' revenue and is invisible once
it has happened. Reporting refuses to divide amounts in different currencies
rather than producing a number that looks authoritative and means nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import Conversion
from trendrelay_api.catalog_identifiers import (
    ONIX_PRODUCT_FORM,
    PRODUCT_FORM_LABEL,
    display_title,
    identifier_from_url,
    infer_product_form,
    loose_work_key,
    parse_identifier,
    work_key,
)
from trendrelay_api.catalog_models import AdSpendEntry, CatalogWork, WorkEdition
from trendrelay_api.opportunity_models import Product

#: A grouping applies itself at or above this confidence. Below it the grouping
#: is shown as a suggestion, because an incorrect merge silently pools the
#: revenue of two books and nothing downstream can detect it afterwards.
AUTO_GROUP_CONFIDENCE = 90

#: Title and author both agree once edition wording is stripped.
EXACT_CONFIDENCE = 96
#: Titles agree only after dropping the subtitle. Editions genuinely disagree
#: about subtitles, so this is a real signal — but it is also how two books in a
#: series get confused, so it never applies itself.
SUBTITLE_CONFIDENCE = 72
#: Titles agree but no author is recorded. Far too many distinct books share a
#: title for this to stand on its own.
TITLE_ONLY_CONFIDENCE = 40

#: Conversions in these states are money that did not survive. Counting them
#: would overstate revenue and flatter every ratio computed from it.
VOID_CONVERSION_STATES = frozenset({"reversed", "refunded"})


@dataclass(frozen=True)
class EditionCandidate:
    """A product read as a manifestation, ready to be matched."""

    product_id: str
    title: str
    author: str | None
    identifier: str
    identifier_scheme: str
    identifier_valid: bool
    product_form: str

    @property
    def onix_product_form(self) -> str:
        return ONIX_PRODUCT_FORM[self.product_form]


@dataclass(frozen=True)
class WorkSuggestion:
    """A proposed grouping, with why it was proposed."""

    match_key: str
    title: str
    author: str | None
    confidence: int
    reason: str
    editions: list[EditionCandidate]

    @property
    def automatic(self) -> bool:
        return self.confidence >= AUTO_GROUP_CONFIDENCE and len(self.editions) > 1


def read_edition(product: Product, author: str | None = None) -> EditionCandidate:
    """Read one catalog row as the manifestation it describes.

    The author is taken from the product's `brand` column when nothing better is
    supplied. For a book catalog that column holds the byline — it is the field
    an import maps an author onto — and matching without it is unsafe.

    Note that `catalog_key` is deliberately not consulted: it is a content hash
    TrendRelay computes to deduplicate imports, and reading it as an identifier
    would produce a valid-looking key that means nothing to any book registry.
    """
    identifier = parse_identifier(
        product.identifier or identifier_from_url(product.product_url)
    )
    byline = author if author is not None else product.brand
    return EditionCandidate(
        product_id=product.id,
        title=product.name,
        author=byline,
        identifier=identifier.canonical or identifier.value,
        identifier_scheme=identifier.scheme,
        identifier_valid=identifier.valid,
        product_form=infer_product_form(product.name, product.category, product.marketplace),
    )


def suggest_groups(editions: list[EditionCandidate]) -> list[WorkSuggestion]:
    """Cluster manifestations into the works they belong to.

    Two passes, strongest first. Exact title-and-author agreement settles most
    of a real catalog; whatever is still alone afterwards gets a second look
    with the subtitle removed, which is where "Dune" and "Dune: A Novel" finally
    meet. Anything matched in the second pass stays a suggestion.
    """
    exact: dict[str, list[EditionCandidate]] = defaultdict(list)
    unkeyed: list[EditionCandidate] = []
    for edition in editions:
        key = work_key(edition.title, edition.author)
        if key:
            exact[key].append(edition)
        else:
            unkeyed.append(edition)

    suggestions: list[WorkSuggestion] = []
    leftovers: list[tuple[str, list[EditionCandidate]]] = []
    for key, group in exact.items():
        has_author = bool(group[0].author and group[0].author.strip())
        if len(group) > 1 and has_author:
            suggestions.append(
                WorkSuggestion(
                    match_key=key,
                    title=_representative_title(group),
                    author=group[0].author,
                    confidence=EXACT_CONFIDENCE,
                    reason="Title and author match once edition wording is removed.",
                    editions=group,
                )
            )
        else:
            leftovers.append((key, group))

    # The looser pass only ever sees what the strict pass could not place, so a
    # confident grouping is never weakened by a later, vaguer one.
    loose: dict[str, list[EditionCandidate]] = defaultdict(list)
    for key, group in leftovers:
        loose[loose_work_key(group[0].title, group[0].author) or key].extend(group)

    for key, group in loose.items():
        has_author = bool(group[0].author and group[0].author.strip())
        if len(group) < 2:
            continue
        suggestions.append(
            WorkSuggestion(
                match_key=key,
                title=_representative_title(group),
                author=group[0].author,
                confidence=SUBTITLE_CONFIDENCE if has_author else TITLE_ONLY_CONFIDENCE,
                reason=(
                    "Titles match once the subtitle is dropped — confirm these are one book."
                    if has_author
                    else "Titles match but no author is recorded, so this needs confirming."
                ),
                editions=group,
            )
        )

    for edition in unkeyed:
        suggestions.append(
            WorkSuggestion(
                match_key="",
                title=edition.title,
                author=edition.author,
                confidence=0,
                reason="No usable title, so this edition cannot be matched.",
                editions=[edition],
            )
        )

    suggestions.sort(key=lambda item: (-item.confidence, item.title))
    return suggestions


def _representative_title(group: list[EditionCandidate]) -> str:
    """The book's name, taken as the fullest title once edition wording is gone.

    Fullest because one edition often drops the subtitle and the longer form is
    the one a person recognises; stripped first so the work is not named after
    whichever edition had the longest format suffix.
    """
    titles = [display_title(edition.title) or edition.title for edition in group]
    return max(titles, key=len)


def settled_product_ids(session: Session, workspace_id: str) -> set[str]:
    """Products whose placement a person decided, which the matcher must not touch."""
    rows = session.execute(
        select(WorkEdition.product_id).where(
            WorkEdition.workspace_id == workspace_id,
            WorkEdition.assignment.in_(("manual", "detached")),
        )
    ).all()
    return {row[0] for row in rows}


def apply_grouping(
    session: Session,
    *,
    workspace_id: str,
    user_id: str,
    suggestions: list[WorkSuggestion],
    include_suggested: bool = False,
) -> dict[str, Any]:
    """Persist groupings, leaving every manual decision exactly as it was found.

    Idempotent by construction: a work is looked up by its match key before being
    created, and an edition row is updated in place rather than added again. That
    is what lets an import run repeatedly without multiplying works.
    """
    settled = settled_product_ids(session, workspace_id)
    created_works = 0
    linked = 0
    skipped_settled = 0

    for suggestion in suggestions:
        if len(suggestion.editions) < 2 or not suggestion.match_key:
            continue
        if not suggestion.automatic and not include_suggested:
            continue

        movable = [item for item in suggestion.editions if item.product_id not in settled]
        skipped_settled += len(suggestion.editions) - len(movable)
        if not movable:
            continue

        work = session.execute(
            select(CatalogWork).where(
                CatalogWork.workspace_id == workspace_id,
                CatalogWork.match_key == suggestion.match_key,
            )
        ).scalar_one_or_none()
        if work is None:
            work = CatalogWork(
                workspace_id=workspace_id,
                match_key=suggestion.match_key,
                title=suggestion.title,
                author=suggestion.author,
                origin="matched",
                created_by=user_id,
            )
            session.add(work)
            session.flush()
            created_works += 1

        for edition in movable:
            linked += _upsert_edition(
                session,
                workspace_id=workspace_id,
                user_id=user_id,
                work_id=work.id,
                edition=edition,
                assignment="automatic",
                confidence=suggestion.confidence,
            )

    return {
        "works_created": created_works,
        "editions_linked": linked,
        "editions_left_alone": skipped_settled,
    }


def _upsert_edition(
    session: Session,
    *,
    workspace_id: str,
    user_id: str,
    work_id: str | None,
    edition: EditionCandidate,
    assignment: str,
    confidence: int,
) -> int:
    """Write one membership row, returning 1 when it actually changed anything."""
    existing = session.execute(
        select(WorkEdition).where(
            WorkEdition.workspace_id == workspace_id,
            WorkEdition.product_id == edition.product_id,
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            WorkEdition(
                workspace_id=workspace_id,
                work_id=work_id,
                product_id=edition.product_id,
                identifier=edition.identifier or None,
                identifier_scheme=edition.identifier_scheme,
                product_form=edition.product_form,
                assignment=assignment,
                confidence=confidence,
                created_by=user_id,
            )
        )
        return 1

    if existing.work_id == work_id and existing.assignment == assignment:
        return 0

    existing.work_id = work_id
    existing.assignment = assignment
    existing.confidence = confidence
    existing.identifier = edition.identifier or None
    existing.identifier_scheme = edition.identifier_scheme
    existing.product_form = edition.product_form
    existing.updated_at = datetime.now(tz=None)
    return 1


# --------------------------------------------------------------------------- #
# Economics
# --------------------------------------------------------------------------- #


@dataclass
class CurrencyBucket:
    """Everything one work earned and spent in a single currency.

    Amounts are never combined across currencies. Doing so is how a table ends
    up showing dong amounts with a dollar total, or the reverse — the figure is
    wrong by a factor of tens of thousands and reads as plausible.
    """

    currency: str
    spend_cents: int = 0
    gross_revenue_cents: int = 0
    royalty_cents: int = 0
    attributed_royalty_cents: int = 0
    units: int = 0
    impressions: int = 0
    clicks: int = 0

    @property
    def roas(self) -> Decimal | None:
        """Revenue returned per unit of ad spend, over ad-attributed revenue."""
        return _ratio(self.attributed_royalty_cents, self.spend_cents)

    @property
    def acos(self) -> Decimal | None:
        """Ad cost as a share of the revenue those ads produced — ROAS inverted."""
        return _ratio(self.spend_cents, self.attributed_royalty_cents)

    @property
    def tacos(self) -> Decimal | None:
        """Ad cost against *all* revenue, the measure of how ad-dependent a book is.

        Total rather than attributed revenue is the whole distinction from ACoS,
        and the reason publishers watch it: a book whose organic sales are
        growing shows a falling TACoS even while ad spend holds steady.
        """
        return _ratio(self.spend_cents, self.royalty_cents)


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    """A ratio, or nothing at all when the denominator would make it meaningless."""
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


@dataclass
class WorkEconomics:
    work_id: str
    title: str
    author: str | None
    edition_count: int
    editions: list[dict[str, Any]] = field(default_factory=list)
    buckets: dict[str, CurrencyBucket] = field(default_factory=dict)

    def bucket(self, currency: str) -> CurrencyBucket:
        return self.buckets.setdefault(currency, CurrencyBucket(currency=currency))

    @property
    def mixed_currency(self) -> bool:
        """True when this work's money arrived in more than one currency."""
        return len({key for key, value in self.buckets.items() if _has_money(value)}) > 1


def _has_money(bucket: CurrencyBucket) -> bool:
    return bool(bucket.spend_cents or bucket.royalty_cents or bucket.gross_revenue_cents)


def work_economics(
    session: Session,
    *,
    workspace_id: str,
    since: date | None = None,
    until: date | None = None,
) -> list[WorkEconomics]:
    """Revenue and ad spend for every grouped work, kept separate by currency.

    Revenue here is commission — the royalty actually earned — not the customer's
    order value, because that is the number a ratio against ad spend has to use
    for the result to mean anything to whoever is paying for the ads.
    """
    works = session.execute(
        select(CatalogWork)
        .where(CatalogWork.workspace_id == workspace_id)
        .order_by(CatalogWork.title)
    ).scalars().all()
    if not works:
        return []

    reports = {
        work.id: WorkEconomics(
            work_id=work.id,
            title=work.title,
            author=work.author,
            edition_count=0,
        )
        for work in works
    }

    membership: dict[str, str] = {}
    edition_rows = session.execute(
        select(WorkEdition, Product)
        .join(Product, Product.id == WorkEdition.product_id)
        .where(
            WorkEdition.workspace_id == workspace_id,
            WorkEdition.work_id.is_not(None),
        )
    ).all()
    for edition, product in edition_rows:
        report = reports.get(edition.work_id or "")
        if report is None:
            continue
        membership[edition.product_id] = edition.work_id or ""
        report.edition_count += 1
        report.editions.append(
            {
                "product_id": product.id,
                "name": product.name,
                "identifier": edition.identifier,
                "identifier_scheme": edition.identifier_scheme,
                "product_form": edition.product_form,
                "product_form_label": PRODUCT_FORM_LABEL[edition.product_form],
                "onix_product_form": ONIX_PRODUCT_FORM[edition.product_form],
                "marketplace": product.marketplace,
                "assignment": edition.assignment,
                "confidence": edition.confidence,
            }
        )

    spend_filters = [AdSpendEntry.workspace_id == workspace_id, AdSpendEntry.work_id.is_not(None)]
    if since is not None:
        spend_filters.append(AdSpendEntry.spend_date >= since)
    if until is not None:
        spend_filters.append(AdSpendEntry.spend_date <= until)

    advertised_campaigns: dict[str, set[str]] = defaultdict(set)
    for entry in session.execute(select(AdSpendEntry).where(*spend_filters)).scalars():
        report = reports.get(entry.work_id or "")
        if report is None:
            continue
        bucket = report.bucket(entry.currency)
        bucket.spend_cents += entry.spend_cents
        bucket.impressions += entry.impressions
        bucket.clicks += entry.clicks
        if entry.campaign_id:
            advertised_campaigns[entry.work_id or ""].add(entry.campaign_id)

    conversion_filters = [
        Conversion.workspace_id == workspace_id,
        Conversion.product_id.is_not(None),
        Conversion.status.not_in(tuple(VOID_CONVERSION_STATES)),
    ]
    if since is not None:
        start = datetime.combine(since, datetime.min.time())
        conversion_filters.append(Conversion.occurred_at >= start)
    if until is not None:
        # The whole of the closing day, since the window is given in dates and a
        # bare date would silently exclude everything that happened that day.
        end = datetime.combine(until, datetime.max.time())
        conversion_filters.append(Conversion.occurred_at <= end)

    for conversion in session.execute(select(Conversion).where(*conversion_filters)).scalars():
        work_id = membership.get(conversion.product_id or "")
        report = reports.get(work_id or "")
        if report is None:
            continue
        bucket = report.bucket(conversion.currency)
        bucket.royalty_cents += conversion.commission_cents
        bucket.gross_revenue_cents += conversion.order_value_cents or 0
        bucket.units += 1
        # Ad-attributed revenue means revenue from a campaign that actually ran
        # ads in this window. Without that restriction ROAS would credit the ads
        # with every organic sale the book made.
        if conversion.campaign_id in advertised_campaigns.get(work_id or "", set()):
            bucket.attributed_royalty_cents += conversion.commission_cents

    return [report for report in reports.values() if report.edition_count]
