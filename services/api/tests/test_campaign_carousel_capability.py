"""A campaign's pictures only go where pictures can go.

Carousel support is narrower than it looks and it belongs to the engine as much
as to the network: only Zernio and WoopSocial declare one, and only for TikTok.
A workspace whose Instagram and Threads run through Buffer has nowhere to send a
gallery, even though both networks have galleries of their own.

Before this, a carousel was paired with any destination at all and the engine
was the thing that said no - after the post had been built, on a campaign that
posts unattended.
"""

from __future__ import annotations

from types import SimpleNamespace

from trendrelay_api.integrations.publishing import carousel_fits_destination

# --- the capability itself ----------------------------------------------------


def test_the_engine_decides_not_just_the_network() -> None:
    """Threads has galleries; Buffer has no contract for one.

    This is the case the whole check exists for. Asking only "does this network
    support carousels" would have said yes and been wrong.
    """
    fits, why = carousel_fits_destination("buffer", "threads", 3)

    assert not fits
    assert "Buffer" in why and "Threads" in why


def test_an_engine_that_carries_them_is_allowed() -> None:
    assert carousel_fits_destination("zernio", "tiktok", 3) == (True, None)
    assert carousel_fits_destination("woopsocial", "tiktok", 3)[0]


def test_an_engine_that_carries_none_says_so_rather_than_listing_nothing() -> None:
    fits, why = carousel_fits_destination("bundle_social", "tiktok", 3)

    assert not fits
    assert "no photo carousels at all" in why


def test_the_networks_own_ceiling_is_enforced() -> None:
    # TikTok takes thirty-five. Refused here rather than by the network, which
    # would reject a post that had already been built and uploaded.
    fits, why = carousel_fits_destination("zernio", "tiktok", 40)

    assert not fits
    assert "35" in why and "40" in why


def test_a_carousel_with_no_pictures_is_refused() -> None:
    assert not carousel_fits_destination("zernio", "tiktok", 0)[0]


def test_an_unknown_login_is_not_refused_on_a_guess() -> None:
    """Not recognising an engine is not evidence the post is wrong.

    The delivery guard refuses what this cannot judge; inventing a constraint
    here would block a destination for being unfamiliar.
    """
    assert carousel_fits_destination("some-future-engine", "tiktok", 3) == (True, None)


# --- the path that posts without anybody watching -----------------------------


def _execution(**overrides) -> SimpleNamespace:
    base = dict(
        caption="Real copy that somebody wrote.",
        offer_ids=[],
        tracking_links=[],
        placement="caption",
        first_comment=None,
        thread=[],
        media_path="",
        title=None,
        image_paths=["a.jpg", "b.jpg"],
        provider="buffer",
        platform="threads",
        integration_id="acc-1",
        post_type="post",
        scheduled_at=None,
    )
    return SimpleNamespace(**(base | overrides))


def test_the_unattended_path_catches_a_carousel_it_cannot_deliver() -> None:
    """The hole this closes.

    `engine_check` is off unattended, deliberately: media hosting and the like
    are environment rather than authorship. But whether a login can post a
    gallery is a fact about what was composed, so switching the whole check off
    meant the one path that publishes with nobody watching was the one that did
    not ask.
    """
    from trendrelay_api.campaign_runner import finalization_problems

    autopilot = SimpleNamespace(workspace_id="ws-1")

    problems = finalization_problems(
        autopilot, _execution(), engine_check=False,
    )

    assert any("carousel" in problem.lower() for problem in problems), problems


def test_a_carousel_its_destination_can_take_raises_nothing() -> None:
    from trendrelay_api.campaign_runner import finalization_problems

    autopilot = SimpleNamespace(workspace_id="ws-1")

    problems = finalization_problems(
        autopilot,
        _execution(provider="zernio", platform="tiktok"),
        engine_check=False,
    )

    assert problems == []


def test_a_video_post_is_not_asked_about_carousels() -> None:
    from trendrelay_api.campaign_runner import finalization_problems

    autopilot = SimpleNamespace(workspace_id="ws-1")

    problems = finalization_problems(
        autopilot,
        _execution(image_paths=[], media_path="clip.mp4"),
        engine_check=False,
    )

    assert problems == []
