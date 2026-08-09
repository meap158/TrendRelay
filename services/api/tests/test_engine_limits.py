"""What each engine allows, and how confident the figure is."""

from __future__ import annotations

from trendrelay_api.integrations.engine_limits import (
    FREE_PLAN,
    Allowance,
    allowances,
    exhausted,
    infer_plan,
    parse_rate_limit,
    parse_rate_limit_policy,
    payload,
    plan_payload,
    spent_note,
)


def by_id(items: list[Allowance]) -> dict[str, Allowance]:
    return {item.id: item for item in items}


# --- the header Buffer sends --------------------------------------------------


def test_a_rate_limit_header_is_read_into_figures() -> None:
    assert parse_rate_limit("limit=100, remaining=97, reset=42") == {
        "limit": 100, "remaining": 97, "reset": 42,
    }


def test_an_unfamiliar_header_costs_a_figure_not_the_page() -> None:
    """An engine that changes its header shape must not break the screen.

    The figure is a convenience; the page it sits on is how someone publishes.
    """
    assert parse_rate_limit(None) == {}
    assert parse_rate_limit("something entirely different") == {}
    assert parse_rate_limit("limit=5; window=60") == {"limit": 5}


# --- the three confidences ----------------------------------------------------


def test_connected_accounts_are_counted_not_quoted() -> None:
    found = by_id(allowances("buffer", account_count=2))
    assert found["accounts"].confidence == "counted"
    assert found["accounts"].used == 2
    assert found["accounts"].limit == 3
    assert found["accounts"].remaining == 1


def test_a_measured_figure_says_the_engine_reported_it() -> None:
    found = by_id(allowances(
        "buffer", account_count=1, rate_limit={"limit": 100, "remaining": 88},
    ))
    assert found["requests"].confidence == "measured"
    assert found["requests"].used == 12
    assert found["requests"].remaining == 88
    assert "Reported by the engine" in found["requests"].note


def test_a_published_figure_says_where_it_came_from_and_when() -> None:
    """The distinction this module exists for.

    A year-old scrape of a pricing page shown as though it were live is worse
    than no figure, because it gets believed and reconciled against a bill.
    """
    found = by_id(allowances("bundle_social", account_count=1))
    monthly = found["posts_per_month"]
    assert monthly.confidence == "published"
    assert monthly.used is None
    assert monthly.remaining is None
    assert "Not read from the engine" in monthly.note
    assert "bundle.social/pricing" in monthly.note


def test_bundle_socials_own_daily_counter_is_measured() -> None:
    found = by_id(allowances(
        "bundle_social", account_count=1,
        daily={"posts": {"used": 4, "limit": 20, "remaining": 16},
               "comments": {"used": 0, "limit": 50}},
    ))
    assert found["daily_posts"].confidence == "measured"
    assert found["daily_posts"].remaining == 16
    assert found["daily_comments"].used == 0


# --- the numbers that are easy to get wrong -----------------------------------


def test_buffers_ten_is_a_queue_depth_not_a_monthly_allowance() -> None:
    # Publishing one frees its slot. Recorded so nobody adds it up as "ten posts
    # a month" and plans a campaign around a number that does not exist.
    found = by_id(allowances("buffer", account_count=1))
    assert found["queued_per_channel"].limit == 10
    assert "not a monthly allowance" in found["queued_per_channel"].note
    assert "posts_per_month" not in found


def test_zernio_is_genuinely_uncapped_rather_than_unknown() -> None:
    """"No limit" and "we do not know" are different answers.

    Zernio charges per connected account and every account, free or paid, posts
    without limit. Reporting that as an unknown would suggest a ceiling to worry
    about that does not exist.
    """
    found = by_id(allowances("zernio", account_count=2))
    assert found["posts_per_month"].unlimited
    assert found["posts_per_month"].remaining is None
    assert "Uncapped" in found["posts_per_month"].note
    assert found["accounts"].limit == 2


def test_a_measured_request_budget_replaces_the_published_one() -> None:
    # Once the engine has reported the 30-day quota in its policy, quoting the
    # pricing page beside it would be a scraped number standing in for a
    # reported one.
    measured = by_id(allowances(
        "buffer", account_count=1, policy={900: 100, 2592000: 7_500}))
    assert measured["requests_per_30_days"].confidence == "measured"
    assert measured["requests_per_30_days"].limit == 7_500

    without = by_id(allowances("buffer", account_count=1))
    assert without["requests_per_30_days"].confidence == "published"
    assert without["requests_per_30_days"].limit == 3_000


def test_the_window_and_the_thirty_day_budget_are_both_shown() -> None:
    """They are different limits, and one does not stand in for the other.

    The `RateLimit` header counts down the 15-minute window; the 30-day quota is
    a separate ceiling with no live remainder. Suppressing either leaves someone
    reading one number as though it were the other.
    """
    found = by_id(allowances(
        "buffer", account_count=1,
        rate_limit={"limit": 100, "remaining": 50},
        policy={900: 100, 2592000: 3_000},
    ))
    assert found["requests"].used == 50
    assert found["requests_per_30_days"].limit == 3_000
    # No remainder on the 30-day figure: the engine never reports one.
    assert found["requests_per_30_days"].used is None


def test_remaining_never_goes_negative() -> None:
    # Over the cap is a real state - a plan can be downgraded with accounts
    # already connected - and a negative remainder reads as a bug.
    found = by_id(allowances("buffer", account_count=9))
    assert found["accounts"].remaining == 0
    assert found["accounts"].used == 9


def test_an_unknown_engine_reports_nothing_rather_than_zero() -> None:
    assert allowances("some_new_engine", account_count=4) == []


def test_the_payload_carries_the_confidence_to_the_page() -> None:
    [first] = [item for item in allowances("zernio", account_count=1)
               if item.id == "accounts"]
    body = payload(first)
    assert body["confidence"] == "counted"
    assert body["remaining"] == 1
    assert body["unlimited"] is False


# --- which plan the account is on ---------------------------------------------


def test_the_policy_header_gives_a_quota_per_window() -> None:
    assert parse_rate_limit_policy("100;w=900, 250;w=86400, 3000;w=2592000") == {
        900: 100, 86400: 250, 2592000: 3000,
    }
    assert parse_rate_limit_policy(None) == {}
    assert parse_rate_limit_policy("no windows here") == {}


def test_a_buffer_plan_is_read_off_its_thirty_day_quota() -> None:
    for quota, expected in ((3_000, "Free"), (7_500, "Essentials"), (15_000, "Team")):
        plan = infer_plan("buffer", policy={900: 100, 2592000: quota})
        assert plan.name == expected, quota
        assert plan.confidence == "measured"


def test_the_short_window_never_names_a_buffer_plan() -> None:
    """The 15-minute quota is 100 on every tier.

    Reading whichever window happened to be in the header would call a Team
    account Free, which is the one wrong answer that matters: it is the figure
    someone checks before deciding they need to upgrade.
    """
    assert infer_plan("buffer", policy={900: 100}).name is None
    assert infer_plan("buffer", policy={900: 100}).confidence == "published"


def test_an_unrecognised_quota_is_left_unnamed_rather_than_rounded() -> None:
    # A tier we have no figure for is a tier we cannot name. Snapping to the
    # nearest published number would state a plan the engine never reported.
    assert infer_plan("buffer", policy={2592000: 9_999}).name is None


def test_bundle_social_above_the_free_cap_is_paid_without_a_tier() -> None:
    free = infer_plan("bundle_social", daily={"posts": {"limit": 20, "used": 3}})
    assert (free.name, free.confidence) == ("Free", "measured")

    paid = infer_plan("bundle_social", daily={"posts": {"limit": 200}})
    assert (paid.name, paid.confidence) == ("Paid", "measured")
    # Named no further, because the paid tiers are not published per-figure.
    assert "not something it says" in paid.note


def test_zernio_is_counted_because_that_is_what_it_charges_by() -> None:
    assert infer_plan("zernio", account_count=2).name == "Free"
    assert infer_plan("zernio", account_count=3).name == "Paid"
    assert infer_plan("zernio", account_count=3).confidence == "counted"


def test_an_engine_that_reported_nothing_names_no_plan() -> None:
    """None is a real answer, and a different one from "Free".

    No engine exposes a plan name, so silence has to read as silence. Defaulting
    to the free tier would show a paying account the wrong limits with no sign
    that the figure was a guess.
    """
    plan = infer_plan("bundle_social")
    assert plan.name is None
    assert plan.confidence == "published"
    assert "does not report which plan" in plan.note
    assert plan_payload(plan) == {
        "name": None, "confidence": "published", "note": plan.note,
    }


# --- running out ---------------------------------------------------------------


def test_a_spent_daily_allowance_is_what_stops_a_post() -> None:
    items = allowances(
        "bundle_social", account_count=1,
        daily={"posts": {"used": 20, "limit": 20, "remaining": 0}},
    )
    spent = exhausted(items)
    assert spent is not None
    assert spent.id == "daily_posts"
    # The numbers, not just the verdict: "out of quota" gives no way to judge
    # whether to wait or to fix something.
    assert spent_note(spent) == "Posts today: 20 of 20 used. Resets daily."


def test_a_full_account_list_does_not_stop_posting() -> None:
    """3 of 3 connected accounts is the plan in full use, not a dead engine.

    Blocking on it would switch off every destination at exactly the moment the
    workspace is using everything it pays for.
    """
    items = allowances("buffer", account_count=3)
    assert by_id(items)["accounts"].remaining == 0
    assert exhausted(items) is None


def test_a_published_figure_can_never_stop_a_publish() -> None:
    """It is a scrape of a pricing page, and it carries no usage at all.

    A year-old marketing number must not be what refuses to send a post, so the
    rule is built to exclude it rather than trusted to.
    """
    items = allowances("bundle_social", account_count=1)
    assert by_id(items)["posts_per_month"].confidence == "published"
    assert exhausted(items) is None


def test_a_spent_request_budget_stops_everything() -> None:
    items = allowances(
        "buffer", account_count=1, rate_limit={"limit": 100, "remaining": 0})
    spent = exhausted(items)
    assert spent is not None and spent.id == "requests"
    assert "100 of 100 used" in spent_note(spent)


def test_room_to_spare_blocks_nothing() -> None:
    items = allowances(
        "bundle_social", account_count=1,
        daily={"posts": {"used": 19, "limit": 20, "remaining": 1}},
    )
    assert exhausted(items) is None


def test_every_plan_records_where_it_was_read_from() -> None:
    for provider, plan in FREE_PLAN.items():
        assert plan["source"].startswith("https://"), provider
        assert plan["plan"], provider
