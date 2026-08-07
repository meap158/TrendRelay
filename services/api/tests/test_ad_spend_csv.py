"""Reading a spend report exported from an ad platform."""

from __future__ import annotations

from datetime import date

import pytest

from trendrelay_api.ad_spend_csv import (
    header_currency,
    parse_date,
    parse_money_cents,
    parse_spend_csv,
    reference_for,
)

META_EXPORT = (
    'Reporting starts,Campaign name,Ad Set Name,Ad name,"Amount spent (USD)",'
    "Impressions,Link clicks\n"
    "2026-07-15,B0H9CLBXDP | Ledger | Prospecting,Broad 25-45,Hook A,\"1,234.56\",\"45,300\",812\n"
    "2026-07-16,B0H9CLBXDP | Ledger | Prospecting,Broad 25-45,Hook A,98.40,3100,55\n"
)


def test_a_meta_export_parses_without_an_identifier_column() -> None:
    parsed = parse_spend_csv(META_EXPORT)
    assert parsed.problems == []
    assert len(parsed.rows) == 2

    first = parsed.rows[0]
    assert first["campaign_name"] == "B0H9CLBXDP | Ledger | Prospecting"
    assert first["adset_name"] == "Broad 25-45"
    assert first["ad_name"] == "Hook A"
    assert first["spend_date"] == "2026-07-15"
    assert first["spend_cents"] == 123_456
    assert first["impressions"] == 45_300
    assert first["clicks"] == 812


def test_the_currency_is_read_from_the_amount_column_header() -> None:
    # Ads Manager writes it there rather than in a column of its own, so an
    # export with no currency column is still unambiguous.
    assert header_currency(['Amount spent (GBP)', 'Impressions']) == "GBP"
    assert parse_spend_csv(META_EXPORT).rows[0]["currency"] == "USD"
    assert parse_spend_csv(META_EXPORT).header_currency == "USD"


def test_a_missing_currency_is_a_problem_rather_than_a_guess() -> None:
    export = "Day,Campaign name,Amount spent,Impressions\n2026-07-15,Ledger,10.00,100\n"
    parsed = parse_spend_csv(export)
    assert parsed.rows == []
    assert "currency" in parsed.problems[0]["reason"].lower()

    chosen = parse_spend_csv(export, default_currency="eur")
    assert chosen.rows[0]["currency"] == "EUR"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-15", date(2026, 7, 15)),
        ("15/07/2026", date(2026, 7, 15)),
        ("2026/07/15", date(2026, 7, 15)),
        ("15 Jul 2026", date(2026, 7, 15)),
        ("Jul 15, 2026", date(2026, 7, 15)),
        ("2026-07-15T00:00:00Z", date(2026, 7, 15)),
        ("not a date", None),
        ("", None),
    ],
)
def test_the_date_formats_these_exports_use_are_all_read(
    value: str, expected: date | None
) -> None:
    assert parse_date(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1234.56", 123_456),
        ("1,234.56", 123_456),
        ("$1,234.56", 123_456),
        # A comma decimal separator, which is what a European account exports.
        ("1.234,56", 123_456),
        ("12,50", 1250),
        ("0", 0),
        ("", None),
        ("n/a", None),
    ],
)
def test_amounts_are_read_in_either_separator_convention(
    value: str, expected: int | None
) -> None:
    assert parse_money_cents(value) == expected


def test_an_unreadable_row_is_reported_with_its_line_rather_than_dropped() -> None:
    export = (
        "Day,Campaign name,Amount spent (USD),Impressions\n"
        "2026-07-15,Ledger,10.00,100\n"
        "not-a-date,Ledger,20.00,200\n"
        "2026-07-17,Ledger,oops,300\n"
    )
    parsed = parse_spend_csv(export)
    # The good row still imports; the bad ones are named. A spend file that
    # silently loses rows produces ratios that look entirely reasonable.
    assert len(parsed.rows) == 1
    assert [item["line"] for item in parsed.problems] == [3, 4]


def test_a_negative_amount_is_refused() -> None:
    export = "Day,Campaign name,Amount spent (USD)\n2026-07-15,Ledger,-10.00\n"
    assert parse_spend_csv(export).rows == []


def test_an_export_with_no_names_to_match_on_is_refused_outright() -> None:
    export = "Day,Amount spent (USD),Impressions\n2026-07-15,10.00,100\n"
    parsed = parse_spend_csv(export)
    assert parsed.rows == []
    assert "no campaign" in parsed.problems[0]["reason"]


def test_an_export_without_an_amount_column_is_refused_outright() -> None:
    export = "Day,Campaign name,Impressions\n2026-07-15,Ledger,100\n"
    parsed = parse_spend_csv(export)
    assert parsed.rows == []
    assert "amount column" in parsed.problems[0]["reason"]


def test_rows_with_no_ad_id_get_a_reference_stable_across_imports() -> None:
    first = parse_spend_csv(META_EXPORT).rows[0]["external_reference"]
    again = parse_spend_csv(META_EXPORT).rows[0]["external_reference"]
    # Stable, so re-importing an overlapping window updates rather than doubles.
    assert first == again
    assert first.startswith("derived:")
    # ...and different per day, so two days of one ad stay two rows.
    assert first != parse_spend_csv(META_EXPORT).rows[1]["external_reference"]


def test_an_exported_ad_id_is_preferred_over_a_derived_one() -> None:
    export = (
        "Day,Ad ID,Campaign name,Amount spent (USD)\n"
        "2026-07-15,238947298347,Ledger,10.00\n"
    )
    assert parse_spend_csv(export).rows[0]["external_reference"] == "238947298347"


def test_column_names_are_matched_regardless_of_case_and_punctuation() -> None:
    export = (
        "date_start,campaign_name,adset_name,spend,impressions,clicks,currency\n"
        "2026-07-15,Ledger,Broad,10.00,100,5,USD\n"
    )
    parsed = parse_spend_csv(export)
    assert parsed.rows[0]["campaign_name"] == "Ledger"
    assert parsed.rows[0]["spend_cents"] == 1000


def test_the_ad_set_column_is_not_filed_as_the_ad_name() -> None:
    # "Ad Set Name" begins with "Ad", and letting a single-word option match as
    # a prefix put the ad set into the ad name column.
    parsed = parse_spend_csv(META_EXPORT)
    assert parsed.rows[0]["adset_name"] == "Broad 25-45"
    assert parsed.rows[0]["ad_name"] == "Hook A"


def test_a_reference_changes_with_the_campaign_it_came_from() -> None:
    day = date(2026, 7, 15)
    one = reference_for({"campaign_name": "A", "adset_name": "", "ad_name": ""}, day)
    two = reference_for({"campaign_name": "B", "adset_name": "", "ad_name": ""}, day)
    assert one != two


def test_an_empty_file_is_reported_rather_than_crashing() -> None:
    assert parse_spend_csv("").problems[0]["reason"] == "The file has no header row."
