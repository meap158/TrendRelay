"""Where the affiliate link lives: the network's call by default, the
operator's by configuration, and never a promise no engine can keep.

Plus the language of composed scaffolding, which follows the campaign rather
than defaulting to English.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trendrelay_api.campaign_autopilot import (
    LOCALISED_TEXTS,
    compose_products,
    language_code,
    localised_text,
    resolve_placement,
)
from trendrelay_api.integrations import publishing
from trendrelay_api.integrations.publishing import (
    first_comment_deliverable,
    thread_deliverable,
)

#: The interface's own list of languages, read from the file that defines it
#: rather than copied here, so adding a locale to the picker without teaching
#: the composer to write it fails a test instead of shipping.
LOCALES_TS = (
    Path(__file__).resolve().parents[3] / "apps" / "web" / "lib" / "i18n" / "locales.ts"
)


def _interface_locales() -> list[dict[str, str]]:
    source = LOCALES_TS.read_text(encoding="utf-8")
    block = source[source.index("export const LOCALES"):source.index("] as const;")]
    found = re.findall(r'code:\s*"([a-z-]+)"\s*,\s*label:\s*"([^"]+)"', block)
    assert found, f"no locales parsed from {LOCALES_TS}"
    return [{"code": code, "label": label} for code, label in found]

# --- who can actually post a comment after the post -----------------------------


#: Which network carries a follow-up, through which Buffer field, and what the
#: reader sees. Written out because two words here read as one thing: *Threads*
#: is the Meta network, *a thread* is a chain of replies that four networks
#: have. Keeping the table in the test is what stops the next reader concluding,
#: as this test itself once did, that having the second rules out the first.
FOLLOW_UP_BY_NETWORK = {
    "instagram": ("firstComment", "first comment"),
    "facebook": ("firstComment", "first comment"),
    "linkedin": ("firstComment", "first comment"),
    "twitter": ("thread[]", "reply in the thread"),
    "threads": ("thread[]", "reply in the thread"),
    "mastodon": ("thread[]", "reply in the thread"),
    "bluesky": ("thread[]", "reply in the thread"),
}
#: Buffer serves these too, and none of them takes anything after the post.
NO_FOLLOW_UP = ("tiktok", "youtube", "pinterest", "googlebusiness")


@pytest.mark.parametrize(("network", "expected"), sorted(FOLLOW_UP_BY_NETWORK.items()))
def test_every_network_that_can_carry_a_follow_up_says_so(network, expected) -> None:
    """Threads used to answer False here, and the campaign fell back to the
    caption saying its engine "cannot post one" - on a network Buffer had been
    posting replies to through the thread array all along. Two fields, one
    capability: the operator is choosing where the link goes, not which of
    Buffer's inputs carries it."""
    _field, called = expected

    assert first_comment_deliverable("buffer", network) is True
    assert publishing.follow_up_kind(network) == called


@pytest.mark.parametrize("network", NO_FOLLOW_UP)
def test_a_network_with_neither_field_still_says_no(network) -> None:
    assert first_comment_deliverable("buffer", network) is False


def test_the_two_fields_stay_apart_even_though_the_capability_is_one() -> None:
    # Buffer rejects a field a network does not declare, so the sets that decide
    # what is *sent* must not be merged along with the question of what is
    # possible.
    assert publishing.FIRST_COMMENT_PLATFORMS.isdisjoint(publishing.THREAD_PLATFORMS)
    assert publishing.FOLLOW_UP_PLATFORMS == (
        publishing.FIRST_COMMENT_PLATFORMS | publishing.THREAD_PLATFORMS
    )


def test_zernio_joins_buffer_on_comment_networks_and_threads_bluesky_only() -> None:
    # Buffer reaches every follow-up network by its two fields. Zernio carries a
    # first comment on the three comment networks through a field of its own,
    # and threads on exactly one reply network - Bluesky's `threadItems` takes
    # the whole chain - while X, Threads and Mastodon through it still take no
    # follow-up. The other two engines have no follow-up field at all.
    assert first_comment_deliverable("buffer", "facebook") is True
    assert first_comment_deliverable("buffer", "threads") is True
    assert first_comment_deliverable("zernio", "instagram") is True
    assert first_comment_deliverable("zernio", "facebook") is True
    assert first_comment_deliverable("zernio", "linkedin") is True
    assert first_comment_deliverable("zernio", "bluesky") is True
    assert first_comment_deliverable("zernio", "threads") is False
    assert first_comment_deliverable("zernio", "mastodon") is False
    assert first_comment_deliverable("zernio", "twitter") is False
    assert first_comment_deliverable("bundle_social", "instagram") is False
    assert first_comment_deliverable("woopsocial", "facebook") is False

    # The narrower question drives reply inputs. Facebook has a first comment
    # through both engines but is not a multi-post thread destination.
    assert thread_deliverable("buffer", "facebook") is False
    assert thread_deliverable("zernio", "facebook") is False
    assert thread_deliverable("buffer", "threads") is True
    assert thread_deliverable("zernio", "bluesky") is True
    assert thread_deliverable("zernio", "threads") is False


def test_other_engines_say_they_drop_comments_instead_of_dropping_silently() -> None:
    """The preview must name the loss. A first comment handed to an engine
    that cannot post one used to vanish between preview and delivery."""
    body = publishing.PublishRequest(
        workspace_id="ws", video_path="clip.mp4", caption="hello",
        date=__import__("datetime").datetime(2026, 8, 17, 12, 0),
        first_comment="the link", thread=["reply"],
        targets=[publishing.PublishTarget(platform="tiktok", integration_id="a1")],
        confirm_external_action=True,
    )

    plan = {item["platform"]: item["notes"] for item in
            publishing._delivery_plan(publishing.PROVIDERS["zernio"], body)}

    assert any("cannot post one after the post" in note for note in plan["tiktok"])
    assert any("cannot post replies" in note for note in plan["tiktok"])


# --- placement configuration ----------------------------------------------------


def test_auto_stays_the_network_s_own_decision() -> None:
    assert resolve_placement("facebook", override="auto").placement == "caption"
    assert resolve_placement("tiktok", override="auto").placement == "bio"


def test_a_caption_override_is_honoured_with_its_trade_off_written_down() -> None:
    placement = resolve_placement("tiktok", override="caption")

    assert placement.placement == "caption"
    assert "not clickable" in placement.reason
    assert "copy" in placement.reason


def test_a_deliverable_first_comment_override_takes_effect() -> None:
    placement = resolve_placement(
        "facebook", override="first_comment", comment_deliverable=True
    )

    assert placement.placement == "first_comment"
    assert "Configured" in placement.reason


def test_an_undeliverable_first_comment_falls_back_loudly() -> None:
    """A link in a comment no engine will post is not a placement, it is a
    lost link - so the configuration falls back to the network default and
    the reason says both halves."""
    placement = resolve_placement(
        "facebook", override="first_comment", comment_deliverable=False
    )

    assert placement.placement == "caption", "facebook's own default"
    assert "cannot post one" in placement.reason
    assert "falling back" in placement.reason


def test_the_composer_follows_the_destination_s_configuration() -> None:
    composed = compose_products(
        platform="facebook",
        body="Three ways to pull a better espresso.",
        products=[("Espresso kit", "https://tr.example/c/abc")],
        disclosure="Affiliate link.",
        placement_override="first_comment",
        comment_deliverable=True,
    )

    assert composed.first_comment is not None
    assert "https://tr.example/c/abc" in composed.first_comment
    assert "https://tr.example/c/abc" not in composed.caption
    assert composed.caption.startswith("Affiliate link."), (
        "the disclosure leads the caption whatever the link placement"
    )


def test_a_first_comment_post_keeps_its_hashtags_in_the_caption() -> None:
    """The one composing branch that lost them.

    Every other placement appends the hashtags to the caption - the bio and
    thread branches delegate to `compose`, the caption branch appends them
    itself - but the first-comment branch built its caption from disclosure
    and body alone. A Facebook-via-Zernio campaign, whose link lives in the
    first comment, posted every caption with the hashtags silently gone.
    """
    composed = compose_products(
        platform="facebook",
        body="Three ways to pull a better espresso.",
        hashtags=["espresso", "#coffee"],
        products=[("Espresso kit", "https://tr.example/c/abc")],
        disclosure="Affiliate link.",
        placement_override="first_comment",
        comment_deliverable=True,
    )

    assert composed.placement.placement == "first_comment"
    assert "#espresso #coffee" in composed.caption
    # The comment is still only the link; hashtags belong to the caption.
    assert "#espresso" not in (composed.first_comment or "")


def test_a_bio_override_keeps_the_caption_pointing_at_the_profile() -> None:
    composed = compose_products(
        platform="facebook",
        body="Body.",
        products=[("Espresso kit", "https://tr.example/c/abc")],
        disclosure="Affiliate link.",
        bio_hint="Link ở tiểu sử",
        placement_override="bio",
    )

    assert composed.placement.placement == "bio"
    assert "https://tr.example/c/abc" not in composed.caption
    assert "Link ở tiểu sử" in composed.caption


# --- language -------------------------------------------------------------------


@pytest.mark.parametrize(("languages", "expected"), [
    (["Vietnamese"], "vi"),
    (["tiếng việt"], "vi"),
    (["vi"], "vi"),
    (["English", "Vietnamese"], "en"),
    (["Klingon"], "en"),
    ([], "en"),
    (None, "en"),
    # The rest of what the interface offers, by code, by English name, and by
    # the name the language calls itself - which is what the picker shows.
    (["ja"], "ja"),
    (["Japanese"], "ja"),
    (["日本語"], "ja"),
    (["fr"], "fr"),
    (["Français"], "fr"),
    (["zh"], "zh"),
    (["Mandarin Chinese"], "zh"),
    (["中文"], "zh"),
    (["ru"], "ru"),
    (["Русский"], "ru"),
    (["ar"], "ar"),
    (["العربية"], "ar"),
])
def test_the_campaign_s_own_language_wins_and_unknowns_stay_english(
    languages, expected
) -> None:
    assert language_code(languages) == expected


def test_every_language_the_interface_offers_can_actually_be_written() -> None:
    """The picker and the scaffolding have to name the same set.

    A language offered in the campaign form but missing from `LOCALISED_TEXTS`
    falls back to English, so the campaign reads as though it accepted the
    choice and then posts the disclosure in the wrong language. That is what the
    old free-text field did with "th".
    """
    offered = {item["code"] for item in _interface_locales()}
    assert offered <= set(LOCALISED_TEXTS), (
        "these languages are offered but have no scaffolding: "
        f"{sorted(offered - set(LOCALISED_TEXTS))}"
    )
    for code in offered:
        for key in LOCALISED_TEXTS["en"]:
            written = localised_text(code, key)
            assert written, f"{code}.{key} is empty"
            if code != "en":
                assert written != LOCALISED_TEXTS["en"][key], (
                    f"{code}.{key} is still the English string"
                )


@pytest.mark.parametrize(("platform", "label", "expected"), [
    ("threads", "halcyonbooks.official", "https://www.threads.net/@halcyonbooks.official"),
    ("tiktok", "@handle", "https://www.tiktok.com/@handle"),
    ("instagram", "handle", "https://www.instagram.com/handle"),
    ("tiktok", "Tiêu Dùng Thông Minh 24h", None),  # a name, not an address
    ("linkedin", "handle", None),  # no address this can vouch for
    ("threads", "", None),
    (None, "handle", None),
])
def test_a_page_link_exists_only_when_the_label_is_an_address(
    platform, label, expected
) -> None:
    from trendrelay_api.campaign_autopilot import profile_url

    assert profile_url(platform, label) == expected


def test_a_shopee_short_link_counts_as_tracked_in_publish() -> None:
    """The network's own link is the tracking link now (ADR 0022).

    The preview used to warn that a post without an internal /c/ link was
    unattributable - which would nag on every correctly-linked post carrying
    the short link Shopee actually pays on.
    """
    def post(caption: str) -> publishing.PublishRequest:
        return publishing.PublishRequest(
            workspace_id="ws", video_path="clip.mp4", caption=caption,
            date=__import__("datetime").datetime(2026, 8, 17, 12, 0),
            targets=[publishing.PublishTarget(platform="facebook", integration_id="a1")],
            confirm_external_action=True,
        )

    assert publishing.carries_tracking_link(
        post("Ba cách pha espresso ngon hơn. https://s.shopee.vn/2gAN9f0Ef6")
    ) is True
    assert publishing.carries_tracking_link(post("No link at all here.")) is False


def test_scaffolding_speaks_the_language_or_falls_back_to_english() -> None:
    assert "hoa hồng" in localised_text("vi", "disclosure")
    assert localised_text("vi", "bio_hint") == "Link ở tiểu sử"
    assert localised_text("de", "disclosure") == localised_text("en", "disclosure"), (
        "an unknown language falls back to English rather than to silence"
    )


# --- switching affiliate off for one account ---------------------------------


def test_an_account_can_be_told_to_carry_no_link() -> None:
    """The answer the placement list was missing.

    Some networks penalise affiliate links, and an account under review wants a
    quiet week. Before this the only way to stop them was to turn products off
    for the whole campaign, which stops them everywhere.
    """
    placement = resolve_placement("tiktok", override="none")

    assert placement.placement == "none"
    # Its own explanation. "No offer is attached to this campaign" is a
    # different fact and would send somebody to fix the campaign's products.
    assert "off for this destination" in placement.reason
    assert "other accounts" in placement.reason


def test_switching_it_off_publishes_no_link_and_no_disclosure() -> None:
    """`none` is not a place a link can go.

    Left to the branches below it, the resolved placement fell through to the
    first-comment case and published the link that had just been switched off.
    """
    post = compose_products(
        platform="tiktok",
        body="Hard techno in a steel cage.",
        hashtags=["techno", "rave"],
        products=[("Ring light", "https://example.test/aff/ring-light")],
        disclosure="#ad",
        placement_override="none",
        comment_deliverable=True,
    )

    assert post.placement.placement == "none"
    assert "example.test" not in post.caption
    assert not post.first_comment
    assert not post.thread
    # Nothing is being endorsed, so there is nothing to disclose. Leaving "#ad"
    # on an organic post claims a relationship that this post does not have.
    assert "#ad" not in post.caption
    # The words and the hashtags still go out - this is a post, not a refusal.
    assert "Hard techno" in post.caption
    assert "#techno" in post.caption


def test_switching_it_off_does_not_need_a_disclosure_to_be_configured() -> None:
    """A campaign mid-setup can still post organically to one account.

    `compose_products` refuses to publish an undisclosed endorsement. With the
    link off there is no endorsement, so the refusal must not fire - otherwise
    turning affiliate off would be blocked by the affiliate settings.
    """
    post = compose_products(
        platform="tiktok",
        body="Just the music.",
        hashtags=[],
        products=[("Ring light", "https://example.test/aff/ring-light")],
        disclosure="",
        require_disclosure=True,
        placement_override="none",
    )

    assert post.placement.placement == "none"
    assert "example.test" not in post.caption


def test_the_other_accounts_still_carry_the_link() -> None:
    """Off here is not off everywhere; that is the whole point of the setting."""
    products = [("Ring light", "https://example.test/aff/ring-light")]

    quiet = compose_products(
        platform="tiktok", body="Same post.", hashtags=[], products=products,
        disclosure="#ad", placement_override="none",
    )
    selling = compose_products(
        platform="facebook", body="Same post.", hashtags=[], products=products,
        disclosure="#ad", placement_override="caption",
    )

    assert "example.test" not in quiet.caption
    assert "example.test" in selling.caption
