"""Capturing a Shopee session by signing in, rather than pasting one.

The browser cannot run in a test, so what is tested is the reasoning around it:
which cookies count as signed in, which expiry is the one that matters, and
what a half-finished capture must never be mistaken for.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trendrelay_api.integrations import shopee_session as shopee


def load_capture():
    spec = importlib.util.spec_from_file_location("shopee_capture", shopee.CAPTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = load_capture()


@pytest.fixture(autouse=True)
def stored_here(monkeypatch, tmp_path):
    monkeypatch.setattr(shopee, "COOKIE_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(shopee, "CAPTURE_STATUS_FILE", tmp_path / "connect-status.json")
    monkeypatch.setattr(shopee, "CAPTURE_OUTPUT_FILE", tmp_path / "connect-captured.json")
    monkeypatch.delenv(shopee.COOKIE_ENV, raising=False)
    return tmp_path


# --- what counts as signed in -------------------------------------------------


def test_visiting_shopee_is_not_signing_in() -> None:
    """A browser that has merely loaded the site has neither of these."""
    assert capture.signed_in({"SPC_F": "anon", "csrftoken": "x"}) is False


def test_half_a_session_is_not_a_session() -> None:
    assert capture.signed_in({"SPC_EC": "abc"}) is False
    assert capture.signed_in({"SPC_U": "42"}) is False


def test_both_markers_mean_signed_in() -> None:
    assert capture.signed_in({"SPC_EC": "abc", "SPC_U": "42"}) is True


# --- when it runs out ---------------------------------------------------------


def test_the_soonest_expiry_is_the_one_that_matters() -> None:
    """The session is only as good as the first of its cookies to lapse."""
    soon = datetime(2026, 9, 1, tzinfo=UTC).timestamp()
    later = datetime(2026, 12, 1, tzinfo=UTC).timestamp()

    found = capture.earliest_expiry([
        {"name": "SPC_EC", "expires": later},
        {"name": "SPC_U", "expires": soon},
    ])

    assert found.startswith("2026-09-01")


def test_cookies_that_are_not_the_session_do_not_set_the_expiry() -> None:
    """A banner-dismissal cookie lapsing tomorrow says nothing about the login."""
    tomorrow = datetime(2026, 8, 15, tzinfo=UTC)
    next_year = datetime(2027, 8, 14, tzinfo=UTC)

    found = capture.earliest_expiry([
        {"name": "SPC_EC", "expires": next_year.timestamp()},
        {"name": "SPC_U", "expires": next_year.timestamp()},
        {"name": "some_banner_dismissal", "expires": tomorrow.timestamp()},
    ])

    assert found.startswith("2027-08-14")


def test_a_browser_session_cookie_dates_nothing() -> None:
    # -1 means "until the browser closes", which says nothing about how long
    # the captured copy will be accepted.
    assert capture.earliest_expiry([{"name": "SPC_EC", "expires": -1}]) is None


def test_no_expiry_at_all_is_not_an_error() -> None:
    assert capture.earliest_expiry([]) is None


# --- adopting what was captured -----------------------------------------------


def write_capture(folder: Path, cookies: dict, expires_at: str | None = None) -> None:
    (folder / "connect-captured.json").write_text(
        json.dumps({"cookies": cookies, "expires_at": expires_at}), encoding="utf-8"
    )


def test_a_captured_session_becomes_the_stored_one(stored_here) -> None:
    write_capture(stored_here, {"SPC_EC": "abc", "SPC_U": "42"})

    assert shopee._adopt_captured() is True
    assert shopee.load_cookies()[0]["SPC_EC"] == "abc"


def test_the_expiry_shopee_gave_is_kept(stored_here) -> None:
    """The whole advantage over pasting: a pasted header dates nothing."""
    write_capture(stored_here, {"SPC_EC": "abc", "SPC_U": "42"}, "2026-12-01T00:00:00Z")

    shopee._adopt_captured()

    assert shopee.health().expires_at.year == 2026
    assert shopee.health().expires_at.month == 12


def test_the_captured_copy_is_removed_once_adopted(stored_here) -> None:
    # A second copy of a live session, and one is already more than anybody
    # wants lying about.
    write_capture(stored_here, {"SPC_EC": "abc", "SPC_U": "42"})

    shopee._adopt_captured()

    assert not (stored_here / "connect-captured.json").exists()


def test_half_a_capture_is_never_adopted(stored_here) -> None:
    write_capture(stored_here, {"SPC_EC": "abc"})

    assert shopee._adopt_captured() is False
    assert shopee.load_cookies()[0] == {}


def test_a_nonsense_capture_is_not_a_session(stored_here) -> None:
    (stored_here / "connect-captured.json").write_text("<not json>", encoding="utf-8")

    assert shopee._adopt_captured() is False


# --- what the window is doing -------------------------------------------------


def test_nothing_attempted_reads_as_disconnected() -> None:
    assert shopee.connection_status()["state"] == "disconnected"


def test_a_connected_session_outranks_a_stale_attempt(stored_here) -> None:
    """The point is whether Shopee is reachable now, not how it was arrived at."""
    shopee._write_capture_status("failed", "an old attempt that went wrong")
    shopee.save_cookies({"SPC_EC": "abc", "SPC_U": "42"})

    assert shopee.connection_status()["state"] == "connected"


def test_a_capture_waiting_to_be_adopted_is_picked_up_by_asking(stored_here) -> None:
    # The browser closing and the connection existing are the same moment as
    # far as anybody watching is concerned.
    write_capture(stored_here, {"SPC_EC": "abc", "SPC_U": "42"})

    assert shopee.connection_status()["state"] == "connected"
    assert shopee.health().ready is True


def stamp_status(folder: Path, state: str, minutes_ago: float) -> None:
    (folder / "connect-status.json").write_text(
        json.dumps({
            "state": state,
            "message": "…",
            "updated_at": (
                datetime.now(UTC) - timedelta(minutes=minutes_ago)
            ).isoformat().replace("+00:00", "Z"),
        }),
        encoding="utf-8",
    )


def test_a_waiting_window_nobody_is_running_expires_rather_than_blocking(
    stored_here, monkeypatch
) -> None:
    """The deadlock a hard kill used to leave behind.

    An API restart drops the process handle, and a capture killed too hard
    never writes its own ending - so the file said "waiting" forever, and
    `start_connection` read that as a window already open and refused to ever
    open another. Recoverable only by deleting the file by hand.
    """
    monkeypatch.setattr(shopee, "_CAPTURE_PROCESS", None)
    stamp_status(stored_here, "waiting_for_login", minutes_ago=15)

    assert shopee.connection_status()["state"] == "failed"


def test_a_window_still_inside_its_own_timeout_is_left_waiting(
    stored_here, monkeypatch
) -> None:
    # The script stamps the file once when the window opens, so a quiet stamp
    # is not a dead one until the window's own timeout has passed it by.
    monkeypatch.setattr(shopee, "_CAPTURE_PROCESS", None)
    stamp_status(stored_here, "waiting_for_login", minutes_ago=2)

    assert shopee.connection_status()["state"] == "waiting_for_login"
