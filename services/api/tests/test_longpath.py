"""Windows' 260-character path limit, and getting real files past it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trendrelay_api.integrations import longpath
from trendrelay_api.integrations.longpath import (
    SAFE_PATH_LIMIT,
    extended,
    is_too_long,
    plain,
    shorten_all,
    shorten_in_place,
    shortened_stem,
)

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="MAX_PATH is a Windows limit."
)


# --- the prefix -----------------------------------------------------------------


@windows_only
def test_an_absolute_path_gains_the_prefix() -> None:
    assert extended(r"C:\media\clip.mp4") == r"\\?\C:\media\clip.mp4"


@windows_only
def test_a_path_that_already_has_the_prefix_is_left_alone() -> None:
    # Applying it twice produces `\\?\\\?\C:\...`, which resolves to nothing.
    once = extended(r"C:\media\clip.mp4")
    assert extended(once) == once


@windows_only
def test_a_network_path_uses_the_unc_form() -> None:
    # `\\?\\\server\share` is not a path. UNC needs its own spelling, and
    # getting it wrong turns a working share into a file-not-found.
    assert extended(r"\\server\share\clip.mp4") == r"\\?\UNC\server\share\clip.mp4"


@windows_only
def test_a_relative_path_is_made_absolute_first() -> None:
    # The prefix disables normalisation, so a relative path would be handed to
    # the filesystem verbatim and never found.
    assert extended("clip.mp4").startswith("\\\\?\\")
    assert "clip.mp4" in extended("clip.mp4")


def test_stripping_the_prefix_gives_back_the_ordinary_path() -> None:
    assert plain(r"\\?\C:\media\clip.mp4") == Path(r"C:\media\clip.mp4")
    assert plain(r"\\?\UNC\server\share\c.mp4") == Path(r"\\server\share\c.mp4")
    assert plain(r"C:\media\clip.mp4") == Path(r"C:\media\clip.mp4")


def test_a_plain_path_survives_a_round_trip() -> None:
    original = Path(r"C:\media\clip.mp4") if os.name == "nt" else Path("/media/clip.mp4")
    assert plain(extended(original)) == original


# --- the naming -----------------------------------------------------------------


def test_a_shortened_stem_respects_its_budget() -> None:
    long_name = Path("x" * 300 + ".mp4")
    for budget in (24, 40, 80):
        assert len(shortened_stem(long_name, budget)) <= budget


def test_names_sharing_a_long_prefix_do_not_collide() -> None:
    # Douyin captions are often the same boilerplate for a hundred characters.
    # A blind truncation would map them all onto one file and lose all but one.
    shared = "同一个开头" * 30
    first = shortened_stem(Path(f"{shared}_a.mp4"), 40)
    second = shortened_stem(Path(f"{shared}_b.mp4"), 40)
    assert first != second


def test_the_readable_part_of_the_name_survives() -> None:
    stem = shortened_stem(Path("露营装备推荐" + "x" * 200 + ".mp4"), 40)
    assert stem.startswith("露营装备推荐")


# --- measuring the way Windows measures -----------------------------------------


def test_an_emoji_counts_double_because_windows_counts_utf16() -> None:
    # The bug this exists to stop: Python calls 🙏 one character and Windows
    # calls it two code units. A caption full of emoji - which Douyin captions
    # are - measured in characters passes a limit it actually exceeds, and the
    # guard signs off on a file that then cannot be opened.
    assert longpath.path_length("abc") == 3
    assert longpath.path_length("ab🙏") == 4
    assert len("ab🙏") == 3, "Python disagrees, which is the whole point"


def test_chinese_stays_one_unit_each() -> None:
    # It is in the basic plane, so it costs the same either way. Only astral
    # characters diverge, which is why this went unnoticed for so long.
    assert longpath.path_length("露营装备") == 4


def test_truncation_never_splits_an_emoji() -> None:
    # Half a surrogate pair is not a name Windows will accept.
    for units in range(1, 9):
        cut = longpath.truncated("a🙏b🙏c", units)
        assert longpath.path_length(cut) <= units
        cut.encode("utf-16-le").decode("utf-16-le")  # raises on a lone surrogate


def test_a_shortened_name_fits_in_windows_units_not_just_characters() -> None:
    emoji_name = Path("🙏" * 120 + ".mp4")
    stem = shortened_stem(emoji_name, 40)
    assert longpath.path_length(stem) <= 40


# --- moving real files ----------------------------------------------------------


def deep_root(tmp_path: Path) -> Path:
    """A directory deep enough that ordinary names overflow beneath it."""
    root = tmp_path
    # Deep enough that a 90-character name plus a `_data.json` tail lands past
    # 259, which is what these tests need to actually be testing.
    while len(str(root)) < 180:
        root = root / "nested-folder"
    root.mkdir(parents=True, exist_ok=True)
    return root


def write(path: Path, payload: bytes = b"media") -> None:
    with open(extended(path), "wb") as handle:
        handle.write(payload)


@windows_only
def test_a_file_windows_could_not_open_is_brought_back_under_the_limit(
    tmp_path: Path,
) -> None:
    root = deep_root(tmp_path)
    original = root / ("长" * 90 + ".mp4")
    assert is_too_long(original), "the fixture must actually exceed the limit"
    write(original)

    moved = shorten_in_place(original, root)

    assert not is_too_long(moved)
    assert len(str(moved)) <= SAFE_PATH_LIMIT
    # Openable without the prefix is the whole point: ffmpeg and OpenCV get a
    # plain path, and neither of them would accept `\\?\`.
    assert moved.read_bytes() == b"media"


@windows_only
def test_the_metadata_sidecar_moves_with_its_video(tmp_path: Path) -> None:
    # The library finds a sidecar by matching stems, and with folderstyle off
    # every post by one creator shares a directory. Renaming the video alone
    # orphans its caption, creator and source link with no way to re-pair them.
    root = deep_root(tmp_path)
    stem = "长" * 90
    write(root / f"{stem}.mp4")
    write(root / f"{stem}_data.json", json.dumps({"desc": "caption"}).encode())

    moved = shorten_in_place(root / f"{stem}.mp4", root)
    sidecar = moved.with_name(f"{moved.stem}_data.json")

    assert sidecar.is_file(), "the sidecar must follow the video"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["desc"] == "caption"
    assert not is_too_long(sidecar), "the sidecar has the longer tail of the two"


@windows_only
def test_a_caption_full_of_emoji_still_ends_up_openable(tmp_path: Path) -> None:
    # The case that actually got through: measured in characters the result sat
    # on 259 and looked fine, while Windows saw 260 and refused to open it.
    root = deep_root(tmp_path)
    original = root / ("🙏" * 45 + ".mp4")
    write(original)

    moved = shorten_in_place(original, root)

    assert longpath.path_length(moved) <= SAFE_PATH_LIMIT
    # The plain call is the assertion: it is what ffmpeg and OpenCV will make.
    assert moved.is_file()
    assert moved.read_bytes() == b"media"


@windows_only
def test_two_over_long_files_do_not_land_on_the_same_name(tmp_path: Path) -> None:
    root = deep_root(tmp_path)
    shared = "同" * 85
    first, second = root / f"{shared}A.mp4", root / f"{shared}B.mp4"
    write(first, b"first")
    write(second, b"second")

    moved_first = shorten_in_place(first, root)
    moved_second = shorten_in_place(second, root)

    assert moved_first != moved_second
    assert moved_first.read_bytes() == b"first"
    assert moved_second.read_bytes() == b"second"


def test_a_path_that_already_fits_is_not_touched(tmp_path: Path) -> None:
    # Renaming a perfectly good file would break every path already recorded
    # for it, so the short case must be a no-op rather than a normalisation.
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"media")
    assert shorten_in_place(path, tmp_path) == path
    assert path.is_file()


def test_shorten_all_reports_what_it_moved(tmp_path: Path) -> None:
    short = tmp_path / "clip.mp4"
    short.write_bytes(b"media")
    kept, renamed = shorten_all([short], tmp_path)
    assert kept == [short]
    assert renamed == []


@windows_only
def test_shorten_all_reports_the_rename(tmp_path: Path) -> None:
    # An operator looking for the caption they saw on Douyin needs to be told
    # the name on disk is not it.
    root = deep_root(tmp_path)
    original = root / ("长" * 90 + ".mp4")
    write(original)

    kept, renamed = shorten_all([original], root)

    assert len(renamed) == 1
    assert renamed[0]["from"] == original.name
    assert renamed[0]["to"] == kept[0].name


# --- the machine-wide setting ---------------------------------------------------


def test_the_registry_setting_is_only_reported(monkeypatch) -> None:
    # It needs an administrator and applies to the whole machine. Reading it is
    # useful; a download tool writing it is not its business.
    assert isinstance(longpath.long_paths_enabled(), bool)
