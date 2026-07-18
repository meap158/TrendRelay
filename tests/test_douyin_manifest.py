import json
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "douyin-downloader"


def test_manifest_references_existing_schemas() -> None:
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["source"]["revision"] == "ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7"
    assert "media.download.batch" in manifest["capabilities"]
    for schema_key in ("input_schema", "output_schema"):
        assert (PLUGIN_DIR / manifest[schema_key]).resolve().is_file()


def test_batch_schema_defaults_to_bounded_download() -> None:
    schema = json.loads(
        (PLUGIN_DIR / "batch-input.schema.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["limit"]["default"] == 50
    assert schema["properties"]["limit"]["minimum"] == 0
