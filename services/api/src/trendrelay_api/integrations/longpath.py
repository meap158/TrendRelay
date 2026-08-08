r"""Windows paths that are longer than Windows wants to allow.

Windows caps a path at 260 characters unless the machine opts out, and this
machine has not: `LongPathsEnabled` is 0. A Douyin caption is routinely 80
characters of Chinese and the creator name adds more, so a download lands
somewhere in the 200s and occasionally past it. Past it, the write fails with
`FileNotFoundError` — a message about the file not existing, for a file that
could not be created — and the failure surfaced as "Douyin rejected the saved
session", which sent a whole afternoon looking at cookies.

Two mechanisms, used together, and neither of them is truncation-by-guessing.

**Writing: the extended-length prefix.** A path beginning `\\?\` skips the
Win32 normalisation that enforces MAX_PATH. It needs no registry change, no
administrator, and no restart. Measured against the pinned downloader's own
path composition: identical file names failed plainly at 280, 305 and 335
characters and succeeded through the prefix at all three.

**Keeping it: shortening afterwards.** The prefix is not a free pass for the
rest of the system. OpenCV does not accept it, a `\\?\` path stored in the
database would be compared against a plain one and never match, and it reads
like line noise in the interface. So a file is *written* wherever the provider
wants to put it and then renamed to something bounded, using the prefix to
reach it. Everything recorded afterwards is a plain, short, ordinary path, and
every tool downstream — ffmpeg, the face detector, the library — sees a path it
can open.

The saved name is truncated but not lossy in the way a blind cut would be: the
original name is already recorded in the artifact metadata, and a hash of the
full relative path keeps two long names that share a prefix from colliding.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: MAX_PATH counts the terminating null, so 259 characters are usable.
MAX_PATH = 260
SAFE_PATH_LIMIT = MAX_PATH - 1
EXTENDED_PREFIX = "\\\\?\\"
UNC_PREFIX = "\\\\?\\UNC\\"
#: Enough for a stem, a separator, the hash and a suffix. Below this there is no
#: name worth writing, and the file belongs somewhere shallower instead.
MIN_NAME_BUDGET = 24
HASH_LENGTH = 6


def on_windows() -> bool:
    return os.name == "nt"


def extended(path: Path | str) -> str:
    """A path the Win32 API will accept past MAX_PATH.

    Only meaningful on Windows and only for a fully-qualified path: the prefix
    disables normalisation, so a relative path, a forward slash or a `..` left
    in it would be passed through to the filesystem verbatim and not found.
    """
    if not on_windows():
        return str(path)
    text = str(path)
    if text.startswith(EXTENDED_PREFIX):
        return text
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        # \\server\share -> \\?\UNC\server\share
        return UNC_PREFIX + absolute[2:]
    return EXTENDED_PREFIX + absolute


def plain(path: Path | str) -> Path:
    """The same path without the prefix, for storing and for showing."""
    text = str(path)
    if text.startswith(UNC_PREFIX):
        return Path("\\\\" + text[len(UNC_PREFIX):])
    if text.startswith(EXTENDED_PREFIX):
        return Path(text[len(EXTENDED_PREFIX):])
    return Path(text)


def path_length(path: Path | str) -> int:
    """How long Windows thinks this path is.

    Not `len()`. Windows measures a path in UTF-16 code units, and Python
    measures a string in characters; for anything outside the basic plane the
    two disagree. An emoji is one character to Python and two units to Windows,
    and Douyin captions are full of them - the 🙏 in the first caption tested
    made a path Python called 259 and Windows called 260, which is one over.
    Measured in characters, the guard would have signed off on a file that
    could not then be opened.
    """
    return len(str(plain(path)).encode("utf-16-le")) // 2


def truncated(text: str, units: int) -> str:
    """`text` cut to at most `units` UTF-16 code units, never mid-character.

    Slicing by units directly could cut a surrogate pair in half and produce a
    lone surrogate, which is not a filename Windows will take.
    """
    if len(text.encode("utf-16-le")) // 2 <= units:
        return text
    kept: list[str] = []
    used = 0
    for character in text:
        width = len(character.encode("utf-16-le")) // 2
        if used + width > units:
            break
        kept.append(character)
        used += width
    return "".join(kept)


def is_too_long(path: Path | str) -> bool:
    """Whether this path is one Windows would refuse without the prefix."""
    return on_windows() and path_length(path) > SAFE_PATH_LIMIT


def long_paths_enabled() -> bool:
    """Whether the machine has already opted out of the 260 limit.

    Reported rather than changed: it is a machine-wide registry setting that
    needs an administrator, and quietly editing the registry of the machine one
    is running on is not a download tool's business.
    """
    if not on_windows():
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
            return bool(value)
    except (ImportError, OSError):
        return False


def shortened_stem(original: Path, budget: int) -> str:
    """A stem of at most `budget` characters, still unique.

    The hash is of the whole original path, so two captions sharing their first
    hundred characters - common, they are often the same boilerplate - do not
    collapse onto each other. The suffix is the caller's business: one stem has
    to serve `.mp4` and `_data.json` alike.
    """
    digest = hashlib.sha256(str(original).encode("utf-8")).hexdigest()[:HASH_LENGTH]
    tail = f"-{digest}"
    return f"{truncated(original.stem, max(1, budget - len(tail)))}{tail}"


def companions(path: Path) -> list[Path]:
    """Files the downloader wrote alongside this one, sharing its stem.

    The provider names its metadata sidecar `<stem>_data.json`, and the library
    finds it by comparing stems. With `folderstyle` off every post by a creator
    shares one directory, so a video renamed on its own stops matching its own
    sidecar and there are too many others for a fallback to guess between - the
    caption, creator and source link would all be silently lost. So the group
    moves together or not at all.
    """
    if not os.path.isdir(extended(path.parent)):
        return [path]
    # Through the prefix throughout. `Path.is_file()` answers False for a path
    # over the limit - not "no" but "I cannot look" - so the plain spelling
    # would quietly drop exactly the files this exists to rescue.
    group = [
        sibling
        for sibling in path.parent.iterdir()
        if sibling.name.startswith(path.stem) and os.path.isfile(extended(sibling))
    ]
    # The media file first, so callers can rely on the head being what they
    # asked about.
    return [path, *sorted(item for item in group if item != path)]


def shorten_in_place(path: Path, root: Path) -> Path:
    """Rename an over-long file so its path fits, and return where it now is.

    `root` is the fallback directory: when the file's own parent is already so
    deep that no name would fit under it, the file moves up rather than being
    given a one-character name.
    """
    if not is_too_long(path):
        return path

    parent = path.parent
    if path_length(parent) + 1 + MIN_NAME_BUDGET > SAFE_PATH_LIMIT:
        parent = root
    # Every measurement here is in the units Windows counts in, not characters.
    available = SAFE_PATH_LIMIT - path_length(parent) - 1
    if available < MIN_NAME_BUDGET:
        # Even the job's own root is too deep. Nothing can be written here, and
        # saying so beats renaming to a name that cannot hold anything.
        raise OSError(f"The download folder is itself too deep for Windows: {parent}")

    group = companions(path)
    # Every member has to fit, and `_data.json` is longer than `.mp4`. Budgeting
    # for the shortest would rename the video successfully and then fail on its
    # sidecar, which is the half-done state this is meant to avoid.
    longest_tail = max(
        path_length(item.name) - path_length(path.stem) for item in group
    )
    budget = available - longest_tail
    if budget < 1:
        raise OSError(f"No name fits under {parent}")

    stem = unique_stem(path, parent, budget)
    moved = path
    for item in group:
        target = parent / f"{stem}{item.name[len(path.stem):]}"
        os.replace(extended(item), extended(target))
        if item == path:
            moved = target
    return moved


def unique_stem(path: Path, parent: Path, budget: int) -> str:
    """A stem that fits and that nothing in `parent` is already using."""
    stem = shortened_stem(path, budget)
    counter = 0
    while os.path.exists(extended(parent / f"{stem}{path.suffix}")):
        counter += 1
        stem = shortened_stem(Path(f"{path}~{counter}"), budget)
        if counter > 50:
            raise OSError(f"Could not find a free name under {parent}")
    return stem


def shorten_all(paths: list[Path], root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    """Bring every over-long file back under the limit.

    Returns the paths as they now stand, plus a record of what moved, so a job
    can report the rename rather than leaving an operator to wonder why the
    file on disk is not the name they saw on Douyin.
    """
    kept: list[Path] = []
    renamed: list[dict[str, str]] = []
    for path in paths:
        if not is_too_long(path):
            kept.append(path)
            continue
        moved = shorten_in_place(path, root)
        kept.append(moved)
        renamed.append({"from": path.name, "to": moved.name})
    return kept, renamed
