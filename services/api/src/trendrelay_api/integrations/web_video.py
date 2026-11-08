"""Fetching video from any site yt-dlp supports.

This is the generic half of what `tiktok.py` does for one site. It runs the same
pinned yt-dlp the same way, through the same helpers (found, versioned and
probed in `tiktok.py` and reached through that module so one install and one
set of test doubles serve both), and answers the same contract the Douyin fetch
does: a source in, an exit code and a sentence out. Scanning, de-duplication and
Library import are the job runner's, shared with every service.

What differs from TikTok, and why
---------------------------------
* **Only sites with a real extractor.** yt-dlp falls back to a generic extractor
  that will fetch any address and look for video in the page. Run on the
  server, from a link somebody pasted, that is a way to make this machine
  request arbitrary URLs. It is switched off, so a link is fetched only if
  yt-dlp has an extractor written for that site. `check_supported` asks the
  extractors up front and refuses the rest in a sentence naming them.
* **Output a browser plays and a disk can afford.** YouTube serves the picture
  and the sound separately, and left alone yt-dlp picks the largest: 4K VP9 or
  AV1 in a webm. Capped near 1080p, H.264 and AAC, merged into an mp4, which
  the Library plays without a proxy.
* **FFmpeg is required.** The merge needs it. Without it yt-dlp quietly falls
  back to a single low-resolution file, which is worse than saying so.
* **No impersonation requirement.** TikTok refuses a plain HTTP client; most
  sites do not. If this install can present a browser fingerprint it does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from trendrelay_api.integrations import tiktok as engine

#: Same ceiling as one TikTok source: a long playlist stops and resumes from the
#: archive file rather than being killed mid-way.
SOURCE_TIMEOUT_SECONDS = engine.SOURCE_TIMEOUT_SECONDS

#: Creator folder, then a name that stays unique when two sites share an id.
OUTPUT_TEMPLATE = "%(uploader,channel,uploader_id,id)s/%(extractor_key)s-%(id)s.%(ext)s"

#: About 1080p, H.264 and AAC where the site offers them, merged into an mp4.
FORMAT_SORT = "res:1080,vcodec:h264,acodec:m4a"

#: Run inside the same interpreter yt-dlp uses, because the install lives in a
#: directory of its own and cannot be imported from here. Reads links on stdin
#: and prints which extractor claims each, or null. No network.
_EXTRACTOR_CHECK = """
import json, sys
if len(sys.argv) > 1 and sys.argv[1]:
    sys.path.insert(1, sys.argv[1])
from yt_dlp.extractor import gen_extractor_classes
classes = [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]
answer = {}
for url in json.load(sys.stdin):
    answer[url] = next((ie.ie_key() for ie in classes if ie.suitable(url)), None)
print(json.dumps(answer))
"""


def _ffmpeg() -> Path | None:
    from trendrelay_api.integrations.openmontage_runtime import FFMPEG  # noqa: PLC0415

    return FFMPEG if FFMPEG.is_file() else None


def _js_runtime_args() -> list[str]:
    """Which JavaScript runtime YouTube's player needs, when deno is absent.

    yt-dlp looks for deno on its own. Node is accepted but has to be named.
    """
    if shutil.which("deno") or not shutil.which("node"):
        return []
    return ["--js-runtimes", "node"]


def provider_status() -> dict[str, Any]:
    """Whether other sites can be fetched here."""
    command = engine._executable()
    if not command:
        return {
            "id": "web",
            "installed": False,
            "active": False,
            "ready": False,
            "revision": "",
            "reason": (
                "yt-dlp is not installed. Install it from Tools and links from "
                "YouTube and other sites become downloadable."
            ),
        }
    revision = engine._version(command)
    if engine._below_minimum(revision):
        return {
            "id": "web",
            "installed": True,
            "active": True,
            "ready": False,
            "revision": revision,
            "reason": (
                f"yt-dlp {revision} is too old. Update it from Tools to "
                f"{engine.MINIMUM_REVISION} or newer."
            ),
        }
    if _ffmpeg() is None:
        return {
            "id": "web",
            "installed": True,
            "active": True,
            "ready": False,
            "revision": revision,
            "reason": (
                "FFmpeg is missing. Sites serve picture and sound separately and "
                "joining them needs it. Run npm install."
            ),
        }
    return {
        "id": "web",
        "installed": True,
        "active": True,
        "ready": True,
        "revision": revision,
        "reason": "",
    }


def extractor_keys(urls: list[str]) -> dict[str, str | None]:
    """Which yt-dlp extractor claims each link, or None when none does.

    Answered offline from the installed extractors. Returns every link as None
    when the extractors cannot be asked (yt-dlp only on PATH, or the check
    failed); `check_supported` reads that as "do not know" and lets yt-dlp
    decide, which with the generic extractor off still refuses an unknown site.
    """
    runtime = engine._runtime_root()
    command = engine._executable()
    if not command or not (runtime or command[:2] == [sys.executable, "-m"]):
        return {}
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _EXTRACTOR_CHECK, str(runtime) if runtime else ""],
            input=json.dumps(list(urls)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        answer = json.loads((completed.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {}
    return {url: (answer.get(url) or None) for url in urls} if isinstance(answer, dict) else {}


def check_supported(urls: list[str]) -> dict[str, str]:
    """The extractor for every link, or a ValueError naming the ones with none."""
    found = extractor_keys(urls)
    if not found:
        return {}
    missing = [url for url in urls if not found.get(url)]
    if missing:
        shown = ", ".join(sorted({urlparse(url).hostname or url for url in missing})[:5])
        raise ValueError(
            f"yt-dlp has no support for {shown}. Only sites it has an extractor "
            "for can be downloaded. Check the link, or paste one from a "
            "supported site."
        )
    return {url: str(key) for url, key in found.items() if key}


def platform_of(extractor_key: str | None, url: str = "") -> str:
    """A short lowercase platform name for the Library, from the extractor.

    `Youtube` and `YoutubeTab` are both YouTube; `Instagram` and `InstagramIOS`
    are both Instagram. Falls back to the link's host, then to `web`.
    """
    key = (extractor_key or "").strip()
    if key:
        base = key
        for suffix in ("Tab", "Playlist", "Channel", "User", "Story", "IOS", "Live", "Clip", "Search"):
            if base.endswith(suffix) and len(base) > len(suffix) + 2:
                base = base[: -len(suffix)]
                break
        return base.lower()[:80]
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    return (parts[-2] if len(parts) >= 2 else host) or "web"


def _command_for(url: str, output_root: Path, request: dict[str, Any]) -> list[str]:
    """The whole invocation for one source, as a list nobody has to quote."""
    command = engine._executable()
    if not command:
        raise RuntimeError("yt-dlp is not installed.")
    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is missing. Run npm install.")
    kinds = set(request.get("media_kinds") or ["video"])
    args = [
        *command,
        "--paths", str(output_root),
        "--output", OUTPUT_TEMPLATE,
        "--no-warnings",
        "--no-progress",
        "--ignore-errors",
        "--no-update",
        # Only sites with an extractor of their own; see the module note.
        "--use-extractors", "default,-generic",
        "--ffmpeg-location", str(ffmpeg),
        "--format-sort", FORMAT_SORT,
        "--merge-output-format", "mp4",
        # A video link that also names a playlist is the video. A link that is
        # only a playlist is still the playlist.
        "--no-playlist",
        # Read by Library import for the title, creator, date and platform.
        "--write-info-json",
        "--no-write-playlist-metafiles",
        "--trim-filenames", "80",
        "--windows-filenames",
        *_js_runtime_args(),
    ]
    target = engine._impersonation_target(tuple(command))
    if target:
        args += ["--impersonate", target]
    limit = int(request.get("limit") or 0)
    if limit > 0:
        args += ["--playlist-items", f"1-{limit}"]
    if request.get("incremental", True):
        args += ["--download-archive", str(output_root / engine.ARCHIVE_NAME)]
    if "image" in kinds:
        args += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
    if "audio" in kinds:
        args += ["--keep-video", "--extract-audio", "--audio-format", "m4a"]
    # After every option, so a link that begins with a dash cannot be one.
    args += ["--", url]
    return args


def download_source(
    url: str, output_root: Path, request: dict[str, Any]
) -> tuple[int, str]:
    """Fetch one link into `output_root`, as (exit code, detail)."""
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
    detail = engine._detail_from(completed)
    if completed.returncode == 0:
        code, said = engine._video_actually_arrived(output_root, request, before)
        if code:
            return code, said.replace("TikTok served", "the site served")
    return completed.returncode, detail
