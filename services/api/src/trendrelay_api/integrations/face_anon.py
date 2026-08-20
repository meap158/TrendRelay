"""Replacing a face with one that belongs to nobody.

Blurring says "someone was here and you may not see them". Anonymising says
nothing at all: the face is replaced by a generated one that keeps the original
expression, head pose and gaze, so the shot still reads as a person reacting
rather than a smear. For a bystander caught in a clip that is the better
outcome, and it is the one thing neither the blur nor the identity work can do.

It is not a face swap. No second person's likeness is involved and the new face
belongs to no one, which is exactly why it is the version of this technology
worth having.

Stills only, and that is a property of the model rather than a gap in the
wiring. It generates each face independently at 512x512, so consecutive video
frames would each receive a different invented face and the result would strobe.
Applying it per frame anyway would produce something visibly broken, so video is
refused rather than quietly mangled.

Why it runs in its own process
------------------------------
face_anon_simple is AGPL-3.0. That licence's copyleft reaches whatever it is
combined with, and TrendRelay is not AGPL. So it is installed in its own
checkout with its own virtual environment and invoked as a subprocess: this
package never imports it, and the two exchange argv, files and JSON on stdout.
That is the same arrangement the Douyin downloader already uses, for a different
reason, and it is the difference between using a program and linking a library.

None of that is legal advice, and the gate below exists so the choice is made
deliberately and on the record rather than by a comment nobody reads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trendrelay_api.tool_registry import PROJECT_ROOT

TOOL_ROOT = PROJECT_ROOT / ".tools" / "catalog" / "face-anon-simple"
SOURCE_DIR = TOOL_ROOT / "source"
VENV_PYTHON = TOOL_ROOT / "venv" / (
    "Scripts/python.exe" if os.name == "nt" else "bin/python"
)
WORKER = PROJECT_ROOT / "scripts" / "face_anon_worker.py"
REVISION = "c36f276352873827e9d559ee8d130b7563491171"
ACKNOWLEDGEMENT_FILE = PROJECT_ROOT / ".data" / "face-anon" / "licence-acknowledged.json"
#: Kept off C: deliberately. The three model repos come to roughly ten
#: gigabytes, Hugging Face caches to the home directory by default, and this
#: machine's C: drive had 8GB free - the download would have died most of the
#: way through with a disk error rather than anything about models.
HF_CACHE = PROJECT_ROOT / ".data" / "models" / "huggingface"
#: Generating a face is seconds per face on a GPU and minutes on a CPU.
TIMEOUT_SECONDS = 1800
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

LICENCE_SUMMARY = (
    "face_anon_simple is AGPL-3.0. TrendRelay runs it as a separate program in "
    "its own environment and never links to it, which is what keeps the "
    "copyleft from reaching this codebase - but that is an arrangement you are "
    "choosing, not advice. Confirm you accept using an AGPL tool this way."
)


class FaceAnonUnavailable(RuntimeError):
    """Raised when a face cannot be anonymised, with the reason to show."""


@dataclass(frozen=True)
class AnonSettings:
    """How far the generated face is pushed away from the original."""

    #: How different the new face must be. Low values keep a family
    #: resemblance, which is not anonymity; the paper's own figure is 1.25.
    degree: float = 1.25
    #: Diffusion steps. More is slower and slightly cleaner; 25 is what the
    #: project uses for a photo with several faces in it.
    steps: int = 25
    guidance: float = 4.0
    #: Fixed so the same image anonymises to the same face twice. Without it a
    #: re-run gives a different stranger and the two cuts cannot be compared.
    seed: int = 0


# --------------------------------------------------------------------------- #
# Availability and the licence gate
# --------------------------------------------------------------------------- #


def installed() -> bool:
    return VENV_PYTHON.is_file() and (SOURCE_DIR / "utils").is_dir()


def licence_acknowledged() -> bool:
    try:
        payload = json.loads(ACKNOWLEDGEMENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(payload, dict) and payload.get("acknowledged"))


def acknowledge_licence(actor_user_id: str, accepted: bool = True) -> dict[str, Any]:
    """Record the decision to run an AGPL tool alongside this one."""
    payload = {
        "acknowledged": bool(accepted),
        "actor_user_id": actor_user_id,
        "licence": LICENCE_SUMMARY,
        "licence_id": "AGPL-3.0",
        "isolation": "separate process, separate virtualenv, no linking",
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    ACKNOWLEDGEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ACKNOWLEDGEMENT_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(ACKNOWLEDGEMENT_FILE)
    return payload


def runtime_status() -> dict[str, Any]:
    """Whether faces can be anonymised, and if not, exactly what is missing."""
    present = installed()
    acknowledged = licence_acknowledged()
    if not present:
        reason = (
            "The face anonymiser is not installed. It is a separate checkout "
            "with its own environment: run `npm run face-anon -- install`."
        )
    elif not acknowledged:
        reason = LICENCE_SUMMARY
    else:
        reason = None
    return {
        "id": "face-anon",
        "available": present and acknowledged,
        "reason": reason,
        "runtime_installed": present,
        "licence_acknowledged": acknowledged,
        "licence": "AGPL-3.0",
        "licence_summary": LICENCE_SUMMARY,
        "revision": REVISION,
        "endpoint": hf_endpoint(),
        "isolation": "subprocess",
        "media": "images only",
    }


def _require_available() -> None:
    status = runtime_status()
    if not status["available"]:
        raise FaceAnonUnavailable(status["reason"] or "Unavailable.")


# --------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------- #


#: Where a Hugging Face read token lives on this machine. Under `.data`, which
#: is git-ignored, so the token stays on the machine that holds it and cannot be
#: committed by accident - the failure mode for a secret in a repository is that
#: nobody notices until it is already public.
TOKEN_FILE = PROJECT_ROOT / ".data" / "face-anon" / "hf-token"


def hf_token() -> str | None:
    """The token, from the environment first and the local file second.

    The environment wins so a shell or CI can override without editing
    anything; the file is what makes the setting survive a reboot without
    putting a secret in the project's tracked configuration.
    """
    from_env = os.environ.get("HF_TOKEN", "").strip()
    if from_env:
        return from_env
    try:
        stored = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return stored or None


def save_hf_token(token: str) -> Path:
    """Write the token where only this machine can read it."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip() + "\n", encoding="utf-8")
    if os.name != "nt":
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE


#: A Hugging Face mirror endpoint, when the default is slow or unreachable.
#:
#: This is a delivery mirror, not a re-upload: hf-mirror.com and ModelScope
#: serve the same files from the same publishers, so it changes where a model
#: is fetched from and nothing about whether you may use it. A model that is
#: withdrawn or licensed non-commercially stays withdrawn or non-commercial
#: whichever host answers - that question is settled by the licence gates, not
#: here.
DEFAULT_HF_ENDPOINT = "https://huggingface.co"
ENDPOINT_FILE = PROJECT_ROOT / ".data" / "face-anon" / "hf-endpoint"


def hf_endpoint() -> str:
    """Where model files are fetched from. Environment first, then local file."""
    from_env = os.environ.get("HF_ENDPOINT", "").strip()
    if from_env:
        return from_env.rstrip("/")
    try:
        stored = ENDPOINT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_HF_ENDPOINT
    return (stored or DEFAULT_HF_ENDPOINT).rstrip("/")


def save_hf_endpoint(endpoint: str) -> Path:
    """Record a mirror to use on this machine. Git-ignored, like the token."""
    cleaned = endpoint.strip().rstrip("/")
    if cleaned and not cleaned.startswith("https://"):
        # Weights fetched over plain HTTP can be altered in transit, and a
        # tampered model is a silent failure rather than a loud one.
        raise ValueError("A model mirror must be https.")
    ENDPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENDPOINT_FILE.write_text((cleaned or DEFAULT_HF_ENDPOINT) + "\n", encoding="utf-8")
    return ENDPOINT_FILE


def _environment() -> dict[str, str]:
    token = hf_token()
    return {
        **os.environ,
        **({"HF_TOKEN": token} if token else {}),
        "HF_HOME": str(HF_CACHE),
        "HF_ENDPOINT": hf_endpoint(),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        # Windows without Developer Mode cannot make the symlinks the cache
        # prefers; it falls back to copies and says so loudly on every run.
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def probe() -> dict[str, Any]:
    """What the tool's own environment reports: torch, CUDA, the device."""
    _require_available()
    completed = _run(["--probe"])
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise FaceAnonUnavailable("The anonymiser did not report its runtime.") from error


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    HF_CACHE.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [str(VENV_PYTHON), str(WORKER), *arguments],
        # The checkout is the working directory because the worker imports
        # `utils.anonymize_faces_in_image` from it.
        cwd=SOURCE_DIR,
        env=_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=TIMEOUT_SECONDS,
    )


def anonymise_image(
    source: Path, destination: Path, settings: AnonSettings | None = None
) -> dict[str, Any]:
    """Replace every face in a still image with a generated one."""
    _require_available()
    settings = settings or AnonSettings()
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        # Refused rather than attempted. Run per frame, the model invents a
        # different face each time and the clip strobes; producing that and
        # calling it anonymised would be worse than declining.
        raise FaceAnonUnavailable(
            f"{source.name} is not a still image. This model generates each face "
            "independently, so a video would flicker between different invented "
            "faces. Use the identity-aware blur for video."
        )
    if not source.is_file():
        raise FaceAnonUnavailable(f"No such image: {source}")

    completed = _run([
        str(source), str(destination),
        "--degree", f"{settings.degree}",
        "--steps", str(settings.steps),
        "--guidance", f"{settings.guidance}",
        "--seed", str(settings.seed),
    ])
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        lines = stderr.splitlines()
        last = lines[-1] if lines else "The face anonymiser failed."
        # Read against the whole of stderr, not just its last line. A Python
        # traceback names the cause on the exception line and then adds a
        # generic suggestion after it, so the last line was "If this is a
        # private repository, make sure to pass a token" while the line above
        # it said the repository does not exist. Matching only the last one
        # sent somebody to find a token for a repository no token can reach.
        raise FaceAnonUnavailable(_explain(stderr, fallback=last))
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise FaceAnonUnavailable("The anonymiser produced no readable result.") from error


#: `stabilityai/stable-diffusion-2-1` is no longer on Hugging Face. A logged-out
#: browser gets a plain 404, and a gated repository would instead show its
#: licence panel publicly - gating is visible by design. The API answers 401 for
#: both cases, which is what made this look like a licence problem at first.
MISSING_BASE_MARKERS = (
    "stable-diffusion-2-1",
    "not a valid model identifier",
    "401 Client Error",
    "RepositoryNotFound",
)


def _explain(detail: str, fallback: str | None = None) -> str:
    """Turn the provider's own wording into something an operator can act on.

    `detail` is matched against in full - give it the whole of stderr. What is
    returned when nothing matches is `fallback`, so a caller can search a
    traceback while still reporting the one line worth showing from it.
    """
    if any(marker in detail for marker in MISSING_BASE_MARKERS):
        return (
            "This model builds on stabilityai/stable-diffusion-2-1, which has "
            "been withdrawn from Hugging Face - it is a 404 now, not a licence "
            "gate, so no token or acceptance will fetch it. It needs its VAE "
            "and scheduler from somewhere else before it can run, and picking "
            "that source is a decision about provenance rather than a setting."
        )
    return fallback if fallback is not None else detail


def install_hint() -> str:
    return (
        f"{sys.executable} -m venv {TOOL_ROOT / 'venv'} and install torch, "
        "diffusers, transformers, accelerate, safetensors and face-alignment "
        "into it; the checkout is pinned at "
        f"{REVISION[:12]}."
    )
