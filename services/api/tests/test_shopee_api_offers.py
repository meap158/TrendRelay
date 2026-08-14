"""Reading the affiliate page's own JSON into the rows an import understands.

The scale is the whole risk here. Shopee's own APIs report money in a "cent"
convention its front-end divides before display, and the same field arrives
already divided on some payloads. A figure a hundred thousand times out is the
one mistake nobody catches on a screen, because it just looks like a big number.
"""

from __future__ import annotations

from trendrelay_api.attribution_shopee import read_api_offers

ROW = {
    "item_id": "57860887539",
    "shop_id": "1834061111",
    "name": "Giấy ăn rút Topgia",
    "shop": "TOP_GIA HOME",
    "price": 95_000 * 100_000,
    "commission": 1_900 * 100_000,
    "commission_rate": 0.02,
    "product_url": "https://shopee.vn/product/1834061111/57860887539",
    "affiliate_url": "https://s.shopee.vn/70JJHPqb6V",
}


def one(**changes):
    rows, _problems = read_api_offers([{**ROW, **changes}])
    return rows[0]


# --- money -------------------------------------------------------------------


def test_a_scaled_price_becomes_whole_dong() -> None:
    assert one().price_dong == 95_000


def test_a_price_that_arrives_already_whole_is_left_alone() -> None:
    """The same field comes both ways depending on the payload."""
    assert one(price=95_000).price_dong == 95_000


def test_the_commission_is_scaled_the_same_way() -> None:
    assert one().commission_dong == 1_900


def test_a_missing_price_is_absent_rather_than_zero() -> None:
    # Zero is a claim about the price. Absent is the truth.
    assert one(price=None).price_dong is None
    assert one(price="").price_dong is None


def test_nonsense_in_a_money_field_is_not_a_number() -> None:
    assert one(price="lots").price_dong is None


# --- the commission rate -----------------------------------------------------


def test_a_fraction_becomes_basis_points() -> None:
    assert one(commission_rate=0.02).commission_bps == 200


def test_a_percentage_becomes_basis_points() -> None:
    assert one(commission_rate=2).commission_bps == 200


def test_a_scaled_rate_becomes_basis_points() -> None:
    assert one(commission_rate=0.02 * 100_000).commission_bps == 200


def test_the_parsed_figures_agree_with_each_other() -> None:
    """The check the CSV export passes on every row: price x rate == commission."""
    row = one()

    assert round(row.price_dong * row.commission_bps / 10_000) == row.commission_dong


# --- rows, and rows that cannot be used --------------------------------------


def test_an_offer_becomes_a_row_the_importer_already_reads() -> None:
    row = one()

    assert row.identifier == "1834061111.57860887539"
    assert row.affiliate_url == "https://s.shopee.vn/70JJHPqb6V"
    assert row.name == "Giấy ăn rút Topgia"


def test_an_offer_with_no_link_at_all_is_reported_not_dropped_silently(  ) -> None:
    rows, problems = read_api_offers([{**ROW, "affiliate_url": "", "product_url": ""}])

    assert rows == []
    assert len(problems) == 1 and "Topgia" in problems[0]


def test_one_unusable_offer_does_not_lose_the_others() -> None:
    rows, problems = read_api_offers([
        {**ROW, "affiliate_url": "", "product_url": ""},
        {**ROW, "item_id": "2", "name": "Second"},
    ])

    assert [row.name for row in rows] == ["Second"]
    assert len(problems) == 1


def test_an_offer_with_only_a_product_url_still_files() -> None:
    # The link the account has is the affiliate one; without it the product URL
    # at least identifies what was seen.
    row = one(affiliate_url="")

    assert row.affiliate_url == ROW["product_url"]


def test_junk_in_the_list_is_skipped_rather_than_raising() -> None:
    rows, _ = read_api_offers([None, "banner", 7, ROW])

    assert len(rows) == 1
