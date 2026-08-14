"""Keeping a borrowed Shopee session usable, and honest about when it is not."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trendrelay_api.integrations import shopee_session as shopee

LIVE = {"SPC_EC": "abc123", "SPC_U": "42", "SPC_ST": "xyz"}


@pytest.fixture(autouse=True)
def stored_here(monkeypatch, tmp_path):
    """Never read or write the operator's real session during a test."""
    monkeypatch.setattr(shopee, "COOKIE_FILE", tmp_path / "cookies.json")
    monkeypatch.delenv(shopee.COOKIE_ENV, raising=False)


# --- what is stored -----------------------------------------------------------


def test_a_saved_session_comes_back() -> None:
    shopee.save_cookies(LIVE)

    cookies, source = shopee.load_cookies()

    assert cookies["SPC_EC"] == "abc123"
    assert source.endswith("cookies.json")


def test_no_session_is_an_empty_answer_rather_than_an_error() -> None:
    assert shopee.load_cookies() == ({}, "none")


def test_the_environment_wins_so_a_one_off_run_needs_no_file(monkeypatch) -> None:
    shopee.save_cookies({"SPC_EC": "from-file", "SPC_U": "1"})
    monkeypatch.setenv(shopee.COOKIE_ENV, "SPC_EC=from-env; SPC_U=2")

    cookies, source = shopee.load_cookies()

    assert cookies["SPC_EC"] == "from-env"
    assert source == shopee.COOKIE_ENV


def test_saving_replaces_rather_than_merges() -> None:
    """One session is one set of cookies.

    Keeping a stale key beside a fresh one is how a request goes out carrying
    two identities.
    """
    shopee.save_cookies({"SPC_EC": "old", "SPC_U": "1", "LEFTOVER": "x"})
    shopee.save_cookies({"SPC_EC": "new", "SPC_U": "1"})

    cookies, _ = shopee.load_cookies()

    assert "LEFTOVER" not in cookies
    assert cookies["SPC_EC"] == "new"


# --- keeping it alive ---------------------------------------------------------


def test_what_shopee_hands_back_is_kept() -> None:
    """The part that actually extends a session's life.

    Shopee rotates these as it goes; replaying the first request's cookies
    forever is how a session that should last weeks stops working in days.
    """
    refreshed = shopee.merge_refreshed(
        LIVE, ["SPC_EC=rotated; Path=/; HttpOnly", "SPC_NEW=hello; Max-Age=3600"]
    )

    assert refreshed["SPC_EC"] == "rotated"
    assert refreshed["SPC_NEW"] == "hello"
    assert refreshed["SPC_U"] == "42", "untouched cookies survive"


def test_a_cookie_shopee_deletes_is_actually_dropped() -> None:
    # `SPC_EC=;` is Shopee saying the session is over. Keeping the old value
    # would hide that until something else failed.
    refreshed = shopee.merge_refreshed(LIVE, ["SPC_EC=; Expires=Thu, 01 Jan 1970"])

    assert "SPC_EC" not in refreshed


def test_a_header_that_makes_no_sense_changes_nothing() -> None:
    assert shopee.merge_refreshed(LIVE, ["", "   ", "; Path=/"]) == LIVE


# --- telling failures apart ---------------------------------------------------


@pytest.mark.parametrize("message", [
    "HTTP 403 Forbidden",
    "Cần đăng nhập",
    "Please login to continue",
    "session expired",
])
def test_a_failure_about_who_we_are_is_recognised(message: str) -> None:
    assert shopee.looks_like_auth_failure(message) is True


@pytest.mark.parametrize("message", [
    "timed out after 15 seconds",
    "Temporary failure in name resolution",
    "Shopee returned malformed JSON",
])
def test_a_failure_about_something_else_is_not_called_an_expiry(message: str) -> None:
    """Reporting every failure as "sign in again" teaches people to ignore it.

    Then the one time it is true, nobody acts on it.
    """
    assert shopee.looks_like_auth_failure(message) is False


# --- what to tell somebody ----------------------------------------------------


def test_no_session_asks_for_one_and_says_what_is_stored() -> None:
    state = shopee.health()

    assert state.ready is False
    assert state.missing == ["SPC_EC", "SPC_U"]
    assert "only on this machine" in state.detail


def test_a_session_missing_only_its_account_is_still_not_ready() -> None:
    shopee.save_cookies({"SPC_EC": "abc"})

    assert shopee.health().missing == ["SPC_U"]


def test_a_good_session_is_simply_connected() -> None:
    shopee.save_cookies(LIVE, expires_at=datetime.now(UTC) + timedelta(days=30))

    state = shopee.health()

    assert state.ready is True
    assert state.tired is False
    assert state.detail == "Shopee connected."


def test_a_session_near_its_end_is_flagged_while_it_still_works() -> None:
    """Said between batches rather than discovered during one."""
    shopee.save_cookies(LIVE, expires_at=datetime.now(UTC) + timedelta(hours=6))

    state = shopee.health()

    assert state.ready is True, "it has not expired yet"
    assert state.tired is True
    assert "expires soon" in state.detail


def test_an_expired_session_is_not_offered_as_usable() -> None:
    shopee.save_cookies(LIVE, expires_at=datetime.now(UTC) - timedelta(minutes=1))

    state = shopee.health()

    assert state.ready is False
    assert "expired" in state.detail


def test_a_session_with_no_known_expiry_is_used_rather_than_doubted() -> None:
    # Shopee does not always say. Refusing to use it would make the common case
    # the broken one.
    shopee.save_cookies(LIVE)

    state = shopee.health()

    assert state.ready is True
    assert state.expires_at is None
    assert state.tired is False


# --- not leaking it -----------------------------------------------------------


def test_a_session_never_appears_in_something_that_gets_logged() -> None:
    """A cookie value is the session. An error quoting one hands it over."""
    message = shopee.redact(
        "refused with Cookie: SPC_EC=supersecretvalue; SPC_U=42; other=fine"
    )

    assert "supersecretvalue" not in message
    assert "SPC_EC=…" in message
    assert "other=fine" in message, "only the session parts are hidden"


def test_the_header_is_built_in_a_stable_order() -> None:
    # So two runs produce the same string, which is what makes a difference
    # between them readable.
    assert shopee.cookie_header(LIVE) == "SPC_EC=abc123; SPC_ST=xyz; SPC_U=42"


def test_an_empty_cookie_is_not_sent_as_an_empty_pair() -> None:
    assert shopee.cookie_header({"SPC_EC": "abc", "SPC_U": ""}) == "SPC_EC=abc"


def test_the_file_never_holds_anything_but_cookies(tmp_path, monkeypatch) -> None:
    """No password is taken anywhere, so none can be written here."""
    monkeypatch.setattr(shopee, "COOKIE_FILE", tmp_path / "cookies.json")
    shopee.save_cookies(LIVE)

    stored = json.loads((tmp_path / "cookies.json").read_text(encoding="utf-8"))

    assert set(stored) == {"cookies", "saved_at", "expires_at"}


# --- the probe ----------------------------------------------------------------


def stage_ids(result) -> list[str]:
    return [stage["id"] for stage in result["stages"]]


def test_with_nothing_stored_the_probe_stops_at_the_first_step() -> None:
    """Reporting "could not parse the product" under "not signed in" is noise.

    The second is caused by the first, and a list of consequences buries the
    cause somebody has to act on.
    """
    result = shopee.probe("https://shopee.vn/product/1/2")

    assert result["ok"] is False
    assert stage_ids(result) == ["session"]
    assert result["reconnect"] is True


def test_a_half_stored_session_says_which_cookie_is_missing() -> None:
    shopee.save_cookies({"SPC_EC": "abc"})

    result = shopee.probe()

    assert stage_ids(result) == ["session", "complete"]
    assert "SPC_U" in result["stages"][-1]["detail"]
    assert result["reconnect"] is True


def test_an_expired_session_stops_before_anything_is_fetched() -> None:
    shopee.save_cookies(LIVE, expires_at=datetime.now(UTC) - timedelta(minutes=1))

    def explode(_url):
        raise AssertionError("nothing should have been fetched")

    result = shopee.probe("https://shopee.vn/product/1/2", fetcher=explode)

    assert stage_ids(result) == ["session", "complete", "fresh"]
    assert result["reconnect"] is True


def test_a_good_session_with_nothing_to_try_passes_without_inventing_a_fetch() -> None:
    # Picking a product to fetch would test somebody else's listing rather than
    # this session.
    shopee.save_cookies(LIVE)

    result = shopee.probe()

    assert result["ok"] is True
    assert stage_ids(result) == ["session", "complete", "fresh"]


def test_a_full_pass_reports_every_step_and_what_it_read() -> None:
    shopee.save_cookies(LIVE)

    result = shopee.probe(
        "https://shopee.vn/product/1/2",
        fetcher=lambda _url: {"name": "Giấy ăn rút", "image_url": "https://cf.shopee.vn/x.jpg"},
    )

    assert result["ok"] is True
    assert stage_ids(result) == ["session", "complete", "fresh", "reach", "parse"]
    assert "name" in result["stages"][-1]["detail"]
    assert result["details"]["name"] == "Giấy ăn rút"


def test_being_refused_as_a_stranger_asks_for_a_reconnection() -> None:
    shopee.save_cookies(LIVE)

    def refused(_url):
        raise RuntimeError("HTTP 403: Cần đăng nhập")

    result = shopee.probe("https://shopee.vn/product/1/2", fetcher=refused)

    assert result["ok"] is False
    assert result["reconnect"] is True
    assert stage_ids(result)[-1] == "reach"


def test_a_slow_link_is_not_reported_as_a_dead_session() -> None:
    """Otherwise a timeout sends somebody to re-authenticate for nothing."""
    shopee.save_cookies(LIVE)

    def slow(_url):
        raise TimeoutError("timed out after 20 seconds")

    result = shopee.probe("https://shopee.vn/product/1/2", fetcher=slow)

    assert result["ok"] is False
    assert result["reconnect"] is False, "a timeout is not an expiry"


def test_a_page_that_loads_but_says_nothing_is_a_parsing_problem() -> None:
    # Distinct from being signed out: the session worked, the markup changed.
    shopee.save_cookies(LIVE)

    result = shopee.probe("https://shopee.vn/product/1/2", fetcher=lambda _url: {})

    assert result["ok"] is False
    assert result["reconnect"] is False
    assert "changed its markup" in result["stages"][-1]["detail"]


def test_a_failure_never_quotes_the_session_back(caplog) -> None:
    """An error carrying a cookie hands the session to whoever reads it."""
    shopee.save_cookies(LIVE)

    def leaky(_url):
        raise RuntimeError("refused with Cookie: SPC_EC=supersecret; SPC_U=42")

    result = shopee.probe("https://shopee.vn/product/1/2", fetcher=leaky)

    assert "supersecret" not in result["stages"][-1]["detail"]
    assert "SPC_EC=…" in result["stages"][-1]["detail"]


# --- reading a product through the browser ------------------------------------
#
# The bridge itself is a real browser and cannot run in a test, so it is stubbed
# at the process boundary. What is worth testing is everything around it: what
# it is handed, what is done with what it returns, and which failures mean
# "sign in again" rather than "try later".

PRODUCT_URL = "https://shopee.vn/product/1834061111/57860887539"


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    """Stand in for the browser, and record how it was called."""
    from trendrelay_api.integrations import tiktok_creative

    monkeypatch.setattr(tiktok_creative, "runtime_python", lambda: "python.exe")
    monkeypatch.setattr(tiktok_creative, "scoped_environment", lambda: {"NO_COLOR": "1"})
    monkeypatch.setattr(shopee, "BRIDGE_PATH", tmp_path / "bridge.py")
    (tmp_path / "bridge.py").write_text("", encoding="utf-8")

    calls: list[dict] = []
    answer = {"returncode": 0, "stdout": "{}", "stderr": ""}

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if isinstance(answer.get("raises"), BaseException):
            raise answer["raises"]
        return SimpleNamespace(
            returncode=answer["returncode"],
            stdout=answer["stdout"],
            stderr=answer["stderr"],
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return SimpleNamespace(calls=calls, answer=answer)


def replies(bridge, payload: dict) -> None:
    bridge.answer["stdout"] = json.dumps(payload)


def test_the_stored_session_is_handed_in_rather_than_read_by_the_browser(bridge) -> None:
    """One place decides which session is used."""
    shopee.save_cookies(LIVE)
    replies(bridge, {"name": "Giấy ăn rút Topgia"})

    shopee.fetch_product(PRODUCT_URL)

    handed = json.loads(bridge.calls[0]["input"])
    assert handed["url"] == PRODUCT_URL
    assert handed["cookies"]["SPC_EC"] == "abc123"


def test_the_browser_inherits_a_scoped_environment_not_this_one(bridge) -> None:
    # It carries a live session into a browser; nothing else this process holds
    # has any business going with it.
    shopee.save_cookies(LIVE)
    replies(bridge, {"name": "Anything"})

    shopee.fetch_product(PRODUCT_URL)

    assert bridge.calls[0]["env"] == {"NO_COLOR": "1"}


def test_what_the_page_said_comes_back(bridge) -> None:
    shopee.save_cookies(LIVE)
    replies(bridge, {
        "name": "Giấy ăn rút Topgia",
        "image_url": "https://down-vn.img.susercontent.com/file/abc",
        "price": "₫95.000",
    })

    found = shopee.fetch_product(PRODUCT_URL)

    assert found["name"] == "Giấy ăn rút Topgia"
    assert found["image_url"].endswith("/abc")


def test_rotated_cookies_are_kept_so_the_session_lives_its_full_term(bridge) -> None:
    """The whole point of returning them.

    Replaying the cookies a session started with is how one that should last
    weeks stops working in days.
    """
    shopee.save_cookies(LIVE)
    replies(bridge, {
        "name": "Anything",
        "refreshed_cookies": {"SPC_EC": "rotated", "SPC_U": "42"},
    })

    shopee.fetch_product(PRODUCT_URL)

    assert shopee.load_cookies()[0]["SPC_EC"] == "rotated"


def test_nothing_rotated_leaves_the_session_alone(bridge) -> None:
    shopee.save_cookies(LIVE)
    replies(bridge, {"name": "Anything"})

    shopee.fetch_product(PRODUCT_URL)

    assert shopee.load_cookies()[0]["SPC_EC"] == "abc123"


def test_a_rotated_session_never_comes_back_to_the_caller(bridge) -> None:
    """Cookies are stored, never returned. What comes back gets logged."""
    shopee.save_cookies(LIVE)
    replies(bridge, {"name": "Anything", "refreshed_cookies": {"SPC_EC": "rotated"}})

    found = shopee.fetch_product(PRODUCT_URL)

    assert "refreshed_cookies" not in found


# --- when it does not work ----------------------------------------------------


def test_a_login_wall_reads_as_authentication(bridge) -> None:
    shopee.save_cookies(LIVE)
    replies(bridge, {"login_wall": True, "title": "Shopee"})

    with pytest.raises(RuntimeError) as raised:
        shopee.fetch_product(PRODUCT_URL)

    assert shopee.looks_like_auth_failure(str(raised.value))


def test_a_timeout_is_not_an_expiry(bridge) -> None:
    """Sending somebody to re-authenticate over a slow link helps nobody."""
    shopee.save_cookies(LIVE)
    bridge.answer["raises"] = subprocess.TimeoutExpired("bridge", 90)

    with pytest.raises(RuntimeError) as raised:
        shopee.fetch_product(PRODUCT_URL)

    assert not shopee.looks_like_auth_failure(str(raised.value))


def test_no_session_is_refused_before_a_browser_is_started(bridge) -> None:
    with pytest.raises(RuntimeError) as raised:
        shopee.fetch_product(PRODUCT_URL)

    assert bridge.calls == [], "nothing should have been launched"
    assert shopee.looks_like_auth_failure(str(raised.value))


def test_a_failing_bridge_never_reports_a_cookie(bridge) -> None:
    """A failing subprocess is exactly what ends up quoted in a log."""
    shopee.save_cookies(LIVE)
    bridge.answer["returncode"] = 1
    bridge.answer["stderr"] = "refused with SPC_EC=abc123 present"

    with pytest.raises(RuntimeError) as raised:
        shopee.fetch_product(PRODUCT_URL)

    assert "abc123" not in str(raised.value)


def test_an_unreadable_answer_says_so_rather_than_raising_a_json_error(bridge) -> None:
    shopee.save_cookies(LIVE)
    bridge.answer["stdout"] = "<html>maintenance</html>"

    with pytest.raises(RuntimeError, match="unreadable"):
        shopee.fetch_product(PRODUCT_URL)


def test_without_a_browser_runtime_it_says_where_one_comes_from(bridge, monkeypatch) -> None:
    from trendrelay_api.integrations import tiktok_creative

    shopee.save_cookies(LIVE)
    monkeypatch.setattr(tiktok_creative, "runtime_python", lambda: None)

    with pytest.raises(RuntimeError, match="browser runtime"):
        shopee.fetch_product(PRODUCT_URL)
