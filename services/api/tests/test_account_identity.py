"""Recognising one page seen through two engines - and refusing to guess."""

from __future__ import annotations

from trendrelay_api.integrations.account_identity import (
    consolidate,
    normalise_handle,
    page_payload,
)


def account(provider: str, identifier: str, platform: str, label: str,
            handle: str | None = None) -> dict[str, object]:
    return {
        "provider": provider, "provider_label": provider.title(), "id": identifier,
        "platform": platform, "label": label, "handle": handle,
    }


# --- normalising --------------------------------------------------------------


def test_a_handle_is_stripped_of_everything_that_is_not_the_handle() -> None:
    for value in (
        "@halcyonbooks", "halcyonbooks", " HalcyonBooks ",
        "https://instagram.com/halcyonbooks",
        "https://www.tiktok.com/@halcyonbooks",
        "https://instagram.com/halcyonbooks/?hl=en",
    ):
        assert normalise_handle(value) == "halcyonbooks", value


def test_nothing_to_compare_returns_none_rather_than_empty() -> None:
    """An empty handle would group every handle-less account into one page.

    That is exactly the wrong merge: it would take unrelated accounts on a
    platform and present them as a single destination.
    """
    for value in (None, "", "   ", "@", "/"):
        assert normalise_handle(value) is None


# --- consolidating ------------------------------------------------------------


def test_one_page_on_two_engines_becomes_one_destination() -> None:
    pages = consolidate([
        account("buffer", "b1", "instagram", "Halcyon Books", "halcyonbooks"),
        account("zernio", "z9", "instagram", "halcyonbooks.official", "@HalcyonBooks"),
    ])
    assert len(pages) == 1
    assert pages[0].shared
    assert [item["provider"] for item in pages[0].reachable_by] == ["buffer", "zernio"]


def test_the_same_handle_on_different_platforms_stays_separate() -> None:
    # One brand's Instagram and TikTok share a handle and are not one page.
    pages = consolidate([
        account("buffer", "b1", "instagram", "Halcyon", "halcyonbooks"),
        account("buffer", "b2", "tiktok", "Halcyon", "halcyonbooks"),
    ])
    assert len(pages) == 2


def test_matching_display_names_are_never_merged() -> None:
    """The merge that would be wrong.

    Display names are chosen freely, change often, and repeat; a workspace can
    easily have two accounts both called "Halcyon Books". Merging on one would
    silently point a post at the wrong account, which is worse than showing a
    duplicate.
    """
    pages = consolidate([
        account("buffer", "b1", "instagram", "Halcyon Books"),
        account("zernio", "z9", "instagram", "Halcyon Books"),
    ])
    assert len(pages) == 2
    assert all(not page.shared for page in pages)


def test_an_engine_listing_an_account_twice_is_not_a_second_route() -> None:
    pages = consolidate([
        account("buffer", "b1", "instagram", "Halcyon", "halcyonbooks"),
        account("buffer", "b1", "instagram", "Halcyon", "halcyonbooks"),
    ])
    assert len(pages) == 1
    assert pages[0].reachable_by == [{
        "provider": "buffer", "provider_label": "Buffer", "id": "b1", "label": "Halcyon",
    }]
    assert not pages[0].shared


def test_the_order_engines_were_read_in_is_preserved() -> None:
    # Arbitrary but stable beats a default that reshuffles between reads.
    pages = consolidate([
        account("zernio", "z9", "youtube", "Naceto", "naceto"),
        account("buffer", "b1", "youtube", "Naceto Books", "naceto"),
    ])
    assert pages[0].reachable_by[0]["provider"] == "zernio"
    assert page_payload(pages[0])["default_provider"] == "zernio"


# --- the payload --------------------------------------------------------------


def test_a_shared_page_names_exactly_one_engine_to_deliver_through() -> None:
    """The whole point of consolidating.

    Delivering a consolidated page through every engine that can reach it would
    publish the same post to the same audience once per engine - the duplicate
    this feature exists to prevent, caused by the feature meant to prevent it.
    """
    [page] = consolidate([
        account("buffer", "b1", "instagram", "Halcyon", "halcyonbooks"),
        account("zernio", "z9", "instagram", "Halcyon", "halcyonbooks"),
    ])
    payload = page_payload(page)
    assert payload["engine_count"] == 2
    assert payload["default_provider"] == "buffer"
    assert payload["default_integration_id"] == "b1"
    # Both routes are offered so the operator can switch, but only one is the
    # default and the payload never implies posting to both.
    assert len(payload["reachable_by"]) == 2


def test_an_unmergeable_account_keeps_an_engine_specific_key() -> None:
    # So two handle-less accounts on one platform can never collide.
    pages = consolidate([
        account("buffer", "b1", "reddit", "r/books"),
        account("zernio", "z9", "reddit", "r/books"),
    ])
    keys = {page.key for page in pages}
    assert keys == {"reddit:buffer:b1", "reddit:zernio:z9"}


def test_a_merged_page_keys_on_the_handle_not_on_an_engine_id() -> None:
    [page] = consolidate([
        account("buffer", "b1", "instagram", "Halcyon", "halcyonbooks"),
        account("zernio", "z9", "instagram", "Halcyon", "halcyonbooks"),
    ])
    assert page.key == "instagram:@halcyonbooks"
