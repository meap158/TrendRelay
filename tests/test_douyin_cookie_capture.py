"""Capturing a Douyin session, and telling the two strengths apart.

Loading douyin.com sets `ttwid`, `odin_tt` and `passport_csrf_token`. That is a
usable session - it downloads a known link and reads the hot board - but it is
not an account, and Douyin refuses to search for it. Only `sessionid` says
somebody signed in.

The capture used to stop at the first set, which meant it announced "connected"
a second after the window opened, before anyone had touched it. Everything then
downstream believed a signed-in session had been captured.
"""

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from scripts import douyin_cookie_capture

ANONYMOUS = [
    {"domain": ".douyin.com", "name": "ttwid", "value": "tw"},
    {"domain": ".douyin.com", "name": "odin_tt", "value": "odin"},
    {"domain": ".douyin.com", "name": "passport_csrf_token", "value": "csrf"},
]
SIGNED_IN = [*ANONYMOUS, {"domain": ".douyin.com", "name": "sessionid", "value": "sid"}]


class FakePage:
    async def goto(self, *_args, **_kwargs):
        return None


class FakeContext:
    def __init__(self, browser: "FakeBrowser") -> None:
        self.browser = browser

    async def new_page(self):
        return FakePage()

    async def cookies(self):
        return self.browser.cookies

    async def add_cookies(self, cookies):
        self.browser.seeded_cookies = cookies

    async def close(self):
        self.browser.closed = True


class FakeBrowser:
    def __init__(self, cookies, closes_after: int | None = None) -> None:
        self.cookies = cookies
        #: How many polls before the operator closes the window, if they do.
        self.closes_after = closes_after
        self.polls = 0
        self.closed = False

    def is_connected(self) -> bool:
        self.polls += 1
        return self.closes_after is None or self.polls <= self.closes_after

    async def new_context(self):
        return FakeContext(self)

    async def close(self):
        return None


def playwright_serving(browser: FakeBrowser):
    class FakeChromium:
        async def launch(self, **_kwargs):
            return browser

    class FakeManager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=FakeChromium())

        async def __aexit__(self, *_args):
            return None

    return FakeManager


def install(monkeypatch, browser: FakeBrowser) -> None:
    api = ModuleType("playwright.async_api")
    api.async_playwright = playwright_serving(browser)
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)


def run(monkeypatch, tmp_path: Path, browser: FakeBrowser, timeout: int = 10):
    install(monkeypatch, browser)
    output = tmp_path / "cookies.json"
    status = tmp_path / "status.json"
    code = asyncio.run(douyin_cookie_capture.capture(output, status, timeout))
    saved = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {}
    reported = json.loads(status.read_text(encoding="utf-8"))
    return code, saved, reported


def test_signing_in_is_detected_without_terminal_input(monkeypatch, tmp_path: Path) -> None:
    code, saved, reported = run(monkeypatch, tmp_path, FakeBrowser(SIGNED_IN))

    assert code == 0
    assert saved["sessionid"] == "sid"
    assert reported["state"] == "connected"
    assert "search" in reported["message"]
    # The values themselves are the one thing that must never be reported.
    assert "sid" not in json.dumps(reported)


def test_a_browsing_session_is_saved_but_not_called_signed_in(
    monkeypatch, tmp_path: Path
) -> None:
    # The bug this exists to prevent: three cookies appear the instant the page
    # loads, and calling that "connected" told everything downstream an account
    # had been captured. It is worth keeping - it downloads - but it is not a
    # sign-in, and searching a topic with it fails.
    code, saved, reported = run(
        monkeypatch, tmp_path, FakeBrowser(ANONYMOUS, closes_after=1)
    )

    assert code == 0, "a download-capable session is a success, not a failure"
    assert saved["ttwid"] == "tw"
    assert "sessionid" not in saved
    assert reported["state"] == "connected"
    # The message must say what an anonymous session still does - read a
    # profile in the browser - and what it does not: topic search.
    assert "profile" in reported["message"]
    assert "search" in reported["message"]


def test_closing_the_window_early_keeps_what_was_captured(
    monkeypatch, tmp_path: Path
) -> None:
    # Waiting for a sign-in must not hold the usable cookies hostage: an
    # operator who never intends to log in still ends up able to download.
    code, saved, _reported = run(
        monkeypatch, tmp_path, FakeBrowser(ANONYMOUS, closes_after=1), timeout=600
    )
    assert code == 0
    assert saved["ttwid"] == "tw"


def test_a_window_that_yields_nothing_is_a_failure(monkeypatch, tmp_path: Path) -> None:
    code, saved, reported = run(
        monkeypatch, tmp_path, FakeBrowser([], closes_after=1), timeout=5
    )
    assert code == 2
    assert saved == {}
    assert reported["state"] == "failed"


def test_anonymous_token_updates_are_saved_before_the_window_closes(
    monkeypatch, tmp_path: Path
) -> None:
    browser = FakeBrowser(list(ANONYMOUS), closes_after=2)

    async def refresh_tokens(_seconds):
        browser.cookies = [
            *ANONYMOUS,
            {"domain": ".douyin.com", "name": "msToken", "value": "refreshed"},
            {"domain": "notdouyin.com", "name": "unrelated", "value": "ignore"},
        ]

    monkeypatch.setattr(douyin_cookie_capture.asyncio, "sleep", refresh_tokens)
    code, saved, reported = run(monkeypatch, tmp_path, browser)

    assert code == 0
    assert saved["msToken"] == "refreshed"
    assert "unrelated" not in saved
    assert "sessionid" not in saved
    assert reported["state"] == "connected"


def test_empty_required_cookies_are_not_download_ready() -> None:
    assert not douyin_cookie_capture.can_download({
        "ttwid": "t", "odin_tt": "", "passport_csrf_token": "p",
    })


def test_default_capture_finishes_without_login_or_manual_close(monkeypatch, tmp_path):
    browser = FakeBrowser(ANONYMOUS)
    clock = [0.0]

    async def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr(douyin_cookie_capture, "monotonic", lambda: clock[0])
    monkeypatch.setattr(douyin_cookie_capture.asyncio, "sleep", advance)
    code, saved, reported = run(monkeypatch, tmp_path, browser, timeout=600)
    assert code == 0
    assert clock[0] == 3
    assert browser.closed
    assert "sessionid" not in saved
    assert reported["state"] == "connected"


def test_explicit_login_keeps_waiting_then_saves_the_account(monkeypatch, tmp_path):
    browser = FakeBrowser(ANONYMOUS)
    clock = [0.0]

    async def advance(seconds):
        clock[0] += seconds
        if clock[0] >= 6:
            browser.cookies = SIGNED_IN

    install(monkeypatch, browser)
    monkeypatch.setattr(douyin_cookie_capture, "monotonic", lambda: clock[0])
    monkeypatch.setattr(douyin_cookie_capture.asyncio, "sleep", advance)
    output = tmp_path / "cookies.json"
    code = asyncio.run(douyin_cookie_capture.capture(
        output, tmp_path / "status.json", 600, require_login=True,
    ))
    assert code == 0
    assert clock[0] == 6
    assert json.loads(output.read_text())["sessionid"] == "sid"


def test_refresh_reuses_existing_saved_cookies(monkeypatch, tmp_path):
    output = tmp_path / "cookies.json"
    output.write_text(json.dumps({"ttwid": "existing"}))
    browser = FakeBrowser(SIGNED_IN)
    code, _, _ = run(monkeypatch, tmp_path, browser)
    assert code == 0
    assert browser.seeded_cookies == [
        {"name": "ttwid", "value": "existing", "url": "https://www.douyin.com/"},
    ]
