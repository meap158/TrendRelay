"""The AutoCut job's housekeeping: stale rendered files are swept away.

A preview is watched once and a full render is copied into the Library by the
ingest that follows it, so both are litter soon after. The prune runs before
each render draws; it must drop what has aged past its window and keep the rest
- and never raise, since a cleanup that failed the render would be worse than
the litter it removes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from trendrelay_api.autocut.jobs import _prune_stale


def _aged_clip(root: Path, name: str, age_seconds: float) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"\x00\x00")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_only_clips_older_than_the_window_are_removed(tmp_path: Path) -> None:
    old = _aged_clip(tmp_path, "old.mp4", age_seconds=7200)   # two hours
    fresh = _aged_clip(tmp_path, "fresh.mp4", age_seconds=60)  # a minute

    _prune_stale(tmp_path, ttl_seconds=3600)  # one-hour window

    assert not old.exists()  # aged out
    assert fresh.exists()    # within the window, kept


def test_non_mp4_files_are_left_alone(tmp_path: Path) -> None:
    keep = _aged_clip(tmp_path, "notes.txt", age_seconds=99999)
    _prune_stale(tmp_path, ttl_seconds=3600)
    assert keep.exists()  # the sweep only targets rendered clips


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    # The first render on a fresh checkout prunes before anything is written.
    _prune_stale(tmp_path / "never-made", ttl_seconds=3600)
