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
