"""Matching a post against what a product's own listing page says.

An import fills four columns. The Attribution tab then fetches the listing and
gets the marketplace's own account of the product: a three-level category
breadcrumb, a brand, attributes, variations and a description. Until this was
wired in, the matcher scored on the import alone.

That was not a small omission in this workspace. Measured on the real
catalogue of 495 products: `category` is empty on all 495 and `brand` on all
495, while the listing carries a breadcrumb for 494 and a brand for 295. Two
of the five fields the matcher scored on were blank for the entire catalogue,
and one of the two was weighted joint-highest.

The assertions here are about what the listing is allowed to decide, and what
it is not.
"""

from __future__ import annotations

import types

from trendrelay_api.campaign_offer_matcher import (
    DESCRIPTION_CHARACTERS,
    _informativeness,
    listing_fields,
    score_offers,
    tokenised,
    tokens,
)


def product(key: str, name: str, *, category: str = "", brand: str = "", listing=None):
    return types.SimpleNamespace(
        id=f"prod-{key}", name=name, category=category, brand=brand,
        marketplace="shopee", listing=listing,
    )


def offer(key: str):
    return types.SimpleNamespace(
        id=f"offer-{key}", merchant=None, availability="available",
        commission_bps=None, commission_flat_cents=None, cookie_days=None,
        price_cents=None, currency="VND", restrictions=[],
        affiliate_url="https://example.test", network="shopee",
    )


def rank(rows, caption: str):
    found = score_offers(
        rows, {"caption": (3.0, tokens(caption))}, platforms={"tiktok"}, limit=10,
    )
    return [match.product_name for match in found]


def test_the_breadcrumb_decides_when_no_product_name_says_it() -> None:
    """The case this was written for, from the real catalogue.

    A post about tidying the kitchen ranked hair clips first, because "gọn
    gàng" - tidy - is in a hair clip's name and nothing in a food container's
    name is about kitchens. The breadcrumb says "Home & Living > Kitchen
    tools", and that is the sentence the post is actually about.
    """
    rows = [
        (offer("clip"), product("clip", "Hair clip, keeps hair tidy")),
        (offer("box"), product(
            "box", "COMBO 10 storage boxes",
            listing={"categories": ["Home & Living", "Kitchen tools", "Food storage"]},
        )),
    ]
    assert rank(rows, "Tidying the kitchen")[0] == "COMBO 10 storage boxes"


def test_a_listing_brand_counts_when_the_imported_one_is_empty() -> None:
    # 295 of 495 products are in exactly this position.
    rows = [
        (offer("a"), product("a", "Cotton shirt")),
        (offer("b"), product("b", "Cotton shirt", listing={"brand": "Uniqlo"})),
    ]
    assert rank(rows, "My new Uniqlo shirt")[0] == "Cotton shirt"
    top = score_offers(
        rows, {"caption": (3.0, tokens("My new Uniqlo shirt"))},
        platforms={"tiktok"}, limit=1,
    )[0]
    assert top.product_id == "prod-b"


def test_attributes_and_options_are_matched() -> None:
    rows = [
        (offer("plain"), product("plain", "Pyjama set")),
        (offer("silk"), product("silk", "Pyjama set", listing={
            "attributes": [{"name": "Material", "value": "Silk satin"}],
            "tier_variations": [{"name": "Colour", "options": ["Cream", "Navy"]}],
        })),
    ]
    assert rank(rows, "silk pyjamas in cream")[0] == "Pyjama set"
    best = score_offers(
        rows, {"caption": (3.0, tokens("silk pyjamas in cream"))},
        platforms={"tiktok"}, limit=1,
    )[0]
    assert best.product_id == "prod-silk"
    assert {"silk", "cream"} <= set(best.matched_terms)


def test_what_a_listing_is_not_allowed_to_decide() -> None:
    """A voucher is not a reason to promote a product in this video.

    Discount, vouchers and price say the offer is attractive; the score has a
    commercial term for that, and it is deliberately unable to create
    relevance. If they leaked into the matched text, a post about a kettle
    could be answered with a discounted mattress.
    """
    listing = {
        "discount_percent": 45,
        "vouchers": [{"code": "SAVE45", "discount_percentage": 45}],
        "price": {"min": 1000},
        "shop_location": "Ha Noi",
        "has_video": True,
    }
    assert listing_fields(product("x", "Mattress", listing=listing)) == []


def test_the_description_is_read_but_not_to_the_end() -> None:
    """Past the blurb a listing turns into shipping terms and shop policy.

    Words shared by every listing in the shop, which cost tokenising and
    distinguish nothing. Rarity would eventually discount them; not reading
    them is cheaper and more certain.
    """
    listing = {"description": "kettle " * 400 + "WARRANTYSENTINEL"}
    fields = dict((label, value) for label, value, _weight in listing_fields(
        product("x", "Thing", listing=listing),
    ))
    assert len(fields["listing description"]) <= DESCRIPTION_CHARACTERS
    assert "WARRANTYSENTINEL" not in fields["listing description"]


def test_listing_words_are_counted_for_rarity_as_well_as_scored() -> None:
    """Scored but uncounted means fully rare, which is the opposite of true.

    `_informativeness` gives an unseen word the full weight of 1.0. A word in
    every listing description would have scored as the single most
    distinguishing term in the catalogue.
    """
    rows = [
        (offer(str(index)), product(str(index), f"Product {index}", listing={
            "description": "convenient and durable, ships nationwide",
        }))
        for index in range(6)
    ]
    # One of them says something only it says.
    rows[0][1].listing["description"] += " thermoregulating"
    rarity = _informativeness(tokenised(rows))
    # Relative, because the floor moves with the size of the catalogue: on six
    # products a universal word still scores 0.36, and on four hundred it does
    # not. What has to hold at every size is the ordering.
    assert rarity["thermoregulating"] == 1.0
    assert rarity["convenient"] < rarity["thermoregulating"] / 2


def test_a_product_with_no_listing_is_still_matched() -> None:
    # Most of a catalogue can be un-fetched, and a product is not less relevant
    # for it - it is only less described.
    rows = [(offer("a"), product("a", "Silk pyjama set"))]
    assert rank(rows, "silk pyjamas") == ["Silk pyjama set"]


def test_a_listing_that_is_not_a_mapping_is_ignored() -> None:
    # The column is JSON; nothing guarantees an object arrives in it.
    for broken in (None, "", [], "not a listing", 7):
        assert listing_fields(product("x", "Thing", listing=broken)) == []


def test_an_empty_listing_field_is_left_out_rather_than_matched_as_blank() -> None:
    fields = listing_fields(product("x", "Thing", listing={
        "categories": [], "brand": "", "attributes": [], "description": "  ",
        "tier_variations": [{"name": "Colour", "options": ["Red"]}],
    }))
    assert [label for label, _value, _weight in fields] == ["listing options"]


def test_having_a_listing_is_not_itself_an_advantage() -> None:
    """The failure a longer document invites, and why this one avoids it.

    Reading listings gives some products a hundred times the text of others -
    a name is sixty characters, a description fifteen hundred - and a ranking
    that grew with the amount of text would promote whichever products had
    been fetched. It is the problem BM25 spends its length-normalisation term
    on.

    It does not arise here because score comes from overlap with the post
    rather than from the size of the document: text that says nothing about
    this post contributes nothing, however much of it there is. Pinned so that
    a future field added by summing rather than intersecting is caught.
    """
    bare = product("bare", "Silk pyjama set")
    rich = product("rich", "Silk pyjama set", listing={
        "categories": ["Automotive", "Car care", "Tyre polish"],
        "attributes": [{"name": "Origin", "value": "Vietnam"}],
        "description": "ships nationwide, warranty twelve months, free returns " * 20,
    })
    scored = score_offers(
        [(offer("bare"), bare), (offer("rich"), rich)],
        {"caption": (3.0, tokens("silk pyjamas for summer"))},
        platforms={"tiktok"}, limit=2,
    )
    assert {match.score for match in scored} == {scored[0].score}, [
        (match.product_id, match.score) for match in scored
    ]


def test_a_listing_that_is_on_topic_does_win() -> None:
    # The other half of the same rule: text that speaks to the post counts.
    bare = product("bare", "Cotton set")
    rich = product("rich", "Cotton set", listing={
        "categories": ["Women's Fashion", "Sleepwear", "Pyjamas"],
    })
    scored = score_offers(
        [(offer("bare"), bare), (offer("rich"), rich)],
        {"caption": (3.0, tokens("sleepwear pyjamas"))},
        platforms={"tiktok"}, limit=2,
    )
    assert scored[0].product_id == "prod-rich"
    assert scored[0].score > scored[1].score
