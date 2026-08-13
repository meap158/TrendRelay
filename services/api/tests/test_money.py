"""Money stored as whole minor units, in currencies that do not all have cents."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trendrelay_api.money import (
    format_amount,
    from_minor,
    has_subunits,
    minor_unit_digits,
    to_minor,
)

# --- the failure this exists to prevent ---------------------------------------


def test_a_dong_price_is_not_multiplied_by_a_hundred() -> None:
    """The bug worth writing a module over.

    95,000₫ stored as 9,500,000 is not slightly wrong. It is a hundred times
    wrong, it looks entirely plausible on screen, and it flows into commission,
    ROAS and ACoS without anything downstream able to disagree with it.
    """
    assert to_minor(95_000, "VND") == 95_000
    assert to_minor("183000", "VND") == 183_000


def test_a_dollar_price_still_becomes_cents() -> None:
    # The common case has to keep working; this is not a change of convention.
    assert to_minor("12.34", "USD") == 1234
    assert to_minor(5, "USD") == 500


def test_a_three_digit_currency_is_neither_of_those() -> None:
    # Dinars are thousandths, so both of the obvious assumptions are wrong.
    assert to_minor("1.234", "KWD") == 1234
    assert to_minor("1.234", "BHD") == 1234


@pytest.mark.parametrize(("currency", "digits"), [
    ("VND", 0), ("JPY", 0), ("KRW", 0),
    ("USD", 2), ("EUR", 2), ("GBP", 2),
    ("KWD", 3),
    ("vnd", 0), (" usd ", 2),
])
def test_each_currency_knows_its_own_smallest_unit(currency: str, digits: int) -> None:
    assert minor_unit_digits(currency) == digits


def test_a_currency_nobody_listed_is_assumed_to_have_cents() -> None:
    """Two is right for almost everything, so it is the safe assumption.

    Being wrong here is a factor of a hundred either way, and guessing zero for
    an unknown code would under-count far more currencies than it helped.
    """
    assert minor_unit_digits("ZZZ") == 2
    assert minor_unit_digits("") == 2


def test_whether_an_amount_can_have_a_decimal_point_at_all() -> None:
    assert has_subunits("USD") is True
    assert has_subunits("VND") is False


# --- round trips --------------------------------------------------------------


@pytest.mark.parametrize(("written", "currency"), [
    ("95000", "VND"),
    ("12.34", "USD"),
    ("1.234", "KWD"),
    ("0", "VND"),
])
def test_an_amount_survives_being_stored_and_read_back(written: str, currency: str) -> None:
    assert from_minor(to_minor(written, currency), currency) == Decimal(written)


def test_reading_back_a_dong_amount_adds_no_decimal_places() -> None:
    assert from_minor(95_000, "VND") == Decimal("95000")
    assert from_minor(1234, "USD") == Decimal("12.34")


# --- rounding -----------------------------------------------------------------


def test_a_half_unit_rounds_the_way_an_invoice_does() -> None:
    """Half up, not to even.

    Python's default would send 0.125 down and 0.135 up, which is defensible in
    statistics and indefensible on a bill.
    """
    assert to_minor("0.125", "USD") == 13
    assert to_minor("0.135", "USD") == 14


def test_a_fractional_dong_rounds_to_a_whole_one() -> None:
    # There is nothing smaller, so this is the only thing it can do.
    assert to_minor("1900.4", "VND") == 1900
    assert to_minor("1900.5", "VND") == 1901


# --- refusing nonsense --------------------------------------------------------


def test_something_that_is_not_money_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="not an amount of money"):
        to_minor("about ninety-five thousand", "VND")


def test_a_negative_amount_is_refused() -> None:
    # Every column this feeds is a price or a commission; neither is negative,
    # and a minus sign is far more likely to be a parsing slip.
    with pytest.raises(ValueError, match="cannot be negative"):
        to_minor("-5", "USD")


# --- showing it ---------------------------------------------------------------


def test_an_amount_is_shown_with_its_own_currency_s_places() -> None:
    assert format_amount(95_000, "VND") == "95,000"
    assert format_amount(1234, "USD") == "12.34"
    assert format_amount(1234, "KWD") == "1.234"


def test_no_amount_shows_as_nothing_rather_than_zero() -> None:
    assert format_amount(None, "VND") == ""


# --- the importer that used to get this wrong ---------------------------------


def test_the_offer_importer_reads_a_price_in_its_own_currency() -> None:
    """It multiplied by a hundred whatever the currency column said.

    A dong price came out a hundred times too large, and every commission,
    ROAS and ACoS computed from that column came with it.
    """
    from trendrelay_api.opportunities_api import _money

    assert _money("95000", "price", "VND") == 95_000
    assert _money("12.34", "price", "USD") == 1234


def test_the_importer_still_defaults_to_cents_when_nothing_says_otherwise() -> None:
    from trendrelay_api.opportunities_api import _money

    assert _money("12.34", "price") == 1234


def test_the_importer_keeps_naming_the_field_it_could_not_read() -> None:
    # The message is what an operator sees against a row of their spreadsheet.
    from trendrelay_api.opportunities_api import _money

    with pytest.raises(ValueError, match="price must be a decimal number"):
        _money("not money", "price", "VND")
    with pytest.raises(ValueError, match="price cannot be negative"):
        _money("-1", "price", "VND")
