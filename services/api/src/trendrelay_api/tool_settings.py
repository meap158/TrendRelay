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
                "value": "",
                "preview": masked_value(field["key"]) if stored else None,
            })
        return described

    def save(self, values: dict[str, str]) -> list[str]:
        from trendrelay_api.env_store import write_env_values

        allowed = {field["key"] for field in ELEVENLABS_FIELDS}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise SettingsError(f"Not an ElevenLabs setting: {unknown[0]}.")
        cleaned: dict[str, str] = {}
        for key, raw in values.items():
            value = str(raw).strip()
            # Long enough to be a key, and one token. Anything more specific
            # would be a guess about a format the service can change, and the
            # real check is the one that follows: the card probes the service
            # with it and reports what came back.
            if value and (len(value) < 20 or any(ch.isspace() for ch in value)):
                raise SettingsError(
                    "That does not look like an API key. Copy the whole value "
                    "from elevenlabs.io → Profile → API key."
                )
            cleaned[key] = value
        return write_env_values(cleaned)


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
