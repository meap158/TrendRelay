import json
import subprocess
from pathlib import Path

import pytest

from trendrelay_api import tool_registry


def create_upstream(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@trendrelay.local"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "TrendRelay Tests"], cwd=path, check=True
    )
    (path / "README.md").write_text("test provider\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial provider"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


@pytest.fixture
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    upstream = tmp_path / "upstream"
    revision = create_upstream(upstream)
    project = tmp_path / "project"
    project.mkdir()
    catalog = {
        "schema_version": 1,
        "tools": [
            {
                "id": "test-tool",
                "name": "Test Tool",
                "repository": str(upstream),
                "revision": revision,
                "install_allowed": True,
                "activation_allowed": True,
                "default_active": False,
                "install_strategy": "source-checkout",
                "root_path": ".tools/catalog/test-tool",
                "source_path": ".tools/catalog/test-tool/source",
            }
        ],
    }
    catalog_path = project / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(tool_registry, "PROJECT_ROOT", project)
    monkeypatch.setattr(tool_registry, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(tool_registry, "STATE_PATH", project / ".data/state.json")
    return catalog["tools"][0]


def test_source_install_activate_and_uninstall(isolated_registry: dict) -> None:
    installed = tool_registry.install_tool("test-tool")
    assert installed["installed"] is True
    assert installed["installed_revision"] == isolated_registry["revision"]

    active = tool_registry.set_active("test-tool", True)
    assert active["active"] is True

    removed = tool_registry.uninstall_tool("test-tool")
    assert removed["installed"] is False
    assert removed["active"] is False


def test_activation_requires_install(isolated_registry: dict) -> None:
    with pytest.raises(tool_registry.ToolRegistryError, match="Install the pinned"):
        tool_registry.set_active("test-tool", True)
