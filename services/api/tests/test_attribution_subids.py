"""Sub IDs: the value that survives into a network's own conversion report."""

from __future__ import annotations

from datetime import UTC, datetime

from trendrelay_api.attribution_subids import (
    DIMENSIONS,
    NETWORKS,
    LinkContext,
    assign,
    link_key,
    network_for,
    sanitise,
)


def context(**overrides) -> LinkContext:
    values = {
        "code": "abc123XY",
        "platform": "tiktok",
        "campaign_id": "campaign_7f3a",
        "campaign_name": "Spring Sale 2026",
        "created_at": datetime(2026, 8, 10, 9, 30, tzinfo=UTC),
        "content_sha256": "9f2c4b7e1a0d5e6f" + "0" * 48,
    }
    return LinkContext(**{**values, **overrides})


SHOPEE = "https://shopee.vn/product-i.123.456"


# --- the rule the whole feature rests on --------------------------------------


def test_a_slot_always_carries_the_same_dimension() -> None:
    """Networks report sub IDs positionally, as a column per slot.

    A slot holding a placement on one link and a campaign on another produces a
    column that cannot be grouped by anything, so the assignment is a constant
    rather than a per-link decision.
    """
    first = assign(SHOPEE, context())
    second = assign(SHOPEE, context(
        code="zzz999", platform="instagram", campaign_name="Autumn",
        content_sha256="1" * 64,
    ))

    assert list(first) == list(second) == [
        "sub_id1", "sub_id2", "sub_id3", "sub_id4", "sub_id5",
    ]
    # Same slot, same meaning: slot 3 is the placement in both.
    assert first["sub_id3"] == "tiktok"
    assert second["sub_id3"] == "instagram"


def test_the_link_key_takes_the_first_slot() -> None:
    """It resolves every other dimension from our own database.

    A network offering one sub ID must spend it on this: a placement there would
    read as more useful and would strand every conversion that came back.
    """
    assert DIMENSIONS[0] == "link"
    assert assign(SHOPEE, context())["sub_id1"] == link_key("abc123XY")

    single = assign("https://click.linksynergy.com/deeplink?id=x", context())
    assert list(single) == ["u1"]
    assert single["u1"] == link_key("abc123XY")


def test_an_unknown_network_is_left_alone() -> None:
    """Guessing a parameter name does not degrade tracking, it breaks the sale.

    Some networks reject a link carrying parameters they do not recognise, so
    silence is the only safe answer for a host we have no contract for.
    """
    assert network_for("https://example.com/thing") is None
    assert assign("https://example.com/thing", context()) == {}


# --- surviving the network's own rules ----------------------------------------


def test_shopee_gets_letters_and_digits_only() -> None:
    """Shopee's builder accepts a-z, A-Z and 0-9, and nothing else."""
    assigned = assign(SHOPEE, context())
    for value in assigned.values():
        assert value.isalnum(), value
    # The campaign name had a space and stays readable without it.
    assert assigned["sub_id4"] == "SpringSale2026"


def test_a_tracking_code_that_shopee_would_reject_still_works() -> None:
    """token_urlsafe puts `-` or `_` in about a quarter of codes.

    Dropping those characters would collide two different links onto one key, so
    the key is derived by hash instead - which also means every link that
    already exists has one without a migration.
    """
    dashed = link_key("ab-cd_ef")
    assert dashed.isalnum()
    assert dashed != link_key("abcdef")
    assert dashed == link_key("ab-cd_ef")


def test_values_are_cut_to_the_network_limit() -> None:
    shopee = next(item for item in NETWORKS if item.id == "shopee")
    assert len(sanitise("x" * 200, shopee)) == shopee.max_length
    # From the left: the distinguishing part of a name is at the front.
    assert sanitise("SpringSaleBanner", shopee).startswith("Spring")


def test_a_dimension_with_nothing_in_it_is_skipped() -> None:
    # Rather than filled with a placeholder, which becomes a value somebody
    # groups a report by.
    assigned = assign(SHOPEE, context(content_sha256=None))
    assert "sub_id2" not in assigned
    assert assigned["sub_id3"] == "tiktok"


def test_a_slot_the_operator_already_filled_is_not_overwritten() -> None:
    """They meant it, and a report may already be built on it."""
    assigned = assign(f"{SHOPEE}?sub_id1=mine", context())
    assert "sub_id1" not in assigned
    assert assigned["sub_id2"].startswith("9f2c4b7e")


# --- the questions an affiliate actually asks ---------------------------------


def test_the_same_video_reports_the_same_wherever_it_is_used() -> None:
    """"Which video sells" is the question this column exists for.

    Keyed on the content hash rather than the plan, so the same cut reused in
    another campaign lands on the same value instead of looking like a new one.
    """
    one = assign(SHOPEE, context(campaign_name="Spring"))
    two = assign(SHOPEE, context(campaign_name="Autumn", code="different"))
    assert one["sub_id2"] == two["sub_id2"]
    assert one["sub_id4"] != two["sub_id4"]


def test_fewer_slots_keep_the_more_valuable_dimensions() -> None:
    impact = assign("https://goto.example.sjv.io/c/123", context())
    assert list(impact) == ["subId1", "subId2", "subId3"]
    assert impact["subId3"] == "tiktok"
    # Campaign and date are what a three-slot network gives up, and both are
    # recoverable from the link key in slot one.
    assert "20260810" not in impact.values()


def test_every_network_records_where_its_contract_came_from() -> None:
    for network in NETWORKS:
        assert network.source, network.id
        assert network.slots, network.id
        assert network.max_length > 0, network.id
        assert len(network.slots) <= len(DIMENSIONS), network.id
