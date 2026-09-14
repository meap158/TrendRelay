"""Sanitized setup presentation for the Telegram approval channel.

The card in Tools is the whole configuration: install the library, save the
bot token and the chat, send one test message. Modelled on the hosted-key
cards (ElevenLabs, Pexels) for the settings, and on yt-dlp for the install -
the library goes into a runtime of its own rather than the API's environment.
"""

from __future__ import annotations

from typing import Any

from trendrelay_api import tool_settings
from trendrelay_api.env_store import masked_value
from trendrelay_api.integrations.telegram import (
    BOT_TOKEN_ENV,
    CHAT_ID_ENV,
    TOOL_ID,
    TelegramUnavailable,
    probe,
    provider_status,
    send_message,
)


def _requirement(
    identifier: str, label: str, status: str, detail: str
) -> dict[str, str]:
    return {"id": identifier, "label": label, "status": status, "detail": detail}


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
        ],
        "configured_secret_names": configured_names,
        "supported_secret_names": [BOT_TOKEN_ENV, CHAT_ID_ENV],
        "secret_previews": {
            name: masked_value(name) for name in configured_names
        },
        "actions": [
            {
                "id": "send-test",
                "label": "Send a test message",
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
            "Send a test message to check the token and the chat together."
        ),
    }


def launch_action(action_id: str) -> dict[str, Any]:
    """The card's one outward action: prove the token and the chat work."""
    if action_id != "send-test":
        raise KeyError(f"{TOOL_ID}:{action_id}")
    try:
        who = probe()
        send_message(
            "TrendRelay can reach this chat. Campaign posts that need approval "
            "will arrive here as cards, with buttons to approve or dismiss them."
        )
    except TelegramUnavailable as error:
        raise RuntimeError(str(error)) from error
    return {
        "status": "ok",
        "message": f"Sent as @{who['username']}. Check the chat.",
    }
