"""Sanitized setup presentation for the Telegram approval channel.

The card in Tools is the whole configuration: install the library, save the
bot token and the chat, send one test carousel. Modelled on the hosted-key
cards (ElevenLabs, Pexels) for the settings, and on yt-dlp for the install -
the library goes into a runtime of its own rather than the API's environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trendrelay_api import tool_settings
from trendrelay_api.config import get_settings
from trendrelay_api.env_store import masked_value
from trendrelay_api.integrations.telegram import (
    BOT_TOKEN_ENV,
    CHAT_ID_ENV,
    TOOL_ID,
    TelegramUnavailable,
    probe,
    provider_status,
    send_card,
)


def _requirement(
    identifier: str, label: str, status: str, detail: str
) -> dict[str, str]:
    return {"id": identifier, "label": label, "status": status, "detail": detail}


def _app_link_works() -> bool:
    """Whether a card can carry a button back to the app.

    The same test the cards apply, asked here so the answer is on the setup
    screen rather than discovered as a button that is quietly never there.
    """
    from trendrelay_api.approval_notices import app_link

    return app_link("any") is not None


def setup_report() -> dict[str, Any]:
    """What is installed and saved, and what it turns on."""
    status = provider_status()
    saved_token = bool(masked_value(BOT_TOKEN_ENV))
    saved_chat = bool(masked_value(CHAT_ID_ENV))
    configured_names = [
        name for name, saved in ((BOT_TOKEN_ENV, saved_token), (CHAT_ID_ENV, saved_chat)) if saved
    ]
    return {
        "summary": (
            "Sends each campaign post that is waiting for approval to a Telegram "
            "chat as a card with the inbox's own buttons - approve, approve and "
            "post now, dismiss - so it can be decided from the phone. Optional, "
            "and campaign by campaign: the switch is in each campaign's approval "
            "inbox once this is set up, and nothing is sent for a campaign that "
            "has not switched it on. Whoever is in the chat can press, narrowed "
            "by the approvers list below."
        ),
        "requirements": [
            _requirement(
                "library",
                "python-telegram-bot installed",
                "ready" if status["installed"] else "setup-required",
                "Installed into its own runtime from this page."
                if status["installed"]
                else "Install it above. It goes into a runtime of its own, not "
                "the API's environment.",
            ),
            _requirement(
                "bot-token",
                "Bot token saved",
                "ready" if saved_token else "setup-required",
                f"{BOT_TOKEN_ENV} is set; its value never leaves the API process."
                if saved_token
                else "Make a bot with @BotFather in Telegram and paste the token "
                "it gives you below.",
            ),
            _requirement(
                "chat",
                "Chat saved",
                "ready" if saved_chat else "setup-required",
                f"{CHAT_ID_ENV} is set. Messages go to this chat."
                if saved_chat
                else "The chat the messages go to. Write to your bot once, or "
                "add it to a group, then read the chat id - the notes say how.",
            ),
            _requirement(
                "app-link",
                "Link back to the app",
                "ready" if _app_link_works() else "optional",
                "PUBLIC_WEB_URL is an address a phone can open, so each card "
                "carries an Open in app button."
                if _app_link_works()
                else "PUBLIC_WEB_URL is "
                f"{get_settings().public_web_url or 'not set'}, which a phone "
                "cannot open, so the cards carry no Open in app button. "
                "Telegram refuses a button to an address like that - and the "
                "message with it - so it is left off rather than risked. "
                "Approving and dismissing work either way.",
            ),
        ],
        "configured_secret_names": configured_names,
        "supported_secret_names": [BOT_TOKEN_ENV, CHAT_ID_ENV],
        "secret_previews": {
            name: masked_value(name) for name in configured_names
        },
        "actions": [
            {
                "id": "send-test",
                "label": "Send a test carousel",
                "kind": "local-launch",
                # It reaches Telegram, so it asks first - the same rule every
                # outward action on this page follows.
                "requires_confirmation": True,
            },
            {
                "id": "open-campaigns",
                "label": "Open Campaigns",
                "kind": "navigate",
                "href": "/campaigns",
            },
        ],
        "settings": tool_settings.fields_for(TOOL_ID),
        "settings_title": "Telegram bot",
        "settings_blurb": (
            "Saved to this machine's local .env and masked here afterwards. "
            "Send a test carousel to check the token and the chat together, and to see what a held post looks like there."
        ),
    }


def _sample_pictures(scratch: Path) -> list[Path]:
    """Three plain pictures in a carousel's shape, made with ffmpeg.

    The test should look like the thing it is a test of - a carousel post
    arriving as an album - and not like a line of text, so the eye learns
    where the pictures sit before a real one arrives.
    """
    import subprocess  # noqa: PLC0415

    from trendrelay_api.integrations.openmontage_runtime import FFMPEG  # noqa: PLC0415

    made: list[Path] = []
    for index, colour in enumerate(("0x2563eb", "0x16a34a", "0xdc2626"), start=1):
        target = scratch / f"sample-{index}.png"
        subprocess.run(
            [
                str(FFMPEG), "-y", "-v", "error", "-f", "lavfi",
                "-i", f"color=c={colour}:s=1080x1350:d=1", "-frames:v", "1", str(target),
            ],
            capture_output=True, check=False, timeout=60,
        )
        if target.is_file():
            made.append(target)
    return made


def launch_action(action_id: str) -> dict[str, Any]:
    """The card's one outward action: prove the token and the chat work.

    Sends what a held carousel will look like - an album of three sample
    pictures, then the card with the inbox's buttons - so the test is the
    flow, not a line saying the flow exists. The buttons answer that this
    was the test, and decide nothing.
    """
    import tempfile  # noqa: PLC0415

    from trendrelay_api import approval_notices  # noqa: PLC0415

    if action_id != "send-test":
        raise KeyError(f"{TOOL_ID}:{action_id}")
    try:
        who = probe()
        with tempfile.TemporaryDirectory(prefix="telegram-test-") as scratch:
            pictures = _sample_pictures(Path(scratch))
            outcome = send_card(
                approval_notices.card_text(
                    "Test campaign",
                    {
                        "destination": "Instagram · test account",
                        "caption": (
                            "This is what a held post looks like here: the pictures "
                            "above, the caption, and why it waits. Press any button - "
                            "on this card they only answer."
                        ),
                        "at": None,
                        "reason": "Test card from Tools. Nothing is waiting.",
                    },
                ),
                buttons=approval_notices.card_buttons("test", "test", test=True),
                images=pictures,
            )
    except TelegramUnavailable as error:
        raise RuntimeError(str(error)) from error
    pictures_sent = outcome.get("media", 0)
    return {
        "status": "ok",
        "message": (
            f"Sent as @{who['username']}: a test carousel of {pictures_sent} "
            f"picture{'' if pictures_sent == 1 else 's'} and its card. Check the chat."
            + (" Pictures left off: " + "; ".join(outcome["skipped"]) if outcome.get("skipped") else "")
        ),
    }
