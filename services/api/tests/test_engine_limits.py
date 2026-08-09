"""What each engine allows, and how confident the figure is."""

from __future__ import annotations

from trendrelay_api.integrations.engine_limits import (
    FREE_PLAN,
    Allowance,
    allowances,
    parse_rate_limit,
    payload,
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
    # Once the engine has told us, quoting the pricing page beside it would be
    # two numbers for one thing.
    measured = by_id(allowances(
        "buffer", account_count=1, rate_limit={"limit": 100, "remaining": 50}))
    assert "requests_per_30_days" not in measured
    assert "requests" in measured

    without = by_id(allowances("buffer", account_count=1))
    assert "requests_per_30_days" in without


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


def test_every_plan_records_where_it_was_read_from() -> None:
    for provider, plan in FREE_PLAN.items():
        assert plan["source"].startswith("https://"), provider
        assert plan["plan"], provider
