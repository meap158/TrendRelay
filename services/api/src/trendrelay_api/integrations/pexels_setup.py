"""Sanitized setup presentation for the hosted Pexels integration.

The card in Tools is the whole configuration: one key, described rather than
returned, and a line saying what it unlocks. Modelled on the ElevenLabs card
because it is the same kind of tool - nothing to install, a hosted service
where the key is the switch.

Deliberately does not probe. A card that spent a search on every page load
would run the rate limit down to tell somebody what the saved key already
says; whether the key works is answered by the first real search, which
reports what Pexels said.
"""

from __future__ import annotations

from typing import Any

from trendrelay_api import tool_settings
from trendrelay_api.env_store import masked_value
from trendrelay_api.integrations.pexels import API_KEY_ENV, provider_status


def _requirement(
    identifier: str, label: str, status: str, detail: str
) -> dict[str, str]:
    return {"id": identifier, "label": label, "status": status, "detail": detail}


def setup_report() -> dict[str, Any]:
    """What is saved, and what it turns on."""
    status = provider_status()
    configured = bool(status["configured"])
    return {
        "summary": (
            "Free stock photos and clips for a script that has no pictures of "
            "its own. Searched from Storytelling in the Library; what is chosen "
            "is filed through the Library's own ingest, with the photographer's "
            "credit stored on the asset as the licence asks."
        ),
        "requirements": [
            _requirement(
                "api-key",
                "API key saved",
                "ready" if configured else "setup-required",
                f"{API_KEY_ENV} is set; its value never leaves the API process."
                if configured
                else "Paste it below. It is free from pexels.com/api, and it is "
                "the whole switch - there is nothing to install.",
            ),
            _requirement(
                "attribution",
                "Attribution",
                "ready",
                "Carried automatically: the credit is shown on every search "
                "result and stored on whatever is imported. Media may not be "
                "sold unaltered, and people in it may not be shown as endorsing "
                "a product.",
            ),
        ],
        "configured_secret_names": [API_KEY_ENV] if configured else [],
        "supported_secret_names": [API_KEY_ENV],
        "secret_previews": (
            {API_KEY_ENV: masked_value(API_KEY_ENV)} if configured else {}
        ),
        "actions": [
            {
                "id": "open-library",
                "label": "Open Library",
                "kind": "navigate",
                "href": "/library",
            }
        ],
        "settings": tool_settings.fields_for("pexels"),
        "settings_title": "Pexels API key",
        "settings_blurb": (
            "Saved to this machine's local .env and masked here afterwards. "
            "Whether it works is answered by the first search rather than by a "
            "check that spends a request to say so."
        ),
    }
