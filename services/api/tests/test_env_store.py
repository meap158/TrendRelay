import pytest

from trendrelay_api import env_store


@pytest.fixture
def env_file(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    monkeypatch.setattr(env_store, "ENV_PATH", path)
    monkeypatch.setattr(env_store, "refresh_settings", lambda: None)
    return path


def test_updates_existing_keys_in_place_and_appends_new_ones(monkeypatch, env_file) -> None:
    env_file.write_text(
        "# Publishing\nBUNDLE_SOCIAL_API_KEY=old\nAPI_PORT=8011\n", encoding="utf-8"
    )
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)

    written = env_store.write_env_values(
        {"BUNDLE_SOCIAL_API_KEY": "new", "ZERNIO_API_KEY": "sk_live"}
    )

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert written == ["BUNDLE_SOCIAL_API_KEY", "ZERNIO_API_KEY"]
    assert lines[0] == "# Publishing"
    assert lines[1] == "BUNDLE_SOCIAL_API_KEY=new"
    assert lines[2] == "API_PORT=8011"
    assert "ZERNIO_API_KEY=sk_live" in lines
    assert env_store.read_env_file()["ZERNIO_API_KEY"] == "sk_live"


def test_quotes_values_that_need_it_and_clears_empty_ones(monkeypatch, env_file) -> None:
    env_store.write_env_values({"BUFFER_API_KEY": "a b#c"})
    assert 'BUFFER_API_KEY="a b#c"' in env_file.read_text(encoding="utf-8")
    assert env_store.read_env_file()["BUFFER_API_KEY"] == "a b#c"

    env_store.write_env_values({"BUFFER_API_KEY": ""})
    assert env_store.read_env_file()["BUFFER_API_KEY"] == ""
    assert "BUFFER_API_KEY" not in __import__("os").environ


def test_rejects_unsupported_keys_and_multiline_values(env_file) -> None:
    with pytest.raises(env_store.EnvWriteError, match="unsupported .env keys"):
        env_store.write_env_values({"lower_case": "x"})
    with pytest.raises(env_store.EnvWriteError, match="single line"):
        env_store.write_env_values({"BUFFER_API_KEY": "one\ntwo"})


def test_configured_keys_reports_booleans_not_values(monkeypatch, env_file) -> None:
    env_file.write_text("BUNDLE_SOCIAL_API_KEY=secret\nZERNIO_API_KEY=\n", encoding="utf-8")
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    monkeypatch.delenv("BUNDLE_SOCIAL_API_KEY", raising=False)

    assert env_store.configured_keys(("BUNDLE_SOCIAL_API_KEY", "ZERNIO_API_KEY")) == {
        "BUNDLE_SOCIAL_API_KEY": True,
        "ZERNIO_API_KEY": False,
    }
