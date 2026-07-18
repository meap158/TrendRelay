import os
from pathlib import Path

from scripts.local_env import load_prefixed_env


def test_loads_only_missing_prefixed_values(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DOUYIN_TTWID=from-file\nDOUYIN_ODIN_TT='quoted'\nUNRELATED=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOUYIN_TTWID", "from-process")
    monkeypatch.delenv("DOUYIN_ODIN_TT", raising=False)
    monkeypatch.delenv("UNRELATED", raising=False)

    load_prefixed_env(env_file, "DOUYIN_")

    assert os.environ["DOUYIN_TTWID"] == "from-process"
    assert os.environ["DOUYIN_ODIN_TT"] == "quoted"
    assert "UNRELATED" not in os.environ
