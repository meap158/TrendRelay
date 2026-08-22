"""A tool that needs a key is configured where the tool is.

The Tools page has rendered a settings form, and posted it back, since the
assistant tunnel needed one. The route carried a single hard-coded tool id, so
the second tool to need a key - ElevenLabs, whose entire configuration is one
line - was told to add it to `.env` by hand. That instruction is the thing a
setup screen exists to replace, and the page was already showing that the key
was missing while it gave it.
"""

from __future__ import annotations

import pytest

from trendrelay_api import env_store, tool_settings
from trendrelay_api.tool_settings import SettingsError, provider_for


@pytest.fixture
def env_file(monkeypatch, tmp_path):
    """A .env of this test's own, and an environment put back afterwards."""
    import os

    path = tmp_path / ".env"
    monkeypatch.setattr(env_store, "ENV_PATH", path)
    monkeypatch.setattr(env_store, "refresh_settings", lambda: None)
    before = os.environ.get("ELEVENLABS_API_KEY")
    yield path
    if before is None:
        os.environ.pop("ELEVENLABS_API_KEY", None)
    else:
        os.environ["ELEVENLABS_API_KEY"] = before


def test_a_tool_with_no_settings_says_so_rather_than_guessing() -> None:
    """A model configured by being downloaded has nothing to type."""
    assert provider_for("faster-whisper") is None
    assert provider_for("sam2") is None


def test_the_tools_that_do_have_settings_are_the_ones_that_need_a_key() -> None:
    assert set(tool_settings.PROVIDERS) == {"mcp-server", "elevenlabs"}


def test_a_key_can_be_saved_from_the_tool_that_needs_it(env_file) -> None:
    provider = provider_for("elevenlabs")

    written = provider.save({"ELEVENLABS_API_KEY": "sk_" + "a" * 40})

    assert written == ["ELEVENLABS_API_KEY"]
    assert "ELEVENLABS_API_KEY" in env_file.read_text(encoding="utf-8")


def test_the_saved_key_is_described_and_never_returned(env_file) -> None:
    """The card can be read over somebody's shoulder."""
    provider = provider_for("elevenlabs")
    provider.save({"ELEVENLABS_API_KEY": "sk_" + "b" * 40})

    field = provider.fields()[0]

    assert field["configured"] is True
    assert field["value"] == ""
    assert field["preview"]
    assert "b" * 40 not in str(field)


def test_something_that_is_not_a_key_is_refused_before_it_is_written(env_file) -> None:
    """Caught here rather than by a service call minutes later."""
    provider = provider_for("elevenlabs")

    for wrong in ("too-short", "has a space in it and is long enough to pass length"):
        with pytest.raises(SettingsError) as refused:
            provider.save({"ELEVENLABS_API_KEY": wrong})
        assert "does not look like an API key" in str(refused.value)
    assert not env_file.exists() or "ELEVENLABS_API_KEY" not in env_file.read_text(
        encoding="utf-8"
    )


def test_a_setting_that_is_not_this_tools_is_refused(env_file) -> None:
    """Without this the form is "write any environment variable"."""
    provider = provider_for("elevenlabs")

    with pytest.raises(SettingsError) as refused:
        provider.save({"CONTROL_PLANE_API_KEY": "x" * 40})

    assert "Not an ElevenLabs setting" in str(refused.value)


def test_clearing_the_key_is_allowed(env_file) -> None:
    """Emptying it is how somebody stops the tool being used at all."""
    provider = provider_for("elevenlabs")
    provider.save({"ELEVENLABS_API_KEY": "sk_" + "c" * 40})

    provider.save({"ELEVENLABS_API_KEY": ""})

    assert provider.fields()[0]["configured"] is False


def test_a_card_can_ask_for_the_form_by_name() -> None:
    """What a tool's card renders, without the card having to know the fields.

    `settings_fields` is the whole join: a report asks for its tool's form and
    gets one or gets nothing, so adding a key-based tool is a provider and a
    line rather than a form written twice.
    """
    from trendrelay_api.tool_settings import fields_for

    assert [field["key"] for field in fields_for("elevenlabs")] == [
        "ELEVENLABS_API_KEY"
    ]
    assert fields_for("faster-whisper") == []


def test_the_form_is_attached_by_the_route_not_by_each_card(env_file) -> None:
    """So a tool that declares settings is configurable the moment it does.

    Every card that wanted a form had to remember to ask for one, which is the
    kind of wiring forgotten exactly once - and then a tool tells somebody to
    edit .env from a screen built to save them that.
    """
    from trendrelay_api.main import _with_settings

    assert [field["key"] for field in _with_settings("mcp-server")["settings"]]
    # A model configured by being downloaded still has nothing to type.
    assert "settings" not in _with_settings("faster-whisper")
