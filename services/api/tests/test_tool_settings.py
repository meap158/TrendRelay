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
    # Every key a card here can write. A save updates the process environment
    # as well as the file, and a key left behind reads as "configured" to
    # every test after this one - a Telegram token from a test here once made
    # the campaign inbox offer Telegram in a test that had set nothing up.
    keys = (
        "ELEVENLABS_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_APPROVER_IDS",
        "XAI_API_KEY", "GEMINI_API_KEY", "VIDEO_PROVIDER_XAI_ENABLED", "VIDEO_PROVIDER_GEMINI_ENABLED",
    )
    before = {key: os.environ.get(key) for key in keys}
    yield path
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_a_tool_with_no_settings_says_so_rather_than_guessing() -> None:
    """A model configured by being downloaded has nothing to type."""
    assert provider_for("faster-whisper") is None
    assert provider_for("sam2") is None


def test_the_tools_that_do_have_settings_are_the_ones_that_need_a_key() -> None:
    assert set(tool_settings.PROVIDERS) == {
        "mcp-server", "elevenlabs", "pexels", "telegram-bot", "video-generation",
    }


def test_a_telegram_card_refuses_what_is_not_a_token_or_a_chat(env_file) -> None:
    """Refused here, in the words of what was expected, rather than by Telegram
    at the first approval request - when nobody is looking at the card."""
    provider = provider_for("telegram-bot")
    with pytest.raises(SettingsError, match="bot token"):
        provider.save({"TELEGRAM_BOT_TOKEN": "my_bot_username"})
    with pytest.raises(SettingsError, match="chat id"):
        provider.save({"TELEGRAM_CHAT_ID": "my chat"})

    written = provider.save({
        "TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35,
        "TELEGRAM_CHAT_ID": "-100200300",
    })
    assert written == ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

    token, chat, _approvers = provider.fields()
    # The token is a secret: described and masked. The chat id is not.
    assert token["configured"] and token["value"] == "" and token["preview"]
    assert "A" * 35 not in token["preview"]
    assert chat["configured"] and chat["value"] == "-100200300"
    assert provider.reveal("TELEGRAM_BOT_TOKEN") == "123456789:" + "A" * 35
    with pytest.raises(SettingsError):
        provider.reveal("TELEGRAM_CHAT_ID")


def test_a_hosted_key_card_refuses_a_setting_that_is_not_its_own(env_file) -> None:
    """Each card writes only the keys it declared.

    The form and the writer read one list, so a field cannot be accepted by one
    and refused by the other - and a request naming somebody else's key cannot
    reach the env file through this card.
    """
    for tool_id, foreign in (("elevenlabs", "PEXELS_API_KEY"),
                             ("pexels", "ELEVENLABS_API_KEY"),
                             ("video-generation", "PEXELS_API_KEY")):
        try:
            provider_for(tool_id).save({foreign: "x" * 40})
        except tool_settings.SettingsError as error:
            assert foreign in str(error)
        else:
            raise AssertionError(f"{tool_id} wrote {foreign}")


def test_a_video_switch_can_change_without_retyping_the_key(env_file) -> None:
    """An empty secret box means leave the key, which is how a switch is saved."""
    provider = provider_for("video-generation")
    provider.save({
        "XAI_API_KEY": "x" * 20,
        "VIDEO_PROVIDER_XAI_ENABLED": "on",
    })
    written = provider.save({
        "XAI_API_KEY": "",
        "VIDEO_PROVIDER_XAI_ENABLED": "off",
    })

    assert written == ["VIDEO_PROVIDER_XAI_ENABLED"]
    assert provider.reveal("XAI_API_KEY") == "x" * 20
    switch = next(field for field in provider.fields() if field["key"] == "VIDEO_PROVIDER_XAI_ENABLED")
    assert switch["value"] == "off"
    assert "x" * 20 not in (switch["preview"] or "")


def test_a_pexels_key_is_described_and_never_returned(env_file) -> None:
    provider = provider_for("pexels")
    provider.save({"PEXELS_API_KEY": "p" * 40})

    field = provider.fields()[0]
    assert field["configured"] is True
    assert field["value"] == ""
    assert field["preview"] and "p" * 40 not in field["preview"]
    # And revealed only when the route has authorized it.
    assert provider.reveal("PEXELS_API_KEY") == "p" * 40


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

    keys = [field["key"] for field in fields_for("elevenlabs")]

    # The key leads the form because it gates everything under it: no key,
    # and no voice or transcription setting beside it does anything.
    assert keys[0] == "ELEVENLABS_API_KEY"
    # That a form exists at all is the join this is about. Deliberately not
    # pinned to an exact list - the voice and speech-to-text settings beside
    # the key are meant to grow, and enumerating them here failed the moment
    # they did, with nothing actually wrong.
    assert len(keys) > 1
    # And a tool that declares none still gets none.
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
