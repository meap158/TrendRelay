"""Local .env reader/writer for operator-supplied provider credentials.

Values are only ever written; they are never returned to a caller. Callers
receive configured booleans so the interface can show setup state without
exposing a secret.
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


def env_file_path() -> Path:
    return ENV_PATH
