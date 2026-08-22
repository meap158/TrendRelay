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
