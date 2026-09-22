"""Telegram, for reaching the person whose approval a post is waiting on.

A campaign post that needs approving waits in the app until somebody opens
the app. The approver is often the one person who is not at the desk, and
the approval is one look and one press. Telegram is where that person already
is, on the phone: each held post arrives as a message with the buttons the
approval inbox offers - approve, approve and post now, dismiss - and pressing
one decides it, the way a card is swiped.

Built on python-telegram-bot, installed from Tools into a runtime of its own
the way yt-dlp is, so the API's environment carries nothing for a machine
that never sends. The bot token and the chat it posts to are saved on the
tool's card; both live in the local `.env` and neither is returned to the
browser. A bot cannot start a conversation, so the chat has to have written
to the bot first - or be a group the bot was added to - which the setup notes
explain.

Who may press is the chat. The bot only sends to the one chat the operator
saved, and a press from anywhere else is ignored; within the chat, an
optional list of Telegram user ids narrows it further. The approver's
Telegram identity is written on the audit event beside the decision.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from trendrelay_api.env_store import configured_keys, effective_value

TOOL_ID = "telegram-bot"
BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
#: Optional. Telegram user ids allowed to press the buttons, comma-separated.
#: Empty means anyone in the saved chat - which is the operator alone in a
#: private chat, and everyone in a group.
APPROVER_IDS_ENV = "TELEGRAM_APPROVER_IDS"

#: Telegram's own ceiling on one message's text, in characters.
MESSAGE_LIMIT = 4096
#: Telegram's ceiling on a photo or video caption, in characters.
CAPTION_LIMIT = 1024
#: Telegram's ceiling on a button's callback data, in bytes.
CALLBACK_LIMIT = 64
#: How many pictures one album may carry, and how large a bot may upload.
ALBUM_LIMIT = 10
PHOTO_LIMIT_BYTES = 10 * 1024 * 1024
VIDEO_LIMIT_BYTES = 50 * 1024 * 1024
#: The long side a picture is scaled to before it is sent: a phone's screen,
#: not a print. A Library original is often a multi-megabyte PNG, and the
#: card is for deciding, not for archiving.
PREVIEW_SIDE = 1280

#: What BotFather hands out: the bot's numeric id, a colon, and a secret.
TOKEN_SHAPE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
#: A chat id is a signed integer - negative for a group - or a public
#: channel's @name.
CHAT_SHAPE = re.compile(r"^(-?\d+|@[A-Za-z0-9_]{5,})$")
#: A list of Telegram user ids, which are positive integers.
APPROVERS_SHAPE = re.compile(r"^\d+(\s*,\s*\d+)*$")

#: A button under a message: a link, or a press that comes back to us.
Button = dict[str, str]


class TelegramUnavailable(RuntimeError):
    """The message could not be sent, and the reason is the message."""


def _runtime_root() -> Path | None:
    """Where Tools installed python-telegram-bot, if it is there.

    Installs go into a directory of their own with `pip --target`, not into
    the API's environment, so "is it installed" is not answered by a plain
    import. Read from the catalogue rather than repeated here.
    """
    try:
        from trendrelay_api.tool_registry import runtime_root_for  # noqa: PLC0415
    except ImportError:
        return None
    root = runtime_root_for(TOOL_ID)
    return root if root and root.is_dir() else None


def _telegram_module() -> Any:
    """The library, from the runtime Tools installed ahead of anything else."""
    root = _runtime_root()
    if root and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import telegram  # noqa: PLC0415 - installed from Tools, not a dependency
    except ImportError as error:
        raise TelegramUnavailable(
            "python-telegram-bot is not installed. Install it from the Telegram card in Tools."
        ) from error
    return telegram


def library_available() -> bool:
    try:
        _telegram_module()
    except TelegramUnavailable:
        return False
    return True


def _settings() -> tuple[str, str]:
    token = (effective_value(BOT_TOKEN_ENV) or "").strip()
    chat = (effective_value(CHAT_ID_ENV) or "").strip()
    if not token:
        raise TelegramUnavailable("No bot token is saved. Add it on the Telegram card in Tools.")
    if not chat:
        raise TelegramUnavailable("No chat is saved. Add the chat id on the Telegram card in Tools.")
    return token, chat


def configured_chat_id() -> str:
    """The one chat the bot speaks to, or empty."""
    return (effective_value(CHAT_ID_ENV) or "").strip()


def approver_ids() -> set[str]:
    """Who may press the buttons, narrowed below the chat. Empty is everyone in it."""
    raw = (effective_value(APPROVER_IDS_ENV) or "").strip()
    return {part.strip() for part in raw.split(",") if part.strip()}


def provider_status() -> dict[str, Any]:
    """Whether a message can go out, and what is missing when it cannot.

    Deliberately does not call Telegram: this is read to draw a card, and a
    card that spent a request on every load would say nothing the saved
    values do not. Whether the token works is answered by the test message.
    """
    saved = configured_keys((BOT_TOKEN_ENV, CHAT_ID_ENV))
    installed = library_available()
    token, chat = saved[BOT_TOKEN_ENV], saved[CHAT_ID_ENV]
    if not installed:
        reason = "python-telegram-bot is not installed. Install it from Tools."
    elif not token:
        reason = "No bot token is saved."
    elif not chat:
        reason = "No chat is saved."
    else:
        reason = ""
    return {
        "id": TOOL_ID,
        "name": "Telegram",
        "installed": installed,
        "configured": bool(token and chat),
        "token_env": BOT_TOKEN_ENV,
        "chat_env": CHAT_ID_ENV,
        "approvers": sorted(approver_ids()),
        "reason": reason,
    }


def ready() -> bool:
    """Whether an approval request can be sent this way right now."""
    return provider_status()["configured"] and library_available()


def _run(coroutine: Any) -> Any:
    """Run the library's coroutine to completion from synchronous code.

    The library is asyncio-only. Callers here are synchronous - a worker, a
    setup action run on a thread - so a loop of their own is the right shape;
    nothing awaits alongside it.
    """
    return asyncio.run(coroutine)


def _said(error: Exception) -> str:
    """Telegram's own reason, in the words its library uses, or the type."""
    text = str(error).strip()
    return text or error.__class__.__name__


def _markup(telegram: Any, buttons: list[list[Button]] | None) -> Any:
    if not buttons:
        return None
    rows = []
    for row in buttons:
        made = []
        for button in row:
            if button.get("url"):
                made.append(telegram.InlineKeyboardButton(button["label"], url=button["url"]))
            else:
                data = button.get("callback", "")
                if len(data.encode("utf-8")) > CALLBACK_LIMIT:
                    raise TelegramUnavailable(
                        f"A button's data is longer than Telegram allows: {data!r}."
                    )
                made.append(telegram.InlineKeyboardButton(button["label"], callback_data=data))
        rows.append(made)
    return telegram.InlineKeyboardMarkup(rows)


def send_message(
    text: str,
    *,
    buttons: list[list[Button]] | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Send one HTML-formatted message to the configured chat.

    `buttons` are rows of `{"label", "url"}` links or `{"label", "callback"}`
    presses that come back through `fetch_updates`. Text past Telegram's
    limit is cut rather than refused: a long caption is still worth
    announcing.
    """
    telegram = _telegram_module()
    token, chat = _settings()
    markup = _markup(telegram, buttons)

    async def go() -> dict[str, Any]:
        async with telegram.Bot(token) as bot:
            sent = await bot.send_message(
                chat_id=chat_id or chat,
                text=text[:MESSAGE_LIMIT],
                parse_mode=telegram.constants.ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return {"message_id": sent.message_id, "chat_id": str(sent.chat_id)}

    try:
        return _run(go())
    except TelegramUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 - the library's reason is the reason
        raise TelegramUnavailable(f"Telegram refused the message: {_said(error)}") from error


def _preview_bytes(path: Path) -> bytes:
    """A picture as a JPEG a phone can show, scaled down, made with ffmpeg.

    ffmpeg rather than an image library, because it is the one image tool
    this app already has - the Library's thumbnails come from it - and a
    second one for the same job is a second thing to install.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from trendrelay_api.integrations.openmontage_runtime import FFMPEG  # noqa: PLC0415

    if not Path(FFMPEG).is_file():
        raise TelegramUnavailable("The pinned local FFmpeg runtime is missing. Run npm install.")
    with tempfile.TemporaryDirectory(prefix="telegram-preview-") as scratch:
        target = Path(scratch) / "preview.jpg"
        completed = subprocess.run(
            [
                str(FFMPEG), "-y", "-v", "error", "-i", str(path), "-frames:v", "1",
                "-vf", f"scale='min({PREVIEW_SIDE},iw)':'min({PREVIEW_SIDE},ih)'"
                       ":force_original_aspect_ratio=decrease",
                "-q:v", "4", str(target),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=120,
        )
        if completed.returncode != 0 or not target.is_file():
            raise TelegramUnavailable(
                (completed.stderr or "The picture could not be prepared.").strip()[-300:]
            )
        return target.read_bytes()


def _thumbnail_beside(video: Path) -> Path | None:
    """The Library's own still for a video, kept next to the original."""
    candidate = video.parent / "thumbnail.jpg"
    return candidate if candidate.is_file() else None


def send_card(
    text: str,
    *,
    buttons: list[list[Button]] | None = None,
    images: list[str | Path] | tuple[str | Path, ...] = (),
    video: str | Path | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Send a post as it will look, with the card's text and buttons.

    The pictures first, then the words and the buttons. A carousel goes as
    an album, which Telegram does not let carry buttons, so the card's text
    follows it as a message that does. One picture, or a video, carries the
    text as its caption when it fits Telegram's caption limit, buttons and
    all; when it does not, the text follows as its own message. A video too
    large for a bot to upload is stood in for by the Library's still of it.

    A picture that cannot be prepared is left out rather than stopping the
    card: what is being decided is the post, and the words and the buttons
    are what decide it. How many media went, and what did not, is returned.
    """
    telegram = _telegram_module()
    token, chat = _settings()
    target = chat_id or chat
    markup = _markup(telegram, buttons)
    mode = telegram.constants.ParseMode.HTML

    previews: list[bytes] = []
    skipped: list[str] = []
    for image in list(images)[:ALBUM_LIMIT]:
        path = Path(image)
        try:
            if not path.is_file():
                raise TelegramUnavailable("not on disk")
            preview = _preview_bytes(path)
            # Scaling makes this all but impossible - a 1280px JPEG is a few
            # hundred kilobytes - but a bot cannot upload past the limit, and
            # a refusal from Telegram would take the whole card with it
            # rather than one picture.
            if len(preview) > PHOTO_LIMIT_BYTES:
                raise TelegramUnavailable("still too large to send after scaling")
            previews.append(preview)
        except (TelegramUnavailable, OSError) as error:
            skipped.append(f"{path.name}: {error}")
    clip: Path | None = None
    still: bytes | None = None
    if video and not previews:
        path = Path(video)
        if path.is_file() and path.stat().st_size <= VIDEO_LIMIT_BYTES:
            clip = path
        elif path.is_file():
            thumbnail = _thumbnail_beside(path)
            if thumbnail is not None:
                try:
                    still = _preview_bytes(thumbnail)
                except TelegramUnavailable as error:
                    skipped.append(f"{thumbnail.name}: {error}")
            else:
                skipped.append(f"{path.name}: too large to send, and no still beside it")
        elif video:
            skipped.append(f"{path.name}: not on disk")

    caption_fits = len(text) <= CAPTION_LIMIT

    async def go() -> dict[str, Any]:
        async with telegram.Bot(token) as bot:
            sent_media = 0
            if len(previews) >= 2:
                await bot.send_media_group(
                    chat_id=target,
                    media=[telegram.InputMediaPhoto(media=item) for item in previews],
                )
                sent_media = len(previews)
                message = await bot.send_message(
                    chat_id=target, text=text[:MESSAGE_LIMIT], parse_mode=mode,
                    reply_markup=markup, disable_web_page_preview=True,
                )
            elif len(previews) == 1 or still is not None:
                photo = previews[0] if previews else still
                message = await bot.send_photo(
                    chat_id=target, photo=photo,
                    caption=text if caption_fits else None, parse_mode=mode,
                    reply_markup=markup if caption_fits else None,
                )
                sent_media = 1
                if not caption_fits:
                    message = await bot.send_message(
                        chat_id=target, text=text[:MESSAGE_LIMIT], parse_mode=mode,
                        reply_markup=markup, disable_web_page_preview=True,
                    )
            elif clip is not None:
                with clip.open("rb") as handle:
                    message = await bot.send_video(
                        chat_id=target, video=handle, supports_streaming=True,
                        caption=text if caption_fits else None, parse_mode=mode,
                        reply_markup=markup if caption_fits else None,
                    )
                sent_media = 1
                if not caption_fits:
                    message = await bot.send_message(
                        chat_id=target, text=text[:MESSAGE_LIMIT], parse_mode=mode,
                        reply_markup=markup, disable_web_page_preview=True,
                    )
            else:
                message = await bot.send_message(
                    chat_id=target, text=text[:MESSAGE_LIMIT], parse_mode=mode,
                    reply_markup=markup, disable_web_page_preview=True,
                )
            return {
                "message_id": message.message_id,
                "chat_id": str(message.chat_id),
                "media": sent_media,
                "skipped": skipped,
            }

    try:
        return _run(go())
    except TelegramUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 - the library's reason is the reason
        raise TelegramUnavailable(f"Telegram refused the card: {_said(error)}") from error


def probe() -> dict[str, Any]:
    """Who the bot is, as Telegram says - the check that the token is a token."""
    telegram = _telegram_module()
    token, _chat = _settings()

    async def go() -> dict[str, Any]:
        async with telegram.Bot(token) as bot:
            me = await bot.get_me()
            return {"username": me.username, "name": me.first_name}

    try:
        return _run(go())
    except TelegramUnavailable:
        raise
    except Exception as error:  # noqa: BLE001
        raise TelegramUnavailable(f"Telegram did not accept the token: {_said(error)}") from error


def fetch_updates(offset: int | None, *, timeout: int = 0) -> list[dict[str, Any]]:
    """Button presses since `offset`, as plain dicts.

    Only presses are asked for: the bot has nothing to say to a typed
    message. `timeout` is Telegram's long poll - zero answers at once with
    whatever is there, a larger value holds the request open until something
    arrives or the time is up, which is how a loop waits without spinning.
    """
    telegram = _telegram_module()
    token, _chat = _settings()

    async def go() -> list[dict[str, Any]]:
        async with telegram.Bot(token) as bot:
            updates = await bot.get_updates(
                offset=offset, timeout=timeout, allowed_updates=["callback_query"],
            )
            found: list[dict[str, Any]] = []
            for update in updates:
                query = update.callback_query
                if query is None:
                    found.append({"update_id": update.update_id})
                    continue
                message = query.message
                found.append({
                    "update_id": update.update_id,
                    "callback": {
                        "id": query.id,
                        "data": query.data or "",
                        "from": {
                            "id": str(query.from_user.id),
                            "username": query.from_user.username or "",
                            "name": query.from_user.first_name or "",
                        },
                        "chat_id": str(message.chat_id) if message is not None else "",
                        "message_id": message.message_id if message is not None else None,
                    },
                })
            return found

    try:
        return _run(go())
    except TelegramUnavailable:
        raise
    except Exception as error:  # noqa: BLE001
        raise TelegramUnavailable(f"Telegram could not be read: {_said(error)}") from error


def settle_button(
    callback_id: str, *, chat_id: str, message_id: int | None, text: str, toast: str,
) -> None:
    """Answer a press and rewrite its message without the buttons.

    The toast is the short line Telegram shows over the chat; the text is
    what the message says from now on - the decision and who made it - so a
    decided post cannot be pressed twice and reads as decided.
    """
    telegram = _telegram_module()
    token, _chat = _settings()

    async def go() -> None:
        async with telegram.Bot(token) as bot:
            try:
                await bot.answer_callback_query(callback_id, text=toast[:200])
            except Exception:  # noqa: BLE001 - a stale press still gets its message rewritten
                pass
            if message_id is not None:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text[:MESSAGE_LIMIT],
                    parse_mode=telegram.constants.ParseMode.HTML, reply_markup=None,
                    disable_web_page_preview=True,
                )

    try:
        _run(go())
    except TelegramUnavailable:
        raise
    except Exception as error:  # noqa: BLE001
        raise TelegramUnavailable(f"Telegram refused the answer: {_said(error)}") from error


def edit_card(
    *,
    chat_id: str,
    message_id: int,
    buttons: list[list[Button]] | None = None,
    text: str | None = None,
) -> bool:
    """Change a card already in the chat, without sending another one.

    A held post that is frozen again - because its delivery failed, or
    because somebody skipped it and the next pass proposed it back - is the
    same post asking the same question, so it re-points the card that is
    already there rather than adding a second one. The buttons carry the new
    execution's id; the words are rewritten only when they have something new
    to say.

    Returns whether the card changed. False rather than raising when Telegram
    will not have it: a card that could not be edited is a card that is still
    there and still readable, and the post is held in the app either way.
    Telegram answers "message is not modified" when the edit is a no-op, which
    is a success that happens to have changed nothing.
    """
    if not chat_id or message_id is None:
        return False
    telegram = _telegram_module()
    token, _chat = _settings()
    markup = _markup(telegram, buttons)

    async def go() -> bool:
        async with telegram.Bot(token) as bot:
            if text is not None:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text[:MESSAGE_LIMIT],
                    parse_mode=telegram.constants.ParseMode.HTML, reply_markup=markup,
                    disable_web_page_preview=True,
                )
            else:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id, reply_markup=markup,
                )
            return True

    try:
        return bool(_run(go()))
    except Exception as error:  # noqa: BLE001 - an un-editable card is not a failure
        if "not modified" in str(error).lower():
            return True
        print(f"Telegram card not updated: {_said(error)}", flush=True)
        return False


# --- local state: where the poll left off, and who the bot is ------------------


def _state_path() -> Path:
    from trendrelay_api.tool_registry import PROJECT_ROOT  # noqa: PLC0415

    return PROJECT_ROOT / ".data" / "telegram" / "state.json"


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(changes: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {**_read_state(), **changes}
    path.write_text(json.dumps(state), encoding="utf-8")


def read_offset() -> int | None:
    """The update id to read from next, so a press is handled once."""
    value = _read_state().get("offset")
    return int(value) if isinstance(value, int) else None


def write_offset(offset: int) -> None:
    _write_state({"offset": int(offset)})


#: One identity refresh at a time, and whether one is running.
_REFRESH_LOCK = __import__("threading").Lock()
_refreshing = False

#: How long a remembered bot name and chat title are trusted before they are
#: asked for again. A bot is renamed about never; a page that asked Telegram
#: on every open would be paying a network round trip to learn nothing.
IDENTITY_TTL_SECONDS = 60 * 60


#: How long the identity probe may take. It runs while a page is being drawn,
#: so it is bounded rather than left to the library's own generous defaults:
#: an unreachable Telegram must cost the campaigns list a moment, not a stall.
PROBE_CONNECT_SECONDS = 3.0
PROBE_READ_SECONDS = 4.0


def _ask_identity() -> dict[str, Any]:
    """Who the bot is and where it posts, as Telegram says right now."""
    telegram = _telegram_module()
    token, chat = _settings()
    bounded = {
        "connect_timeout": PROBE_CONNECT_SECONDS,
        "read_timeout": PROBE_READ_SECONDS,
        "pool_timeout": PROBE_CONNECT_SECONDS,
    }

    async def go() -> dict[str, Any]:
        async with telegram.Bot(token) as bot:
            me = await bot.get_me(**bounded)
            room = await bot.get_chat(chat, **bounded)
            title = room.title or " ".join(
                part for part in (room.first_name, room.last_name) if part
            ) or (f"@{room.username}" if room.username else str(chat))
            return {
                "bot_username": me.username or "",
                "bot_name": me.first_name or "",
                "chat_title": title,
                "chat_type": room.type or "",
            }

    try:
        return _run(go())
    except TelegramUnavailable:
        raise
    except Exception as error:  # noqa: BLE001
        raise TelegramUnavailable(f"Telegram could not be asked who the bot is: {_said(error)}") from error


def identity(*, refresh: bool = False) -> dict[str, Any] | None:
    """The bot's name and the chat's title, remembered between asks.

    Keyed to the token and chat they were learned for, so changing either on
    the card does not show the old bot's name beside the new one's token. None
    when Telegram is not set up, or has not answered yet and could not now.
    """
    import time  # noqa: PLC0415

    saved = configured_keys((BOT_TOKEN_ENV, CHAT_ID_ENV))
    if not (saved[BOT_TOKEN_ENV] and saved[CHAT_ID_ENV]):
        return None
    token = (effective_value(BOT_TOKEN_ENV) or "").strip()
    chat = configured_chat_id()
    key = f"{token[: token.find(':')]}:{chat}"
    remembered = _read_state().get("identity")
    for_this_pair = isinstance(remembered, dict) and remembered.get("for") == key
    fresh = (
        for_this_pair
        and time.time() - float(remembered.get("checked_at") or 0) < IDENTITY_TTL_SECONDS
    )
    if fresh and not refresh:
        return remembered
    if for_this_pair and not refresh:
        # Stale but known: answer from memory and go and check behind the
        # page. A bot is renamed about never, and nothing on the screen is
        # worth waiting on Telegram for - which is what this did, once an
        # hour, in the middle of drawing the campaigns list.
        _refresh_in_background(key)
        return remembered
    try:
        learned = _ask_identity()
    except TelegramUnavailable:
        return remembered if for_this_pair else None
    learned.update({"for": key, "checked_at": time.time()})
    _write_state({"identity": learned})
    return learned


def _refresh_in_background(key: str) -> None:
    """Learn the names again without anybody waiting for them.

    One thread at a time, and it takes the same bounded probe: a check
    nobody is waiting for must not pile up threads against an unreachable
    Telegram either.
    """
    import threading  # noqa: PLC0415

    global _refreshing
    with _REFRESH_LOCK:
        if _refreshing:
            return
        _refreshing = True

    def learn() -> None:
        global _refreshing
        try:
            learned = _ask_identity()
        except Exception:  # noqa: BLE001 - nothing is waiting for the answer
            return
        finally:
            with _REFRESH_LOCK:
                _refreshing = False
        import time as clock  # noqa: PLC0415

        learned.update({"for": key, "checked_at": clock.time()})
        _write_state({"identity": learned})

    threading.Thread(target=learn, name="telegram-identity", daemon=True).start()


def connection_summary() -> dict[str, Any]:
    """What a settings screen says about Telegram: connected, and as whom.

    The names come from memory when there is one, so drawing a form costs
    nothing; Telegram is asked only when nothing is remembered for the saved
    token and chat, or the memory is an hour old.
    """
    status = provider_status()
    if not (status["configured"] and status["installed"]):
        return {"connected": False, "bot": "", "chat": "", "reason": status["reason"]}
    who = identity()
    if not who:
        return {
            "connected": False, "bot": "", "chat": "",
            "reason": "Telegram has not answered yet. Send the test carousel from Tools.",
        }
    return {
        "connected": True,
        "bot": f"@{who['bot_username']}" if who.get("bot_username") else who.get("bot_name", ""),
        "chat": who.get("chat_title", ""),
        "reason": "",
    }
