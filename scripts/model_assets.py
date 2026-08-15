"""Model files the vision features want, fetched once and verified.

Some capabilities here degrade rather than fail when their model is missing, and
that was meant to be a courtesy. In practice it became the normal state: nobody
knew a file was supposed to be dropped into `.data/models`, so every install ran
the fallback and the fallback is visibly worse. The face detector is the clear
case — without YuNet the blur and the object overlay fall back to OpenCV's
bundled Haar cascade, which finds fewer faces, mistakes patterned clothing for
one, and returns no landmarks at all, so nothing can be tilted to match a head.

These are small (YuNet is 227KB), permissively licensed and redistributable, so
there is no reason for a fresh install not to have them. That is the whole
decision: fetch them at setup rather than describing them in a README.

Every asset is pinned by SHA-256 and the hash is checked before the file is put
where anything will load it. A model is code in the sense that matters — it
decides what the software does to somebody's face — and "downloaded from the
internet at setup time" is only acceptable with that check. A mismatch is a hard
failure to install, never a quiet acceptance.

Nothing here is required. No network, a proxy in the way, or an upstream that
has moved leaves the fallback in place and says so; setup carries on.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Set to skip fetching entirely — an air-gapped machine, or an operator who
#: would rather place the files by hand.
SKIP = "TRENDRELAY_SKIP_MODEL_DOWNLOAD"

DEFAULT_TIMEOUT_SECONDS = 60
#: Nothing here is remotely this big. A ceiling means a redirect to something
#: unexpected cannot fill the disk before the hash gets a chance to reject it.
MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ModelAsset:
    """One downloadable file, and everything needed to trust it."""

    name: str
    #: Where the code that loads it looks, relative to the project root.
    path: str
    #: Both official sources for the same bytes. The second is tried when the
    #: first cannot be reached, not when it returns something unexpected — a
    #: wrong hash is a reason to stop, not to go looking elsewhere.
    urls: tuple[str, ...]
    sha256: str
    size_bytes: int
    licence: str
    #: What is lost without it, said here because that is what an operator
    #: reading a skipped-download message needs to decide whether to care.
    without_it: str

    @property
    def destination(self) -> Path:
        return ROOT / self.path


#: YuNet, the face detector OpenCV's `FaceDetectorYN` runs.
#:
#: The 2023 revision rather than the 2026 one: that newer file has dynamic input
#: dimensions for OpenCV 5's ONNX engine, and `services/api` pins
#: `opencv-python-headless>=4.10,<5`, which infers on the exact shape it is
#: given. Picking the newer file would be picking one for a runtime this project
#: does not install.
YUNET = ModelAsset(
    name="YuNet face detector",
    path=".data/models/face_detection_yunet.onnx",
    urls=(
        # opencv_zoo itself, which is where the file lives. It is stored in Git
        # LFS, so it comes from the media host — `raw.githubusercontent.com`
        # serves a 130-byte pointer, which would arrive as a "model".
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main"
        "/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        # The `opencv` organisation's own mirror on Hugging Face, for a network
        # that cannot reach the first.
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main"
        "/face_detection_yunet_2023mar.onnx",
    ),
    # Confirmed identical from both mirrors, and equal to the `oid` the
    # opencv_zoo LFS pointer commits to — so this is the repository's own
    # record of the content, not just what one server happened to send.
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    size_bytes=232589,
    licence="MIT",
    without_it=(
        "faces are found by OpenCV's bundled Haar cascade, which misses faces at "
        "an angle, mistakes patterned clothing for one, and returns no landmarks "
        "— so an object placed on a face cannot lean with a tilted head"
    ),
)

#: Everything fetched at setup. MediaPipe's `face_landmarker.task` is
#: deliberately absent: it is useless without the optional `mediapipe` package,
#: so downloading it for every install would be fetching a file almost nobody
#: can load. Adding it here is one entry if that changes.
ASSETS: tuple[ModelAsset, ...] = (YUNET,)


def digest_of(path: Path) -> str:
    reader = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            reader.update(block)
    return reader.hexdigest()


def is_installed(asset: ModelAsset) -> bool:
    """Whether the file is present *and* is the file we meant.

    Checked by content rather than by existence. A truncated download from a
    dropped connection is the likely way this goes wrong, and a half-written
    ONNX file loads as an error somewhere far from here.
    """
    destination = asset.destination
    if not destination.is_file() or destination.stat().st_size != asset.size_bytes:
        return False
    return digest_of(destination) == asset.sha256


def _download(url: str, into: Path, timeout: int) -> int:
    """Stream one URL to a file. Returns how many bytes arrived."""
    request = urllib.request.Request(url, headers={"User-Agent": "TrendRelay-setup"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_BYTES:
            raise ValueError(f"offered {declared} bytes, which is too large")
        written = 0
        with into.open("wb") as handle:
            while block := response.read(1024 * 128):
                written += len(block)
                if written > MAX_BYTES:
                    raise ValueError(f"sent more than {MAX_BYTES} bytes")
                handle.write(block)
    return written


def fetch(asset: ModelAsset, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Make sure the asset is in place. Returns whether it is, and why.

    Downloaded to a temporary file and hashed before being moved into place, so
    nothing ever loads a partial file and a wrong one is never put where it
    would be found.

    A transfer that arrives short is a transport problem and the next mirror is
    tried. A *complete* file whose hash does not match is not: two official
    sources disagreeing with the pin is a supply-chain signal, and falling
    through would turn it into "keep trying until something passes".

    The distinction is load-bearing rather than pedantic. A connection that
    yields nothing produces a perfectly valid hash — of the empty string — and
    without the length check that reads as "the upstream file changed", which
    both misdiagnoses it and skips the mirror that would have worked.
    """
    if is_installed(asset):
        return True, "already installed"

    asset.destination.parent.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for url in asset.urls:
        handle, temporary_name = tempfile.mkstemp(
            dir=asset.destination.parent, suffix=".part"
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            written = _download(url, temporary, timeout)
            if written != asset.size_bytes:
                failures.append(
                    f"{url}: {written} bytes arrived, {asset.size_bytes} expected"
                )
                continue
            found = digest_of(temporary)
            if found != asset.sha256:
                return False, (
                    f"the file at {url} is the right length but not the one this "
                    f"build pins (expected {asset.sha256[:12]}…, got {found[:12]}…). "
                    "Nothing was installed."
                )
            # Replace, rather than write in place: anything loading the model
            # sees either the old file or the new one, never a partial one.
            shutil.move(str(temporary), str(asset.destination))
            return True, "installed"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            failures.append(f"{url}: {error}")
        finally:
            temporary.unlink(missing_ok=True)
    return False, "; ".join(failures) or "no source could be reached"


def ensure_all(
    assets: tuple[ModelAsset, ...] = ASSETS,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    announce=print,
) -> bool:
    """Fetch every missing asset. True when they are all in place.

    Never fatal to setup. The features these belong to are built to run without
    them, so a machine with no route to the internet still installs — it just
    gets told, once, what it is running without.
    """
    if os.environ.get(SKIP):
        announce(f"[Models] Skipped: {SKIP} is set.")
        return False

    complete = True
    for asset in assets:
        ok, reason = fetch(asset, timeout)
        if ok and reason == "already installed":
            continue
        if ok:
            announce(f"[Models] Installed {asset.name} ({asset.size_bytes // 1024}KB, "
                     f"{asset.licence}).")
            continue
        complete = False
        announce(
            f"[Models] {asset.name} was not installed: {reason}\n"
            f"          Without it, {asset.without_it}.\n"
            f"          Setup continues; put the file at {asset.path} to enable it."
        )
    return complete


def main() -> int:
    for asset in ASSETS:
        state = "present" if is_installed(asset) else "missing"
        print(f"{asset.name}: {state} ({asset.path})")
    ensure_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
