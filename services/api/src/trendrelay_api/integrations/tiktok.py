"""Fetching TikTok media, through yt-dlp rather than a scraper of our own.

Why yt-dlp
----------
TikTok's playback URLs are signed and the signing changes. Writing that
ourselves would mean owning a moving target forever, and re-learning it every
time it moved. yt-dlp already extracts TikTok videos, profiles and collections,
is public domain, and has a community that fixes the extractor within days of a
change - which is the whole reason to depend on it rather than to reimplement
it. This module is a wrapper: it builds a command, runs it, and reads what came
back.

What it does not do
-------------------
Only what the extractors actually deliver. Likes are private on TikTok, and
yt-dlp marks its `sound` and `tag` extractors broken; those modes are absent
from this provider's declaration rather than offered and then failing.

Impersonation
-------------
TikTok increasingly answers plain HTTP clients with nothing at all. yt-dlp can
present itself as a real browser's TLS fingerprint when `curl_cffi` matching its
own build is installed, and TikTok is materially more reliable when it can.
That is a fact about the installation rather than about the request, so it is
reported by `provider_status` instead of being discovered as an intermittent
empty download - measured, listed, and left to the operator to fix.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

#: The same half-hour ceiling one Douyin source gets. A profile larger than
#: this is a resumable stop, not a crash - the archive file below means the
#: next run starts where this one left off.
SOURCE_TIMEOUT_SECONDS = 1800

#: Names the archive of ids already fetched, kept beside the media so a resumed
#: or repeated run skips what it holds. yt-dlp's own mechanism; there is no
#: reason to invent a second one.
ARCHIVE_NAME = ".yt-dlp-archive.txt"

#: What counts as "the video arrived". Kept here rather than imported from the
#: Douyin module, which owns its own copy for its own scanner.
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm"}

#: One file per post, filed under the account it came from, so a profile fetch
#: lands as a folder somebody can recognise. The id keeps two posts with the
#: same title apart.
OUTPUT_TEMPLATE = "%(uploader,uploader_id,id)s/%(id)s.%(ext)s"


def _runtime_root() -> Path | None:
    """Where Tools installs yt-dlp, if it is there.

    Installs go into a directory of their own with `pip --target`, not into the
    API's environment. So "is yt-dlp installed" cannot be answered by importing
    it: the copy Tools just installed is invisible to a plain import, and the
    provider reported "not installed" immediately after a successful install.

    The location is read from the catalogue rather than repeated here, so the
    directory the installer writes to and the directory this runs from cannot
    drift apart.
    """
    try:
        from trendrelay_api.tool_registry import runtime_root_for  # noqa: PLC0415
    except ImportError:
        return None
    root = runtime_root_for("yt-dlp")
    return root if root and root.is_dir() else None


def _executable() -> list[str] | None:
    """How to invoke yt-dlp here, in the order the copies are trusted.

    The runtime copy first: it is the one the catalogue pins and the one Tools
    installs, so it is the one whose version the status reports. Then the API's
    own environment, then a binary on PATH - which may be someone else's, of
    some other vintage, and is the last resort rather than the first answer.
    """
    runtime = _runtime_root()
    if runtime and (runtime / "yt_dlp").is_dir():
        # Run with the runtime ahead of everything else on the module path, so
        # this is the copy that answers rather than an older one installed
        # beside the interpreter.
        return [sys.executable, "-c", _RUNTIME_LAUNCHER, str(runtime)]
    try:
        import yt_dlp  # noqa: F401  PLC0415 - presence is the question
    except ImportError:
        found = shutil.which("yt-dlp")
        return [found] if found else None
    return [sys.executable, "-m", "yt_dlp"]


#: Put the runtime first on `sys.path`, then hand the rest of the command line
#: to yt-dlp exactly as if it had been invoked with `-m`.
_RUNTIME_LAUNCHER = (
    "import sys; sys.path.insert(1, sys.argv[1]); del sys.argv[1];"
    " from yt_dlp import main; main()"
)


def _minimum_revision() -> str:
    """The oldest yt-dlp the catalogue says will do, or "" when it says nothing.

    Read from the catalogue rather than written here, so the version the
    installer fetches and the version this insists on cannot drift apart.
    """
    try:
        from trendrelay_api.tool_registry import _tool  # noqa: PLC0415

        return str((_tool("yt-dlp") or {}).get("minimum_version") or "")
    except Exception:  # noqa: BLE001 - a missing catalogue is not a status failure
        return ""


MINIMUM_REVISION = "2026.08.30"


def _as_numbers(revision: str) -> tuple[int, ...]:
    """`2026.08.30.232658` as numbers, for comparing one build against another.

    yt-dlp versions are date-shaped with an optional nightly suffix, so the
    parts compare numerically and a nightly sorts after the stable release of
    the same day. Anything unparseable sorts as "unknown" rather than as old:
    refusing to run because a version string was surprising is worse than the
    problem it guards.
    """
    parts: list[int] = []
    for piece in revision.strip().split("."):
        digits = "".join(character for character in piece if character.isdigit())
        if not digits:
            return ()
        parts.append(int(digits))
    return tuple(parts)


def _below_minimum(revision: str) -> bool:
    minimum = _minimum_revision() or MINIMUM_REVISION
    installed = _as_numbers(revision)
    wanted = _as_numbers(minimum)
    if not installed or not wanted:
        return False
    # Compared pair by pair so `2026.08.30` and `2026.08.30.232658` agree, the
    # nightly counting as the later of the two.
    return installed < wanted[: len(installed)] or (
        installed[: len(wanted)] < wanted
    )


def _version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            [*command, "--version"],
            capture_output=True, text=True, timeout=60, check=False,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    # stdout only: this environment prints an unrelated urllib3 warning on
    # stderr, and reading the two together turns a version into a paragraph.
    return (completed.stdout or "").strip().splitlines()[-1:][0] if completed.stdout else ""


def _impersonation_available(command: list[str]) -> bool:
    """Whether yt-dlp can present a browser's TLS fingerprint.

    `curl_cffi` being importable is not the question - it has to be a build
    this yt-dlp knows how to drive, and a mismatched pair reports every target
    as unavailable while importing perfectly well.
    """
    try:
        completed = subprocess.run(
            [*command, "--list-impersonate-targets"],
            capture_output=True, text=True, timeout=90, check=False,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    rows = [
        line for line in (completed.stdout or "").splitlines()
        if line.strip() and not line.startswith(("[", "Client", "---"))
    ]
    return any("unavailable" not in line.lower() for line in rows)


#: The browser yt-dlp is asked to look like. Any available target works; this
#: one is asked for by name because leaving the choice to yt-dlp is what the
#: bug below turned out to be.
IMPERSONATE_TARGET = "chrome"


@lru_cache(maxsize=4)
def _impersonation_target(command: tuple[str, ...]) -> str | None:
    """The target to pass, or None when this install has none.

    Cached: it is a property of the installation, not of the request, and
    probing it costs a subprocess that would otherwise run once per source in
    a channel fetch.
    """
    return IMPERSONATE_TARGET if _impersonation_available(list(command)) else None


def provider_status() -> dict[str, Any]:
    """Whether TikTok can be fetched here, and how well.

    Three separate answers, because they need three different things done about
    them: no yt-dlp at all is an install; yt-dlp without impersonation is a
    working setup that will intermittently return nothing on profiles; both
    present is ready.
    """
    command = _executable()
    if not command:
        return {
            "id": "tiktok",
            "installed": False,
            "active": False,
            "ready": False,
            "impersonation": False,
            "revision": "",
            "reason": (
                "yt-dlp is not installed. Install it from Tools and TikTok "
                "links become downloadable."
            ),
        }
    revision = _version(command)
    impersonation = _impersonation_available(command)
    stale = _below_minimum(revision)
    if stale:
        # A build old enough to fail is not a working install. 2026.08.19 reads
        # a single video perfectly and cannot list a channel at all - it
        # answers "Unable to extract secondary user ID" for a channel that a
        # newer build walks without complaint. Reporting "ready" there sent
        # somebody to look at their link, or to the manual scroll-and-copy
        # fallback, for a fault that an upgrade fixes.
        return {
            "id": "tiktok",
            "installed": True,
            "active": True,
            "ready": False,
            "impersonation": impersonation,
            "revision": revision,
            "reason": (
                f"yt-dlp {revision} is too old for TikTok: it can read a single "
                f"video but cannot list a channel. Update it from Tools to "
                f"{MINIMUM_REVISION} or newer."
            ),
        }
    return {
        "id": "tiktok",
        "installed": True,
        "active": True,
        # Not ready without impersonation, because the downloads do not work
        # without it. Metadata still resolves, which is what makes this worth
        # stating plainly: the link looks recognised, the fetch then fails on
        # the webpage request every time. Reporting "ready" here would send
        # somebody to debug their link.
        "ready": impersonation,
        "impersonation": impersonation,
        "revision": revision,
        "reason": "" if impersonation else (
            "yt-dlp here cannot present a browser fingerprint, and TikTok "
            "refuses the download without one - reading a link works, fetching "
            "it does not. Install yt-dlp with its impersonation support "
            "(`yt-dlp[default,curl-cffi]`); a curl_cffi that does not match the "
            "yt-dlp build reports every target unavailable and behaves as if it "
            "were absent."
        ),
    }


def _command_for(url: str, output_root: Path, request: dict[str, Any]) -> list[str]:
    """The whole invocation for one source, as a list nobody has to quote."""
    command = _executable()
    if not command:
        raise RuntimeError("yt-dlp is not installed.")
    kinds = set(request.get("media_kinds") or ["video"])
    args = [
        *command,
        url,
        "--paths", str(output_root),
        "--output", OUTPUT_TEMPLATE,
        # Quiet on stdout so what remains is progress and errors worth reading;
        # the exit code carries the verdict.
        "--no-warnings",
        "--no-progress",
        # Never let one bad post stop a profile: the run keeps going and the
        # failures are reported alongside what did arrive.
        "--ignore-errors",
        # A pinned build is the one that was tested; checking for a newer one
        # mid-download is a network call nobody asked for.
        "--no-update",
        # Written beside the media, so the Library ingest that follows can read
        # a title and a creator without asking TikTok a second time.
        "--write-info-json",
        # Per video, never for the channel as a whole.
        #
        # The playlist-level file is what broke every channel download on this
        # machine. Its name comes from the playlist's own fields, where there
        # is no `uploader` to use - so the template fell through to `id`, a
        # 76-character TikTok channel id, and used it for both the folder and
        # the file. With the job's own output root that came to 269 characters
        # and Windows refused the write, which yt-dlp reports as a fatal
        # "Cannot write playlist metadata to JSON file" before saving a single
        # video. Nothing reads that file; the per-video ones are the ones
        # ingest needs.
        "--no-write-playlist-metafiles",
        # A second belt for the same trouser. Any one component of a path stays
        # short enough that a long id, a long title or a deep workspace folder
        # cannot add up to a refusal 200 files into a channel.
        "--trim-filenames", "80",
        # Windows-safe names even when this runs elsewhere, so a library copied
        # between machines keeps working.
        "--windows-filenames",
    ]
    # Asked for by name rather than left to yt-dlp to choose.
    #
    # This is the whole of the "Unable to extract secondary user ID" failure. A
    # channel page fetched without a browser fingerprint comes back missing the
    # id the extractor needs - not always, which is what made it look like a
    # broken channel rather than a broken request. Measured on two channels:
    # 0/3 and 0/1 without the flag, 4/4 and 3/3 with it.
    #
    # Only when the install actually has a target. Naming one it does not have
    # is a hard error from yt-dlp before it fetches anything.
    target = _impersonation_target(tuple(command))
    if target:
        args += ["--impersonate", target]
    limit = int(request.get("limit") or 0)
    if limit > 0:
        args += ["--playlist-items", f"1-{limit}"]
    if request.get("incremental", True):
        args += ["--download-archive", str(output_root / ARCHIVE_NAME)]
    if "image" in kinds:
        # The cover, as a file beside the video rather than embedded in it.
        args += ["--write-thumbnail"]
    if "audio" in kinds:
        # The track on its own, kept alongside the video rather than instead of
        # it - `--extract-audio` alone would throw the picture away.
        args += ["--keep-video", "--extract-audio", "--audio-format", "m4a"]
    return args


def _detail_from(completed: subprocess.CompletedProcess[str]) -> str:
    """One readable sentence about what happened, from yt-dlp's own words."""
    lines = [
        line.strip()
        for stream in (completed.stderr or "", completed.stdout or "")
        for line in stream.splitlines()
        if line.strip()
    ]
    errors = [line for line in lines if line.startswith("ERROR")]
    if errors:
        return errors[-1]
    for line in reversed(lines):
        if "has already been recorded" in line or "already been downloaded" in line:
            return "Every requested item was already downloaded."
    return lines[-1] if lines else ""


def download_source(
    url: str, output_root: Path, request: dict[str, Any]
) -> tuple[int, str]:
    """Fetch one TikTok source into `output_root`, as (exit code, detail).

    The same contract the Douyin fetch answers, so the job runner around it -
    scanning, fingerprinting, handing files to the Library, honouring a cancel
    between sources - is shared rather than written twice.
    """
    try:
        args = _command_for(url, output_root, request)
    except RuntimeError as error:
        return 1, str(error)
    output_root.mkdir(parents=True, exist_ok=True)
    before = {path for path in output_root.rglob("*") if path.is_file()}
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=SOURCE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        minutes = SOURCE_TIMEOUT_SECONDS // 60
        return 1, (
            f"This source ran past the {minutes}-minute limit for one fetch. "
            "Whatever downloaded is kept; resume to continue from there."
        )
    except OSError as error:
        return 1, f"yt-dlp could not be started: {error}"
    detail = _detail_from(completed)
    # yt-dlp currently has an upstream site issue where a public channel page
    # can render in a browser but omit the secondary user id from its HTML.
    # Do not surface its internal extractor syntax as if the operator had
    # pasted a malformed URL; direct post links remain fully downloadable and
    # the UI offers the supported browser-scroll/import fallback.
    if completed.returncode != 0 and "secondary user id" in detail.lower():
        detail = (
            "TikTok did not expose this channel's ID to yt-dlp. "
            "Open the channel in Chrome, scroll to load posts, then use Import links; "
            "direct video links download normally."
        )
    if completed.returncode == 0:
        code, said = _video_actually_arrived(output_root, request, before)
        if code:
            return code, said
    return completed.returncode, detail


def _video_actually_arrived(
    output_root: Path, request: dict[str, Any], before: set[Path]
) -> tuple[int, str]:
    """Refuse to call it a success when the video asked for is not there.

    TikTok sometimes serves a post with its audio track and no video stream at
    all - the formats list is one entry, `audio`. yt-dlp downloads that entry
    and exits 0, perfectly correctly: it fetched the only thing on offer.

    For a request that asked for video that is not success. The mp3 is then
    filtered out downstream by the requested kinds, so the batch recorded
    nothing, reported nothing, and looked like a download that silently did
    not happen. Said here instead, where the reason is still known.
    """
    if "video" not in set(request.get("media_kinds") or ["video"]):
        return 0, ""
    arrived = {
        path for path in output_root.rglob("*") if path.is_file()
    } - before
    if not arrived:
        return 0, ""
    if any(path.suffix.lower() in VIDEO_SUFFIXES for path in arrived):
        return 0, ""
    kinds = sorted({path.suffix.lstrip(".").lower() for path in arrived})
    # Worded for what arrived rather than for "this post". A channel fetch
    # narrowed to its newest few can land entirely on posts like this, and
    # blaming one post would be describing a source that is mostly fine.
    remedy = (
        "Tick Audio under what to fetch to keep the soundtrack, or raise "
        "the per-source limit if this was a channel's newest post."
        if "audio" not in set(request.get("media_kinds") or [])
        # Advice for a box already ticked is not advice.
        else "Raise the per-source limit if this was a channel's newest post."
    )
    return 3, (
        "No video arrived from this source - TikTok served only "
        + ", ".join(kinds)
        + ". A post can carry its audio track with no video stream, when the "
        "video has been removed or is restricted where this download runs "
        "from. " + remedy
    )


#: Long enough to page a channel listing, short enough that a slow one cannot
#: hold a finished download open. Coverage is an annotation on a job, never a
#: reason for one to hang.
PROFILE_STATS_TIMEOUT_SECONDS = 240


def is_profile_source(url: str) -> bool:
    """Whether this link is a channel rather than one post.

    `tiktok.com/@handle` is the channel; the same handle with `/video/...` or
    `/photo/...` after it is a single post. Douyin says the same thing with
    `/user/`, which is why this cannot be one shared test.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host != "tiktok.com" and not host.endswith(".tiktok.com"):
        return False
    path = parsed.path.lower().rstrip("/")
    if not path.startswith("/@"):
        return False
    return "/video/" not in path and "/photo/" not in path


def _handle(url: str) -> str:
    """The channel's own name, which is also the folder its media lands in."""
    path = urlparse(url).path.lstrip("/")
    return path.split("/")[0].lstrip("@") if path.startswith("@") else ""


def held_counts(workspace_id: str) -> dict[str, int]:
    """Distinct posts held per channel, across every run in this workspace.

    Counted from the files rather than from yt-dlp's archive: the archive
    records which ids were fetched and not whose they are, and the question
    being answered is per channel. `OUTPUT_TEMPLATE` puts every post at
    `<run>/<channel>/<post id>.<ext>`, so the channel is the folder and the id
    is what the name starts with - which is also why this counts distinct ids
    rather than files. One post saves a video, a thumbnail, an info file and
    sometimes an audio track, and four files are still one post.

    Cumulative because each run gets its own folder: a channel fetched over
    three sittings is complete, and a count that only saw the last one would
    say it was a third done.
    """
    from trendrelay_api.integrations.douyin import output_root_for

    root = output_root_for("tiktok") / workspace_id
    if not root.is_dir():
        return {}
    found: dict[str, set[str]] = {}
    for run in root.iterdir():
        if not run.is_dir():
            continue
        for channel in run.iterdir():
            if not channel.is_dir():
                continue
            for item in channel.iterdir():
                if item.is_file():
                    found.setdefault(channel.name, set()).add(item.name.split(".")[0])
    return {name: len(ids) for name, ids in found.items()}


def _declared_total(url: str) -> tuple[int, str]:
    """How many posts this channel lists, and what it calls itself.

    A listing, not a download: `--flat-playlist` asks for the index and no
    media. What it returns is what the channel offers a signed-out reader,
    which is the honest ceiling to measure against - it is not a claim about
    what the channel has ever posted, and this is the same limit the Douyin
    side names in its own note.

    Zero on any failure. Coverage is an annotation on a download, never a
    precondition for one.
    """
    command = _executable()
    if not command:
        return 0, ""
    listing = [
        *command, url, "--flat-playlist", "--dump-single-json",
        "--no-warnings", "--no-update",
    ]
    # The same fingerprint the download itself asks for, and for the same
    # reason: a channel page fetched without one comes back missing the id the
    # extractor needs, and answers "Unable to extract secondary user ID". Not
    # always, which is what makes it look like a broken channel rather than a
    # broken request - this listing succeeded by luck once and failed the next
    # minute on the same channel.
    target = _impersonation_target(tuple(command))
    if target:
        listing += ["--impersonate", target]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            listing,
            capture_output=True,
            text=True,
            timeout=PROFILE_STATS_TIMEOUT_SECONDS,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout or "null")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return 0, ""
    if not isinstance(payload, dict):
        return 0, ""
    total = payload.get("playlist_count")
    if not isinstance(total, int):
        total = len(payload.get("entries") or [])
    name = payload.get("uploader") or payload.get("channel") or payload.get("title") or ""
    return max(0, int(total)), str(name)


def coverage_stats(urls: list[str], *, workspace_id: str) -> list[dict[str, Any]]:
    """Per-channel coverage, in the shape the Douyin side reports it.

    The same badge draws both, so the fields have to be the same fields. What
    differs is where each half comes from: Douyin reads a declared post count
    from its provider and a held count from that provider's own database,
    while a TikTok channel is measured by what its listing offers and by what
    is on this machine. Both answer "how much of this channel is here".

    Only channels. A link to one post has nothing to be a share of.
    """
    channels = [url for url in urls if is_profile_source(url)]
    if not channels:
        return []
    held = held_counts(workspace_id)
    stats: list[dict[str, Any]] = []
    for url in dict.fromkeys(channels):
        handle = _handle(url)
        total, name = _declared_total(url)
        if not total:
            continue
        stats.append({
            "url": url,
            "kind": "profile",
            "nickname": name or handle,
            "declared_total": total,
            # Keyed by the folder yt-dlp writes, which is the channel's own
            # name; the handle from the link is what that resolves to.
            "held": held.get(name, held.get(handle, 0)),
        })
    return stats


def describe_source(url: str) -> dict[str, Any]:
    """What a link is, without downloading it - for a preview, not a fetch.

    Metadata only. Used where the interface wants to show whose profile this is
    before anybody commits to fetching it.
    """
    command = _executable()
    if not command:
        return {}
    try:
        completed = subprocess.run(
            [
                *command, url, "--skip-download", "--dump-single-json",
                "--playlist-items", "1", "--no-warnings", "--no-update",
            ],
            capture_output=True, text=True, timeout=180, check=False,
            encoding="utf-8", errors="replace",
        )
        # stdout alone. The warning this environment prints on stderr is not
        # JSON, and reading both together fails to parse a perfectly good
        # answer.
        payload = json.loads(completed.stdout or "null")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "id": payload.get("id") or "",
        "title": payload.get("title") or "",
        "creator": payload.get("uploader") or payload.get("channel") or "",
        "kind": "profile" if payload.get("_type") == "playlist" else "video",
        "count": len(payload.get("entries") or []) or None,
    }
