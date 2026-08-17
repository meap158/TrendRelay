"""Two logins to one engine, reaching two different sets of accounts.

The registry beside this proves connections can be written down. This proves
they are actually used: that discovery asks each login separately, that the
accounts come back tagged with the login that found them, and - the one that
matters most - that a call made for one login authenticates with that login's
key rather than the first one's.
"""

from __future__ import annotations

import pytest

from trendrelay_api import publishing_connections as connections
from trendrelay_api.integrations import publishing


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from trendrelay_api import env_store

    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_store, "ENV_PATH", path)
    # Saving a value also puts it in this process's environment, where it
    # outlives the temporary file it was written to. Clearing only the keys
    # named here would miss the suffixed ones a connection invents, and a key
    # left behind is read by the next test as though it had been configured -
    # which is the same way a stale key would fool a real installation.
    import os

    for key in list(os.environ):
        if key.startswith(("BUFFER_", "ZERNIO_", "BUNDLE_SOCIAL_", "WOOPSOCIAL_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(connections.REGISTRY_KEY, raising=False)
    return path


@pytest.fixture
def two_logins(monkeypatch):
    """A second Buffer login, each with a key and one account of its own."""
    from trendrelay_api import env_store

    second = connections.add(publishing.PROVIDERS, "buffer", "Client B")
    env_store.write_env_values({
        "BUFFER_API_KEY": "key-for-the-first",
        second.key_for("BUFFER_API_KEY"): "key-for-the-second",
    })

    def accounts():
        # Which key is in force decides which accounts come back, exactly as a
        # real engine would answer.
        token = publishing._required_credential(publishing.PROVIDERS["buffer"], "api_key")
        handle = "first" if token == "key-for-the-first" else "second"
        return [{
            "id": f"{handle}-account", "platform": "instagram",
            "label": f"IG {handle}", "handle": handle,
        }]

    monkeypatch.setitem(publishing.ACCOUNT_READERS, "buffer", accounts)
    # The other engines have no keys here and would each report themselves
    # unconfigured, which is fine and is not what these tests are about.
    return second


# --- the credential a call authenticates with ---------------------------------


def test_a_call_uses_the_key_of_the_login_in_force(two_logins) -> None:
    """The crux. Everything else is presentation."""
    buffer = publishing.PROVIDERS["buffer"]

    with publishing.using_connection(connections.find(publishing.PROVIDERS, "buffer")):
        first = publishing._required_credential(buffer, "api_key")
    with publishing.using_connection(two_logins):
        second = publishing._required_credential(buffer, "api_key")

    assert first == "key-for-the-first"
    assert second == "key-for-the-second"


def test_no_login_in_force_means_the_first_one(two_logins) -> None:
    # Which is what every call site meant before connections existed.
    value = publishing._required_credential(publishing.PROVIDERS["buffer"], "api_key")

    assert value == "key-for-the-first"


def test_one_engine_does_not_read_another_s_key_through_a_suffix(two_logins) -> None:
    """A Bundle team id is read while publishing through Bundle, and a Buffer
    connection being in force must not send that lookup to a suffixed key."""
    with publishing.using_connection(two_logins):
        key = publishing._credential_key("bundle_social", "BUNDLE_SOCIAL_API_KEY")

    assert key == "BUNDLE_SOCIAL_API_KEY"


def test_a_missing_key_names_the_login_it_is_missing_for(monkeypatch) -> None:
    # "Buffer API key is not configured" is confusing when one Buffer login
    # works and another does not.
    second = connections.add(publishing.PROVIDERS, "buffer", "Client B")

    with publishing.using_connection(second), pytest.raises(RuntimeError, match="Client B"):
        publishing._required_credential(publishing.PROVIDERS["buffer"], "api_key")


# --- what discovery returns ---------------------------------------------------


def test_both_logins_accounts_come_back(two_logins) -> None:
    found = publishing.discover_all_integrations()

    handles = {account["handle"] for account in found["accounts"]}
    assert handles == {"first", "second"}


def test_each_account_says_which_login_found_it(two_logins) -> None:
    # This is the value a destination stores, and what routes the publish.
    found = publishing.discover_all_integrations()

    by_handle = {account["handle"]: account for account in found["accounts"]}
    assert by_handle["first"]["provider"] == "buffer"
    assert by_handle["second"]["provider"] == two_logins.id
    assert by_handle["second"]["engine"] == "buffer"


def test_the_second_login_is_named_on_screen(two_logins) -> None:
    found = publishing.discover_all_integrations()

    by_handle = {account["handle"]: account for account in found["accounts"]}
    assert by_handle["first"]["provider_label"] == "Buffer"
    assert "Client B" in by_handle["second"]["provider_label"]


def test_each_login_gets_its_own_card(two_logins) -> None:
    found = publishing.discover_all_integrations()

    buffer_cards = [card for card in found["engines"] if card["engine"] == "buffer"]
    assert len(buffer_cards) == 2
    assert {card["id"] for card in buffer_cards} == {"buffer", two_logins.id}
    assert all(card["account_count"] == 1 for card in buffer_cards)


def test_one_login_failing_does_not_hide_the_other(two_logins, monkeypatch) -> None:
    """A refused key on one login must not look like the engine being down."""
    def accounts():
        token = publishing._required_credential(publishing.PROVIDERS["buffer"], "api_key")
        if token == "key-for-the-second":
            raise RuntimeError("Buffer refused this key.")
        return [{"id": "a", "platform": "instagram", "label": "IG", "handle": "first"}]

    monkeypatch.setitem(publishing.ACCOUNT_READERS, "buffer", accounts)

    found = publishing.discover_all_integrations()

    cards = {card["id"]: card for card in found["engines"] if card["engine"] == "buffer"}
    assert cards["buffer"]["reachable"]
    assert not cards[two_logins.id]["reachable"]
    assert {account["handle"] for account in found["accounts"]} == {"first"}


# --- what a stored destination resolves to ------------------------------------


def test_an_engine_id_still_resolves_to_that_engine(two_logins) -> None:
    """Every destination written before connections existed carries one."""
    assert publishing.resolve_provider("buffer").id == "buffer"
    assert publishing.resolve_connection("buffer").is_default


def test_a_connection_id_resolves_to_its_engine_s_capabilities(two_logins) -> None:
    # Capabilities belong to the engine; only the credentials differ.
    assert publishing.resolve_provider(two_logins.id).id == "buffer"


def test_an_unknown_id_is_still_refused(two_logins) -> None:
    with pytest.raises(ValueError, match="Unknown publishing provider"):
        publishing.resolve_provider("buffer-that-was-deleted")
