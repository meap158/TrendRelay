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

    async def close(self):
        return None


class FakeBrowser:
    def __init__(self, cookies, closes_after: int | None = None) -> None:
        self.cookies = cookies
        #: How many polls before the operator closes the window, if they do.
        self.closes_after = closes_after
        self.polls = 0

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
