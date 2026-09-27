"""Nothing this install writes belongs on the system drive.

Rendering writes its intermediates through `tempfile`, which on Windows means
`%LOCALAPPDATA%\\Temp` - a different drive from the one the media lives on,
and the one that reached zero bytes free on this machine while the project
drive still had fifty gigabytes. A single autocut render writes several
gigabytes that way, so the failure is not subtle: the render dies partway
through with a disk error, or with an out-of-memory error from a library that
could not grow its page file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from trendrelay_api.project_storage import (
    INSIGHTFACE_ROOT,
    MODEL_CACHES,
    PROJECT_ROOT,
    SCRATCH,
    keep_work_on_the_project_drive,
)


def test_importing_the_package_moves_scratch_onto_the_project_drive() -> None:
    """Done in `__init__` rather than in each entry point, because it has to
    happen before anything renders, and every path into this code - the API,
    the worker, the MCP server, this test - starts by importing the package.
    """
    scratch = Path(tempfile.gettempdir()).resolve()

    assert scratch == SCRATCH.resolve()
    assert PROJECT_ROOT.resolve() in scratch.parents
    assert scratch.is_dir(), "it exists by the time anything could write to it"


def test_a_child_process_inherits_the_same_scratch() -> None:
    """Most of the heavy writing is ffmpeg's and yt-dlp's, not Python's, and
    they read the environment. Both names are on the allowlist every
    integration builds its subprocess environment from."""
    for name in ("TEMP", "TMP", "TMPDIR"):
        assert Path(os.environ[name]).resolve() == SCRATCH.resolve(), name


def test_every_model_cache_is_pointed_at_the_project() -> None:
    """A library left to itself puts its weights under the home directory.
    face-anon's notes record `C:` hitting 86 MB free with uv's cache alone
    holding 4.7 GB of torch wheels."""
    for name, path in MODEL_CACHES.items():
        assert PROJECT_ROOT.resolve() in path.resolve().parents, name
        assert Path(os.environ[name]).resolve() == path.resolve(), name


def test_insightface_is_kept_beside_its_own_licence() -> None:
    """It takes its root as an argument rather than reading the environment,
    so `face_identity` passes it; this is the value it passes."""
    assert PROJECT_ROOT.resolve() in INSIGHTFACE_ROOT.resolve().parents
    assert INSIGHTFACE_ROOT.name == "insightface"


def test_a_deliberate_cache_of_the_operator_s_own_is_left_alone(monkeypatch) -> None:
    """The scratch is moved outright because moving it is the whole point. A
    cache someone has deliberately pointed elsewhere is theirs."""
    monkeypatch.setenv("HF_HOME", "D:/somewhere/else")

    keep_work_on_the_project_drive()

    assert os.environ["HF_HOME"] == "D:/somewhere/else"
    assert Path(tempfile.gettempdir()).resolve() == SCRATCH.resolve()
