"""Sanitized setup presentation for the hosted ElevenLabs integration."""

from __future__ import annotations

from typing import Any

from trendrelay_api import tool_settings
from trendrelay_api.env_store import masked_value
from trendrelay_api.integrations.elevenlabs import API_KEY_ENV, defaults, provider_status


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
    configured_defaults = defaults()
    stt = configured_defaults["transcription"]
    requirements.extend([
        _requirement(
            "voice-defaults",
            "Voice defaults",
            "ready" if configured_defaults["voice_id"] else "optional",
            (
                f"{configured_defaults['voice_id']} · {configured_defaults['model_id']}"
                if configured_defaults["voice_id"]
                else f"{configured_defaults['model_id']} · choose a voice per clip"
            ),
        ),
        _requirement(
            "transcription-route",
            "Library transcription",
            "ready" if stt["provider"] == "elevenlabs-scribe" and configured else "optional",
            (
                f"Scribe is selected ({stt['model_id']}); requested media is uploaded and "
                "returns a timed machine draft for review."
                if stt["provider"] == "elevenlabs-scribe"
                else "Local faster-whisper remains selected. Choose Scribe below to opt in."
            ),
        ),
        _requirement(
            "caption-boundary",
            "Captions and subtitles",
            "ready",
            "Reviewed speech feeds the existing subtitle preview, translation, sidecar, "
            "and burn-in pipeline regardless of which transcription provider made the draft.",
        ),
    ])
    return {
        "summary": (
            "Hosted voice generation and optional Scribe transcription. Text or media "
            "is sent only for the action you choose; machine transcripts still require "
            "review before they can become captions or a voiceover script."
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
        "settings_title": "Voice and transcription defaults",
        "settings_blurb": (
            "The API key stays masked. Non-secret defaults are saved here, copied into "
            "each job for reliable resumes, and remain editable per clip in Library."
        ),
        "elevenlabs": status,
    }
