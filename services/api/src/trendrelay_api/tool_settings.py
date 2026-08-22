"""Which tools own settings, and how each one reads and writes its own.

The Tools page already renders a settings form for any tool that declares
fields, and already posts them back to `/api/tools/{id}/settings`. Only the
route refused to carry anyone but the assistant tunnel, so a tool whose whole
configuration is one API key sent people to a text editor:

    Add ELEVENLABS_API_KEY to the local .env file.

That is the instruction a setup screen exists to replace. It is also the worst
of both worlds - the page knows the key is missing, knows its name, and knows
whether the service accepted it, and still hands the job over.

A provider here is three things: the fields, what is stored in them with
secrets masked, and a save that validates before it writes. The tunnel already
had all three; this names them as a contract so the second tool did not have
to invent one, and so the route can stop naming tools it knows about.
"""

from __future__ import annotations

from typing import Any, Protocol


class SettingsError(ValueError):
    """A setting was rejected before anything was written."""


class SettingsProvider(Protocol):
    """What a tool must offer to be configurable from its own card."""

    def fields(self) -> list[dict[str, Any]]:
        """The form, with what is stored in it and secrets described."""

    def save(self, values: dict[str, str]) -> list[str]:
        """Validate everything, write it, and name what was written."""

    def reveal(self, key: str) -> str:
        """Return one declared secret after the API has authorized disclosure."""


class _Tunnel:
    """The assistant tunnel, which already had the shape."""

    def fields(self) -> list[dict[str, Any]]:
        from trendrelay_api.integrations.mcp import tunnel

        return tunnel.settings_view()

    def save(self, values: dict[str, str]) -> list[str]:
        from trendrelay_api.integrations.mcp import tunnel

        try:
            return tunnel.save_settings(values)
        except tunnel.TunnelSettingsError as error:
            raise SettingsError(str(error)) from error

    def reveal(self, key: str) -> str:
        raise SettingsError("This tool does not expose saved secrets in the setup UI.")


#: What a hosted service needs before it can be asked anything: its key.
#:
#: Declared rather than assumed, so the form, the validation and the mask all
#: read from one place - the pair that drifts apart is how a field ends up
#: accepted by the form and refused by the thing it configures.
ELEVENLABS_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "ELEVENLABS_API_KEY",
        "label": "API key",
        "kind": "text",
        "secret": True,
        "required": True,
        "help": (
            "From elevenlabs.io → Profile → API key. It is the whole switch: "
            "there is nothing to install and no activation apart from it."
        ),
        "help_url": "https://elevenlabs.io/app/settings/api-keys",
    },
    {
        "key": "ELEVENLABS_TTS_VOICE_ID",
        "label": "Default voice ID",
        "kind": "text",
        "secret": False,
        "required": False,
        "placeholder": "Choose per clip when left empty",
        "help": "The voice Library selects first. A per-take choice still wins.",
    },
    {
        "key": "ELEVENLABS_TTS_MODEL_ID",
        "label": "Default speech model",
        "kind": "choice",
        "secret": False,
        "required": True,
        "options": ["eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_turbo_v2_5"],
        "default": "eleven_multilingual_v2",
        "help": "Library starts with this model when the key can use it.",
    },
    {
        "key": "ELEVENLABS_TTS_LANGUAGE_CODE",
        "label": "Default spoken language",
        "kind": "text",
        "secret": False,
        "required": False,
        "placeholder": "Auto-detect",
        "help": "Optional ISO 639-1 code. A transcript or per-take choice can override it.",
    },
    {
        "key": "ELEVENLABS_TTS_STABILITY",
        "label": "Default stability",
        "kind": "number",
        "secret": False,
        "required": True,
        "min": 0,
        "max": 1,
        "step": 0.05,
        "default": "0.5",
        "help": "Lower is more expressive; higher is more consistent.",
    },
    {
        "key": "ELEVENLABS_TTS_SIMILARITY_BOOST",
        "label": "Default similarity",
        "kind": "number",
        "secret": False,
        "required": True,
        "min": 0,
        "max": 1,
        "step": 0.05,
        "default": "0.75",
        "help": "How closely speech should follow the selected voice.",
    },
    {
        "key": "ELEVENLABS_TTS_STYLE",
        "label": "Default style",
        "kind": "number",
        "secret": False,
        "required": True,
        "min": 0,
        "max": 1,
        "step": 0.05,
        "default": "0",
        "help": "Style exaggeration for models that support it.",
    },
    {
        "key": "ELEVENLABS_TTS_SPEED",
        "label": "Default speed",
        "kind": "number",
        "secret": False,
        "required": True,
        "min": 0.7,
        "max": 1.2,
        "step": 0.05,
        "default": "1",
        "help": "1 is the original pace; lower is slower and higher is faster.",
    },
    {
        "key": "ELEVENLABS_TTS_SPEAKER_BOOST",
        "label": "Speaker boost",
        "kind": "choice",
        "secret": False,
        "required": True,
        "options": ["on", "off"],
        "default": "on",
        "help": "Keep the generated take closer to the original voice where supported.",
    },
    {
        "key": "MEDIA_AI_SPEECH_PROVIDER",
        "label": "Transcription provider",
        "kind": "choice",
        "secret": False,
        "required": True,
        "options": ["faster-whisper", "elevenlabs-scribe"],
        "default": "faster-whisper",
        "help": "Local keeps media on this machine. Scribe uploads it to ElevenLabs on request.",
    },
    {
        "key": "ELEVENLABS_STT_MODEL_ID",
        "label": "Scribe model",
        "kind": "choice",
        "secret": False,
        "required": True,
        "options": ["scribe_v2", "scribe_v1"],
        "default": "scribe_v2",
        "help": "Scribe v2 is the recommended hosted transcription model.",
    },
    {
        "key": "ELEVENLABS_STT_DIARIZE",
        "label": "Identify speakers",
        "kind": "choice",
        "secret": False,
        "required": True,
        "options": ["off", "on"],
        "default": "off",
        "help": "Adds speaker IDs to timed words. Enable only when the distinction is useful.",
    },
    {
        "key": "ELEVENLABS_STT_TAG_AUDIO_EVENTS",
        "label": "Tag audio events",
        "kind": "choice",
        "secret": False,
        "required": True,
        "options": ["on", "off"],
        "default": "on",
        "help": "Includes events such as laughter and music in the machine draft.",
    },
)


class _ElevenLabs:
    def fields(self) -> list[dict[str, Any]]:
        from trendrelay_api.env_store import effective_value, masked_value

        described: list[dict[str, Any]] = []
        for field in ELEVENLABS_FIELDS:
            stored = (effective_value(field["key"]) or "").strip()
            described.append({
                **field,
                "configured": bool(stored),
                # A secret is described, never returned - the same rule the
                # tunnel's form follows, and the reason this page can be read
                # over somebody's shoulder.
                "value": "" if field["secret"] else stored or field.get("default", ""),
                "preview": masked_value(field["key"]) if stored else None,
            })
        return described

    def save(self, values: dict[str, str]) -> list[str]:
        from trendrelay_api.env_store import write_env_values

        allowed = {field["key"] for field in ELEVENLABS_FIELDS}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise SettingsError(f"Not an ElevenLabs setting: {unknown[0]}.")
        by_key = {field["key"]: field for field in ELEVENLABS_FIELDS}
        cleaned: dict[str, str] = {}
        for key, raw in values.items():
            value = str(raw).strip()
            # Long enough to be a key, and one token. Anything more specific
            # would be a guess about a format the service can change, and the
            # real check is the one that follows: the card probes the service
            # with it and reports what came back.
            if key == "ELEVENLABS_API_KEY" and value and (
                len(value) < 20 or any(ch.isspace() for ch in value)
            ):
                raise SettingsError(
                    "That does not look like an API key. Copy the whole value "
                    "from elevenlabs.io → Profile → API key."
                )
            field = by_key[key]
            if field["kind"] == "choice" and value not in field["options"]:
                raise SettingsError(f"Choose one of the offered values for {field['label']}.")
            if field["kind"] == "number":
                try:
                    number = float(value)
                except ValueError as error:
                    raise SettingsError(f"{field['label']} must be a number.") from error
                if not field["min"] <= number <= field["max"]:
                    raise SettingsError(
                        f"{field['label']} must be between {field['min']} and {field['max']}."
                    )
                value = f"{number:g}"
            cleaned[key] = value
        return write_env_values(cleaned)

    def reveal(self, key: str) -> str:
        """Reveal only a field explicitly declared as a secret by this provider."""
        from trendrelay_api.env_store import effective_value

        field = next((item for item in ELEVENLABS_FIELDS if item["key"] == key), None)
        if field is None or not field["secret"]:
            raise SettingsError("That setting is not an exposable secret.")
        value = (effective_value(key) or "").strip()
        if not value:
            raise SettingsError("No saved value is available for that secret.")
        return value


#: Tool id to its settings. A tool absent here has none, which is the honest
#: answer for a model that is configured by being downloaded.
PROVIDERS: dict[str, SettingsProvider] = {
    "mcp-server": _Tunnel(),
    "elevenlabs": _ElevenLabs(),
}


def provider_for(tool_id: str) -> SettingsProvider | None:
    return PROVIDERS.get(tool_id)


def fields_for(tool_id: str) -> list[dict[str, Any]]:
    """A tool's settings form, or nothing where it has none.

    The whole join between a tool's card and its configuration: a card asks by
    name and gets a form or an empty list, so adding a key-based tool is a
    provider here and one line there rather than a form written twice.
    """
    provider = provider_for(tool_id)
    return provider.fields() if provider else []
