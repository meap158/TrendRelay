"""Music found on demand, limited to licences a commercial post may carry.

TrendRelay publishes affiliate content, which is commercial use, to networks a
"free for YouTube" licence was never written for. So music is not sourced from
wherever it can be downloaded. It is sourced from an index that says, per track,
what the licence is - and only two of those licences are let through.

Why these two
-------------
- **CC0** - no conditions at all. Nothing is owed.
- **CC BY** - commercial use and adaptation allowed, on one condition: credit.
  That condition is met by the app rather than remembered by a person, because
  a credit that depends on someone remembering it is a credit that gets missed.

Everything else Openverse indexes is refused. `BY-NC` forbids commercial use.
`BY-ND` forbids adaptation, and cutting a track to fit a video and laying it
under a voice is adaptation. `BY-SA` would oblige the finished video itself to
be released under the same licence. The Public Domain Mark is not a licence the
operator chose to accept here, so it is left out rather than assumed equivalent.

Where the rule is enforced
--------------------------
Twice, on purpose. Openverse is asked for `license=cc0,by`, and every result is
checked again on the way out, because an index's filter is a courtesy and this
is a promise. And an import never trusts a licence sent by the browser: it takes
a track id, reads the track back from Openverse, and records what Openverse says
- otherwise a request could claim CC0 for a CC BY track and skip its credit.

What is recorded
----------------
A licence as reported at import is only as good as the index that reported it.
So the track's landing page is kept with it (in the asset's `source_url`), which
is where the licence can be checked again at its origin.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_ROOT = "https://api.openverse.org/v1"
TIMEOUT_SECONDS = 30
#: Openverse asks API clients to identify themselves.
USER_AGENT = "TrendRelay/1.0 (on-demand music library)"

#: Openverse's own licence codes, mapped to SPDX. Nothing outside this table is
#: ever returned or imported.
ALLOWED_LICENCES = frozenset({"cc0", "by"})

#: The largest track worth fetching. A music bed for a short video is a few
#: megabytes; anything past this is an album, a stream or a mistake.
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024

#: What each reported file type is saved as, limited to what the Library can
#: ingest. `mp32` is Openverse's name for an MP3 variant; `ogx` is an Ogg file.
SUFFIXES = {
    "mp3": ".mp3",
    "mp32": ".mp3",
    "ogg": ".ogg",
    "ogx": ".ogg",
    "oga": ".ogg",
    "wav": ".wav",
    "flac": ".flac",
    "m4a": ".m4a",
    "aac": ".aac",
}

_TRACK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class MusicUnavailable(RuntimeError):
    """Openverse could not answer, said in a sentence someone can act on."""


class LicenceRefused(ValueError):
    """A track whose licence this workspace does not publish under."""


def spdx(code: str | None, version: str | None) -> str | None:
    """Openverse's licence code and version as an SPDX identifier.

    SPDX because it is the standard name, and unambiguous where "CC BY" is not:
    3.0 and 4.0 carry different attribution terms.
    """
    code = (code or "").strip().lower()
    version = (version or "").strip()
    if code == "cc0":
        return "CC0-1.0"
    if code == "by" and re.fullmatch(r"\d+\.\d+", version):
        return f"CC-BY-{version}"
    return None


def credit_required(licence: str | None) -> bool:
    """Whether publishing under this licence owes a credit."""
    return bool(licence) and licence.upper().startswith("CC-BY-")


def licence_label(licence: str) -> str:
    """`CC-BY-4.0` as a person writes it: `CC BY 4.0`."""
    if licence == "CC0-1.0":
        return "CC0 1.0"
    return licence.replace("-", " ")


def credit_line(title: str, creator: str | None, licence: str) -> str | None:
    """The credit a post owes for this track, or None when it owes nothing.

    Title, author and licence - the parts of the credit that read in a caption.
    The licence link and the track's own page are kept on the asset rather than
    pasted here: several networks read a link in a caption as spam, and a credit
    that gets a post penalised is a credit nobody keeps.
    """
    if not credit_required(licence):
        return None
    who = f" by {creator.strip()}" if creator and creator.strip() else ""
    return f'Music: "{title.strip()}"{who} ({licence_label(licence)})'


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    creator: str | None
    licence: str
    licence_url: str | None
    landing_url: str | None
    file_url: str
    suffix: str
    duration_ms: int | None
    source: str | None
    genres: tuple[str, ...]

    @property
    def credit(self) -> str | None:
        return credit_line(self.title, self.creator, self.licence)

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "creator": self.creator,
            "license": self.licence,
            "license_label": licence_label(self.licence),
            "license_url": self.licence_url,
            "landing_url": self.landing_url,
            "preview_url": self.file_url,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "genres": list(self.genres),
            "credit_required": credit_required(self.licence),
            "credit": self.credit,
        }


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(
        f"{API_ROOT}{path}{query}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise LookupError("Openverse has no track with that id.") from error
        if error.code == 429:
            raise MusicUnavailable(
                "Openverse is limiting requests from this app. Wait a minute and search again."
            ) from error
        raise MusicUnavailable(f"Openverse answered HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise MusicUnavailable(
            "Openverse could not be reached, so nothing was searched or downloaded."
        ) from error


def _track(row: dict[str, Any]) -> Track | None:
    """A result as a Track, or None when it must not be offered.

    The licence is checked here as well as in the query: an index's filter is a
    courtesy, and this rule is a promise about what gets published.
    """
    if (row.get("license") or "").lower() not in ALLOWED_LICENCES:
        return None
    if row.get("mature"):
        return None
    licence = spdx(row.get("license"), row.get("license_version"))
    suffix = SUFFIXES.get((row.get("filetype") or "").lower())
    track_id = str(row.get("id") or "")
    file_url = str(row.get("url") or "")
    if not (licence and suffix and _TRACK_ID.match(track_id) and file_url.startswith("https://")):
        return None
    duration = row.get("duration")
    return Track(
        id=track_id,
        title=str(row.get("title") or "Untitled").strip()[:280],
        creator=(str(row.get("creator")).strip()[:190] or None) if row.get("creator") else None,
        licence=licence,
        licence_url=row.get("license_url") or None,
        landing_url=row.get("foreign_landing_url") or None,
        file_url=file_url,
        suffix=suffix,
        duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
        source=row.get("source") or None,
        genres=tuple(str(genre) for genre in (row.get("genres") or [])[:6]),
    )


def search(query: str, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """Music matching a query, limited to CC0 and CC BY, non-mature."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Type what the music should sound like.")
    if not 1 <= page <= 50:
        raise ValueError("page must be between 1 and 50.")
    if not 1 <= page_size <= 50:
        raise ValueError("page_size must be between 1 and 50.")
    body = _get(
        "/audio/",
        {
            "q": query[:200],
            "license": ",".join(sorted(ALLOWED_LICENCES)),
            "category": "music",
            "mature": "false",
            "page": page,
            "page_size": page_size,
        },
    )
    tracks = [track for row in body.get("results") or [] if (track := _track(row))]
    return {
        "tracks": [track.payload() for track in tracks],
        "total": int(body.get("result_count") or 0),
        "page": page,
        "page_count": int(body.get("page_count") or 0),
    }


def track(track_id: str) -> Track:
    """One track, read back from Openverse rather than taken from a request.

    Raises LicenceRefused when the track is real but its licence is not one this
    workspace publishes under - including a track whose licence changed at the
    source since it was found.
    """
    if not _TRACK_ID.match(track_id or ""):
        raise ValueError("That is not an Openverse track id.")
    row = _get(f"/audio/{track_id}/")
    found = _track(row) if isinstance(row, dict) else None
    if found is None:
        code = (row.get("license") if isinstance(row, dict) else None) or "unknown"
        raise LicenceRefused(
            f"This track is licensed {code.upper()}, and only CC0 and CC BY music can be "
            "added: the others forbid commercial use or editing, or would bind the "
            "finished video to their own licence."
        )
    return found


def download(found: Track, destination: Path) -> Path:
    """Fetch a track's file into a directory, bounded in size and kind.

    Named after the Openverse id so a second import of the same track lands on
    the same file, and the Library's own hash check does the de-duplication.
    """
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{found.id}{found.suffix}"
    request = urllib.request.Request(found.file_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS * 4) as response:
            kind = (response.headers.get("Content-Type") or "").lower()
            if kind and not (kind.startswith("audio/") or kind == "application/ogg"
                             or kind == "application/octet-stream"):
                raise MusicUnavailable("That track's file is not audio, so it was not saved.")
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_DOWNLOAD_BYTES:
                raise MusicUnavailable("That track is too large to use as background music.")
            partial = target.with_suffix(target.suffix + ".part")
            written = 0
            with partial.open("wb") as handle:
                while chunk := response.read(64 * 1024):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        handle.close()
                        partial.unlink(missing_ok=True)
                        raise MusicUnavailable(
                            "That track is too large to use as background music."
                        )
                    handle.write(chunk)
            if written == 0:
                partial.unlink(missing_ok=True)
                raise MusicUnavailable("That track's file came back empty.")
            partial.replace(target)
    except urllib.error.HTTPError as error:
        raise MusicUnavailable(
            f"The track's host answered HTTP {error.code}, so nothing was saved."
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise MusicUnavailable("The track's host could not be reached.") from error
    return target
