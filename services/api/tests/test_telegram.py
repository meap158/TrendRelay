"""Telegram as an approval channel: what is sent and read, and what refuses to.

The library is asyncio-only and installed from Tools into a runtime of its
own, so these tests stand in a small fake for it - the shape of `Bot`,
`InlineKeyboardMarkup` and the HTML parse mode - and check what this side
hands it. Whether Telegram accepts a token is Telegram's to say, at the test
message.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trendrelay_api import env_store
from trendrelay_api.integrations import telegram


class _FakeBot:
    """Records what it was asked to do, as the real Bot would do it."""

    sent: list[dict] = []
    edited: list[dict] = []
    answered: list[dict] = []
    asked: list[dict] = []
    updates: list = []
    tokens: list[str] = []
    refuse: Exception | None = None

    def __init__(self, token: str) -> None:
        _FakeBot.tokens.append(token)

    async def __aenter__(self) -> "_FakeBot":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send_message(self, **kwargs) -> SimpleNamespace:
        if _FakeBot.refuse:
            raise _FakeBot.refuse
        _FakeBot.sent.append(kwargs)
        return SimpleNamespace(message_id=7, chat_id=kwargs["chat_id"])

    async def get_me(self) -> SimpleNamespace:
        if _FakeBot.refuse:
            raise _FakeBot.refuse
        return SimpleNamespace(username="trendrelay_bot", first_name="TrendRelay")

    async def get_updates(self, **kwargs) -> list:
        _FakeBot.asked.append(kwargs)
        return _FakeBot.updates

    async def answer_callback_query(self, callback_id, **kwargs) -> None:
        _FakeBot.answered.append({"id": callback_id, **kwargs})

    async def edit_message_text(self, **kwargs) -> None:
        _FakeBot.edited.append(kwargs)


def _fake_module() -> SimpleNamespace:
    return SimpleNamespace(
        Bot=_FakeBot,
        InlineKeyboardMarkup=lambda rows: {"rows": rows},
        InlineKeyboardButton=lambda label, url=None, callback_data=None: {
            "label": label, "url": url, "callback_data": callback_data,
        },
        constants=SimpleNamespace(ParseMode=SimpleNamespace(HTML="HTML")),
    )


@pytest.fixture
def configured(monkeypatch, tmp_path):
    """A bot, a chat, and the fake library in place of the real one."""
    monkeypatch.setattr(env_store, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(env_store, "refresh_settings", lambda: None)
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "123456789:" + "A" * 35)
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "-100200300")
    monkeypatch.delenv(telegram.APPROVER_IDS_ENV, raising=False)
    monkeypatch.setattr(telegram, "_telegram_module", _fake_module)
    for name in ("sent", "edited", "answered", "asked", "updates", "tokens"):
        setattr(_FakeBot, name, [])
    _FakeBot.refuse = None
    yield


def test_a_message_goes_to_the_saved_chat_as_html_with_its_buttons_underneath(configured) -> None:
    result = telegram.send_message(
        "<b>Why the sea is blue</b> is waiting",
        buttons=[
            [{"label": "Approve", "callback": "apr:pubexec_1"}],
            [{"label": "Open", "url": "https://app.example/campaigns/c1"}],
        ],
    )

    assert result == {"message_id": 7, "chat_id": "-100200300"}
    [sent] = _FakeBot.sent
    assert sent["chat_id"] == "-100200300"
    assert sent["parse_mode"] == "HTML"
    assert sent["reply_markup"] == {"rows": [
        [{"label": "Approve", "url": None, "callback_data": "apr:pubexec_1"}],
        [{"label": "Open", "url": "https://app.example/campaigns/c1", "callback_data": None}],
    ]}
    # No link preview: the message is the post, not a card for the app.
    assert sent["disable_web_page_preview"] is True
    assert _FakeBot.tokens == ["123456789:" + "A" * 35]


def test_a_button_past_telegram_s_data_limit_is_refused_here(configured) -> None:
    with pytest.raises(telegram.TelegramUnavailable, match="longer than Telegram allows"):
        telegram.send_message("x", buttons=[[{"label": "b", "callback": "a" * 65}]])


def test_a_message_past_telegram_s_limit_is_cut_rather_than_refused(configured) -> None:
    telegram.send_message("x" * (telegram.MESSAGE_LIMIT + 500))
    assert len(_FakeBot.sent[0]["text"]) == telegram.MESSAGE_LIMIT


def test_telegram_s_own_refusal_is_the_reason_given(configured) -> None:
    _FakeBot.refuse = RuntimeError("Chat not found")
    with pytest.raises(telegram.TelegramUnavailable, match="Chat not found"):
        telegram.send_message("hello")
    with pytest.raises(telegram.TelegramUnavailable, match="Chat not found"):
        telegram.probe()


def test_nothing_is_sent_without_a_token_or_a_chat(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env_store, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    monkeypatch.setattr(telegram, "_telegram_module", _fake_module)
    with pytest.raises(telegram.TelegramUnavailable, match="token"):
        telegram.send_message("hello")
    status = telegram.provider_status()
    assert status["configured"] is False
    assert telegram.ready() is False


def test_the_library_missing_is_said_and_points_at_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env_store, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(telegram, "_runtime_root", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "telegram", None)
    assert telegram.library_available() is False
    assert "Tools" in telegram.provider_status()["reason"]


def test_presses_are_read_as_plain_dicts_and_only_presses_are_asked_for(configured) -> None:
    _FakeBot.updates = [
        SimpleNamespace(update_id=41, callback_query=None),
        SimpleNamespace(
            update_id=42,
            callback_query=SimpleNamespace(
                id="cb-9", data="apr:pubexec_1",
                from_user=SimpleNamespace(id=42, username="ana", first_name="Ana"),
                message=SimpleNamespace(chat_id=-100200300, message_id=5),
            ),
        ),
    ]
    found = telegram.fetch_updates(41, timeout=25)
    assert _FakeBot.asked == [{"offset": 41, "timeout": 25, "allowed_updates": ["callback_query"]}]
    assert found == [
        {"update_id": 41},
        {"update_id": 42, "callback": {
            "id": "cb-9", "data": "apr:pubexec_1",
            "from": {"id": "42", "username": "ana", "name": "Ana"},
            "chat_id": "-100200300", "message_id": 5,
        }},
    ]


def test_settling_a_press_answers_it_and_rewrites_the_card_without_buttons(configured) -> None:
    telegram.settle_button("cb-9", chat_id="-100200300", message_id=5, text="✅ Approved by @ana", toast="Approved")
    assert _FakeBot.answered == [{"id": "cb-9", "text": "Approved"}]
    [edited] = _FakeBot.edited
    assert edited["message_id"] == 5 and edited["reply_markup"] is None
    assert edited["text"] == "✅ Approved by @ana"
    # A refusal answers only: the card stays as it was.
    telegram.settle_button("cb-9", chat_id="-100200300", message_id=None, text="", toast="Not yours")
    assert len(_FakeBot.edited) == 1


def test_the_poll_offset_survives_between_polls(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(telegram, "_state_path", lambda: tmp_path / "telegram" / "state.json")
    assert telegram.read_offset() is None
    telegram.write_offset(43)
    assert telegram.read_offset() == 43


def test_the_setup_card_says_what_is_missing_and_what_the_test_does(configured, monkeypatch) -> None:
    from trendrelay_api.integrations import telegram_setup

    monkeypatch.setattr(telegram, "library_available", lambda: True)
    report = telegram_setup.setup_report()
    by_id = {item["id"]: item for item in report["requirements"]}
    assert by_id["library"]["status"] == "ready"
    assert by_id["bot-token"]["status"] == "ready"
    assert by_id["chat"]["status"] == "ready"
    # The token is described, never returned; the chat id is not a secret.
    assert set(report["configured_secret_names"]) == {telegram.BOT_TOKEN_ENV, telegram.CHAT_ID_ENV}
    assert "A" * 35 not in str(report)
    # The one outward action asks first.
    test_action = next(action for action in report["actions"] if action["id"] == "send-test")
    assert test_action["kind"] == "local-launch"
    assert test_action["requires_confirmation"] is True

    outcome = telegram_setup.launch_action("send-test")
    assert outcome["status"] == "ok"
    assert "@trendrelay_bot" in outcome["message"]
    assert "approval" in _FakeBot.sent[0]["text"]

    with pytest.raises(KeyError):
        telegram_setup.launch_action("not-a-thing")
