"""Money as whole minor units, for currencies that do not all have cents.

Every amount in this app is stored as an integer of the currency's smallest
unit - the column is called ``price_cents`` because most of the world's
currencies have hundredths. Not all of them do, and the ones that do not are
the ones this app actually sells in: a Shopee price is in Vietnamese dong,
which has no subdivision at all.

Multiplying by a hundred regardless is the failure this exists to prevent, and
it is a bad one. ``95,000₫`` stored as ``9,500,000`` is not slightly wrong; it
is a hundred times wrong, it looks entirely plausible on screen, and it flows
straight into the commission, ROAS and ACoS figures that Catalog and
Opportunities compute from the same columns. Nothing downstream can detect it,
because there is no second source to disagree with.

No exchange rates
-----------------
This converts between an amount as written and an amount as stored. It does not
convert between currencies, and deliberately: `work_economics` already keeps
every currency in its own bucket, and a rate applied here would produce totals
that disagree with the ones it computes - two numbers that should match and do
not, which is worse than a total nobody can add up.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: Currencies whose smallest unit is not a hundredth, by ISO 4217.
#:
#: Only the exceptions are listed; everything absent has two digits, which is
#: the overwhelming majority and the safe assumption for an unknown code.
MINOR_UNIT_DIGITS: dict[str, int] = {
    # No subdivision in practice.
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0,
    "KRW": 0, "PYG": 0, "RWF": 0, "UGX": 0, "UYI": 0, "VND": 0, "VUV": 0,
    "XAF": 0, "XOF": 0, "XPF": 0,
    # Thousandths.
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
}

#: What a currency none of us have heard of is assumed to be. Two is right for
#: almost everything, and being wrong here is a factor of a hundred, so an
#: unknown code is worth naming rather than silently guessing.
DEFAULT_MINOR_UNIT_DIGITS = 2


def minor_unit_digits(currency: str) -> int:
    """How many digits this currency's smallest unit has."""
    return MINOR_UNIT_DIGITS.get((currency or "").strip().upper(), DEFAULT_MINOR_UNIT_DIGITS)


def has_subunits(currency: str) -> bool:
    """Whether an amount in this currency can have anything after the point."""
    return minor_unit_digits(currency) > 0


def to_minor(amount: Decimal | int | float | str, currency: str) -> int:
    """An amount as written, as a whole number of the smallest unit.

    `95000` VND is 95000; `12.34` USD is 1234; `1.234` KWD is 1234.

    Rounds half up, the way money is rounded on an invoice rather than the way
    Python rounds by default, so a half-cent does not land on whichever side
    happens to be even.
    """
    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, AttributeError, ValueError) as error:
        raise ValueError(f"{amount!r} is not an amount of money.") from error
    if value < 0:
        raise ValueError("An amount of money cannot be negative here.")
    scale = Decimal(10) ** minor_unit_digits(currency)
    return int((value * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor(value: int, currency: str) -> Decimal:
    """The stored amount, back as it would be written."""
    return Decimal(value) / (Decimal(10) ** minor_unit_digits(currency))


def format_amount(value: int | None, currency: str) -> str:
    """A stored amount as somebody would read it, with its own currency's places.

    Grouped, because these run to seven digits in dong and an ungrouped run of
    them cannot be read at a glance.
    """
    if value is None:
        return ""
    digits = minor_unit_digits(currency)
    amount = from_minor(value, currency)
    return f"{amount:,.{digits}f}"
