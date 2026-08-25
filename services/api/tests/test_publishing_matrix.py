"""The capability table, and the promise that it cannot disagree with delivery.

Every cell is read from the declaration the runtime enforces. These tests exist
to keep it that way: each one asks the table a question and checks the answer
against the code that would actually refuse the post, so a table that drifts
fails here rather than in somebody's account.
"""

from __future__ import annotations

from trendrelay_api.integrations import engine_limits
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


def test_the_ceiling_and_the_engines_that_can_reach_it_are_separate_facts() -> None:
    """The case the whole table exists for.

    "Does Instagram do carousels" and "can anything here post one" are
    different questions, and answering the first when asked the second gets a
    post refused at delivery. Instagram was the standing example of the two
    diverging - the network took ten and no engine declared any - until the
    exclusion turned out to rest on Buffer's refusal being read as Zernio's.

    So it is pinned as two facts rather than one: the ceiling is the network's,
    and the engine list is only those that declare it. Buffer reaches Instagram
    and is absent here, which is the whole point.
    """
    instagram = rows()["instagram"]

    assert instagram["carousel_limit"] == 10
    assert instagram["carousel_engines"] == ["zernio"]
    assert "buffer" in instagram["engines"], "Buffer posts here, just not galleries"


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


def test_two_engines_carry_text_after_a_post_and_only_one_reaches_threads() -> None:
    """Worth stating plainly: it is the constraint that shapes affiliate posts."""
    found = rows()
    carriers = {
        engine
        for row in found.values()
        for engine in row["follow_up_engines"]
    }

    # Buffer and Zernio both carry a first comment; nothing else carries anything.
    assert carriers == {"buffer", "zernio"}
    # A comment network is reached by both engines...
    assert set(found["instagram"]["follow_up_engines"]) == {"buffer", "zernio"}
    # ...Bluesky's thread is reached by both - Zernio's `threadItems` takes the
    # whole chain there - while the other reply networks stay Buffer's alone.
    assert set(found["bluesky"]["follow_up_engines"]) == {"buffer", "zernio"}
    assert found["threads"]["follow_up_engines"] == ["buffer"]
    assert found["mastodon"]["follow_up_engines"] == ["buffer"]


def test_an_engine_that_cannot_fetch_a_url_says_so() -> None:
    engines = {row["id"]: row for row in capability_matrix()["engines"]}

    assert engines["woopsocial"]["ingests_media_url"] is False
    assert engines["buffer"]["requires_public_media"] is True


# --- published prices, and the honesty they need -------------------------------


def test_every_engine_publishes_a_plan_ladder_with_a_free_tier() -> None:
    """Somebody comparing tiers needs the whole ladder, not just what they have.

    `FREE_PLAN` answers "what does this account probably allow". This answers
    "what would the next one cost", which is a different question and the reason
    quoting paid tiers is safe here: nothing in it claims to be the plan in
    force.
    """
    for row in capability_matrix()["engines"]:
        plans = row["plans"]
        assert plans["tiers"], f"{row['id']} lists no plans"
        free = [tier for tier in plans["tiers"] if tier["free"]]
        assert len(free) == 1, f"{row['id']} should have exactly one free tier"
        assert plans["tiers"][0]["free"], f"{row['id']} should list the free tier first"


def test_no_price_is_shown_without_a_date_and_a_source() -> None:
    """A figure nobody can verify from inside the app is the one most likely to
    be believed after it stops being true.

    So every tier carries the page it was read from and the day it was read. A
    price with neither is worse than no price.
    """
    for row in capability_matrix()["engines"]:
        plans = row["plans"]
        assert plans["checked_on"], f"{row['id']} does not say when it was checked"
        assert str(plans["source"] or "").startswith("https://"), (
            f"{row['id']} does not link the pricing page it was read from"
        )


def test_the_ladder_agrees_with_the_free_terms_already_enforced() -> None:
    """Two records of the free tier must not drift.

    `FREE_PLAN` is what the allowance logic reads; the ladder is what the
    operator reads. If they disagree, the interface is quoting a limit the app
    does not apply.
    """
    for engine_id, terms in engine_limits.FREE_PLAN.items():
        free = next(
            tier for tier in engine_limits.plan_ladder_payload(engine_id)["tiers"]
            if tier["free"]
        )
        assert str(terms["accounts"]) in free["accounts"], (
            f"{engine_id}: ladder says {free['accounts']!r} accounts, "
            f"free terms say {terms['accounts']!r}"
        )


def test_a_plan_says_what_it_means_for_publishing() -> None:
    """A pricing page sells the whole product, and most of it is not publishing.

    WoopSocial's credits are the case that matters: they meter AI generation,
    TrendRelay never spends one, and reading them as a posting allowance would
    make a free account look capped when it is not.
    """
    caveats = {
        row["id"]: row["plans"]["caveat"] for row in capability_matrix()["engines"]
    }

    assert all(caveats.values()), f"no caveat for: {[k for k, v in caveats.items() if not v]}"
    assert "not publishing" in caveats["woopsocial"]
    assert "queue depth" in caveats["buffer"]
