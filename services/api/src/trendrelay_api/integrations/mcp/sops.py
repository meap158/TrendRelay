"""Action-oriented operating procedures exposed to MCP callers.

The catalogue is intentionally filesystem-backed: adding a reviewed Markdown
file under ``docs/sops`` is enough to make a new procedure discoverable.  The
front matter is validated on every read so duplicate action names or malformed
entries fail visibly instead of sending an assistant ambiguous instructions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from trendrelay_api.tool_registry import PROJECT_ROOT

SOP_ROOT = PROJECT_ROOT / "docs" / "sops"
_REQUIRED_TEXT = ("id", "action", "title", "summary")


def _string_list(value: Any, field: str, path: Path) -> list[str]:
    if value is None:
        return []
    valid_items = isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value
    )
    if not valid_items:
        raise ValueError(f"{path}: {field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _selector(value: str) -> str:
    """Normalise an action supplied by a caller without making paths from it."""
    return value.strip().casefold().replace("_", "-").replace(" ", "-")


def _read(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError(f"{path}: SOP Markdown must begin with YAML front matter")
    front_matter, markdown = text[4:].split("\n---\n", 1)
    metadata = yaml.safe_load(front_matter)
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: SOP front matter must be a mapping")
    for field in _REQUIRED_TEXT:
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise ValueError(f"{path}: {field} must be a non-empty string")
    version = metadata.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"{path}: version must be a positive integer")
    body = markdown.strip()
    if not body:
        raise ValueError(f"{path}: SOP body cannot be empty")

    action = metadata["action"].strip()
    return {
        "id": metadata["id"].strip(),
        "action": action,
        "title": metadata["title"].strip(),
        "summary": metadata["summary"].strip(),
        "version": version,
        "tags": _string_list(metadata.get("tags"), "tags", path),
        "aliases": _string_list(metadata.get("aliases"), "aliases", path),
        "resource_uri": f"trendrelay://sops/{action}",
        "path": path.relative_to(SOP_ROOT).as_posix(),
        "markdown": body + "\n",
    }


def _entries() -> list[dict[str, Any]]:
    if not SOP_ROOT.is_dir():
        return []
    entries = [_read(path) for path in sorted(SOP_ROOT.rglob("*.md")) if path.name != "README.md"]
    owners: dict[str, str] = {}
    for entry in entries:
        for candidate in (entry["id"], entry["action"], *entry["aliases"]):
            key = _selector(candidate)
            if key in owners and owners[key] != entry["path"]:
                raise ValueError(
                    f"Duplicate SOP selector {candidate!r}: {owners[key]} and {entry['path']}"
                )
            owners.setdefault(key, entry["path"])
    return entries


def list_sops(action: str | None = None) -> list[dict[str, Any]]:
    """List SOP metadata, optionally resolving one action or alias."""
    entries = _entries()
    if action:
        wanted = _selector(action)
        entries = [entry for entry in entries if wanted in _selectors(entry)]
    return [{key: value for key, value in entry.items() if key != "markdown"} for entry in entries]


def get_sop(action: str) -> dict[str, Any]:
    """Return the procedure matching a canonical action, id, or alias."""
    wanted = _selector(action)
    for entry in _entries():
        if wanted in _selectors(entry):
            return entry
    available = ", ".join(entry["action"] for entry in _entries()) or "none"
    raise LookupError(f"No SOP matches action {action!r}. Available actions: {available}.")


def _selectors(entry: dict[str, Any]) -> set[str]:
    values = (entry["id"], entry["action"], *entry["aliases"])
    return {_selector(value) for value in values}


def catalogue_markdown() -> str:
    """A human- and model-readable index for MCP resource clients."""
    entries = list_sops()
    lines = [
        "# TrendRelay SOP catalog",
        "",
        "Choose the SOP matching the action you are about to perform, then read it before acting.",
        "",
    ]
    for entry in entries:
        lines.extend(
            [
                f"## {entry['title']}",
                "",
                f"- Action: `{entry['action']}`",
                f"- Version: {entry['version']}",
                f"- Resource: `{entry['resource_uri']}`",
                f"- Summary: {entry['summary']}",
                "",
            ]
        )
    if not entries:
        lines.append("No SOPs are installed.\n")
    return "\n".join(lines).rstrip() + "\n"
