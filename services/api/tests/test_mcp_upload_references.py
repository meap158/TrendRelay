"""Small contract tests for MCP file-reference diagnostics."""

from __future__ import annotations

import pytest

from trendrelay_api.integrations.mcp import intake


@pytest.mark.parametrize(
    "reference",
    ["file-generated-123", r"/mnt/data/generated.png", r"C:\\runtime\\generated.png"],
)
def test_private_file_references_name_the_transfer_boundary(reference: str) -> None:
    with pytest.raises(ValueError, match="private file registry|mounted runtime"):
        intake.upload_image("ws", image=reference)


def test_attachment_url_aliases_from_clients_are_accepted() -> None:
    fetched: list[str] = []

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            intake,
            "_ingest_fetched",
            lambda *_args, **_kwargs: {"job_id": "queued"},
        )
        result = intake.upload_media(
            "ws",
            media={"file_id": "private", "file_url": "https://files.example/one.png"},
            fetch=lambda url: fetched.append(url) or (b"image", "image/png"),
        )

    assert fetched == ["https://files.example/one.png"]
    assert result == {"job_id": "queued"}
