"""Action-oriented operating procedures exposed to MCP callers.

The catalogue is intentionally filesystem-backed: adding a reviewed Markdown
file under ``SOP`` is enough to make a new procedure discoverable.  The
front matter is validated on every read so duplicate action names or malformed
entries fail visibly instead of sending an assistant ambiguous instructions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from trendrelay_api.tool_registry import PROJECT_ROOT

SOP_ROOT = PROJECT_ROOT / "SOP"
MCP_GUIDE_PATH = SOP_ROOT / "MCP_GUIDE.md"
_CATALOG_RESERVED = frozenset({"README.md", MCP_GUIDE_PATH.name})
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
    entries = [
        _read(path)
        for path in sorted(SOP_ROOT.rglob("*.md"))
        if path.name not in _CATALOG_RESERVED
    ]
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
    return [
        {key: value for key, value in entry.items() if key != "markdown"}
        for entry in entries
    ]


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


#: How much of a procedure one fetch returns by default.
#:
#: Chosen so every SOP written so far comes back whole in a single call: paging
#: is here for the document that outgrows a client's tool-result budget, not as
#: a toll on the ones that fit. A procedure read in half is worse than one not
#: read at all, because the half that arrived looks complete.
SOP_PAGE_CHARACTERS = 20_000

#: The most one call will return, whatever a caller asks for.
MAX_SOP_PAGE_CHARACTERS = 60_000


def _page_end(text: str, budget: int) -> int:
    """Where a page should stop so it stops somewhere a reader would.

    Preferring a section heading, then a paragraph, then a line - and never
    mid-word. A page that ends halfway through a rule is a page whose last
    instruction is a fragment, and a fragment of a rule reads as a whole one.

    The index returned is where the *next* page begins, so concatenating every
    page reproduces the document exactly.
    """
    if len(text) <= budget:
        return len(text)
    window = text[:budget]
    # Well past halfway, or the break is worse than the budget it saved.
    floor = budget // 2
    for marker in ("\n## ", "\n\n", "\n"):
        cut = window.rfind(marker)
        if cut > floor:
            return cut + 1
    return budget


def get_sop_page(
    action: str, offset: int = 0, limit: int | None = None
) -> dict[str, Any]:
    """One procedure, in pages a caller can always finish.

    The same `offset`/`more`/`next_offset` contract the queue tools use, so a
    caller that can page one can page the other. The paging fields are present
    even when everything fits, so the contract is visible from a single-page
    read rather than discovered on the first document long enough to need it.
    """
    entry = dict(get_sop(action))
    markdown = entry["markdown"]
    total = len(markdown)
    budget = min(limit or SOP_PAGE_CHARACTERS, MAX_SOP_PAGE_CHARACTERS)
    if budget < 1:
        raise ValueError("A page must be at least one character.")
    start = max(0, min(offset, total))
    end = start + _page_end(markdown[start:], budget)

    entry["markdown"] = markdown[start:end]
    entry["offset"] = start
    entry["characters"] = total
    entry["more"] = end < total
    entry["next_offset"] = end if end < total else None
    if entry["more"]:
        # Said in the payload rather than left to the caller to infer from two
        # numbers, because the failure this guards against is a caller that
        # reads one page and believes it read the procedure.
        entry["note"] = (
            f"This is characters {start:,}-{end:,} of {total:,}. "
            f"Call get_sop again with offset={end} for the rest; "
            "do not act on a partial procedure."
        )
    return entry


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


def _guidance_file(path: Path, label: str) -> str:
    """Read a canonical MCP guidance file, failing clearly if it was omitted."""
    if not path.is_file():
        raise FileNotFoundError(f"The canonical {label} file is missing: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"The canonical {label} file is empty: {path}")
    return content + "\n"


def mcp_guide_markdown() -> str:
    return _guidance_file(MCP_GUIDE_PATH, "MCP guide")


def server_instructions(base: str) -> str:
    """Initialization guidance read from the same file MCP resources expose."""
    return "\n\n".join([base.strip(), mcp_guide_markdown().strip()])
