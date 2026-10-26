"""The operator can trust one more network, and nothing changes until they do.

Remote use over a private overlay network (Tailscale) works by treating the
tailnet like the LAN. That has to be an explicit choice: with the setting empty
the trusted addresses, and the browser origins admitted, are exactly what they
were before the setting existed.
"""

from __future__ import annotations

import re

import pytest

from trendrelay_api.auth import (
    client_is_local_operator,
    extra_trusted_networks,
    operator_origin_regex,
)
from trendrelay_api.config import Settings, get_settings

TAILNET_HOST = "100.101.102.103"
BASE = r"^(?:http://(?i:localhost):(3000|3001))$"


@pytest.fixture(autouse=True)
def fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_nothing_is_added_by_default() -> None:
    assert not client_is_local_operator(TAILNET_HOST)
    assert client_is_local_operator("192.168.1.20")
    assert client_is_local_operator("127.0.0.1")
    assert not client_is_local_operator("203.0.113.9")


def test_an_added_network_is_trusted_and_the_rest_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_CLIENT_NETWORKS", "100.64.0.0/10")
    assert client_is_local_operator(TAILNET_HOST)
    assert not client_is_local_operator("100.128.0.1")
    assert not client_is_local_operator("203.0.113.9")
    assert client_is_local_operator("192.168.1.20")


def test_a_typo_trusts_nothing_and_does_not_raise() -> None:
    assert extra_trusted_networks("not-a-network, ,100.64.0.0/10")[0].prefixlen == 10
    assert len(extra_trusted_networks("not-a-network")) == 0


def test_origins_are_unchanged_until_a_network_is_added() -> None:
    assert operator_origin_regex(Settings(trusted_client_networks=""), BASE) == BASE
    assert operator_origin_regex(Settings(trusted_client_networks=""), None) is None


def test_tailnet_origins_are_admitted_only_when_a_network_is_added() -> None:
    widened = operator_origin_regex(Settings(trusted_client_networks="100.64.0.0/10"), BASE)
    assert widened is not None
    for origin in (
        f"http://{TAILNET_HOST}:3001",
        "http://my-pc.tail1234.ts.net:3001",
        "http://localhost:3000",
    ):
        assert re.fullmatch(widened, origin), origin
    for origin in (
        "https://evil.example.com",
        "http://evil.example.com:3001",
        f"http://{TAILNET_HOST}:9999",
        "http://tail1234.ts.net.evil.com:3001",
    ):
        assert not re.fullmatch(widened, origin), origin
    assert not re.fullmatch(BASE, f"http://{TAILNET_HOST}:3001")


def test_production_gets_no_pattern_even_with_a_network() -> None:
    assert operator_origin_regex(Settings(trusted_client_networks="100.64.0.0/10"), None) is None
