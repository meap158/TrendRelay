"""Two logins to one engine, reaching two different sets of accounts.

The registry beside this proves connections can be written down. This proves
they are actually used: that discovery asks each login separately, that the
accounts come back tagged with the login that found them, and - the one that
matters most - that a call made for one login authenticates with that login's
key rather than the first one's.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trendrelay_api import publishing_connections as connections
from trendrelay_api.integrations import publishing


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from trendrelay_api import env_store

    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_store, "ENV_PATH", path)
    # Saving a value also puts it in this process's environment, and that
    # outlives the temporary file it was written to. `monkeypatch.delenv` does
    # not help: on a key that was absent it records nothing, so a key these
    # tests go on to create survives teardown and the next test reads it as a
    # login somebody configured. So the environment is restored by hand.
    import os

    prefixes = ("BUFFER_", "ZERNIO_", "BUNDLE_SOCIAL_", "WOOPSOCIAL_",
                connections.REGISTRY_KEY)
    before = {key: value for key, value in os.environ.items() if key.startswith(prefixes)}
    for key in before:
        del os.environ[key]

    yield path

    for key in [k for k in os.environ if k.startswith(prefixes)]:
        del os.environ[key]
    os.environ.update(before)


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


# --- delivering to the right login --------------------------------------------


def test_a_post_is_delivered_with_the_key_of_the_login_it_names(two_logins, monkeypatch) -> None:
    """The whole point, at the moment it matters.

    Two destinations, one on each login, in a single post. Each engine call has
    to authenticate as the login that owns its destination - getting this wrong
    publishes a client's post to somebody else's account.
    """
    seen: list[tuple[str, str]] = []

    def dispatch(provider, part, request_id):
        seen.append((
            part.targets[0].provider,
            publishing._required_credential(publishing.PROVIDERS["buffer"], "api_key"),
        ))
        return {"remote_post_ids": ["x"], "permalinks": []}

    monkeypatch.setattr(publishing, "_dispatch", dispatch)
    monkeypatch.setattr(publishing, "_validate_request", lambda *args, **kwargs: None)

    request = publishing.PublishRequest(
        workspace_id="ws", caption="hello",
        date=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        media_url="https://example.test/v.mp4",
        targets=[
            {"platform": "instagram", "integration_id": "first-account", "provider": "buffer"},
            {"platform": "instagram", "integration_id": "second-account",
             "provider": two_logins.id},
        ],
    )
    publishing._execute_publish(request, "request-1")

    assert dict(seen) == {
        "buffer": "key-for-the-first",
        two_logins.id: "key-for-the-second",
    }


def test_a_destination_naming_a_login_that_does_not_exist_is_refused(two_logins) -> None:
    # It used to be a Literal of engine ids, and refusing nonsense is a property
    # worth keeping now that the set is open enough to hold connections.
    with pytest.raises(ValueError, match="Unknown publishing provider"):
        publishing.PublishTarget(
            platform="instagram", integration_id="a", provider="buffer-never-added"
        )


def test_a_destination_may_still_name_a_bare_engine(two_logins) -> None:
    target = publishing.PublishTarget(
        platform="instagram", integration_id="a", provider="buffer"
    )

    assert target.provider == "buffer"


# --- what Campaigns inherits --------------------------------------------------


def test_a_campaign_destination_on_a_second_login_is_a_valid_target(two_logins) -> None:
    """Campaigns stores `provider` on a destination and hands it to a target.

    It needs no concept of its own: the accounts it offers come from Publish's
    discovery, which now tags each with the login that found it, and the value
    it stores travels unchanged into the post. This asserts the join holds - a
    destination carrying a connection id builds a target that names that login.
    """
    found = publishing.discover_all_integrations()
    second = next(a for a in found["accounts"] if a["handle"] == "second")

    target = publishing.PublishTarget(
        platform=second["platform"],
        integration_id=second["id"],
        provider=second["provider"],
    )

    assert target.provider == two_logins.id
    # And the engine's capabilities still come from the engine, not the login.
    assert publishing.resolve_provider(target.provider).id == "buffer"


def test_a_destination_stored_before_connections_still_builds_a_target(two_logins) -> None:
    # `provider="buffer"` is what every row written before today carries.
    target = publishing.PublishTarget(
        platform="instagram", integration_id="first-account", provider="buffer"
    )

    assert publishing.resolve_connection(target.provider).is_default


def test_a_connection_id_fits_the_column_campaigns_stores_it_in(two_logins) -> None:
    from trendrelay_api.autopilot_models import CampaignDestination

    width = CampaignDestination.__table__.columns["provider"].type.length

    assert len(two_logins.id) <= width


# --- what the page is told about each login -----------------------------------


def test_a_new_login_does_not_report_the_first_one_s_key_as_its_own(two_logins) -> None:
    """Otherwise a second Buffer card reads as configured before anything is typed."""
    fresh = connections.add(publishing.PROVIDERS, "buffer", "Third")

    status = publishing.provider_status(fresh.id, probe=False)

    assert not status["configured"]
    assert all(not field["configured"] for field in status["credential_fields"])


def test_each_login_reports_its_own_key_state(two_logins) -> None:
    first = publishing.provider_status("buffer", probe=False)
    second = publishing.provider_status(two_logins.id, probe=False)

    assert first["configured"] and second["configured"]
    assert first["id"] == "buffer" and second["id"] == two_logins.id
    assert second["engine"] == "buffer"


def test_the_hint_names_the_variable_this_login_would_set(two_logins) -> None:
    # An empty field says "or set X in .env", and X differs per login.
    status = publishing.provider_status(two_logins.id, probe=False)

    keys = {field["key"] for field in status["credential_fields"]}
    assert "BUFFER_API_KEY" not in keys
    assert any(key.startswith("BUFFER_API_KEY") for key in keys)


def test_every_login_gets_a_card(two_logins) -> None:
    found = publishing.connection_status(probe=False)

    ids = [row["id"] for row in found["providers"]]
    assert "buffer" in ids and two_logins.id in ids


# --- reveal reaches a login's key, and no further -----------------------------


def test_a_second_login_s_key_can_be_revealed(two_logins) -> None:
    # Without this the button works on the first login and silently not on the
    # second, which reads as the key having failed to save.
    assert two_logins.key_for("BUFFER_API_KEY") in publishing.revealable_keys()


def test_reveal_still_reaches_nothing_it_should_not(two_logins) -> None:
    """The guard this list exists for: not "read any environment variable"."""
    revealable = publishing.revealable_keys()

    for forbidden in ("DATABASE_URL", "SUPABASE_SERVICE_KEY", "PUBLISHING_CONNECTIONS"):
        assert forbidden not in revealable
