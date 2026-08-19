"""The notes a tool ships with, which the Docs link had nothing to open.

The catalogue records `documentation` as a repository path. The web app never
served the checkout, so every Docs link on the Tools tab was a 404 with the
file sitting there all along.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trendrelay_api.tool_registry import PROJECT_ROOT, documentation_for


def test_a_tool_with_notes_returns_them() -> None:
    doc = documentation_for("mcp-server")

    assert doc["path"] == "docs/third-party/mcp.md"
    assert doc["markdown"].startswith("# Assistant Access (MCP)")


def test_every_documented_tool_points_at_a_file_that_exists() -> None:
    """A path in the catalogue is a promise the Tools tab makes on every row."""
    catalog = json.loads(
        (PROJECT_ROOT / "config" / "tool-catalog.json").read_text(encoding="utf-8")
    )
    tools = catalog if isinstance(catalog, list) else catalog.get("tools", [])
    missing = [
        f"{tool['id']} -> {tool['documentation']}"
        for tool in tools
        if tool.get("documentation")
        and not (PROJECT_ROOT / str(tool["documentation"])).is_file()
    ]

    assert missing == [], f"documentation paths that are not in the checkout: {missing}"


def test_an_unknown_tool_is_not_found() -> None:
    with pytest.raises(KeyError):
        documentation_for("no-such-tool")


def test_a_path_outside_the_docs_directory_is_refused(monkeypatch) -> None:
    """The catalogue is a file, not a request - and still not a licence to read
    anything on disk. One edit away is not far enough."""
    from trendrelay_api import tool_registry

    monkeypatch.setattr(
        tool_registry,
        "_catalog",
        lambda: [{"id": "escapee", "documentation": "../../../etc/passwd"}],
    )

    with pytest.raises(FileNotFoundError):
        documentation_for("escapee")


def test_a_documented_path_that_is_not_markdown_is_refused(monkeypatch) -> None:
    from trendrelay_api import tool_registry

    monkeypatch.setattr(
        tool_registry,
        "_catalog",
        lambda: [{"id": "sneaky", "documentation": "docs/../config/tool-catalog.json"}],
    )

    with pytest.raises(FileNotFoundError):
        documentation_for("sneaky")
