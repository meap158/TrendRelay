"""Store identical media once by hard-linking the copies together.

Imports used to copy every downloaded video into the library, so the same bytes
sat under `.data/downloads` and `.data/media`. This finds files with identical
content under the data folders and turns the extra copies into hard links to one
of them. Every path keeps working and keeps its content; only the storage is
shared. Nothing is deleted, and nothing outside the data folders is touched.

    python scripts/dedupe_media.py            # report what would be linked
    python scripts/dedupe_media.py --apply    # do it

Safe to run repeatedly and while the app is up: each link is made under a
temporary name and swapped in atomically, files modified in the last few
minutes are left alone, and a file already sharing storage is skipped. It needs
the copies to be on one volume; a pair that is not is reported and skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".data"

#: Which copy is kept as the one the others link to. The library is what the
#: database points at, so it wins; a download is the least precious name.
KEEP_ORDER = ("media", "productions", "downloads")
MIN_SIZE = 1 << 20
SETTLE_SECONDS = 600
_CHUNK = 1 << 20
_LONG = "\\\\?\\"


def _long(path: Path) -> str:
    """The path in a form Windows accepts beyond 260 characters."""
    text = str(path)
    if os.name == "nt" and not text.startswith(_LONG):
        return _LONG + text
    return text


def _files(folder: Path) -> Iterator[Path]:
    for current, _, names in os.walk(_long(folder)):
        for name in names:
            yield Path(current.removeprefix(_LONG)) / name


def _digest(path: Path, limit: int | None = None) -> str:
    sha = hashlib.sha256()
    remaining = limit
    with open(_long(path), "rb") as handle:
        while True:
            want = _CHUNK if remaining is None else min(_CHUNK, remaining)
            if want <= 0:
                break
            block = handle.read(want)
            if not block:
                break
            sha.update(block)
            if remaining is not None:
                remaining -= len(block)
    return sha.hexdigest()


def _rank(path: Path, data: Path) -> tuple[int, str]:
    try:
        top = path.relative_to(data).parts[0]
    except (ValueError, IndexError):
        top = ""
    return (KEEP_ORDER.index(top) if top in KEEP_ORDER else len(KEEP_ORDER), str(path))


def plan(data: Path = DATA, now: float | None = None) -> tuple[list[tuple[Path, Path, int]], int]:
    """Pairs of (keeper, duplicate, size) to link, and how many were skipped."""
    now = time.time() if now is None else now
    # size -> (device, inode) -> every path that name the same stored file. Paths
    # already linked together are one file, so they are read once, not once each.
    by_size: dict[int, dict[tuple[int, int], list[Path]]] = defaultdict(lambda: defaultdict(list))
    for top in KEEP_ORDER:
        folder = data / top
        if not folder.is_dir():
            continue
        for path in _files(folder):
            try:
                stat = os.stat(_long(path))
            except OSError:
                continue
            if stat.st_size >= MIN_SIZE:
                by_size[stat.st_size][(stat.st_dev, stat.st_ino)].append(path)

    links: list[tuple[Path, Path, int]] = []
    skipped = 0
    for size, files in by_size.items():
        if len(files) < 2:
            continue
        for names in files.values():
            names.sort(key=lambda item: _rank(item, data))
        # A cheap hash of the first block rules out most same-size strangers
        # before any whole file is read.
        heads: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for key, names in files.items():
            heads[_digest(names[0], _CHUNK)].append(key)
        for keys in heads.values():
            if len(keys) < 2:
                continue
            whole: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for key in keys:
                whole[_digest(files[key][0])].append(key)
            for same in whole.values():
                if len(same) < 2:
                    continue
                same.sort(key=lambda key: _rank(files[key][0], data))
                keeper = files[same[0]][0]
                for key in same[1:]:
                    for duplicate in files[key]:
                        stat = os.stat(_long(duplicate))
                        if stat.st_dev != same[0][0] or now - stat.st_mtime < SETTLE_SECONDS:
                            skipped += 1
                            continue
                        links.append((keeper, duplicate, size))
    return links, skipped


def apply(links: list[tuple[Path, Path, int]]) -> int:
    """Swap each duplicate for a link to its keeper. Returns how many failed."""
    failed = 0
    for keeper, duplicate, _ in links:
        temporary = Path(str(duplicate) + ".dedupe.tmp")
        try:
            os.link(_long(keeper), _long(temporary))
            os.replace(_long(temporary), _long(duplicate))
        except OSError as error:
            failed += 1
            print(f"could not link {duplicate}: {error}", file=sys.stderr)
            try:
                os.remove(_long(temporary))
            except OSError:
                pass
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the links; default only reports")
    parser.add_argument("--data", type=Path, default=DATA, help="the data folder to scan")
    args = parser.parse_args(argv)
    links, skipped = plan(args.data.resolve())
    saved = sum(size for _, _, size in links)
    print(f"{len(links)} duplicate files, {saved / 1e9:.1f} GB to reclaim, {skipped} skipped")
    if not args.apply:
        print("Dry run. Pass --apply to link them.")
        return 0
    failed = apply(links)
    print(f"linked {len(links) - failed}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
