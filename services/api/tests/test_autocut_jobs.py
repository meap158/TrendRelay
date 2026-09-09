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

from trendrelay_api.autocut.jobs import _prune_stale, canonical_aspect, dimensions


def test_a_ratio_named_aspect_is_the_shape_it_names_not_portrait() -> None:
    """Storytelling's control sends "16:9"/"9:16"/"1:1"; AutoCut's sends the
    shape key. Both must land on the same canvas - the ratio strings used to
    miss the table and fall back to portrait, so every wide narration came out
    tall."""
    assert canonical_aspect("16:9") == "landscape"
    assert canonical_aspect("9:16") == "portrait"
    assert canonical_aspect("1:1") == "square"
    # The shape keys still resolve to themselves, and a nonsense value is the
    # default rather than an error.
    assert canonical_aspect("landscape") == "landscape"
    assert canonical_aspect("nonsense") == "portrait"
    # The dimensions follow: a "16:9" render is genuinely wide now.
    assert dimensions("16:9", preview=False) == (1920, 1080)
    assert dimensions("9:16", preview=False) == (1080, 1920)


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
