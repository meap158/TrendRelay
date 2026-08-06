"""Working out which book an ad campaign was advertising."""

from __future__ import annotations

import pytest

from trendrelay_api.ad_attribution import campaign_key, extract_identifiers


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (("B0H9CLBXDP | The Quiet Ledger | Prospecting",), ["B0H9CLBXDP"]),
        (("[9798187897681] Ledger - Retargeting",), ["9798187897681"]),
        # An ISBN-10 in a name still resolves to the ISBN-13 the editions carry.
        (("Ledger 0306406152 broad",), ["9780306406157"]),
        (("Ledger US Broad", "B0H9CLBXDP adset"), ["B0H9CLBXDP"]),
        (("Ledger US Broad",), []),
    ],
)
def test_identifiers_are_read_out_of_campaign_names(
    names: tuple[str, ...], expected: list[str]
) -> None:
    assert extract_identifiers(*names) == expected


def test_numbers_that_are_not_identifiers_are_ignored() -> None:
    # Campaign names are full of numbers — budgets, dates, audience sizes. A
    # thirteen-digit run that fails its check digit is one of those, not a book.
    assert extract_identifiers("Ledger 9781234567890 budget 2026") == []
    assert extract_identifiers("Q3 2026 launch 15000 daily") == []


def test_an_identifier_inside_a_longer_word_is_not_read() -> None:
    assert extract_identifiers("XB0H9CLBXDPX") == []


def test_several_identifiers_in_one_name_are_all_returned() -> None:
    # Reported rather than resolved: two books in one campaign has no single
    # right answer, and the resolver refuses instead of picking.
    found = extract_identifiers("B0H9CLBXDP + 9798187897681 bundle")
    assert found == ["B0H9CLBXDP", "9798187897681"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("The Quiet Ledger | US | Broad", "the quiet ledger us broad"),
        ("the-quiet-ledger_us_broad", "the quiet ledger us broad"),
        ("  The  Quiet   Ledger  ", "the quiet ledger"),
        ("Les Misérables Retargeting", "les miserables retargeting"),
        (None, ""),
    ],
)
def test_campaign_keys_survive_punctuation_and_casing(name: str | None, expected: str) -> None:
    assert campaign_key(name) == expected
