"""Every tool says where its work shows up and whether it leaves the machine.

The Tools page grouped nothing: sixteen tools carried eleven categories between
them - "Media intelligence", "Media editing", "Trend research", "Social
research" - which is nearly one category per tool. And nothing anywhere
answered the two questions somebody actually arrives with: what does this
power, and does it reach a third party.

These are guarded because the page renders by group. A tool added without a
surface does not land in an unknown pile; it does not render at all.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CATALOG = PROJECT_ROOT / "config" / "tool-catalog.json"

#: The groups the Tools page draws, in the order it draws them.
SURFACES = {"discover", "download", "library", "assistant"}
RUNS = {"local", "network"}


def tools() -> list[dict]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return data["tools"] if isinstance(data, dict) else data


def test_every_tool_belongs_to_a_group_the_page_draws() -> None:
    """A tool with no surface renders nowhere, silently."""
    missing = [tool["id"] for tool in tools() if tool.get("surface") not in SURFACES]

    assert missing == [], f"no surface, so these would not appear at all: {missing}"


def test_every_tool_says_whether_it_leaves_the_machine() -> None:
    # The line that decides privacy, cost, and what breaks when the network
    # does. A local model and a service called with somebody's key looked
    # identical on this page before it was recorded.
    missing = [tool["id"] for tool in tools() if tool.get("runs") not in RUNS]

    assert missing == [], f"no run mode: {missing}"


def test_research_tools_are_the_ones_that_reach_out() -> None:
    """The grouping earns its keep by making this true at a glance.

    Everything under Discover and Download fetches from somebody else; every
    model under Library runs here. If that ever stops holding, the groups are
    telling the operator something false about their data.
    """
    by_surface = {}
    for tool in tools():
        by_surface.setdefault(tool["surface"], set()).add(tool["runs"])

    assert by_surface["discover"] == {"network"}
    assert by_surface["download"] == {"network"}
    assert by_surface["library"] == {"local"}


def test_the_catalogue_still_carries_its_finer_category() -> None:
    # Grouping replaced the category as the organising idea, not as a fact.
    # It stays on the card as the more precise description it always was.
    assert all(tool.get("category") for tool in tools())
