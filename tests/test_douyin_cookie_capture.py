import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from scripts import douyin_cookie_capture


class FakePage:
    async def goto(self, *_args, **_kwargs):
        return None


class FakeContext:
    async def new_page(self):
        return FakePage()

    async def cookies(self):
        return [
            {"domain": ".douyin.com", "name": "ttwid", "value": "tw"},
            {"domain": ".douyin.com", "name": "odin_tt", "value": "odin"},
            {
                "domain": ".douyin.com",
                "name": "passport_csrf_token",
                "value": "csrf",
            },
        ]

    async def close(self):
        return None


class FakeBrowser:
    async def new_context(self):
        return FakeContext()

    async def close(self):
        return None


class FakeChromium:
    async def launch(self, **_kwargs):
        return FakeBrowser()


class FakePlaywrightManager:
    async def __aenter__(self):
        return SimpleNamespace(chromium=FakeChromium())

    async def __aexit__(self, *_args):
        return None


def test_capture_detects_login_without_terminal_input(
    monkeypatch, tmp_path: Path
) -> None:
    package = ModuleType("playwright")
    api = ModuleType("playwright.async_api")
    api.async_playwright = lambda: FakePlaywrightManager()
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    output = tmp_path / "cookies.json"
    status = tmp_path / "status.json"

    result = asyncio.run(douyin_cookie_capture.capture(output, status, 10))

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ttwid"] == "tw"
    public_status = json.loads(status.read_text(encoding="utf-8"))
    assert public_status["state"] == "connected"
    assert "tw" not in json.dumps(public_status)
