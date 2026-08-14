"""The bridge's own refusals, run as the script it is.

Everywhere else the subprocess is stubbed, which is right - a test cannot open
a browser. That leaves the guards at the top of the script untested by anything
but reading them, and they are the ones that matter: this process is handed a
live Shopee session, so where it is pointed is not something to take on trust
from whatever assembled the request.

Run through the ordinary interpreter rather than the browser runtime. The
guards all sit above the Playwright import, so they answer without it.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from trendrelay_api.integrations.shopee_session import BRIDGE_PATH

SESSION = {"SPC_EC": "not-a-real-cookie", "SPC_U": "42"}


def run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BRIDGE_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def refused(payload: dict, because: str) -> None:
    done = run(payload)
    assert done.returncode != 0, f"expected a refusal, got: {done.stdout[:200]}"
    assert because in done.stderr, done.stderr


def test_the_script_is_where_the_session_expects_it() -> None:
    assert BRIDGE_PATH.is_file(), f"no bridge at {BRIDGE_PATH}"


# --- what it will not open ----------------------------------------------------


def test_a_request_with_no_url_is_refused() -> None:
    refused({"cookies": SESSION}, "non-https")


def test_plain_http_is_refused() -> None:
    """A session over http would be a session sent in clear."""
    refused({"url": "http://shopee.vn/product/1/2", "cookies": SESSION}, "non-https")


def test_somewhere_that_is_not_shopee_is_refused() -> None:
    refused({"url": "https://evil.example/product/1/2", "cookies": SESSION}, "not Shopee")


def test_a_host_that_merely_starts_with_shopee_is_refused() -> None:
    """`shopee.vn.evil.example` is not Shopee, and reads like it at a glance."""
    refused({"url": "https://shopee.vn.evil.example/p", "cookies": SESSION}, "not Shopee")


def test_a_host_that_merely_contains_shopee_is_refused() -> None:
    refused({"url": "https://notshopee.example/p", "cookies": SESSION}, "not Shopee")


@pytest.mark.parametrize(
    "host",
    ["shopee.vn", "shopee.sg", "shopee.com.my", "shopee.co.id", "www.shopee.vn"],
)
def test_shopee_in_its_other_countries_is_allowed_through(host: str) -> None:
    """Refused later for want of a browser, never for the URL."""
    done = run({"url": f"https://{host}/product/1/2", "cookies": SESSION})

    assert "not Shopee" not in done.stderr
    assert "non-https" not in done.stderr


# --- what it will not do without ----------------------------------------------


def test_no_session_means_no_browser_is_opened() -> None:
    # Cheap to check first: launching a browser to discover there is nothing to
    # authenticate with is a slow way to fail.
    refused({"url": "https://shopee.vn/product/1/2"}, "connected Shopee session")


def test_an_empty_session_is_no_session() -> None:
    refused({"url": "https://shopee.vn/product/1/2", "cookies": {}},
            "connected Shopee session")


def test_nonsense_on_stdin_is_refused_rather_than_crashing() -> None:
    done = subprocess.run(
        [sys.executable, str(BRIDGE_PATH)],
        input="<html>not json</html>", capture_output=True, text=True, timeout=60,
    )

    assert done.returncode != 0
    assert "invalid request" in done.stderr
