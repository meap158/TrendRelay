"""The capability table, and the promise that it cannot disagree with delivery.

Every cell is read from the declaration the runtime enforces. These tests exist
to keep it that way: each one asks the table a question and checks the answer
against the code that would actually refuse the post, so a table that drifts
fails here rather than in somebody's account.
"""

from __future__ import annotations

from trendrelay_api.integrations.publishing import (
    PLATFORM_LABELS,
    PROVIDERS,
    carousel_fits_destination,
    first_comment_deliverable,
)
from trendrelay_api.integrations.publishing_matrix import capability_matrix


def rows() -> dict[str, dict]:
    return {row["id"]: row for row in capability_matrix()["platforms"]}


# --- the table matches what delivery does -------------------------------------


def test_every_carousel_cell_agrees_with_the_guard_that_refuses_one() -> None:
    """The table and the refusal are the same fact, asked twice.

    If these ever disagree, one of them is lying to somebody composing a post.
    """
    for platform, row in rows().items():
        for engine_id in PROVIDERS:
            promised = engine_id in row["carousel_engines"]
            allowed, _ = carousel_fits_destination(engine_id, platform, 1)
            # The guard passes an engine it does not recognise; here every id is
            # known, so a pass means it genuinely carries one.
            assert promised == allowed, (
                f"{engine_id} on {platform}: table says {promised}, guard says {allowed}"
            )


def test_every_follow_up_cell_agrees_with_the_delivery_predicate() -> None:
    for platform, row in rows().items():
        for engine_id in PROVIDERS:
            reaches = platform in PROVIDERS[engine_id].platforms
            promised = engine_id in row["follow_up_engines"]
            assert promised == (reaches and first_comment_deliverable(engine_id, platform))


def test_every_network_the_engines_declare_has_a_row() -> None:
    """No engine offers a network the table cannot name."""
    listed = set(rows())
    for engine in PROVIDERS.values():
        assert set(engine.platforms) <= listed
    assert listed == set(PLATFORM_LABELS)


# --- the facts an operator is reading it for ----------------------------------


def test_instagram_and_tiktok_are_the_networks_with_no_clickable_link() -> None:
    """The affiliate question. A URL in those captions is text, not a link."""
    found = rows()

    assert found["instagram"]["link"]["clickable"] is False
    assert found["tiktok"]["link"]["clickable"] is False
    for platform in ("facebook", "threads", "linkedin", "twitter", "youtube"):
        assert found[platform]["link"]["clickable"] is True, platform


def test_a_ceiling_no_engine_can_reach_is_shown_as_such() -> None:
    """Instagram takes ten images and nothing here can post them.

    The case the whole table exists for: the network has the feature, so asking
    only "does Instagram do carousels" gives an answer that gets a post refused.
    """
    instagram = rows()["instagram"]

    assert instagram["carousel_limit"] == 10
    assert instagram["carousel_engines"] == []


def test_the_reachable_carousel_is_tiktok_through_two_engines() -> None:
    tiktok = rows()["tiktok"]

    assert tiktok["carousel_limit"] == 35
    assert set(tiktok["carousel_engines"]) == {"zernio", "woopsocial"}


def test_a_thread_network_calls_it_a_reply_and_a_comment_network_a_comment() -> None:
    # On Threads the reply *is* the next post; calling it a comment describes
    # something the reader will never see.
    found = rows()

    assert found["threads"]["follow_up_label"] == "Reply in the thread"
    assert found["instagram"]["follow_up_label"] == "First comment"
    assert found["youtube"]["follow_up_label"] is None


def test_only_one_engine_carries_anything_after_a_post() -> None:
    """Worth stating plainly: it is the constraint that shapes affiliate posts."""
    carriers = {
        engine
        for row in rows().values()
        for engine in row["follow_up_engines"]
    }

    assert carriers == {"buffer"}


def test_an_engine_that_cannot_fetch_a_url_says_so() -> None:
    engines = {row["id"]: row for row in capability_matrix()["engines"]}

    assert engines["woopsocial"]["ingests_media_url"] is False
    assert engines["buffer"]["requires_public_media"] is True
