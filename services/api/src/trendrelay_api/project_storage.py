"""Keep what this install writes on the project's own drive.

Three separate things had found their way onto `C:` - the system drive, which
on this machine reached zero bytes free while the project drive still had
fifty gigabytes.

The scratch is the one that matters. Rendering writes its intermediates
through `tempfile`, which on Windows means `%LOCALAPPDATA%\\Temp`: a cut, its
frames, its audio and its muxed output all land there before the finished
file is moved into the Library. A single autocut render is happy to write
several gigabytes that way, and it is written to whichever drive Python was
told about at startup rather than the one the media lives on. When that drive
is full the render does not fail politely - it dies partway through with a
disk error, or, worse, an out-of-memory error from a library that could not
grow its page file.

The model caches are the same story more slowly. `insightface` keeps its
detection pack under the home directory, and the Hugging Face and torch
caches do the same unless told otherwise; face-anon's notes already record
`C:` hitting 86 MB free with uv's cache alone holding 4.7 GB of torch wheels.
`face_anon` had redirected Hugging Face for exactly this reason. This does
the rest of them in one place instead of one library at a time.

`TEMP` and `TMP` are set as well as `tempfile.tempdir`, because most of the
heavy writing is done by ffmpeg and yt-dlp rather than by Python, and those
read the environment. Both names are already on the allowlist every
integration builds its subprocess environment from, so a child process
inherits the project's scratch rather than falling back to the system's.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATA_ROOT = PROJECT_ROOT / ".data"

#: Where every intermediate file goes while it is being made.
SCRATCH = DATA_ROOT / "tmp"

#: Where a library's downloaded weights go. `insightface` is not here because
#: it takes its root as an argument rather than reading the environment - see
#: `INSIGHTFACE_ROOT` and its use in `face_identity`.
MODEL_CACHES: dict[str, Path] = {
    "HF_HOME": DATA_ROOT / "models" / "huggingface",
    "TORCH_HOME": DATA_ROOT / "models" / "torch",
    "EASYOCR_MODULE_PATH": DATA_ROOT / "models" / "easyocr",
    "UV_CACHE_DIR": DATA_ROOT / "uv-cache",
}

#: The detection pack `insightface` downloads, kept beside the licence
#: acknowledgement that already lives here rather than under the home
#: directory. Six hundred megabytes on the wrong drive.
INSIGHTFACE_ROOT = DATA_ROOT / "insightface"


def keep_work_on_the_project_drive() -> Path:
    """Point scratch and model caches at the project. Returns the scratch dir.

    Called once at the start of each long-lived process - the API and the
    worker - and safe to call again: it only ever assigns the same values.

    The scratch is set outright because moving it is the whole point. The
    caches are only defaulted, so an operator who has deliberately pointed one
    somewhere else keeps their choice.
    """
    SCRATCH.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(SCRATCH)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(SCRATCH)
    for name, path in MODEL_CACHES.items():
        os.environ.setdefault(name, str(path))
    return SCRATCH


#: How long an abandoned scratch entry is left alone before it is swept.
#:
#: Two days, which is far longer than any render takes and far shorter than
#: the two months of leftovers found here: 3,056 files and 8.6 GB, the oldest
#: from July, of pip build directories, node compile caches, dev-server logs
#: and half-finished cuts. Nothing cleans these up on its own - a render that
#: crashes, or a worker killed mid-job, leaves its directory behind, and
#: `TemporaryDirectory` only removes what it made if the process lives long
#: enough to unwind.
SCRATCH_GRACE = timedelta(days=2)


def sweep_abandoned_scratch(
    older_than: timedelta = SCRATCH_GRACE, *, now: datetime | None = None
) -> tuple[int, int]:
    """Delete scratch entries nothing can still be using. Returns (files, bytes).

    Only by age, and only inside the project's own scratch: an entry younger
    than the grace period might belong to a render happening right now, and an
    entry outside this directory is not ours to touch.

    Never raises. A file held open by another process is skipped and swept on
    the next pass; failing the worker's startup over a leftover would be a
    worse outcome than leaving it on disk.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - older_than
    removed = freed = 0
    if not SCRATCH.is_dir():
        return (0, 0)
    for entry in SCRATCH.iterdir():
        try:
            if datetime.fromtimestamp(entry.stat().st_mtime, UTC) >= cutoff:
                continue
            if entry.is_dir():
                for child in entry.rglob("*"):
                    if child.is_file():
                        freed += child.stat().st_size
                        removed += 1
                shutil.rmtree(entry, ignore_errors=True)
            else:
                freed += entry.stat().st_size
                removed += 1
                entry.unlink(missing_ok=True)
        except OSError:
            continue
    return (removed, freed)
