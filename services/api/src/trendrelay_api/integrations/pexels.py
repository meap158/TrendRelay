"""Pexels as a b-roll source: stock photos and clips, searched by words.

Storytelling cuts pictures to a narration, and a script about a thing nobody
filmed has no pictures in the Library to cut to. Pexels is the answer for the
generic half of that - a city at night, hands typing, a road in the rain - and
is deliberately not the answer for the other half: a dramatised recreation of a
specific event is not stock footage, and pretending otherwise would put a
smiling model in a story about something that happened to somebody.

Read-only and keyed. What comes back is a list of candidates with their
preview URLs and their photographer; nothing is downloaded until somebody picks
one, and what is picked goes through the Library's own ingest like any other
file rather than being rendered from a URL.

Attribution travels with the media. The licence asks for the photographer to be
credited where the media is shown, so the credit is carried on the candidate,
stored on the imported asset, and is not something a caller has to remember.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trendrelay_api.env_store import configured_keys, effective_value

API_KEY_ENV = "PEXELS_API_KEY"
API_ROOT = "https://api.pexels.com"
TIMEOUT_SECONDS = 30

#: One page is what a picker shows without scrolling into the hundreds. A
#: narration wants a handful of good candidates a sentence, not a catalogue.
PAGE_SIZE = 24
MAX_PAGE_SIZE = 80


class PexelsUnavailable(RuntimeError):
    """Pexels could not be asked, or refused to answer."""


@dataclass(frozen=True)
class Candidate:
    """One searchable result, before anybody has committed to downloading it."""

    id: str
    kind: str
    #: What a picker draws. Small, and never the file that would be imported.
    preview_url: str
    #: The file that would be imported, at the largest size worth keeping.
    source_url: str
    width: int
    height: int
    #: Who took it, and where their page is. The licence asks for this to be
    #: shown wherever the media is, so it is carried rather than looked up.
    photographer: str = ""
    photographer_url: str = ""
    #: Where the media itself lives on Pexels, for a credit that links back.
    page_url: str = ""
    duration_seconds: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def credit(self) -> str:
        """The line to show under it, in the form the licence asks for."""
        who = self.photographer.strip() or "Pexels"
        return f"Photo by {who} on Pexels" if self.kind == "image" else (
            f"Video by {who} on Pexels"
        )


def api_key() -> str:
    return effective_value(API_KEY_ENV).strip()


def provider_status() -> dict[str, Any]:
    """Whether b-roll can be searched, and what to do when it cannot.

    Deliberately does not call Pexels. A status check that spends a request on
    every page load is a status check that runs the rate limit down for nothing;
    the first real search is where a wrong key announces itself, and it does so
    with the reason.
    """
    saved = configured_keys((API_KEY_ENV,))[API_KEY_ENV]
    return {
        "id": "pexels",
        "name": "Pexels",
        "configured": bool(saved),
        "key_env": API_KEY_ENV,
        "reason": (
            "" if saved
            else "Add a free Pexels API key in Tools to search stock photos and clips."
        ),
    }


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    key = api_key()
    if not key:
        raise PexelsUnavailable(
            "No Pexels API key is saved. Add one in Tools to search b-roll."
        )
    url = f"{API_ROOT}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Authorization": key})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            raise PexelsUnavailable("Pexels refused the key. Check it in Tools.") from error
        if error.code == 429:
            raise PexelsUnavailable(
                "Pexels is rate limiting this key. Wait a moment before searching again."
            ) from error
        raise PexelsUnavailable(f"Pexels answered HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise PexelsUnavailable("Pexels could not be reached.") from error


def _photo(item: dict[str, Any]) -> Candidate:
    sources = item.get("src") or {}
    return Candidate(
        id=str(item.get("id") or ""),
        kind="image",
        preview_url=str(sources.get("medium") or sources.get("small") or ""),
        # "large2x" rather than "original": original can be a 40-megapixel
        # print scan, and nothing here renders above 1080 lines.
        source_url=str(
            sources.get("large2x") or sources.get("large") or sources.get("original") or ""
        ),
        width=int(item.get("width") or 0),
        height=int(item.get("height") or 0),
        photographer=str(item.get("photographer") or ""),
        photographer_url=str(item.get("photographer_url") or ""),
        page_url=str(item.get("url") or ""),
    )


def _clip(item: dict[str, Any]) -> Candidate:
    files = [entry for entry in (item.get("video_files") or []) if entry.get("link")]
    # The largest file at or under 1080 lines. Above that is bytes nothing will
    # use; below it is a clip that will be upscaled into a narration.
    usable = [entry for entry in files if int(entry.get("height") or 0) <= 1080]
    best = max(
        usable or files,
        key=lambda entry: int(entry.get("height") or 0),
        default={},
    )
    pictures = item.get("video_pictures") or []
    user = item.get("user") or {}
    return Candidate(
        id=str(item.get("id") or ""),
        kind="video",
        preview_url=str(
            (pictures[0].get("picture") if pictures else "") or item.get("image") or ""
        ),
        source_url=str(best.get("link") or ""),
        width=int(best.get("width") or item.get("width") or 0),
        height=int(best.get("height") or item.get("height") or 0),
        photographer=str(user.get("name") or ""),
        photographer_url=str(user.get("url") or ""),
        page_url=str(item.get("url") or ""),
        duration_seconds=float(item.get("duration") or 0) or None,
    )


def search(
    query: str,
    *,
    kind: str = "image",
    per_page: int = PAGE_SIZE,
    page: int = 1,
    orientation: str = "",
    locale: str = "",
) -> dict[str, Any]:
    """Candidates for one search, in the shape a picker draws.

    `orientation` is worth passing: a narration rendered wide wants landscape
    and one rendered tall wants portrait, and asking for the wrong shape means
    every candidate arrives to be cropped in half.
    """
    words = " ".join(query.split())
    if not words:
        raise PexelsUnavailable("Say what the b-roll should show.")
    params: dict[str, Any] = {
        "query": words,
        "per_page": max(1, min(int(per_page), MAX_PAGE_SIZE)),
        "page": max(1, int(page)),
    }
    if orientation in {"landscape", "portrait", "square"}:
        params["orientation"] = orientation
    # Pexels matches its own metadata, which is written in the locale asked
    # for - a Vietnamese script searching in Vietnamese finds what a
    # Vietnamese speaker would call it.
    if locale:
        params["locale"] = locale
    if kind == "video":
        payload = _get("/videos/search", params)
        found = [_clip(item) for item in payload.get("videos") or []]
    else:
        payload = _get("/v1/search", params)
        found = [_photo(item) for item in payload.get("photos") or []]
    return {
        "query": words,
        "kind": kind,
        "page": params["page"],
        # Only what can actually be fetched. A candidate with no file behind it
        # is a tile that fails when it is picked.
        "results": [item for item in found if item.source_url and item.preview_url],
        "total": int(payload.get("total_results") or 0),
        "next_page": bool(payload.get("next_page")),
    }


#: Where fetched b-roll lands before the Library copies it into its own store.
#: Under the downloads root beside the other providers, so one folder holds
#: everything that arrived from outside and one sweep clears it.
def _staging_root() -> Path:
    from trendrelay_api.integrations.douyin import OUTPUT_ROOT

    return OUTPUT_ROOT.parent / "pexels"


def fetch(candidate: Candidate, workspace_id: str) -> Path:
    """Download one candidate, and return where it landed.

    Streamed to a file rather than held in memory: a 1080-line clip is tens of
    megabytes, and a narration may want a dozen of them.
    """
    if not candidate.source_url:
        raise PexelsUnavailable("That result has no file behind it.")
    suffix = ".mp4" if candidate.kind == "video" else ".jpg"
    root = _staging_root() / workspace_id
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"pexels-{candidate.kind}-{candidate.id}{suffix}"
    if destination.is_file() and destination.stat().st_size > 0:
        # Already fetched. The id is Pexels' own, so the same result asked for
        # twice is the same file rather than a second copy.
        return destination
    request = urllib.request.Request(
        candidate.source_url, headers={"Authorization": api_key()},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS * 4) as response:
            temporary = destination.with_suffix(destination.suffix + ".part")
            with temporary.open("wb") as sink:
                while chunk := response.read(1 << 20):
                    sink.write(chunk)
            temporary.replace(destination)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PexelsUnavailable("That b-roll could not be downloaded.") from error
    return destination


def import_candidate(
    candidate: Candidate, *, workspace_id: str, actor_user_id: str, query: str = "",
    factory: Any = None,
) -> dict[str, Any]:
    """Fetch one candidate and queue it into the Library like any other file.

    Through the same ingest as a download or an upload, so b-roll is hashed,
    de-duplicated, thumbnailed and searchable on arrival - and so a narration
    plans over Library assets whatever they came from, with no second path
    through the renderer for "the stock ones".

    The credit is stored on the asset rather than left in the picker. The
    licence asks for the photographer to be shown wherever the media is, and a
    credit that lives only in the search results is one that is lost the moment
    the tab is closed.
    """
    from trendrelay_api.media_library import JOB_SESSION_FACTORY, create_ingest_job

    path = fetch(candidate, workspace_id)
    return create_ingest_job(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        path=str(path),
        title=(query.strip() or "Pexels b-roll")[:200],
        source_type="pexels",
        source_url=candidate.page_url or None,
        platform="pexels",
        creator=candidate.photographer or None,
        caption=candidate.credit,
        engagement={
            "pexels_id": candidate.id,
            "credit": candidate.credit,
            "photographer": candidate.photographer,
            "photographer_url": candidate.photographer_url,
            "source_page": candidate.page_url,
            "searched_for": query,
        },
        factory=factory or JOB_SESSION_FACTORY,
    )
