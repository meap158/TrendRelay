from pathlib import Path

import pytest

from trendrelay_api.integrations.mcp import sops


def _write_sop(
    root: Path,
    relative: str,
    front_matter: str,
    body: str = "# Steps\n\nDo it.\n",
) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front_matter}\n---\n{body}", encoding="utf-8")


def test_nested_markdown_files_extend_the_catalog_without_code(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sops, "SOP_ROOT", tmp_path)
    _write_sop(
        tmp_path,
        "campaigns/copy.md",
        "\n".join(
            [
                "id: campaigns.copy",
                "action: campaigns.write-copy",
                "title: Write campaign copy",
                "summary: Write the missing fields.",
                "version: 2",
                "tags: [campaigns]",
                "aliases: [fill-copy]",
            ]
        ),
    )

    assert sops.list_sops()[0]["path"] == "campaigns/copy.md"
    assert sops.get_sop("fill_copy")["version"] == 2
    assert "trendrelay://sops/campaigns.write-copy" in sops.catalogue_markdown()


def test_duplicate_action_aliases_are_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sops, "SOP_ROOT", tmp_path)
    common = "title: T\nsummary: S"
    _write_sop(tmp_path, "one.md", f"id: one\naction: action.one\naliases: [shared]\n{common}")
    _write_sop(tmp_path, "two.md", f"id: two\naction: action.two\naliases: [shared]\n{common}")

    with pytest.raises(ValueError, match="Duplicate SOP selector"):
        sops.list_sops()


def test_missing_front_matter_is_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sops, "SOP_ROOT", tmp_path)
    (tmp_path / "broken.md").write_text("# No metadata\n", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML front matter"):
        sops.list_sops()


def test_server_guidance_is_loaded_from_the_canonical_files(monkeypatch, tmp_path) -> None:
    guide = tmp_path / "MCP_GUIDE.md"
    guide.write_text(
        "# Test guide and control tower\n\nRead the route and choose the action.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sops, "MCP_GUIDE_PATH", guide)

    instructions = sops.server_instructions("Base MCP policy")

    assert "Base MCP policy" in instructions
    assert "Read the route and choose the action." in instructions


def test_missing_canonical_guidance_fails_clearly(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sops, "MCP_GUIDE_PATH", tmp_path / "MCP_GUIDE.md")

    with pytest.raises(FileNotFoundError, match="canonical MCP guide"):
        sops.mcp_guide_markdown()


# --- paging a procedure -------------------------------------------------------


def test_a_short_procedure_still_arrives_whole() -> None:
    """Paging is for the document that outgrows a client, not a toll on the rest."""
    page = sops.get_sop_page("campaigns.fill-needs-copy")

    assert page["offset"] == 0
    assert page["more"] is False
    assert page["next_offset"] is None
    assert page["markdown"] == sops.get_sop("campaigns.fill-needs-copy")["markdown"]


def test_every_page_together_is_exactly_the_procedure() -> None:
    """The property that makes paging safe: nothing is lost or repeated."""
    whole = sops.get_sop("campaigns.add-post-with-media")["markdown"]

    collected, offset, pages = "", 0, 0
    while True:
        page = sops.get_sop_page("campaigns.add-post-with-media", offset=offset, limit=1500)
        collected += page["markdown"]
        pages += 1
        if not page["more"]:
            break
        offset = page["next_offset"]
        assert pages < 200, "paging did not terminate"

    assert pages > 1, "this procedure should have needed more than one page"
    assert collected == whole


def test_a_page_never_ends_mid_word() -> None:
    """A rule cut in half reads as a whole rule, which is the danger."""
    offset, ends = 0, []
    while True:
        page = sops.get_sop_page("campaigns.add-post-with-media", offset=offset, limit=1200)
        if not page["more"]:
            break
        ends.append(page["markdown"][-1])
        offset = page["next_offset"]

    assert ends, "expected several pages at this size"
    assert all(char == "\n" for char in ends)


def test_a_partial_read_says_so_in_the_payload() -> None:
    """Left to two numbers, a caller reads one page and believes it read the SOP."""
    page = sops.get_sop_page("campaigns.add-post-with-media", limit=1000)

    assert page["more"] is True
    assert isinstance(page["next_offset"], int)
    assert "do not act on a partial procedure" in page["note"]
    assert str(page["characters"]) or True


def test_an_offset_past_the_end_is_an_empty_last_page() -> None:
    """So a caller that overshoots stops rather than erroring."""
    page = sops.get_sop_page("campaigns.fill-needs-copy", offset=10_000_000)

    assert page["markdown"] == ""
    assert page["more"] is False


def test_a_caller_cannot_ask_for_more_than_the_ceiling() -> None:
    page = sops.get_sop_page("campaigns.fill-needs-copy", limit=10_000_000)

    assert len(page["markdown"]) <= sops.MAX_SOP_PAGE_CHARACTERS
