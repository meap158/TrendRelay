"""Which database the app opens, and where it looks for it."""

from __future__ import annotations

from pathlib import Path


# --- which database this is ---------------------------------------------------


def test_a_relative_sqlite_path_is_the_same_file_from_any_directory() -> None:
    """The default is relative, and a relative path is resolved against the
    directory the process started in - which is not a preference, it decides
    which database this is.

    The API launched from the repository root and a worker or a test run
    launched from `services/api` were reading two different files and neither
    said so: the missing directory is created and SQLite creates the missing
    file, so the second one gets an empty database rather than an error, and
    then reports that a table does not exist.
    """
    from trendrelay_api.database import _anchored
    from trendrelay_api.tool_registry import PROJECT_ROOT

    anchored = _anchored("sqlite:///.data/trendrelay.db")

    assert anchored == f"sqlite:///{PROJECT_ROOT / '.data/trendrelay.db'}"
    assert Path(anchored.removeprefix("sqlite:///")).is_absolute()


def test_a_path_somebody_chose_is_left_where_they_put_it() -> None:
    """Anchoring is for the relative default, not for an explicit location.

    The four-slash form is the one to watch on Windows: it is rooted but has no
    drive, so `Path.is_absolute()` calls it relative and anchoring would move
    somebody's chosen path onto another disk.
    """
    from trendrelay_api.database import _anchored

    for url in (
        "sqlite:////tmp/trendrelay.db",
        "sqlite:///C:/data/trendrelay.db",
        "sqlite:///:memory:",
        "sqlite://",
        "postgresql://localhost/trendrelay",
    ):
        assert _anchored(url) == url
