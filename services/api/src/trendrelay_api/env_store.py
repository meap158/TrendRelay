"""Local .env reader/writer for operator-supplied provider credentials.

Values are written, and read back only in two deliberate shapes.

`masked_value` is what a screen shows: the last few characters, enough to tell
one saved key from another and to see that the right one is in place, and not
enough to use. It is safe in an ordinary status payload.

`effective_value` returns the secret itself and is for the code that calls the
engine. Anything handing it to a browser must gate it the way a mutation is
gated - loopback, role, explicit confirmation - and must restrict which keys can
be asked for, or the same endpoint reads every secret in the file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from trendrelay_api.config import refresh_settings
from trendrelay_api.tool_registry import PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class EnvWriteError(RuntimeError):
    """Raised when the local .env file cannot be updated safely."""


def _split_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.removeprefix("export ").split("=", 1)
    return key.strip(), value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    if value == "" or re.fullmatch(r"[A-Za-z0-9_.:/@+,=-]*", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_env_file() -> dict[str, str]:
    """Return the raw key/value pairs currently stored in the local .env file."""
    if not ENV_PATH.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        pair = _split_line(raw_line)
        if pair:
            values[pair[0]] = _unquote(pair[1])
    return values


def effective_value(key: str) -> str:
    """Resolve a key from the process environment first, then the .env file."""
    from_environment = os.environ.get(key)
    if from_environment:
        return from_environment
    return read_env_file().get(key, "")


def configured_keys(keys: tuple[str, ...]) -> dict[str, bool]:
    """Report which of the requested keys hold a non-empty value."""
    stored = read_env_file()
    return {key: bool(os.environ.get(key) or stored.get(key)) for key in keys}


#: How much of a saved secret a masked preview keeps. Four is what a payment
#: form or an API dashboard shows, and it is enough to recognise which key is in
#: place without being enough to use one.
MASK_TAIL = 4


def masked_value(key: str) -> str | None:
    """A saved value with everything but its last few characters hidden.

    None when nothing is saved, which a caller shows as "not set" rather than as
    an empty secret.

    A value too short to mask keeps none of itself. Showing the last four of a
    six-character secret would give away most of it, and the point of the tail is
    recognition, not verification.
    """
    value = effective_value(key)
    if not value:
        return None
    if len(value) <= MASK_TAIL * 2:
        return "•" * len(value)
    return "•" * (len(value) - MASK_TAIL) + value[-MASK_TAIL:]


def write_env_values(values: dict[str, str]) -> list[str]:
    """Update the local .env file in place and refresh the cached settings.

    Existing lines keep their position and surrounding comments; unknown keys
    are appended. Returns the keys that were written.
    """
    invalid = [key for key in values if not KEY_PATTERN.fullmatch(key)]
    if invalid:
        names = ", ".join(sorted(invalid))
        raise EnvWriteError(f"Refusing to write unsupported .env keys: {names}")
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise EnvWriteError(f"{key} must be a single line.")

    existing_text = ENV_PATH.read_text(encoding="utf-8-sig") if ENV_PATH.is_file() else ""
    lines = existing_text.splitlines()
    remaining = dict(values)

    for index, raw_line in enumerate(lines):
        pair = _split_line(raw_line)
        if not pair or pair[0] not in remaining:
            continue
        key = pair[0]
        lines[index] = f"{key}={_quote(remaining.pop(key))}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        for key, value in remaining.items():
            lines.append(f"{key}={_quote(value)}")

    temporary = ENV_PATH.with_suffix(".env.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(ENV_PATH)

    for key, value in values.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    refresh_settings()
    return sorted(values)


def remove_env_values(keys: tuple[str, ...]) -> list[str]:
    """Delete keys from the local .env file outright.

    Writing an empty value would do for reading - an empty key is not
    configured - but it leaves a line behind for a thing that no longer exists.
    This file is meant to be read by hand, and a login that was removed a month
    ago should not still have a row in it.

    Only whole lines are removed, so surrounding comments and ordering survive.
    """
    invalid = [key for key in keys if not KEY_PATTERN.fullmatch(key)]
    if invalid:
        names = ", ".join(sorted(invalid))
        raise EnvWriteError(f"Refusing to touch unsupported .env keys: {names}")
    if not ENV_PATH.is_file():
        return []

    wanted = set(keys)
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
    kept, removed = [], []
    for raw_line in lines:
        pair = _split_line(raw_line)
        if pair and pair[0] in wanted:
            removed.append(pair[0])
            continue
        kept.append(raw_line)

    if removed:
        temporary = ENV_PATH.with_suffix(".env.tmp")
        temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
        temporary.replace(ENV_PATH)
    for key in wanted:
        os.environ.pop(key, None)
    refresh_settings()
    return sorted(removed)


def env_file_path() -> Path:
    return ENV_PATH
