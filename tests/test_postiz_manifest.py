import json
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "postiz-agent"


def test_manifest_is_pinned_and_confirmation_gated() -> None:
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["revision"] == "41c5a9dbd6b2776863e7c05c22e7a385c208321c"
    assert manifest["source"]["license"] == "AGPL-3.0"
    assert "social.publish.short_video" in manifest["capabilities"]
    assert "social.publish.schedule" in manifest["requires_confirmation"]


def test_manifest_schemas_exist() -> None:
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    for key in ("input_schema", "output_schema"):
        assert (PLUGIN_DIR / manifest[key]).resolve().is_file()
