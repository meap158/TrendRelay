"""Two logins to the same engine, without disturbing the first one.

The property that matters most here is the one about existing data: every
destination, slot and execution already written down carries `provider="zernio"`
and nothing rewrites them. They keep working because a connection's id defaults
to its engine's id, so that value is now a connection id too. The first test
is the one that guards it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from trendrelay_api import publishing_connections as connections

PROVIDERS = {
    "bundle_social": SimpleNamespace(label="Bundle.social"),
    "zernio": SimpleNamespace(label="Zernio"),
    "buffer": SimpleNamespace(label="Buffer"),
}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """A real `.env` file, since that is where the registry lives."""
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



# --- what must not change -----------------------------------------------------


def test_every_engine_starts_with_a_connection_named_after_it() -> None:
    """The row that lets existing data keep resolving."""
    found = {row.id: row for row in connections.connections(PROVIDERS)}

    for provider_id in PROVIDERS:
        assert provider_id in found
        assert found[provider_id].provider == provider_id
        assert found[provider_id].is_default


def test_the_first_connection_reads_the_key_it_always_read() -> None:
    # An existing .env, and the documentation describing it, stay correct.
    first = connections.find(PROVIDERS, "zernio")

    assert first.key_for("ZERNIO_API_KEY") == "ZERNIO_API_KEY"


def test_a_later_connection_reads_a_key_of_its_own() -> None:
    added = connections.add(PROVIDERS, "zernio", "Brand B")

    key = added.key_for("ZERNIO_API_KEY")

    assert key != "ZERNIO_API_KEY"
    assert key.startswith("ZERNIO_API_KEY")


def test_the_first_connection_cannot_be_removed() -> None:
    # Removing it would orphan every destination that predates connections.
    with pytest.raises(ValueError, match="cannot be removed"):
        connections.remove(PROVIDERS, "zernio", ("ZERNIO_API_KEY",))


# --- adding -------------------------------------------------------------------


def test_adding_gives_the_engine_a_second_connection() -> None:
    added = connections.add(PROVIDERS, "buffer", "Client account")

    rows = connections.for_provider(PROVIDERS, "buffer")
    assert len(rows) == 2
    assert added.id in {row.id for row in rows}
    assert added.label == "Client account"
    assert not added.is_default


def test_an_id_is_derived_from_the_name_it_was_given() -> None:
    # `buffer-client-account` says more in a log line than `buffer-2`.
    added = connections.add(PROVIDERS, "buffer", "Client Account")

    assert added.id == "buffer-client-account"


def test_two_connections_named_the_same_do_not_collide() -> None:
    first = connections.add(PROVIDERS, "buffer", "Client")
    second = connections.add(PROVIDERS, "buffer", "Client")

    assert first.id != second.id
    assert len({row.id for row in connections.for_provider(PROVIDERS, "buffer")}) == 3


def test_a_name_that_slugs_to_the_engine_does_not_shadow_the_default() -> None:
    added = connections.add(PROVIDERS, "buffer", "Buffer")

    assert added.id != "buffer"
    assert connections.find(PROVIDERS, "buffer").is_default


def test_an_unknown_engine_is_refused() -> None:
    with pytest.raises(ValueError, match="no publishing engine"):
        connections.add(PROVIDERS, "not-an-engine", "Whatever")


def test_one_engine_cannot_be_given_unlimited_connections() -> None:
    for index in range(connections.MAX_CONNECTIONS_PER_ENGINE - 1):
        connections.add(PROVIDERS, "buffer", f"Account {index}")

    with pytest.raises(ValueError, match="most"):
        connections.add(PROVIDERS, "buffer", "One too many")


# --- removing -----------------------------------------------------------------


def test_removing_clears_the_credentials_that_were_only_for_it() -> None:
    """Or the next connection to take that id would inherit them."""
    from trendrelay_api import env_store

    added = connections.add(PROVIDERS, "zernio", "Brand B")
    key = added.key_for("ZERNIO_API_KEY")
    env_store.write_env_values({key: "secret-value"})

    connections.remove(PROVIDERS, added.id, ("ZERNIO_API_KEY",))

    assert not env_store.effective_value(key)
    assert connections.find(PROVIDERS, added.id) is None


def test_removing_one_leaves_the_others_alone() -> None:
    from trendrelay_api import env_store

    keep = connections.add(PROVIDERS, "zernio", "Keep")
    drop = connections.add(PROVIDERS, "zernio", "Drop")
    env_store.write_env_values({keep.key_for("ZERNIO_API_KEY"): "still-here"})

    connections.remove(PROVIDERS, drop.id, ("ZERNIO_API_KEY",))

    assert env_store.effective_value(keep.key_for("ZERNIO_API_KEY")) == "still-here"
    assert connections.find(PROVIDERS, keep.id) is not None


# --- surviving a hand-edited file ---------------------------------------------


def test_a_broken_registry_costs_the_extra_connections_and_nothing_else() -> None:
    # This is read on the way to showing somebody their accounts. One bad edit
    # should not take the page down with it.
    from trendrelay_api import env_store

    env_store.write_env_values({connections.REGISTRY_KEY: "{not json at all"})

    rows = connections.connections(PROVIDERS)

    assert {row.id for row in rows} == set(PROVIDERS)


def test_a_row_for_a_removed_engine_is_ignored() -> None:
    from trendrelay_api import env_store

    env_store.write_env_values({connections.REGISTRY_KEY: json.dumps([
        {"id": "gone-2", "provider": "an-engine-we-dropped", "label": "Old"},
    ])})

    assert connections.find(PROVIDERS, "gone-2") is None


def test_a_row_that_would_shadow_a_default_is_ignored() -> None:
    """Or credentials would go to whichever row was read second."""
    from trendrelay_api import env_store

    env_store.write_env_values({connections.REGISTRY_KEY: json.dumps([
        {"id": "zernio", "provider": "buffer", "label": "Impostor"},
    ])})

    row = connections.find(PROVIDERS, "zernio")

    assert row.provider == "zernio"
    assert row.is_default


def test_an_id_fits_the_column_a_destination_stores_it_in() -> None:
    """`String(32)`, on four tables. Postgres would refuse a longer one."""
    added = connections.add(
        PROVIDERS, "bundle_social",
        "A rather long account name somebody typed in full",
    )

    assert len(added.id) <= connections.MAX_ID_LENGTH
    assert added.id.startswith("bundle_social-")


def test_a_truncated_id_still_does_not_collide_with_the_default() -> None:
    # Trimming to fit must not trim away everything that made it distinct.
    added = connections.add(PROVIDERS, "bundle_social", "!!!")

    assert added.id != "bundle_social"
    assert connections.find(PROVIDERS, "bundle_social").is_default


def test_removing_leaves_no_line_behind_for_a_login_that_is_gone() -> None:
    """Blanking the value would read correctly and still clutter the file."""
    from trendrelay_api import env_store

    added = connections.add(PROVIDERS, "zernio", "Temporary")
    key = added.key_for("ZERNIO_API_KEY")
    env_store.write_env_values({key: "value"})
    assert key in env_store.ENV_PATH.read_text(encoding="utf-8")

    connections.remove(PROVIDERS, added.id, ("ZERNIO_API_KEY",))

    assert key not in env_store.ENV_PATH.read_text(encoding="utf-8")


def test_removing_one_login_does_not_disturb_another_s_line() -> None:
    from trendrelay_api import env_store

    keep = connections.add(PROVIDERS, "zernio", "Keep")
    drop = connections.add(PROVIDERS, "zernio", "Drop")
    env_store.write_env_values({
        keep.key_for("ZERNIO_API_KEY"): "still-here",
        drop.key_for("ZERNIO_API_KEY"): "going",
    })

    connections.remove(PROVIDERS, drop.id, ("ZERNIO_API_KEY",))

    text = env_store.ENV_PATH.read_text(encoding="utf-8")
    assert keep.key_for("ZERNIO_API_KEY") in text
    assert drop.key_for("ZERNIO_API_KEY") not in text


def test_a_connection_is_still_there_after_a_restart() -> None:
    """The failure that looked like a broken engine.

    Every test above passes without reading the file. `write_env_values` also
    sets the value in this process's environment and `effective_value` prefers
    that, so the registry never made the round trip through `.env` where it was
    being corrupted - the escaping `_quote` applies was not undone on the way
    back, the JSON would not parse, and the registry read as empty.

    Nothing noticed until the API was restarted. Then the second login was gone
    from every list, its card had no connection behind it, and the page could
    only report the engine as unreachable. Dropping the environment copy is what
    reproduces that, so this is the one test here that reads from disk.
    """
    import os

    added = connections.add(PROVIDERS, "zernio", "Zernio 2")
    os.environ.pop(connections.REGISTRY_KEY, None)

    found = [row for row in connections.connections(PROVIDERS) if row.provider == "zernio"]

    assert [row.id for row in found] == ["zernio", added.id]
    assert [row.label for row in found] == ["Zernio", "Zernio 2"]
    # And it still points at the key its credentials were written under, or the
    # connection would come back as one with no key saved.
    assert found[1].key_for("ZERNIO_API_KEY") == added.key_for("ZERNIO_API_KEY")


def test_several_connections_all_survive_a_restart() -> None:
    """One surviving by luck is not the same as the registry being readable."""
    import os

    first = connections.add(PROVIDERS, "zernio", "Second login")
    second = connections.add(PROVIDERS, "buffer", "Brand B")
    third = connections.add(PROVIDERS, "zernio", "Third login")
    os.environ.pop(connections.REGISTRY_KEY, None)

    ids = {row.id for row in connections.connections(PROVIDERS)}

    assert {first.id, second.id, third.id} <= ids
