"""Alembic and the API have to mean the same file.

The default URL is relative - `sqlite:///.data/trendrelay.db` - and
`migrations/env.py` used to hand it to Alembic exactly as written. Run from
the repository root that is the real database; run from `services/api`, which
is where the API's own tooling lives, it is a second file that SQLite creates
empty on the spot. `alembic upgrade head` then migrated the empty one and
printed success, leaving the database the API opens on its old revision with
nothing to say it had been skipped.

`create_database_engine` had been anchored against the project root for this
very reason; the migration runner was the one caller left holding the raw
setting.
"""

from __future__ import annotations

from pathlib import Path


def test_migrations_resolve_to_the_database_the_api_opens(monkeypatch, tmp_path):
    """The URL Alembic configures is the one the engine would open."""
    from trendrelay_api.config import get_settings
    from trendrelay_api.database import configured_database_url

    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        from trendrelay_api.database import _anchored

        settings_url = get_settings().database_url
        assert configured_database_url() == _anchored(settings_url)
        # And, for a relative default, that it is not read against the
        # directory the process happens to be sitting in.
        if settings_url.startswith("sqlite:///") and not Path(
            settings_url.removeprefix("sqlite:///")
        ).is_absolute():
            assert str(tmp_path) not in configured_database_url()
    finally:
        get_settings.cache_clear()


def test_env_py_asks_for_the_anchored_url():
    """The migration runner reads it from the one function that anchors.

    Asserted on the source because importing `migrations/env.py` runs the
    migrations. What matters is that it does not reach for the raw setting.
    """
    env_py = (
        Path(__file__).resolve().parents[1] / "migrations" / "env.py"
    ).read_text(encoding="utf-8")

    assert "configured_database_url" in env_py
    assert "get_settings().database_url" not in env_py


def test_an_absolute_url_is_left_alone(monkeypatch, tmp_path):
    """An explicit path is honoured - that is how a copy gets migrated."""
    from trendrelay_api.config import get_settings
    from trendrelay_api.database import configured_database_url

    target = tmp_path / "elsewhere.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")
    get_settings.cache_clear()
    try:
        assert configured_database_url() == f"sqlite:///{target}"
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        get_settings.cache_clear()


def test_the_setting_is_read_from_an_unprefixed_name():
    """`DATABASE_URL`, with no project prefix.

    Written down because guessing wrong is quiet: a prefixed name is simply
    ignored, the default is used, and a command meant for a scratch copy runs
    against the real database instead.
    """
    from trendrelay_api.config import Settings

    assert Settings.model_config.get("env_prefix") in (None, "")
    assert "database_url" in Settings.model_fields
