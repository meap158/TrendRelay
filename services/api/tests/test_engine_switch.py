"""Switching a publishing engine off, where everything that posts can see it.

The switch existed before this and lived in the browser that threw it: the
Publish composer kept a list of switched-off engines in local storage and
filtered its own account picker with it. So a campaign went on planning and
delivering through an engine the operator had switched off, every screen
agreed it was off, and nothing anywhere contradicted the posts arriving.

It is now a setting, next to the engine's keys and the connection registry,
because the planner and the worker both have to honour it and neither of them
has a local storage.
"""

from __future__ import annotations

import os

import pytest

from trendrelay_api import publishing_connections as connections
from trendrelay_api.integrations import publishing


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """A `.env` of this test's own, and an environment left as it was found.

    Written values also land in `os.environ`, which outlives the temporary
    file, so a key created here would be read by the next test as a setting
    somebody had chosen - see the same fixture in the multi-connection tests.
    """
    from trendrelay_api import env_store

    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_store, "ENV_PATH", path)

    keys = (publishing.ENGINES_OFF_KEY, connections.REGISTRY_KEY)
    before = {key: os.environ[key] for key in keys if key in os.environ}
    for key in before:
        del os.environ[key]

    yield path

    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(before)


def test_nothing_is_switched_off_to_begin_with() -> None:
    assert publishing.engines_off() == set()
    assert publishing.engine_off_note("buffer") is None


def test_switching_an_engine_off_stops_everything_that_posts() -> None:
    publishing.set_engine_enabled("buffer", False)

    assert publishing.engines_off() == {"buffer"}
    note = publishing.engine_off_note("buffer")
    assert note is not None
    assert "Buffer" in note and "switched off in Publish" in note
    # One engine off is not every engine off.
    assert publishing.engine_off_note("zernio") is None


def test_switching_it_back_on_leaves_no_setting_behind() -> None:
    """Absent, not empty: a state every reader already handles."""
    publishing.set_engine_enabled("buffer", False)
    publishing.set_engine_enabled("buffer", True)

    assert publishing.engines_off() == set()
    from trendrelay_api import env_store

    assert publishing.ENGINES_OFF_KEY not in env_store.read_env_file()


def test_each_login_is_switched_separately() -> None:
    """Two logins to one engine are two engines as far as this is concerned.

    The switch is per connection, because that is what a destination stores
    and what the picker lists. Switching off a second Buffer login has no
    business stopping the first one's posts.
    """
    second = connections.add(publishing.PROVIDERS, "buffer", "Client B")
    publishing.set_engine_enabled(second.id, False)

    assert publishing.engine_off_note(second.id) is not None
    assert publishing.engine_off_note("buffer") is None
    note = publishing.engine_off_note(second.id)
    assert note is not None and "Client B" in note


def test_an_unknown_login_is_not_treated_as_switched_off() -> None:
    """A destination naming a login that has been removed is broken, not off.

    It has its own error further down the delivery path, and answering "it is
    switched off" here would send somebody to a switch that does not exist.
    """
    assert publishing.engine_off_note("engine-that-was-removed") is None


def test_a_hand_edited_setting_that_makes_no_sense_posts_rather_than_stops() -> None:
    """The failure mode of guessing wrong here is a campaign that stops."""
    from trendrelay_api import env_store

    env_store.write_env_values({publishing.ENGINES_OFF_KEY: "buffer, zernio"})

    assert publishing.engines_off() == set()
    assert publishing.engine_off_note("buffer") is None


def test_the_engine_card_says_whether_it_is_switched_on() -> None:
    """Intent, beside capability, on the same payload the card reads.

    Never probed: whether somebody wants to use an engine is a setting, and
    asking the engine about it would be a request made to learn nothing.
    """
    assert publishing.provider_status("buffer", probe=False)["enabled"]

    publishing.set_engine_enabled("buffer", False)

    assert not publishing.provider_status("buffer", probe=False)["enabled"]
    assert publishing.provider_status("zernio", probe=False)["enabled"]


def test_a_switched_off_engine_can_still_be_the_default_one() -> None:
    """Switched off is a statement of intent, not a claim about the setup.

    The pair of them is how somebody says "I am not using this one for now"
    without dismantling it, and refusing the switch on the active engine would
    make the operator choose a new default before they could stop posting.
    """
    publishing.set_engine_enabled(publishing.active_provider_id(), False)

    status = publishing.connection_status(probe=False)
    active = next(item for item in status["providers"] if item["id"] == status["active_provider"])
    assert not active["enabled"]
