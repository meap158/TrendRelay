"""Sanitized setup presentation for the hosted ElevenLabs integration."""

from __future__ import annotations

from typing import Any

from trendrelay_api import tool_settings
from trendrelay_api.env_store import masked_value
from trendrelay_api.integrations.elevenlabs import API_KEY_ENV, provider_status


def _requirement(
    identifier: str, label: str, status: str, detail: str
) -> dict[str, str]:
    return {"id": identifier, "label": label, "status": status, "detail": detail}


def setup_report() -> dict[str, Any]:
    """Report the saved key, reachability, and live character allowance."""
    status = provider_status(probe=True)
    configured = bool(status["configured"])
    requirements = [
        _requirement(
            "api-key",
            "API key saved",
            "ready" if configured else "setup-required",
            f"{API_KEY_ENV} is set; its value never leaves the API process."
            if configured
            else "Paste it below. The key is the switch: there is nothing to "
            "install and no activation separate from it.",
        ),
        _requirement(
            "reachable",
            "Key accepted",
            (
                "ready"
                if status["reachable"]
                else "setup-required"
                if configured
                else "optional"
            ),
            (
                f"Answering as the {status['tier']} plan."
                if status["reachable"]
                else str(status["reason"] or "Not checked without a key.")
            ),
        ),
    ]
    allowance = next(iter(status["allowances"]), None)
    if allowance:
        remaining = allowance["remaining"]
        requirements.append(
            _requirement(
                "characters",
                allowance["label"],
                "ready" if remaining is None or remaining > 0 else "blocked",
                (
                    f"{remaining:,} of {allowance['limit']:,} left."
                    if remaining is not None and allowance["limit"] is not None
                    else "Reported by the service."
                )
                + " Billed per character, so this is what a generation spends.",
            )
        )
    return {
        "summary": (
            "Hosted text-to-speech. Unlike the models in the Library this one is "
            "metered and the words are sent to a third party, so the allowance is "
            "read live rather than assumed."
        ),
        "requirements": requirements,
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
        "settings": tool_settings.fields_for("elevenlabs"),
        "settings_title": "ElevenLabs key",
        "settings_blurb": (
            "Saved to this machine's .env and never returned to the browser. "
            "Saving re-checks it against the service, so the rows above answer "
            "immediately."
        ),
        "elevenlabs": status,
    }
