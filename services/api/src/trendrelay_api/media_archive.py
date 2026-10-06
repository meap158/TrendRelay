"""Library selections handed back as one .zip, the way a file drive does it.

Two requests, because a download the browser itself performs cannot carry the
bearer token every other route reads. The authenticated one checks the
selection and issues a ticket; the browser then navigates to the ticket's URL
and saves what comes back with its own progress bar, its own "Save as" and
nothing held in the tab's memory. A selection of a few thousand clips is
gigabytes, which a `fetch` into a blob would hold twice.

The archive is streamed as it is written, never built on disk first: entries
go out with data descriptors, so the first bytes leave before the last file
has been read and the size of a selection costs nothing but time. Pictures,
video and compressed audio are stored rather than deflated - they are already
compressed, and deflating them again spends the CPU to make them larger.

A ticket is good for a few minutes and may be fetched more than once inside
them. Download managers take a browser's request over by asking for the same
URL again, so a single-use ticket would hand them an error in place of the
file.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from urllib.parse import quote

#: How long an issued link stays good. Long enough to survive a slow "Save as"
#: dialog and a download manager's second request, short enough that a link
#: copied out of the network panel is soon nothing.
TICKET_LIFETIME_SECONDS = 10 * 60
#: Tickets held at once. Each is a list of paths, small, but nothing should be
#: allowed to grow without a bound because a button was pressed repeatedly.
MAX_TICKETS = 200
#: Read and written in these pieces, so a 4 GB video moves through a megabyte
#: of memory rather than four gigabytes.
CHUNK_BYTES = 1024 * 1024

#: Formats whose bytes are already compressed. Deflate finds nothing left to
#: take out of these and adds its own overhead, so they are stored as they are.
STORED_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".avif",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
    ".zip", ".gz", ".7z", ".rar",
})

_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)
#: Long enough for any real title, short enough that the extracted path stays
#: well inside Windows' 260-character limit once a folder is in front of it.
MAX_STEM = 120


@dataclass(frozen=True)
class ArchiveEntry:
    path: Path
    name: str


@dataclass(frozen=True)
class Ticket:
    workspace_id: str
    entries: tuple[ArchiveEntry, ...]
    filename: str
    media_type: str
    expires_at: float


_tickets: dict[str, Ticket] = {}
_lock = threading.Lock()


def entry_name(title: str, original_path: str) -> str:
    """A name that extracts cleanly on Windows, macOS and Linux.

    The title is what the Library shows, so it is what a person looks for in
    the folder; the extension comes from the stored file, because a title is
    free text and the file is the truth about what the bytes are.
    """
    suffix = PurePath(original_path).suffix.lower()
    stem = title.strip()
    if suffix and stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)]
    stem = _UNSAFE.sub("_", stem).strip(" .")
    if not stem:
        stem = PurePath(original_path).stem or "file"
        stem = _UNSAFE.sub("_", stem).strip(" .") or "file"
    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return f"{stem[:MAX_STEM].rstrip(' .')}{suffix}"


def unique_names(names: Sequence[str]) -> list[str]:
    """Number repeats as "name (2).png", the way a file manager does.

    Compared without case: Windows and macOS would extract "Clip.png" and
    "clip.png" onto each other and ask which one to keep.
    """
    taken: set[str] = set()
    result: list[str] = []
    for name in names:
        candidate = name
        path = PurePath(name)
        stem, suffix = (path.stem, path.suffix) if path.suffix else (name, "")
        count = 1
        while candidate.lower() in taken:
            count += 1
            candidate = f"{stem} ({count}){suffix}"
        taken.add(candidate.lower())
        result.append(candidate)
    return result


def archive_filename(count: int, today: str) -> str:
    return f"TrendRelay-{count}-files-{today}.zip"


def issue(
    workspace_id: str,
    entries: Sequence[ArchiveEntry],
    filename: str,
    media_type: str = "application/zip",
) -> str:
    """Hold a selection under an unguessable token and return the token."""
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        for key in [key for key, held in _tickets.items() if held.expires_at <= now]:
            del _tickets[key]
        while len(_tickets) >= MAX_TICKETS:
            del _tickets[next(iter(_tickets))]
        _tickets[token] = Ticket(
            workspace_id=workspace_id,
            entries=tuple(entries),
            filename=filename,
            media_type=media_type,
            expires_at=now + TICKET_LIFETIME_SECONDS,
        )
    return token


def redeem(workspace_id: str, token: str) -> Ticket | None:
    """The selection behind a token, while it is good and for this workspace."""
    with _lock:
        ticket = _tickets.get(token)
        if ticket is None:
            return None
        if ticket.expires_at <= time.monotonic():
            del _tickets[token]
            return None
    if not secrets.compare_digest(ticket.workspace_id, workspace_id):
        return None
    return ticket


class _Sink:
    """A write-only stream the zip writer fills and the response drains."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._parts.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> Iterator[bytes]:
        if self._parts:
            parts, self._parts = self._parts, []
            yield b"".join(parts)


def stream_zip(entries: Sequence[ArchiveEntry]) -> Iterator[bytes]:
    """Yield a .zip of the entries as it is written.

    ZIP64 is switched on per entry by the writer when a file is near 4 GB, and
    names outside ASCII carry the UTF-8 flag, so Vietnamese and Chinese titles
    extract as written rather than as mojibake. A file that has gone missing
    since the ticket was issued is left out rather than failing the rest.
    """
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", allowZip64=True) as archive:  # type: ignore[arg-type]
        for entry in entries:
            try:
                info = zipfile.ZipInfo.from_file(
                    entry.path, entry.name, strict_timestamps=False
                )
                source = entry.path.open("rb")
            except OSError:
                continue
            if entry.path.suffix.lower() in STORED_SUFFIXES:
                info.compress_type = zipfile.ZIP_STORED
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                info.compress_level = 6
            with source, archive.open(info, "w") as target:
                while chunk := source.read(CHUNK_BYTES):
                    target.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()


def content_disposition(filename: str) -> str:
    """`attachment` with both spellings of the name (RFC 6266 / 5987)."""
    fallback = re.sub(r'[^\x20-\x7e]|["\\]', "_", filename)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
