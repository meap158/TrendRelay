"""Getting rid of an engine key the engine refuses.

The counterpart the code already pointed at: removing an engine's first
connection is refused with "clear its key instead", and until now nothing did.
Replacing a key only helps somebody who has another to type - a revoked one,
or an engine no longer used, reported an authorization failure on every probe
with no way to quiet it.
"""

from __future__ import annotations

import os

import pytest

from trendrelay_api.integrations import publishing


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """A real `.env`, restored by hand for the reason the neighbouring suite is."""
    from trendrelay_api import env_store, publishing_connections

    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_store, "ENV_PATH", path)

    prefixes = ("BUFFER_", "ZERNIO_", "BUNDLE_SOCIAL_", "WOOPSOCIAL_",
                publishing_connections.REGISTRY_KEY)
    before = {key: value for key, value in os.environ.items() if key.startswith(prefixes)}
    for key in before:
        del os.environ[key]
    publishing._IDENTITIES.clear()

    yield path

    for key in [k for k in os.environ if k.startswith(prefixes)]:
        del os.environ[key]
    os.environ.update(before)
    publishing._IDENTITIES.clear()


def test_clearing_a_key_returns_the_engine_to_unconfigured() -> None:
    """The way out of an authorization error that replacing a key does not give."""
    publishing.save_provider_credentials("buffer", {"api_key": "revoked"})
    assert publishing.provider_status("buffer", probe=False)["configured"]

    publishing.clear_provider_credentials("buffer")

    assert not publishing.provider_status("buffer", probe=False)["configured"]


def test_clearing_deletes_the_line_rather_than_blanking_it(env) -> None:
    # A key that no longer exists should not still have a row in a file somebody
    # reads by hand, and an empty value is a different state from an absent one.
    publishing.save_provider_credentials("buffer", {"api_key": "revoked"})
    assert "BUFFER_API_KEY" in env.read_text(encoding="utf-8")

    publishing.clear_provider_credentials("buffer")

    assert "BUFFER_API_KEY" not in env.read_text(encoding="utf-8")


def test_clearing_one_login_leaves_the_other_alone() -> None:
    """The whole point of connections: two keys, cleared one at a time."""
    from trendrelay_api import publishing_connections

    second = publishing_connections.add(publishing.PROVIDERS, "buffer", "Client B")
    publishing.save_provider_credentials("buffer", {"api_key": "first"})
    publishing.save_provider_credentials(second.id, {"api_key": "second"})

    publishing.clear_provider_credentials(second.id)

    assert publishing.provider_status("buffer", probe=False)["configured"]
    assert not publishing.provider_status(second.id, probe=False)["configured"]
