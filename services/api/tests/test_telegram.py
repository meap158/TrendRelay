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
        _FakeBot.asked.append({"get_me": True})
        return SimpleNamespace(username="trendrelay_bot", first_name="TrendRelay")

    async def get_chat(self, chat_id) -> SimpleNamespace:
        if _FakeBot.refuse:
            raise _FakeBot.refuse
        return SimpleNamespace(
            title="Approvals", first_name=None, last_name=None, username=None, type="group",
        )

    async def send_media_group(self, **kwargs) -> list:
        _FakeBot.sent.append({"kind": "album", **kwargs})
        return [SimpleNamespace(message_id=8, chat_id=kwargs["chat_id"])]

    async def send_photo(self, **kwargs) -> SimpleNamespace:
        _FakeBot.sent.append({"kind": "photo", **kwargs})
        return SimpleNamespace(message_id=9, chat_id=kwargs["chat_id"])

    async def send_video(self, **kwargs) -> SimpleNamespace:
        _FakeBot.sent.append({"kind": "video", **{k: v for k, v in kwargs.items() if k != "video"}})
        return SimpleNamespace(message_id=10, chat_id=kwargs["chat_id"])

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
        InputMediaPhoto=lambda media: {"photo": media},
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


# --- a post as it will look ----------------------------------------------------


@pytest.fixture
def pictures(monkeypatch, tmp_path):
    """Three pictures on disk, and a preview step that needs no ffmpeg."""
    made = []
    for index in range(3):
        path = tmp_path / f"slide-{index}.png"
        path.write_bytes(b"png" + bytes([index]))
        made.append(path)
    monkeypatch.setattr(telegram, "_preview_bytes", lambda path: b"jpeg:" + path.name.encode())
    return made


def test_a_carousel_goes_as_an_album_then_the_card_with_its_buttons(configured, pictures) -> None:
    buttons = [[{"label": "Approve", "callback": "apr:pubexec_1"}]]
    outcome = telegram.send_card("<b>Launch</b> · Instagram", buttons=buttons, images=pictures)

    album, card = _FakeBot.sent
    assert album["kind"] == "album"
    assert album["media"] == [{"photo": b"jpeg:slide-0.png"}, {"photo": b"jpeg:slide-1.png"}, {"photo": b"jpeg:slide-2.png"}]
    # The album cannot carry buttons; the card that follows does.
    assert "reply_markup" not in album
    assert card["text"] == "<b>Launch</b> · Instagram" and card["reply_markup"]["rows"]
    assert outcome["media"] == 3 and outcome["skipped"] == []
    assert outcome["message_id"] == 7  # the card, which is what a press comes back to


def test_one_picture_carries_the_card_as_its_caption_when_it_fits(configured, pictures) -> None:
    buttons = [[{"label": "Approve", "callback": "apr:pubexec_1"}]]
    telegram.send_card("short card", buttons=buttons, images=pictures[:1])
    [photo] = _FakeBot.sent
    assert photo["kind"] == "photo" and photo["caption"] == "short card"
    assert photo["reply_markup"]["rows"]

    # Past Telegram's caption limit the words follow as their own message.
    _FakeBot.sent = []
    telegram.send_card("x" * (telegram.CAPTION_LIMIT + 1), buttons=buttons, images=pictures[:1])
    photo, card = _FakeBot.sent
    assert photo["caption"] is None and photo["reply_markup"] is None
    assert card["reply_markup"]["rows"] and len(card["text"]) == telegram.CAPTION_LIMIT + 1


def test_a_video_is_sent_when_a_bot_may_upload_it_and_its_still_when_not(
    configured, monkeypatch, tmp_path,
) -> None:
    clip = tmp_path / "original.mp4"
    clip.write_bytes(b"mp4")
    telegram.send_card("card", images=[], video=clip)
    [video] = _FakeBot.sent
    assert video["kind"] == "video" and video["caption"] == "card" and video["supports_streaming"] is True

    # Too large to upload: the Library keeps a still beside every original.
    _FakeBot.sent = []
    monkeypatch.setattr(telegram, "VIDEO_LIMIT_BYTES", 1)
    (tmp_path / "thumbnail.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(telegram, "_preview_bytes", lambda path: b"still")
    outcome = telegram.send_card("card", video=clip)
    [photo] = _FakeBot.sent
    assert photo["kind"] == "photo" and photo["photo"] == b"still"
    assert outcome["media"] == 1


def test_a_picture_that_cannot_be_prepared_is_left_off_and_named(configured, pictures, monkeypatch) -> None:
    def prepare(path):
        if path.name == "slide-1.png":
            raise telegram.TelegramUnavailable("not a picture")
        return b"jpeg"

    monkeypatch.setattr(telegram, "_preview_bytes", prepare)
    outcome = telegram.send_card("card", images=[*pictures, pictures[0].parent / "missing.png"])
    album, _card = _FakeBot.sent
    assert len(album["media"]) == 2
    assert outcome["skipped"] == ["slide-1.png: not a picture", "missing.png: not on disk"]

    # With nothing that can be shown, the card still goes as words.
    _FakeBot.sent = []
    monkeypatch.setattr(telegram, "_preview_bytes", lambda path: (_ for _ in ()).throw(telegram.TelegramUnavailable("no ffmpeg")))
    outcome = telegram.send_card("card", images=pictures)
    assert [item.get("kind") for item in _FakeBot.sent] == [None]
    assert outcome["media"] == 0 and len(outcome["skipped"]) == 3


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


def test_who_the_bot_is_is_asked_once_and_remembered_for_the_saved_pair(configured, monkeypatch, tmp_path) -> None:
    """A settings form draws from memory; Telegram is asked when nothing is
    remembered for this token and chat, and again after an hour."""
    monkeypatch.setattr(telegram, "_state_path", lambda: tmp_path / "state.json")
    first = telegram.connection_summary()
    assert first == {"connected": True, "bot": "@trendrelay_bot", "chat": "Approvals", "reason": ""}
    asked = [item for item in _FakeBot.asked if item.get("get_me")]
    assert len(asked) == 1

    telegram.connection_summary()
    assert len([item for item in _FakeBot.asked if item.get("get_me")]) == 1  # remembered
    # The offset kept beside it is untouched by remembering.
    telegram.write_offset(9)
    assert telegram.identity()["bot_username"] == "trendrelay_bot"
    assert telegram.read_offset() == 9

    # A different chat is a different memory: asked again.
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "-100999")
    telegram.connection_summary()
    assert len([item for item in _FakeBot.asked if item.get("get_me")]) == 2

    # Telegram not answering leaves the form honest rather than blank-and-connected.
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "-100777")
    _FakeBot.refuse = RuntimeError("timed out")
    summary = telegram.connection_summary()
    assert summary["connected"] is False and "has not answered" in summary["reason"]


def test_a_settings_form_says_not_connected_without_asking_when_nothing_is_saved(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env_store, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    monkeypatch.setattr(telegram, "_telegram_module", _fake_module)
    summary = telegram.connection_summary()
    assert summary["connected"] is False and summary["reason"]


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

    # The test is the flow: an album of sample pictures, then the card with
    # buttons that only answer.
    monkeypatch.setattr(telegram_setup, "_sample_pictures", lambda scratch: [
        (scratch / f"s{index}.png") for index in range(3)
    ])
    for index in range(3):
        pass
    monkeypatch.setattr(telegram, "_preview_bytes", lambda path: b"sample")
    written: list = []

    def make(scratch):
        for index in range(3):
            path = scratch / f"s{index}.png"
            path.write_bytes(b"png")
            written.append(path)
        return list(written)

    monkeypatch.setattr(telegram_setup, "_sample_pictures", make)
    outcome = telegram_setup.launch_action("send-test")
    assert outcome["status"] == "ok"
    assert "@trendrelay_bot" in outcome["message"] and "3 pictures" in outcome["message"]
    album, card = _FakeBot.sent
    assert album["kind"] == "album" and len(album["media"]) == 3
    assert "held post" in card["text"]
    assert card["reply_markup"]["rows"][0][0]["callback_data"] == "tst:test"

    with pytest.raises(KeyError):
        telegram_setup.launch_action("not-a-thing")
