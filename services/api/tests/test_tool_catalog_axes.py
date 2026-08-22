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

    Everything under Discover and Download fetches from somebody else. Library
    used to be wholly local and its heading said so; adding a hosted voice
    service broke that, and the heading was changed rather than the claim being
    left standing over a card it no longer covered.

    So the invariant is narrower now, and the next assertion is what carries the
    weight: a Library tool that reaches out has to be marked as one.
    """
    by_surface = {}
    for tool in tools():
        by_surface.setdefault(tool["surface"], set()).add(tool["runs"])

    assert by_surface["discover"] == {"network"}
    assert by_surface["download"] == {"network"}


def test_a_library_tool_that_reaches_out_says_where_it_sends_things() -> None:
    """The Library heading no longer promises locality, so the card must carry it.

    A hosted model among local ones is the case where "does this leave my
    machine" stops being answerable from the group heading, and it is also the
    case where the answer matters most - it is metered, and the media goes with
    it. So the card has to say so itself, and name the service it reaches.

    What licence such a tool carries is deliberately not asserted here. That is
    a judgement about terms somebody has read, made per tool, and a test that
    pinned it would be encoding one tool's answer as a rule for all of them.
    """
    hosted = [
        tool for tool in tools()
        if tool["surface"] == "library" and tool["runs"] == "network"
    ]

    for tool in hosted:
        assert tool["license"], tool["id"]
        assert str(tool["license_url"]).startswith("https://"), tool["id"]
        assert tool.get("documentation"), f"{tool['id']} reaches out and explains nowhere"


def test_the_catalogue_still_carries_its_finer_category() -> None:
    # Grouping replaced the category as the organising idea, not as a fact.
    # It stays on the card as the more precise description it always was.
    assert all(tool.get("category") for tool in tools())


def test_a_tool_nothing_can_call_does_not_offer_to_install_itself() -> None:
    """The Tools page offered Install on three models no code reaches.

    BiRefNet, SAM 2 and CatVTON are catalogued and licence-checked with a
    pinned revision each, and not one line of TrendRelay references any of
    them. Install was a large download spent on something no feature could
    ever call - and, on CatVTON, a non-commercial licence accepted for nothing.

    The entries stay: a licence check with a documented reason for choosing it
    over its alternatives is worth keeping. What must not stay is the button,
    and the way to say so is the one InsightFace and ElevenLabs already use.
    """
    offered = [
        tool["id"] for tool in tools()
        if tool.get("integration_status") == "evaluated-not-adapted"
        and (tool.get("install_allowed") or tool.get("activation_allowed"))
    ]

    assert offered == [], (
        f"nothing adapts these, so installing them achieves nothing: {offered}"
    )


def test_a_tool_that_cannot_be_installed_says_why() -> None:
    """A greyed-out button with no reason is indistinguishable from a bug."""
    silent = [
        tool["id"] for tool in tools()
        if not tool.get("install_allowed") and not (tool.get("block_reason") or "").strip()
    ]

    assert silent == [], f"no install and no reason given: {silent}"


def test_a_tool_the_app_actually_calls_is_marked_adapted() -> None:
    """Status is read by people deciding what to trust, so a stale one misleads.

    faster-whisper powers the Library's transcription, is installed, is active,
    and still claimed to be evaluated-not-adapted - the same label carried by
    three models with no code at all behind them.
    """
    by_id = {tool["id"]: tool for tool in tools()}
    # The three the media-analysis surface drives, named in `MEDIA_AI_TOOLS`.
    for tool_id in ("faster-whisper", "rapidocr", "argos-translate"):
        assert by_id[tool_id]["integration_status"] == "adapted", tool_id
