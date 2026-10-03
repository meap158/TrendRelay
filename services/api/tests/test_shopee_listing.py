"""Reading a product listing out of Shopee's public page.

The page embeds the product-details module's initial state in a
`text/mfe-initial-data` script; these tests pin the extraction against a
synthetic page in that exact shape, and the honest refusal when the page
comes back as the challenged shell instead. Shapes verified against live
listings on 2026-09-06.
"""

from __future__ import annotations

import base64
import json

import pytest

from trendrelay_api.integrations import shopee_listing


def page_for(state: dict, module: str = "pcmall-productdetailspage") -> bytes:
    encoded = base64.b64encode(module.encode()).decode()
    body = json.dumps({"initialState": state})
    return (
        "<!doctype html><html><head></head><body>"
        f'<script type="text/mfe-initial-data" data-module="{encoded}">{body}</script>'
        "</body></html>"
    ).encode("utf-8")


class Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def opener_for(body: bytes):
    def opener(_request, timeout):
        return Response(body)

    return opener


STATE = {
    "item": {
        "items": {
            "51760891537": {
                "title": "Khăn giấy TopGia",
                "description": "Mềm mịn " * 3,
                "images": ["vn-hash-1", "vn-hash-2"],
                "brand": "Top Gia",
                "show_discount": 37,
                "currency": "VND",
                "categories": [{"display_name": "Nhà cửa & Đời sống"}],
                "attributes": [{"name": "Kiểu đóng gói", "value": "Đơn"}],
                "tier_variations": [{"name": "Phân loại", "options": ["10 bịch", "20 gói"]}],
                "models": [{"name": "10 bịch"}, {"name": "20 gói"}],
                "shop_vouchers": [{"voucher_code": "TOPGTGH", "min_spend": 9900000000}],
                "shop_location": "Thành phố Hà Nội",
                "item_has_video": False,
                "ctime": 1779336849,
                "price_min": None,
                "stock": None,
                "historical_sold": None,
                "item_rating": {"rating_star": None, "total_rating_count": None},
            }
        }
    }
}


def test_both_public_url_forms_yield_the_same_ids() -> None:
    assert shopee_listing.parse_product_ids(
        "https://shopee.vn/product/1834061111/51760891537"
    ) == (1834061111, 51760891537)
    assert shopee_listing.parse_product_ids(
        "https://shopee.vn/Kh%C4%83n-gi%E1%BA%A5y-i.1834061111.51760891537?sp_atk=x"
    ) == (1834061111, 51760891537)
    assert shopee_listing.parse_product_ids("https://shopee.vn/some-shop") is None


def test_the_embedded_state_becomes_one_bounded_listing() -> None:
    listing = shopee_listing.fetch_listing(
        "https://shopee.vn/product/1834061111/51760891537",
        opener=opener_for(page_for(STATE)),
    )
    assert listing["title"] == "Khăn giấy TopGia"
    assert listing["brand"] == "Top Gia"
    assert listing["discount_percent"] == 37
    assert listing["images"] == [
        "https://down-vn.img.susercontent.com/file/vn-hash-1",
        "https://down-vn.img.susercontent.com/file/vn-hash-2",
    ]
    assert listing["categories"] == ["Nhà cửa & Đời sống"]
    assert listing["tier_variations"] == [
        {"name": "Phân loại", "options": ["10 bịch", "20 gói"], "images": []}
    ]
    assert listing["models"] == ["10 bịch", "20 gói"]
    assert listing["vouchers"][0]["code"] == "TOPGTGH"
    assert listing["shop_location"] == "Thành phố Hà Nội"
    assert listing["listed_at"].startswith("2026-05-21")
    # What the signed-out page withholds is named, not stored as answers.
    assert listing["withheld_signed_out"] == ["price", "stock", "sold", "rating"]


def test_the_whole_gallery_and_description_are_kept() -> None:
    """Every picture and the full text: these are what posts get written from.

    The old caps - twelve images, six thousand characters - were tuned for a
    row summary and quietly threw away exactly what the read exists to hold.
    Variation thumbnails ride along too: a colour is a picture first.
    """
    state = {
        "item": {
            "items": {
                "2": {
                    "title": "Đầm hoa",
                    "description": "x" * 9000,
                    "images": [f"hash-{index}" for index in range(20)],
                    "tier_variations": [{
                        "name": "Màu", "options": ["Đỏ", "Xanh"],
                        "images": ["tier-a", "tier-b"],
                    }],
                    "item_rating": {},
                }
            }
        }
    }
    listing = shopee_listing.fetch_listing(
        "https://shopee.vn/product/1/2", opener=opener_for(page_for(state)),
    )
    assert len(listing["images"]) == 20
    assert len(listing["description"]) == 9000
    assert listing["tier_variations"][0]["images"] == [
        "https://down-vn.img.susercontent.com/file/tier-a",
        "https://down-vn.img.susercontent.com/file/tier-b",
    ]


def test_a_challenged_shell_is_refused_rather_than_stored() -> None:
    # The page answered, but with the wrong module - or with none at all.
    other = page_for({"item": {"items": {}}}, module="pcmall-somethingelse")
    with pytest.raises(shopee_listing.ListingUnavailable):
        shopee_listing.fetch_listing(
            "https://shopee.vn/product/1/2", opener=opener_for(other)
        )
    with pytest.raises(shopee_listing.ListingUnavailable):
        shopee_listing.fetch_listing(
            "https://shopee.vn/product/1/2",
            opener=opener_for(b"<!doctype html><html><body>captcha</body></html>"),
        )


def test_a_link_that_is_not_a_product_is_refused_before_any_request() -> None:
    def exploding_opener(_request, timeout):
        raise AssertionError("no request should have been made")

    with pytest.raises(shopee_listing.ListingUnavailable):
        shopee_listing.fetch_listing(
            "https://shopee.vn/mall-page", opener=exploding_opener
        )


@pytest.mark.parametrize("state", [
    {}, {"item": {"items": {}}},
    {"item": {"items": {"2": {"title": None}}}},
    {"item": {"items": {"999": {"title": "Another product"}}}},
])
def test_empty_or_unrelated_product_state_is_not_a_listing(state) -> None:
    with pytest.raises(shopee_listing.ListingNotFound):
        shopee_listing.fetch_listing(
            "https://shopee.vn/product/1/2", opener=opener_for(page_for(state)),
        )


def test_mobilemall_module_is_recognized_and_extracted() -> None:
    listing = shopee_listing.fetch_listing(
        "https://shopee.vn/product/1834061111/51760891537",
        opener=opener_for(page_for(STATE, module="mobilemall-productdetailspage")),
    )
    assert listing["title"] == "Khăn giấy TopGia"
    assert listing["brand"] == "Top Gia"

