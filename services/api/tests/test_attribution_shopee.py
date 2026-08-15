"""Reading a Shopee affiliate link."""

from __future__ import annotations

import pytest

from trendrelay_api.attribution_shopee import (
    canonical_url,
    is_shopee_link,
    is_short_link,
    parse_ids,
    read_link,
    split_links,
)

#: The shape a share link resolves to, tracking tail and all. Taken from a real
#: one rather than invented, because the tail is the part that varies.
RESOLVED = (
    "https://shopee.vn/opaanlp/1834061111/57860887539?__mobile__=1"
    "&credential_token=8wEwiDL7ZS18DH1t&exp_group=rollout&gads_t_sig=gqRjZGVr"
    "&utm_campaign=id_3GWbrDSUwIj&utm_medium=affiliates&utm_term=fdbwd6kn9x79"
)
SHORT = "https://s.shopee.vn/1BLWKRbU8L"


# --- recognising one ----------------------------------------------------------


def test_a_short_link_is_known_to_need_following() -> None:
    assert is_shopee_link(SHORT) is True
    assert is_short_link(SHORT) is True
    # A resolved one is Shopee, but there is nothing left to follow.
    assert is_short_link(RESOLVED) is False


@pytest.mark.parametrize("url", [
    "https://shopee.vn/product/123456/7891011",
    "https://shopee.co.id/Some-Item-i.123456.7891011",
    "https://shp.ee/abcdef",
])
def test_every_country_and_shape_is_recognised(url: str) -> None:
    assert is_shopee_link(url) is True


@pytest.mark.parametrize("url", [
    "https://example.com/product/1/2",
    "https://notshopee.vn/product/1/2",
    "https://shopee.vn.evil.test/product/1/2",
])
def test_something_that_only_looks_like_shopee_is_refused(url: str) -> None:
    # The host has to end in a Shopee domain, not merely contain one.
    assert is_shopee_link(url) is False


# --- what it points at --------------------------------------------------------


def test_the_shop_and_item_come_out_of_a_resolved_link() -> None:
    assert parse_ids(RESOLVED) == ("1834061111", "57860887539")


def test_a_desktop_slug_link_is_read_the_same_way() -> None:
    ids = parse_ids("https://shopee.vn/Ao-thun-nam-i.123456.7891011")
    assert ids == ("123456", "7891011")


def test_the_older_product_form_still_parses() -> None:
    assert parse_ids("https://shopee.vn/product/123456/7891011") == ("123456", "7891011")


def test_a_link_with_no_ids_yields_nothing_rather_than_guessing() -> None:
    assert parse_ids("https://shopee.vn/mall/search?keyword=coffee") == (None, None)


def test_two_shares_of_one_product_carry_the_same_identity() -> None:
    """The tracking tail differs every time the same product is shared.

    Keying on the whole URL would file one product as many, which is how a
    catalogue stops being able to answer "how is this product doing".
    """
    other_share = RESOLVED.replace("fdbwd6kn9x79", "totallydifferent").replace(
        "id_3GWbrDSUwIj", "id_SomethingElse"
    )

    assert read_link(SHORT, RESOLVED).identifier == read_link(SHORT, other_share).identifier
    assert read_link(SHORT, RESOLVED).identifier == "1834061111.57860887539"


def test_the_canonical_url_is_rebuilt_rather_than_stripped() -> None:
    """A new tracking parameter would otherwise become part of the identity.

    The parameters Shopee attaches are not a fixed list, so removing the known
    ones leaves the unknown ones in.
    """
    assert canonical_url(RESOLVED) == "https://shopee.vn/product/1834061111/57860887539"
    assert "utm_" not in (canonical_url(RESOLVED) or "")


# --- reading one --------------------------------------------------------------


def test_the_pasted_link_is_kept_exactly_as_given() -> None:
    """It is what gets posted, and its tail is what earns the commission."""
    offer = read_link(SHORT, RESOLVED)

    assert offer.affiliate_url == SHORT
    assert offer.resolved is True


def test_an_unfollowed_short_link_is_honest_about_knowing_nothing() -> None:
    # Nothing in the short form says what it sells, so nothing is invented.
    offer = read_link(SHORT)

    assert offer.resolved is False
    assert offer.identifier is None
    assert offer.affiliate_url == SHORT


# --- a batch of them ----------------------------------------------------------


def test_links_are_found_in_whatever_was_pasted() -> None:
    text = f"""
      here are two {SHORT}
      and another: https://shopee.vn/product/1/2, plus noise
    """
    assert split_links(text) == [SHORT, "https://shopee.vn/product/1/2"]


def test_the_same_link_twice_is_one_offer() -> None:
    # Pasting a column from a spreadsheet repeats rows; that is a slip rather
    # than a request for two offers.
    assert split_links(f"{SHORT} {SHORT}") == [SHORT]


def test_order_is_kept_because_a_paste_has_one() -> None:
    second = "https://shopee.vn/product/9/9"
    assert split_links(f"{second}\n{SHORT}") == [second, SHORT]


def test_anything_that_is_not_a_shopee_link_is_left_out() -> None:
    assert split_links("https://example.com/x notalink https://tiktok.com/@a") == []


def test_an_empty_paste_is_an_empty_list_rather_than_an_error() -> None:
    assert split_links("") == []
    assert split_links("   ") == []


# --- following one ------------------------------------------------------------


def test_a_link_that_is_not_shopee_is_never_fetched(monkeypatch) -> None:
    """A paste is untrusted input, so this is not a general URL fetcher.

    Following whatever was pasted would turn an import box into a way to make
    this machine request arbitrary hosts.
    """
    import trendrelay_api.attribution_shopee as shopee

    def explode(*_args, **_kwargs):
        raise AssertionError("nothing should have been fetched")

    monkeypatch.setattr("urllib.request.urlopen", explode)

    with pytest.raises(ValueError, match="not a Shopee link"):
        shopee.resolve_short_link("https://example.com/whatever")


def test_a_redirect_off_shopee_is_refused(monkeypatch) -> None:
    # An open redirect on a Shopee domain would otherwise be a way through the
    # check above.
    import trendrelay_api.attribution_shopee as shopee

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://elsewhere.example/landing"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: Response())

    with pytest.raises(ValueError, match="not Shopee"):
        shopee.resolve_short_link(SHORT)


def test_a_link_that_could_not_be_followed_says_so(monkeypatch) -> None:
    """Rather than returning the input, which reads as "it pointed at itself"."""
    import trendrelay_api.attribution_shopee as shopee

    def refuse(*_args, **_kwargs):
        raise OSError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", refuse)

    with pytest.raises(ValueError, match="did not answer"):
        shopee.resolve_short_link(SHORT)


# --- the bulk export ----------------------------------------------------------

#: Three real rows from a "Lấy link sản phẩm hàng loạt" export, headings and
#: number formats untouched. The reference file itself is git-ignored, so the
#: shapes that matter live here instead.
EXPORT = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,"
    "Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"
    "57860887539,Giấy ăn rút Topgia thùng 40 gói,\"95,0k\",8k+,TOP_GIA HOME,"
    "2%,₫1.900,https://shopee.vn/product/1834061111/57860887539,https://s.shopee.vn/70JJHPqb6V\n"
    "20567055749,Tẩy Tế Bào Chết Body Dove,\"183,0k\",900k+,Unilever,"
    "8%,₫14.640,https://shopee.vn/product/111138057/20567055749,https://s.shopee.vn/6AkCHstlnM\n"
    "27386960576,Ba lô chống gù đi học,\"176,0k\",10k+,Dailynecessities,"
    "9%,₫15.840,https://shopee.vn/product/1218445657/27386960576,https://s.shopee.vn/60Qm5ZuP8L\n"
)


def test_the_real_export_reads_without_complaint() -> None:
    from trendrelay_api.attribution_shopee import read_export

    products, problems = read_export(EXPORT)

    assert problems == []
    assert [p.name[:12] for p in products] == ["Giấy ăn rút ", "Tẩy Tế Bào C", "Ba lô chống "]


def test_the_utf8_bom_in_a_shopee_csv_is_ignored() -> None:
    from trendrelay_api.attribution_shopee import read_export

    products, problems = read_export("\ufeff" + EXPORT)

    assert problems == []
    assert len(products) == 3


def test_the_commission_column_agrees_with_price_times_rate() -> None:
    """The strongest check available: two parsers meeting on a third number.

    Vietnamese money puts the decimal point where English puts the thousands
    separator, so reading one as the other is wrong by a factor of a thousand
    rather than slightly wrong. If the money and the rate are both read right,
    their product is the commission Shopee itself printed.
    """
    from trendrelay_api.attribution_shopee import read_export

    products, _ = read_export(EXPORT)

    for product in products:
        expected = round(product.price_dong * product.commission_bps / 10_000)
        assert product.commission_dong == expected, product.name


@pytest.mark.parametrize(("written", "dong"), [
    ("95,0k", 95_000),
    ("183,0k", 183_000),
    ("₫1.900", 1_900),
    ("₫14.640", 14_640),
    ("1,5tr", 1_500_000),
    ("", None),
    ("--", None),
])
def test_vietnamese_money_is_read_as_written(written: str, dong: int | None) -> None:
    from trendrelay_api.attribution_shopee import parse_money

    assert parse_money(written) == dong


def test_no_price_is_not_a_price_of_zero() -> None:
    # No number is a fact about the export; zero is a claim about the product.
    from trendrelay_api.attribution_shopee import parse_money

    assert parse_money("") is None


@pytest.mark.parametrize(("written", "bps"), [("2%", 200), ("8%", 800), ("2,5%", 250), ("", None)])
def test_a_commission_rate_becomes_basis_points(written: str, bps: int | None) -> None:
    from trendrelay_api.attribution_shopee import parse_rate_bps

    assert parse_rate_bps(written) == bps


def test_identity_needs_both_columns_read_together() -> None:
    """The id column is the item alone; the shop is only in the product URL."""
    from trendrelay_api.attribution_shopee import read_export

    products, _ = read_export(EXPORT)

    assert products[0].identifier == "1834061111.57860887539"


def test_a_row_without_a_link_is_reported_rather_than_dropped() -> None:
    from trendrelay_api.attribution_shopee import read_export

    broken = EXPORT + "999,Something,\"1,0k\",1k+,Shop,1%,₫10,,\n"

    products, problems = read_export(broken)

    assert len(products) == 3
    assert any("Row 5" in problem and "Something" in problem for problem in problems)


def test_two_hundred_rows_with_three_bad_ones_import_a_hundred_and_ninety_seven() -> None:
    # Stopping at the first odd row would make the whole file unusable because
    # of three of them.
    from trendrelay_api.attribution_shopee import read_export

    products, problems = read_export(EXPORT + "1,No link,,,,,,,\n" * 3)

    assert len(products) == 3
    assert len(problems) == 3


def test_a_trailing_blank_line_is_not_a_problem() -> None:
    from trendrelay_api.attribution_shopee import read_export

    products, problems = read_export(EXPORT + ",,,,,,,,\n")

    assert len(products) == 3
    assert problems == []


def test_an_export_in_english_reads_the_same_way() -> None:
    from trendrelay_api.attribution_shopee import read_export

    english = (
        "Product ID,Product Name,Price,Shop Name,Commission Rate,Commission,"
        "Product Link,Offer Link\n"
        "57860887539,A product,\"95,0k\",A shop,2%,₫1.900,"
        "https://shopee.vn/product/1834061111/57860887539,https://s.shopee.vn/70JJHPqb6V\n"
    )

    products, problems = read_export(english)

    assert problems == []
    assert products[0].identifier == "1834061111.57860887539"
    assert products[0].price_dong == 95_000


def test_rows_copied_from_excel_may_be_tab_delimited_with_a_preamble() -> None:
    from trendrelay_api.attribution_shopee import read_export

    pasted = (
        "Shopee Product Offer export\nGenerated 2026-08-15\n"
        "Product ID\tProduct Name\tPrice\tShop Name\tCommission Rate\t"
        "Product Link\tOffer Link\n"
        "57860887539\tA product\t95000\tA shop\t2%\t"
        "https://shopee.vn/product/1834061111/57860887539\t"
        "https://s.shopee.vn/70JJHPqb6V\n"
    )

    products, problems = read_export(pasted)

    assert problems == []
    assert len(products) == 1
    assert products[0].name == "A product"
    assert products[0].identifier == "1834061111.57860887539"


def test_a_file_that_is_not_an_export_says_so_rather_than_importing_nothing() -> None:
    """Silence would read as "your export was empty", which is a different problem."""
    from trendrelay_api.attribution_shopee import read_export

    products, problems = read_export("date,clicks\n2026-08-01,12\n")

    assert products == []
    assert problems and "does not look like a Shopee product export" in problems[0]
