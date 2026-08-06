"""Deciding which book an ad was for.

Ad platforms report spend against their own objects — an ad, an ad set, a
campaign — and none of them knows what an ISBN is. Something has to close that
gap, and the industry has settled on campaign naming conventions: advertisers
put the SKU in the campaign name precisely so their reporting can find it again.
Amazon Ads makes the link structural by tying campaigns to ASINs; on Meta it is
a convention, so it has to be read rather than looked up.

Three ways to resolve a row, in strict order of how much they can be trusted:

1. A stored mapping for this campaign. Somebody decided it, so nothing else may
   overrule it and no later import may quietly change it.
2. An identifier in one of the names. Deterministic, and it resolves through the
   editions already grouped under a work.
3. The book's title appearing in the name. A real signal and often the only one
   available, but it guesses — so it is offered for confirmation and never
   applied on its own.

Unresolved spend is reported, never dropped. Spend that silently fails to
attribute makes every book's return look better than it is, which is the one
failure here nobody would notice from the numbers.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.catalog_identifiers import normalise_title, parse_identifier
from trendrelay_api.catalog_models import AdCampaignMapping, CatalogWork, WorkEdition

ResolutionMethod = Literal["mapping", "identifier", "title", "ambiguous", "unresolved"]

#: Methods precise enough to import without asking. Anything else is a
#: suggestion, for the same reason a loose title match never groups a book.
AUTOMATIC_METHODS = frozenset({"mapping", "identifier"})

#: Candidate identifier tokens inside a free-text name. Bounded by non-alphanumerics
#: so an ASIN embedded in a longer word is not mistaken for one.
_TOKEN = re.compile(
    r"(?<![0-9A-Za-z])"
    r"((?:97[89][0-9]{10})|(?:B[0-9A-Z]{9})|(?:[0-9]{9}[0-9X]))"
    r"(?![0-9A-Za-z])"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Resolution:
    """Which book a spend row belongs to, and how that was decided."""

    work_id: str | None
    method: ResolutionMethod
    confidence: int
    reason: str
    #: The normalised campaign name, so a confirmed guess can be stored against it.
    campaign_key: str = ""

    @property
    def automatic(self) -> bool:
        return self.work_id is not None and self.method in AUTOMATIC_METHODS


def campaign_key(*names: str | None) -> str:
    """A stable key for a campaign name, insensitive to case and punctuation.

    Built from the whole name rather than a parsed part of it. Advertisers rename
    campaigns by adding a suffix far more often than they rewrite them, but a key
    that tried to be clever about which part matters would move the moment the
    convention changed.
    """
    joined = " ".join(name for name in names if name)
    decomposed = unicodedata.normalize("NFKD", joined).lower()
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_ALNUM.sub(" ", stripped).strip()


def extract_identifiers(*names: str | None) -> list[str]:
    """Every ISBN or ASIN that appears in these names, checksums verified.

    Verification matters more here than when reading a catalog column: campaign
    names are full of numbers — dates, budgets, audience sizes — and a
    thirteen-digit run that fails its check digit is one of those, not a book.
    """
    found: list[str] = []
    for name in names:
        if not name:
            continue
        for match in _TOKEN.finditer(name.upper()):
            identifier = parse_identifier(match.group(1))
            if identifier.valid and identifier.canonical not in found:
                found.append(identifier.canonical)
    return found


def _title_tokens(title: str) -> list[str]:
    return [token for token in normalise_title(title).split() if token]


def resolve(
    session: Session,
    *,
    workspace_id: str,
    source: str,
    campaign_name: str | None,
    adset_name: str | None = None,
    ad_name: str | None = None,
) -> Resolution:
    """Work out which book one ad's spend belongs to."""
    key = campaign_key(campaign_name)

    if key:
        mapping = session.execute(
            select(AdCampaignMapping).where(
                AdCampaignMapping.workspace_id == workspace_id,
                AdCampaignMapping.source == source,
                AdCampaignMapping.campaign_key == key,
            )
        ).scalar_one_or_none()
        if mapping is not None:
            return Resolution(
                work_id=mapping.work_id,
                method="mapping",
                confidence=100,
                reason="This campaign is already mapped to a book.",
                campaign_key=key,
            )

    identifiers = extract_identifiers(ad_name, adset_name, campaign_name)
    if identifiers:
        rows = session.execute(
            select(WorkEdition).where(
                WorkEdition.workspace_id == workspace_id,
                WorkEdition.work_id.is_not(None),
                WorkEdition.identifier.in_(identifiers),
            )
        ).scalars().all()
        works = {row.work_id for row in rows}
        if len(works) == 1:
            return Resolution(
                work_id=next(iter(works)),
                method="identifier",
                confidence=98,
                reason=f"The name contains {identifiers[0]}.",
                campaign_key=key,
            )
        if len(works) > 1:
            # Two books named in one campaign. Splitting the cost between them
            # would be an invention, so this waits for someone to say which.
            return Resolution(
                work_id=None,
                method="ambiguous",
                confidence=0,
                reason="The name names more than one book.",
                campaign_key=key,
            )

    haystack = campaign_key(campaign_name, adset_name, ad_name)
    if haystack:
        works = session.execute(
            select(CatalogWork).where(CatalogWork.workspace_id == workspace_id)
        ).scalars().all()
        matches = [
            work for work in works
            if _title_tokens(work.title)
            and _contains_tokens(haystack, _title_tokens(work.title))
        ]
        if len(matches) == 1:
            return Resolution(
                work_id=matches[0].id,
                method="title",
                confidence=65,
                reason=f"The name reads like “{matches[0].title}” — confirm before importing.",
                campaign_key=key,
            )
        if len(matches) > 1:
            return Resolution(
                work_id=None,
                method="ambiguous",
                confidence=0,
                reason="The name could be any of several books.",
                campaign_key=key,
            )

    return Resolution(
        work_id=None,
        method="unresolved",
        confidence=0,
        reason="Nothing in the name identifies a book.",
        campaign_key=key,
    )


def _contains_tokens(haystack: str, tokens: list[str]) -> bool:
    """Whether the title's words appear in order as whole words in the name.

    Whole words in order, rather than a substring: "Ledger" must not match
    "Ledgerdemain", and a campaign called "Quiet Ledger US" should still find
    "The Quiet Ledger". Requiring the order keeps unrelated books that merely
    share vocabulary from matching.
    """
    pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(token) for token in tokens) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None
