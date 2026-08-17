"""Whose login an engine key belongs to, and getting rid of one that is refused.

Two connections to the same engine are two rows carrying the same engine name.
What tells them apart is the account each key reaches, which the engine itself
knows - so the probe that checks a key brings the answer back with it.

The clearing half is the counterpart the code already pointed at: removing an
engine's first connection is refused with "clear its key instead", and until now
there was nothing that did.
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


# --- naming the login ---------------------------------------------------------


def test_an_engine_that_names_nobody_reports_nothing() -> None:
    """A blank, not a placeholder.

    "Unknown account" under two identical cards is worse than no line at all: it
    occupies the space where the distinguishing fact belongs and says nothing.
    """
    assert publishing._identity(email=None, name=None, scope="account") == {}
    assert publishing._identity(email="  ", name="", scope="account") == {}


def test_an_email_is_preferred_and_a_name_is_kept_when_there_is_no_email() -> None:
    # Only Buffer publishes an email; Zernio gives the owner's name and no more.
    assert publishing._identity(email="a@b.com", name="A", scope="account") == {
        "email": "a@b.com", "name": "A", "scope": "account",
    }
    assert publishing._identity(name="Long", scope="account") == {
        "name": "Long", "scope": "account",
    }


def test_the_identity_is_read_once_per_key(monkeypatch) -> None:
    """The cost this cache exists to remove.

    The accounts list loads every connection at once, and Zernio has to be asked
    twice to produce a name. Without this the page would pay for that on every
    open, for an answer that cannot change while the key does not.
    """
    publishing.save_provider_credentials("buffer", {"api_key": "tok"})
    calls = []
    monkeypatch.setattr(
        publishing, "_authenticate",
        lambda provider: calls.append(provider.id) or {"email": "a@b.com", "scope": "account"},
    )

    first = publishing.cached_identity("buffer")
    second = publishing.cached_identity("buffer")

    assert first == second == {"email": "a@b.com", "scope": "account"}
    assert calls == ["buffer"], "the engine was asked more than once for the same key"


def test_a_replaced_key_is_asked_again(monkeypatch) -> None:
    """Keyed on the key, so a new one is a different question.

    A cache keyed on the connection id alone would answer for the old key after
    somebody pointed the connection at a different account - which is the one
    case where getting it wrong sends a post to the wrong place.
    """
    publishing.save_provider_credentials("buffer", {"api_key": "first"})
    monkeypatch.setattr(publishing, "_authenticate", lambda provider: {"email": "one@b.com"})
    assert publishing.cached_identity("buffer") == {"email": "one@b.com"}

    publishing.save_provider_credentials("buffer", {"api_key": "second"})
    monkeypatch.setattr(publishing, "_authenticate", lambda provider: {"email": "two@b.com"})

    assert publishing.cached_identity("buffer") == {"email": "two@b.com"}


def test_an_engine_that_will_not_answer_costs_nothing_but_the_name(monkeypatch) -> None:
    """Best-effort: not knowing who owns a key says nothing about the key."""
    publishing.save_provider_credentials("buffer", {"api_key": "tok"})

    def refuse(provider):
        raise RuntimeError("api.buffer.com: HTTP 500")

    monkeypatch.setattr(publishing, "_authenticate", refuse)

    assert publishing.cached_identity("buffer") == {}


def test_an_unconfigured_engine_is_never_asked(monkeypatch) -> None:
    """No key, no account, and no request to find that out."""
    asked = []
    monkeypatch.setattr(publishing, "_authenticate", lambda provider: asked.append(1) or {})

    assert publishing.cached_identity("buffer") == {}
    assert asked == []
