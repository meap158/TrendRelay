"""Giving an engine that said "too many requests" the time it asked for.

One campaign put 56,176 refused publishing jobs in the database, every one of
them `Too many requests from this client` from api.buffer.com, and the client
asking too many times was this one. Nothing waited: a refusal was charged to
the post, the delivery was retried three times within seconds, the execution
settled as failed, its slot came free, and the next tick froze the next
approved post into the same slot - sixty-five posts a slot, three deliveries
each.

What was missing is the thing `delivery_block` was documented to do all along:
recognise a quota refusal when it arrives. A throttle refuses a client rather
than a plan, so no published figure and no rate-limit header reports it, and
the only evidence there will ever be is the refusal itself.
"""

from __future__ import annotations

import pytest

from trendrelay_api.integrations import publishing


@pytest.fixture(autouse=True)
def _quiet_engines():
    """Nothing is waiting when each of these starts.

    The suite's own fixture restores this between tests; this one is about the
    state each test begins in, which for a throttle is "nobody has refused
    anything yet".
    """
    publishing._RATE_LIMITED.clear()
    yield
    publishing._RATE_LIMITED.clear()


def test_an_engine_that_has_not_refused_anything_is_not_waiting() -> None:
    assert publishing.rate_limit_pause("buffer") is None
    assert publishing.delivery_block("buffer") is None


def test_a_refusal_buys_the_engine_a_quiet_few_minutes() -> None:
    waited = publishing.note_rate_limit_refusal("buffer")

    assert waited == publishing.RATE_LIMIT_BACKOFF[0]
    note = publishing.rate_limit_pause("buffer")
    assert note is not None
    assert "Buffer" in note and "too many requests" in note
    # Asked before anything an engine publishes about itself, because a
    # throttle is not in any of it.
    assert publishing.delivery_block("buffer") == note


def test_the_wait_grows_while_the_engine_goes_on_refusing() -> None:
    """The second refusal says the first wait was not long enough."""
    waits = [publishing.note_rate_limit_refusal("buffer") for _ in range(6)]

    assert waits[:5] == list(publishing.RATE_LIMIT_BACKOFF)
    assert waits[5] == publishing.RATE_LIMIT_BACKOFF[-1], "capped, not doubling forever"


def test_a_post_that_went_through_ends_the_wait() -> None:
    publishing.note_rate_limit_refusal("buffer")
    publishing.clear_rate_limit("buffer")

    assert publishing.rate_limit_pause("buffer") is None
    # And the next refusal starts from the shortest wait again.
    assert publishing.note_rate_limit_refusal("buffer") == publishing.RATE_LIMIT_BACKOFF[0]


def test_an_isolated_refusal_next_week_is_not_this_afternoon_s_fifth() -> None:
    """Written into the module's own memory rather than by moving the clock.

    The clock is the process's, and the whole suite reads it. What this is about
    is the age of the last refusal, so the age is what the test states.
    """
    import time

    long_ago = time.monotonic() - publishing.RATE_LIMIT_MEMORY - 1
    publishing._RATE_LIMITED["buffer"] = (long_ago, 4, long_ago)

    assert publishing.note_rate_limit_refusal("buffer") == publishing.RATE_LIMIT_BACKOFF[0]


def test_the_wait_ends_by_itself() -> None:
    """Nothing sweeps it: the next question is what notices it has passed."""
    import time

    passed = time.monotonic() - 1
    publishing._RATE_LIMITED["buffer"] = (passed, 1, passed)

    assert publishing.rate_limit_pause("buffer") is None
    assert "buffer" not in publishing._RATE_LIMITED, "forgotten once it has passed"


def test_each_login_waits_for_itself() -> None:
    """The limit is counted against an API key, so a second Buffer login has a
    budget of its own and must not be silenced by the first one's."""
    publishing.note_rate_limit_refusal("buffer")

    assert publishing.rate_limit_pause("buffer") is not None
    assert publishing.rate_limit_pause("zernio") is None


def test_an_engine_nobody_recognises_is_still_given_its_wait() -> None:
    """A destination naming a login that has been removed still gets refused by
    something, and the refusal is still worth honouring - it is the same key
    behind it."""
    publishing.note_rate_limit_refusal("engine-that-was-removed")

    assert publishing.rate_limit_pause("engine-that-was-removed") is not None
