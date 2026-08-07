"""Reading an ad platform's spend export.

What arrives is whatever Ads Manager exported: column names that differ by
locale and by which report was run, an amount whose currency is written into the
header rather than a column, and often no stable row id at all.

Nothing here guesses at the numbers. A row whose date or amount cannot be read
is returned as a problem with its line number rather than silently dropped or
defaulted to zero, because a spend import that quietly loses rows produces
ratios that look fine and are wrong.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

#: Column names seen across Meta's exports, the API's own field names, and the
#: lowercase forms other tools produce. Compared after normalising, so casing
#: and punctuation in the header do not matter.
COLUMNS: dict[str, tuple[str, ...]] = {
    "campaign_name": ("campaign name", "campaign", "campaign_name"),
    "adset_name": ("ad set name", "adset name", "ad set", "adset_name"),
    "ad_name": ("ad name", "ad", "ad_name"),
    "external_reference": ("ad id", "ad_id", "adset id", "adset_id", "reference"),
    "spend": ("amount spent", "spend", "cost", "amount_spent"),
    "impressions": ("impressions", "impr"),
    "clicks": ("link clicks", "clicks", "clicks all", "link_clicks"),
    "spend_date": ("day", "date", "reporting starts", "date_start", "date start"),
    "currency": ("currency", "account currency", "account_currency"),
}

#: Date formats these exports use. ISO first because the API emits it; the
#: others are what Ads Manager writes depending on the account's locale.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y")

_CURRENCY_IN_HEADER = re.compile(r"\(([A-Z]{3})\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass
class ParsedSpend:
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: One entry per row that could not be read, with the line number, so a
    #: partial import is visible rather than looking like a complete one.
    problems: list[dict[str, Any]] = field(default_factory=list)
    #: The currency taken from the header, when the export wrote it there.
    header_currency: str | None = None


def _normalise(header: str) -> str:
    return _NON_ALNUM.sub(" ", header.strip().lower()).strip()


def _matches(normalised: str, option: str) -> bool:
    """Whether a normalised header names this option.

    Multi-word options may match as a prefix, so "amount spent USD" still finds
    "amount spent" once the bracketed currency has been normalised away. A
    single-word option must match exactly: allowing "ad" to match as a prefix
    filed the "Ad Set Name" column as the ad name.
    """
    if normalised == option:
        return True
    return " " in option and normalised.startswith(f"{option} ")


def _map_headers(fieldnames: list[str]) -> dict[str, str]:
    """Match each known field to whichever column the export used for it.

    One column maps to one field. Without stopping at the first match a header
    could be claimed by two fields, and the more specific name is listed first.
    """
    found: dict[str, str] = {}
    for raw in fieldnames:
        normalised = _normalise(raw)
        for field_name, options in COLUMNS.items():
            if field_name in found:
                continue
            if any(_matches(normalised, option) for option in options):
                found[field_name] = raw
                break
    return found


def header_currency(fieldnames: list[str]) -> str | None:
    """The currency Ads Manager writes into the amount column's own name."""
    for raw in fieldnames:
        if "amount spent" in _normalise(raw) or _normalise(raw).startswith("spend"):
            match = _CURRENCY_IN_HEADER.search(raw)
            if match:
                return match.group(1).upper()
    return None


def parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    # Some exports write a full timestamp; the day is all this needs.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_money_cents(value: str) -> int | None:
    """Read an amount as whole cents.

    Thousands separators and a currency symbol are stripped, but a value that
    still will not parse returns nothing rather than zero — zero spend is a
    meaningful number and must not be invented.
    """
    text = (value or "").strip()
    if not text:
        return None
    text = re.sub(r"[^\d.,\-]", "", text)
    if not text:
        return None
    # A comma used as the decimal separator, as in "1.234,56" or "12,50".
    if "," in text and ("." not in text or text.rfind(",") > text.rfind(".")):
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return int((Decimal(text) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


def _count(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def reference_for(row: dict[str, str], spend_date: date) -> str:
    """A stable id for a row the export gave no id.

    Built from the names and the day, so re-importing the same window updates
    the same rows instead of adding a second copy of the spend. Hashed to keep
    it inside the column, and prefixed so it is recognisable as ours.
    """
    material = "\x1f".join(
        [
            row.get("campaign_name", ""),
            row.get("adset_name", ""),
            row.get("ad_name", ""),
            spend_date.isoformat(),
        ]
    )
    return "derived:" + sha256(material.encode("utf-8")).hexdigest()[:32]


def parse_spend_csv(csv_text: str, *, default_currency: str | None = None) -> ParsedSpend:
    """Turn an exported spend report into rows the import understands."""
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("﻿")))
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        return ParsedSpend(problems=[{"line": 0, "reason": "The file has no header row."}])

    columns = _map_headers(fieldnames)
    from_header = header_currency(fieldnames)
    parsed = ParsedSpend(header_currency=from_header)

    missing = [name for name in ("spend", "spend_date") if name not in columns]
    if missing:
        parsed.problems.append(
            {
                "line": 0,
                "reason": (
                    "The export needs an amount column and a date column; "
                    f"none matched {', '.join(missing)}."
                ),
            }
        )
        return parsed

    if not any(columns.get(name) for name in ("campaign_name", "adset_name", "ad_name")):
        parsed.problems.append(
            {"line": 0, "reason": "The export has no campaign, ad set or ad name to match on."}
        )
        return parsed

    def value(row: dict[str, str], name: str) -> str:
        column = columns.get(name)
        return (row.get(column) or "").strip() if column else ""

    for line, row in enumerate(reader, start=2):
        spend_date = parse_date(value(row, "spend_date"))
        if spend_date is None:
            parsed.problems.append(
                {"line": line, "reason": f"Could not read the date {value(row, 'spend_date')!r}."}
            )
            continue

        spend_cents = parse_money_cents(value(row, "spend"))
        if spend_cents is None:
            parsed.problems.append(
                {"line": line, "reason": f"Could not read the amount {value(row, 'spend')!r}."}
            )
            continue
        if spend_cents < 0:
            parsed.problems.append({"line": line, "reason": "The amount is negative."})
            continue

        currency = (value(row, "currency") or from_header or default_currency or "").upper()
        if len(currency) != 3:
            parsed.problems.append(
                {"line": line, "reason": "No currency in the export, and none was chosen."}
            )
            continue

        names = {
            "campaign_name": value(row, "campaign_name") or None,
            "adset_name": value(row, "adset_name") or None,
            "ad_name": value(row, "ad_name") or None,
        }
        if not any(names.values()):
            parsed.problems.append({"line": line, "reason": "The row names no campaign or ad."})
            continue

        reference = value(row, "external_reference") or reference_for(
            {key: item or "" for key, item in names.items()}, spend_date
        )

        parsed.rows.append(
            {
                **names,
                "external_reference": reference[:200],
                "spend_date": spend_date.isoformat(),
                "currency": currency,
                "spend_cents": spend_cents,
                "impressions": _count(value(row, "impressions")),
                "clicks": _count(value(row, "clicks")),
            }
        )

    return parsed
