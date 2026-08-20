"""Isolated local transcription and OCR providers for Media Library drafts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from trendrelay_api.config import get_settings
from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations.openmontage_runtime import FFMPEG
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
    now_utc,
    report_progress,
)
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import DurableJob
from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

JOB_KIND = "media_enrichment"
JOB_SESSION_FACTORY = SessionFactory
RUNTIME_ROOT = PROJECT_ROOT / ".tools" / "media-ai" / "runtime"
MODEL_ROOT = PROJECT_ROOT / ".data" / "media-ai" / "models"
WORK_ROOT = PROJECT_ROOT / ".data" / "media-ai" / "work"
SPEECH_PACKAGE = "faster-whisper"
SPEECH_VERSION = "1.2.1"
OCR_PACKAGE = "rapidocr"
OCR_VERSION = "3.9.2"
ONNX_VERSION = "1.28.0"
#: Shares the CTranslate2 runtime faster-whisper already installs, so this is a
#: package rather than a second stack. Language pairs arrive separately.
TRANSLATE_PACKAGE = "argostranslate"
TRANSLATE_VERSION = "1.11.0"
Mode = Literal["speech", "ocr"]


def _runtime_path() -> None:
    value = str(RUNTIME_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)


def _module_present(name: str) -> bool:
    _runtime_path()
    return importlib.util.find_spec(name) is not None


#: What each provider needs in the isolated runtime before it can run at all.
PROVIDER_MODULES: dict[str, tuple[str, ...]] = {
    "speech": ("faster_whisper",),
    "ocr": ("rapidocr", "onnxruntime"),
    "translate": ("argostranslate",),
}


def runtime_ready(provider: str) -> bool:
    return all(_module_present(name) for name in PROVIDER_MODULES[provider])


def _translation_pairs() -> list[dict[str, str]]:
    """Which language directions are installed, or none if the runtime is not.

    Imported lazily and failure-tolerant because this is called to build a
    status page: a machine that never prepared the runtime should see an empty
    list, not an error where the page should be.
    """
    try:
        from trendrelay_api.subtitle_translate import installed_pairs

        return installed_pairs()
    except Exception:
        return []


def _active_tools() -> dict[str, bool]:
    return {item["id"]: bool(item["active"]) for item in list_tools()}


def provider_status() -> dict[str, Any]:
    active = _active_tools()
    model = get_settings().media_ai_speech_model
    model_root = MODEL_ROOT / "faster-whisper"
    model_cached = model_root.is_dir() and any(model_root.rglob("model.bin"))
    speech_runtime = runtime_ready("speech")
    ocr_runtime = runtime_ready("ocr")
    translate_runtime = runtime_ready("translate")
    return {
        "speech": {
            "provider": f"faster-whisper {SPEECH_VERSION}",
            "tool_id": PROVIDER_TOOL["speech"],
            "source_active": active.get("faster-whisper", False),
            "runtime_ready": speech_runtime,
            "model": model,
            "model_cached": model_cached,
            # Everything the runtime needs is downloaded, whether or not the
            # operator currently has the provider switched on. This is what
            # separates "not set up yet", which costs a download, from "turned
            # off", which is one click either way.
            "prepared": bool(speech_runtime and model_cached),
            "ready": bool(
                active.get("faster-whisper", False) and speech_runtime and model_cached
            ),
            "network_during_analysis": False,
        },
        "ocr": {
            "provider": f"RapidOCR {OCR_VERSION} / ONNX Runtime {ONNX_VERSION}",
            "tool_id": PROVIDER_TOOL["ocr"],
            "source_active": active.get("rapidocr", False),
            "runtime_ready": ocr_runtime,
            "prepared": ocr_runtime,
            "ready": bool(active.get("rapidocr", False) and ocr_runtime),
            "network_during_analysis": False,
        },
        "translation": {
            "provider": f"Argos Translate {TRANSLATE_VERSION}",
            "tool_id": PROVIDER_TOOL["translate"],
            "source_active": active.get("argos-translate", False),
            "runtime_ready": translate_runtime,
            # Which directions can be translated right now. A language pair is
            # a separate download, so a ready runtime with no packages can
            # still translate nothing - and offering a target that will fail is
            # worse than not offering it.
            "pairs": _translation_pairs(),
            "prepared": bool(translate_runtime and _translation_pairs()),
            "ready": bool(
                active.get("argos-translate", False)
                and translate_runtime
                and _translation_pairs()
            ),
            "network_during_analysis": False,
        },
        "review_required": True,
        "runtime_root": str(RUNTIME_ROOT),
    }


# --------------------------------------------------------------------------- #
# Preparing a provider
# --------------------------------------------------------------------------- #
#
# Getting a provider running means a git checkout, a pip download of a few
# hundred megabytes, and a model or language pack fetched over the network. That
# used to be a command in the documentation, and an interface that ends in "now
# open a terminal and type this" is an interface that stops there: it cannot say
# how far the download got, cannot report why it failed, and leaves the operator
# to work out which of three separate things is the one that is missing.
#
# So it is a durable job, like every other minutes-long piece of work here. The
# app queues it, the worker runs it, and the same progress and error surface
# every other job already has explains itself.

SETUP_JOB_KIND = "media_ai_setup"
#: Durable jobs are keyed by workspace and this work is not: one machine has one
#: runtime, shared by every workspace signed in to it. Sharing a key is what
#: stops two workspaces queueing the same multi-hundred-megabyte download twice.
SETUP_WORKSPACE_KEY = "local-machine"
#: The runtime is downloaded once and then kept, so a stalled attempt should
#: report itself rather than silently spending another twenty minutes.
SETUP_MAX_ATTEMPTS = 1
SETUP_LEASE_SECONDS = 3600

#: Which catalog entry gates each provider. Preparing a runtime is also
#: accepting a third-party tool, so the switch flipped here is the same one the
#: Tools page shows - there is no second, hidden way to turn a provider on.
PROVIDER_TOOL: dict[str, str] = {
    "speech": "faster-whisper",
    "ocr": "rapidocr",
    "translate": "argos-translate",
}

#: Pinned to the same versions `provider_status` reports, so what the page says
#: is running is what was installed.
PROVIDER_PACKAGES: dict[str, tuple[str, ...]] = {
    "speech": (f"faster-whisper=={SPEECH_VERSION}",),
    "ocr": (f"rapidocr=={OCR_VERSION}", f"onnxruntime=={ONNX_VERSION}"),
    "translate": (f"argostranslate=={TRANSLATE_VERSION}",),
}

#: Prepared by default because they are the directions TrendRelay's own
#: interface implies: the languages it is translated into, paired with English,
#: which is the hub Argos routes most pairs through anyway.
DEFAULT_TRANSLATION_PAIRS: tuple[tuple[str, str], ...] = (
    ("en", "vi"), ("vi", "en"),
    ("en", "ja"), ("ja", "en"),
    ("en", "fr"), ("fr", "en"),
    ("en", "zh"), ("zh", "en"),
    ("en", "ru"), ("ru", "en"),
    ("en", "ar"), ("ar", "en"),
)


def pip_install(packages: tuple[str, ...] | list[str]) -> None:
    """Packages into the isolated runtime, never into the API's own environment.

    `--target` rather than a virtual environment because the API already adds
    this directory to `sys.path` on demand: a provider the operator never
    prepared costs nothing, and one they did is importable without a second
    interpreter to keep in step with this one.
    """
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--upgrade",
            "--target", str(RUNTIME_ROOT), *packages,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode == 0:
        return
    # pip's own last words, not "exit code 1". The usual causes - no network, a
    # wheel with no build for this Python - are all named in that output, and
    # the operator cannot see the console this ran in.
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    raise RuntimeError(
        "The download failed. " + (detail[-1][:400] if detail else "pip reported no reason.")
    )


def _prepare_speech(stage: Any = None) -> list[str]:
    """Fetch the configured Whisper model into the local cache.

    Anonymously, deliberately. The Systran models are public, and
    `huggingface_hub` otherwise resolves a token from the environment or from
    whatever `huggingface-cli login` last wrote to the user's home directory.
    A stale one there is not ignored: the Hub answers a credentialled request
    for a public repo with 401, which arrives as `RepositoryNotFoundError` and
    reads as though the model does not exist. That is what it said on this
    machine, against a token from two years ago and a repo that resolves fine
    with no token at all.

    `False` rather than `None` is the distinction that matters - `None` means
    "find me a token", `False` means "send none".
    """
    _runtime_path()
    from faster_whisper import WhisperModel

    settings = get_settings()
    if stage:
        stage(0.1, f"Downloading the {settings.media_ai_speech_model} speech model")
    model_root = MODEL_ROOT / "faster-whisper"
    model_root.mkdir(parents=True, exist_ok=True)
    WhisperModel(
        settings.media_ai_speech_model,
        device="cpu",
        compute_type="int8",
        download_root=str(model_root),
        use_auth_token=False,
    )
    return []


def _prepare_ocr(stage: Any = None) -> list[str]:
    _runtime_path()
    if importlib.util.find_spec("rapidocr") is None:
        raise RuntimeError("RapidOCR was downloaded but cannot be imported.")
    from rapidocr import RapidOCR

    RapidOCR()
    return []


def _argos_available_packages(package: Any) -> list[Any]:
    """The Argos catalogue, without walking into its retry loop.

    `argostranslate.package` has a trap in it:

        except FileNotFoundError:
            update_package_index()          # catches everything, returns
            return get_available_packages() # index still absent, recurse

    `update_package_index` swallows the reason it failed, so the recursion has
    no exit but `RecursionError` - about a thousand requests deep. Against
    `raw.githubusercontent.com`, which is where the catalogue lives, the loop
    earns the 429 that then keeps it going, and "Preparing the model" sits
    there until something gives out. That is what it was doing here.

    So the index is checked rather than assumed, and a usable one from an
    earlier run is preferred to a failed refresh. `get_available_packages` is
    only ever called with the file already on disk, which is the one condition
    under which it cannot recurse.
    """
    from argostranslate import settings

    index = Path(settings.local_package_index)

    def usable() -> bool:
        try:
            return bool(json.loads(index.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return False

    had_one = usable()
    # Never fatal on its own: a stale catalogue still installs packages, and it
    # is a better answer than refusing to translate because GitHub is busy.
    package.update_package_index()
    if not usable() and not _fetch_argos_index(settings.remote_package_index, index):
        if had_one:
            raise RuntimeError(
                "The language catalogue could not be refreshed and the copy on disk "
                "is unreadable. Delete it and try again: " + str(index)
            )
        raise RuntimeError(
            "The language catalogue could not be downloaded from "
            f"{settings.remote_package_index} or its mirror. That host rate-limits, "
            "so this is usually temporary - wait a few minutes and try again. Set "
            "ARGOS_PACKAGE_INDEX to name a different source."
        )
    return list(package.get_available_packages())


#: Other routes to the same catalogue, for when `raw.githubusercontent.com`
#: rate-limits - which it does per address, and was answering 429 to every
#: request from this machine.
#:
#: The API's contents endpoint is first because it is the one that held up when
#: this was measured. It keeps a rate limit of its own, but a separate one, and
#: sixty an hour is generous for fetching a catalogue. jsDelivr is second and
#: not relied upon: it served the file and then answered 404 for the same URL
#: minutes later, which is branch references being cached inconsistently.
#: Proxies that merely front raw.githubusercontent - githack and friends -
#: inherit its 429 and are no use here.
#:
#: Only the index needs any of this. The packages come from argos-net.com and
#: were reachable throughout.
ARGOS_INDEX_MIRRORS = (
    "https://api.github.com/repos/argosopentech/argospm-index/contents/index.json",
    "https://cdn.jsdelivr.net/gh/argosopentech/argospm-index@main/index.json",
)


def _argos_index_payload(body: bytes) -> bytes | None:
    """The catalogue itself, unwrapped, or None if this is not a catalogue.

    The API's contents endpoint answers with the file base64-encoded inside a
    JSON envelope, so the two mirrors do not return the same shape. Checked
    rather than trusted: a 429 body or an error page is still bytes, and
    writing one to the path argostranslate reads would trade a clear failure
    for a confusing one.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if isinstance(parsed, dict) and parsed.get("encoding") == "base64":
        try:
            body = base64.b64decode(parsed.get("content", ""))
            parsed = json.loads(body)
        except (ValueError, binascii.Error):
            return None
    return body if isinstance(parsed, list) and parsed else None


def _fetch_argos_index(configured: str, destination: Path) -> bool:
    """Write a usable catalogue from a mirror. True if one arrived.

    Skipped entirely when the operator pointed `ARGOS_PACKAGE_INDEX` somewhere
    of their own: a mirror of the default index is not what they asked for, and
    silently substituting it would be the wrong kind of helpful.
    """
    import urllib.request

    if "argosopentech/argospm-index" not in configured:
        return False
    for url in ARGOS_INDEX_MIRRORS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "TrendRelay"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = _argos_index_payload(response.read())
        except OSError:
            continue
        if payload is None:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return True
    return False


#: How long a socket may say nothing before the download is treated as dead.
#:
#: Per socket operation rather than per transfer, so a slow package still
#: arrives - a hundred megabytes at dial-up speed never waits two minutes
#: between packets - while one that has stopped sending is given up on.
ARGOS_SOCKET_TIMEOUT = 120.0


@contextmanager
def _socket_deadline(seconds: float):
    """Give argostranslate's downloads a timeout, since it passes none.

    Both of its network calls are bare `urllib.request.urlopen(...)` with no
    `timeout`, and Python's default is to wait forever. A connection that is
    accepted and then stalls therefore hangs the whole setup: this run stopped
    18 minutes into fetching ru->en with nothing downloaded and nothing logged,
    which from the interface is indistinguishable from the loop fixed above.

    Set globally because that is the only way in - `urlopen` consults the
    default when given no timeout of its own. Scoped to this call and restored
    afterwards, and safe to do here because provider setup runs in the durable
    worker rather than in the process serving requests.
    """
    import socket

    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)


def _prepare_translate(stage: Any = None) -> list[str]:
    """Fetch the language packages, reporting rather than failing on a gap.

    Eleven working directions and one missing is a better outcome than none, and
    the caller records what was skipped so the interface can say which.
    """
    _runtime_path()
    from trendrelay_api.subtitle_translate import _argos_settings

    _argos_settings()
    from argostranslate import package

    say = stage or (lambda fraction, label: None)
    with _socket_deadline(ARGOS_SOCKET_TIMEOUT):
        skipped = _install_translation_packages(package, say)
        say(0.95, "Fetching the sentence splitters")
        skipped += _fetch_sentence_splitters(say)
    return skipped


def _fetch_sentence_splitters(stage: Any = None) -> list[str]:
    """Download the sentence models now, so translating needs no network later.

    Argos fetches these on first use, which would put a download inside the
    first caption an operator translates - and the card promises this provider
    runs on the machine with nothing uploaded. Pulling them here is what makes
    that promise true rather than true-after-a-warm-up.

    Only for the languages we install packages for. MiniSBD publishes eighty-two
    and there is no reason to hold the ones nobody can translate from.
    """
    # The mapping between Argos's language codes and MiniSBD's names lives in
    # the sentencizer that consumes it rather than being copied here.
    from argostranslate.sbd import MiniSBDSentencizer
    from minisbd import models

    published = set(models.list_models())
    wanted: set[str] = set()
    for source, _target in DEFAULT_TRANSLATION_PAIRS:
        name = MiniSBDSentencizer.LANGUAGE_CODE_MAPPING.get(source, source)
        wanted.add(name if name in published else "en")

    skipped: list[str] = []
    for name in sorted(wanted):
        try:
            models.get_model_file(name)
        except Exception as error:
            # One missing splitter is not worth failing the whole setup: Argos
            # will try again on first use, and every other language still works.
            skipped.append(f"sentence splitter {name}: {type(error).__name__}")
    return skipped


def _install_translation_packages(package: Any, stage: Any = None) -> list[str]:
    say = stage or (lambda fraction, label: None)
    available = _argos_available_packages(package)
    installed = {(item.from_code, item.to_code) for item in package.get_installed_packages()}
    skipped: list[str] = []
    total = len(DEFAULT_TRANSLATION_PAIRS)
    for index, (source, target) in enumerate(DEFAULT_TRANSLATION_PAIRS):
        # Named and counted, because these are not uniform: most are around
        # sixty megabytes and ru->en is a hundred and fifty-six, which on this
        # host arrived at a fiftieth of the speed of the rest and took an hour
        # and fifty minutes. Against a bar that said only "Preparing the model"
        # that is indistinguishable from a hang, and it is the reason somebody
        # gives up on a download that was going to finish.
        say(index / total, f"Downloading {source}→{target} ({index + 1} of {total})")
        if (source, target) in installed:
            continue
        match = next(
            (item for item in available
             if item.from_code == source and item.to_code == target),
            None,
        )
        if match is None:
            skipped.append(f"{source}→{target}: no package published")
            continue
        try:
            package.install_from_path(match.download())
        except Exception as error:
            skipped.append(f"{source}→{target}: {type(error).__name__}")
    return skipped


PROVIDER_PREPARE = {
    "speech": _prepare_speech,
    "ocr": _prepare_ocr,
    "translate": _prepare_translate,
}


def prepare_provider(provider: str, *, on_stage: Any = None) -> list[str]:
    """Install, download, warm and switch on one provider. Returns what it skipped.

    The whole of "enable transcription" in one call, because it is one decision.
    Split across three buttons it becomes three chances to stop half way and a
    status page that says a provider is unavailable without saying which of the
    three steps is the reason.
    """
    if provider not in PROVIDER_TOOL:
        raise ValueError(f"Unknown media analysis provider: {provider}.")
    from trendrelay_api.tool_registry import install_tool, list_tools, set_active

    stage = on_stage or (lambda fraction, label: None)
    tool_id = PROVIDER_TOOL[provider]
    tool = next((item for item in list_tools() if item["id"] == tool_id), None)
    if tool is None:
        raise RuntimeError(f"{tool_id} is not in the tool catalog.")

    if not tool["installed"]:
        stage(0.05, "Fetching the pinned source")
        install_tool(tool_id)
    if not runtime_ready(provider):
        stage(0.25, "Downloading the runtime")
        pip_install(PROVIDER_PACKAGES[provider])
    stage(0.6, "Preparing the model")
    # The prepare step owns the longest stretch of this job by far - one
    # language package here took an hour and fifty minutes - so it is given the
    # rest of the bar to report itself across rather than leaving 0.6 on screen
    # until it is done.
    skipped = PROVIDER_PREPARE[provider](
        lambda fraction, label: stage(0.6 + 0.35 * max(0.0, min(1.0, fraction)), label)
    )
    stage(0.95, "Switching the provider on")
    set_active(tool_id, True)
    return skipped


def create_setup_job(
    provider: str, *, actor_user_id: str, factory=None
) -> dict[str, Any]:
    """Queue the preparation, or hand back the one already running.

    Asking twice is what an operator does when a download looks stuck, and two
    pip processes writing the same directory is how it actually breaks. So a
    second request joins the first rather than starting one.
    """
    if provider not in PROVIDER_TOOL:
        raise ValueError(f"Unknown media analysis provider: {provider}.")
    factory = factory or JOB_SESSION_FACTORY
    unfinished = _unfinished_setup_job(provider, factory=factory)
    if unfinished:
        return unfinished
    job_id = "mediaaisetup_" + hashlib.sha256(
        f"{provider}:{PROVIDER_PACKAGES[provider]}:{now_utc().isoformat()}".encode()
    ).hexdigest()[:20]
    return create_job_record(
        job_id,
        SETUP_WORKSPACE_KEY,
        SETUP_JOB_KIND,
        {
            "id": job_id,
            "provider": provider,
            "tool_id": PROVIDER_TOOL[provider],
            "packages": list(PROVIDER_PACKAGES[provider]),
            "actor_user_id": actor_user_id,
        },
        max_attempts=SETUP_MAX_ATTEMPTS,
        factory=factory,
    )


def _unfinished_setup_job(provider: str, *, factory) -> dict[str, Any] | None:
    for record in list_job_records(SETUP_WORKSPACE_KEY, SETUP_JOB_KIND, 20, factory=factory):
        if record["payload"].get("provider") != provider:
            continue
        if record["status"] in {"queued", "running"} and not record["stalled"]:
            return record
        # Only the newest attempt per provider decides; an older running row
        # whose worker died is history, not a reason to refuse.
        return None
    return None


def latest_setup_jobs(*, factory=None) -> dict[str, dict[str, Any]]:
    """The most recent preparation per provider, for the interface to poll."""
    factory = factory or JOB_SESSION_FACTORY
    newest: dict[str, dict[str, Any]] = {}
    for record in list_job_records(SETUP_WORKSPACE_KEY, SETUP_JOB_KIND, 60, factory=factory):
        provider = record["payload"].get("provider")
        # Already newest-first, so the first of each provider is the one.
        if provider and provider not in newest:
            newest[provider] = record
    return newest


#: A recognised failure, and what the operator can do about it. Matched against
#: the exception's text in order, so the more specific patterns come first.
#:
#: Deliberately short. This list earns its place only for causes that have an
#: action attached; anything else is better served by the library's own words
#: than by a guess of ours dressed up as a diagnosis.
SETUP_FAILURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("401", "unauthorized", "access token", "authentication"),
        "Hugging Face rejected the request. This download needs no account, so the "
        "cause is usually an expired token being picked up from HF_TOKEN or from "
        "~/.cache/huggingface/token. Clear whichever is set and try again.",
    ),
    (
        ("gated",),
        "This model is gated on Hugging Face and cannot be downloaded without "
        "accepting its terms there. Choose a different speech model.",
    ),
    (
        ("429", "too many requests", "rate limit"),
        "The download host is rate-limiting this machine. It is temporary - wait a "
        "few minutes and try again.",
    ),
    (
        ("no space left", "errno 28", "disk full"),
        "The disk is full. Free some space and try again.",
    ),
    (
        ("getaddrinfo", "name or service not known", "temporary failure in name",
         "connection refused", "connection aborted", "network is unreachable",
         "timed out", "timeout"),
        "The download could not reach the internet. Check the connection and try "
        "again.",
    ),
    (
        ("no module named", "cannot be imported"),
        "The runtime downloaded but will not import, so the install is incomplete. "
        "Delete .tools/media-ai/runtime and set it up again.",
    ),
)


def setup_failure(error: BaseException) -> str:
    """What to show an operator when preparing a provider fails.

    The raw exception went straight to the card, and for the failure that
    prompted this it read: `RepositoryNotFoundError: 401 Client Error. (Request
    ID: Root=1-6a83...) Repository Not Found for url: ... Please make sure you
    specified the correct repo_id and repo_type ... User Access Token "First" is
    expired`. Every word of that is true and only the last clause matters, and
    it is behind two lines of advice about arguments the operator never passed.

    Our own `RuntimeError`s are already written for this audience and pass
    through untouched. Everything else is matched against the causes that have
    an action attached; an unrecognised one keeps the library's own first line,
    which is worth more than a vaguer sentence from us.
    """
    text = " ".join(str(error).split())
    if isinstance(error, RuntimeError):
        # Raised by this module, for this reader. Naming the class in front of
        # it would only make our own sentence look like a stack trace.
        return text
    haystack = text.lower()
    for needles, message in SETUP_FAILURES:
        if any(needle in haystack for needle in needles):
            return message
    first = text.split(". ")[0].strip().rstrip(".")
    return f"{type(error).__name__}: {first}." if first else f"{type(error).__name__}."


def run_setup_job(
    job_id: str, worker_id: str = "media-ai-worker", *, factory=None
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    claimed = claim_job(job_id, worker_id, lease_seconds=SETUP_LEASE_SECONDS, factory=factory)
    provider = claimed["payload"]["provider"]
    try:
        skipped = prepare_provider(
            provider,
            on_stage=lambda fraction, label: report_progress(
                job_id, fraction, label, factory=factory
            ),
        )
        status = provider_status()
        return complete_job(
            job_id,
            worker_id,
            {
                "provider": provider,
                "skipped": skipped,
                "ready": status["translation" if provider == "translate" else provider]["ready"],
            },
            factory=factory,
        )
    except Exception as error:
        fail_job(job_id, worker_id, setup_failure(error), factory=factory)
        raise


def _version_path(session: Any, asset_id: str, kind: str) -> Path | None:
    item = session.scalar(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind == kind,
        )
        .order_by(MediaAssetVersion.created_at.desc())
        .limit(1)
    )
    if not item:
        return None
    try:
        path = Path(item.path).resolve(strict=True)
    except OSError:
        return None
    return path if path.is_file() else None


def _speech_draft(path: Path, language: str | None) -> dict[str, Any]:
    _runtime_path()
    from faster_whisper import WhisperModel

    settings = get_settings()
    model_root = MODEL_ROOT / "faster-whisper"
    if not model_root.is_dir() or not any(model_root.rglob("model.bin")):
        raise RuntimeError(
            "The configured faster-whisper model is not downloaded. "
            "Open the transcription switch in the Library, or the faster-whisper "
            "card in Tools, and choose Download and switch on."
        )
    model = WhisperModel(
        settings.media_ai_speech_model,
        device=settings.media_ai_device,
        compute_type=settings.media_ai_compute_type,
        download_root=str(model_root),
        local_files_only=True,
    )
    segments, info = model.transcribe(
        str(path),
        language=None if not language or language == "auto" else language,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )
    records = []
    text_parts = []
    for segment in segments:
        text = " ".join(str(segment.text).strip().split())
        if not text:
            continue
        text_parts.append(text)
        records.append(
            {
                "start_ms": round(float(segment.start) * 1000),
                "end_ms": round(float(segment.end) * 1000),
                "text": text,
                "avg_logprob": round(float(segment.avg_logprob), 4),
                "no_speech_prob": round(float(segment.no_speech_prob), 4),
                "words": [
                    {
                        "start_ms": round(float(word.start) * 1000),
                        "end_ms": round(float(word.end) * 1000),
                        "text": str(word.word),
                        "probability": round(float(word.probability), 4),
                    }
                    for word in (segment.words or [])
                ],
            }
        )
    text = " ".join(text_parts).strip()
    if not text:
        raise RuntimeError("The speech provider found no spoken text.")
    return {
        "language": str(info.language or language or "und"),
        "text": text[:100_000],
        "segments": records,
        "provider": f"faster-whisper@{SPEECH_VERSION}:{settings.media_ai_speech_model}",
    }


def _extract_ocr_frames(asset: MediaAsset, source: Path, work: Path) -> list[Path]:
    if asset.media_kind == "image":
        return [source]
    if not FFMPEG.is_file():
        raise RuntimeError("The pinned local FFmpeg runtime is missing. Run npm install.")
    work.mkdir(parents=True, exist_ok=True)
    interval = get_settings().media_ai_ocr_interval_seconds
    pattern = work / "frame-%04d.jpg"
    completed = subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(source),
            "-vf",
            f"fps=1/{interval},scale=w='min(1280,iw)':h=-2",
            "-frames:v",
            str(get_settings().media_ai_max_ocr_frames),
            str(pattern),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Frame extraction failed.").strip()
        raise RuntimeError(detail[-1500:])
    frames = sorted(work.glob("frame-*.jpg"))
    if not frames:
        raise RuntimeError("No frames were available for OCR.")
    return frames


def _rapidocr_text(result: Any) -> tuple[list[str], list[float]]:
    texts = list(getattr(result, "txts", []) or [])
    scores = [float(value) for value in (getattr(result, "scores", []) or [])]
    if texts:
        return [str(value) for value in texts], scores
    payload = result.to_json() if hasattr(result, "to_json") else result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if isinstance(payload, dict):
        texts = list(payload.get("txts") or payload.get("texts") or [])
        scores = [float(value) for value in (payload.get("scores") or [])]
    return [str(value) for value in texts], scores


def _ocr_draft(asset: MediaAsset, source: Path, work: Path) -> dict[str, Any]:
    _runtime_path()
    from rapidocr import RapidOCR

    engine = RapidOCR()
    frames = _extract_ocr_frames(asset, source, work)
    records = []
    unique: list[str] = []
    seen: set[str] = set()
    interval_ms = round(get_settings().media_ai_ocr_interval_seconds * 1000)
    for index, frame in enumerate(frames):
        result = engine(str(frame))
        texts, scores = _rapidocr_text(result)
        kept = []
        for text, score in zip(texts, scores or [1.0] * len(texts), strict=False):
            normalized = " ".join(text.strip().split())
            if not normalized or score < 0.45:
                continue
            key = normalized.casefold()
            kept.append({"text": normalized, "confidence": round(score, 4)})
            if key not in seen:
                seen.add(key)
                unique.append(normalized)
        if kept:
            records.append(
                {
                    "timestamp_ms": 0 if asset.media_kind == "image" else index * interval_ms,
                    "lines": kept,
                }
            )
    text = "\n".join(unique).strip()
    if not text:
        raise RuntimeError("The OCR provider found no on-screen text.")
    return {
        "language": "und",
        "text": text[:100_000],
        "segments": records,
        "provider": f"rapidocr@{OCR_VERSION}:onnxruntime@{ONNX_VERSION}",
    }


SPEECH_RUNNER = _speech_draft
OCR_RUNNER = _ocr_draft


def create_enrichment_job(
    *,
    workspace_id: str,
    asset_id: str,
    actor_user_id: str,
    modes: list[Mode],
    language: str | None,
    factory=None,
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    normalized_modes = sorted(set(modes))
    if not normalized_modes:
        raise ValueError("Select speech transcription, OCR, or both.")
    with factory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id,
                MediaAsset.workspace_id == workspace_id,
            )
        )
        if not asset:
            raise ValueError("Media asset was not found.")
        if "speech" in normalized_modes and not asset.has_audio:
            raise ValueError("This asset has no audio track to transcribe.")
        if "ocr" in normalized_modes and asset.media_kind not in {"video", "image"}:
            raise ValueError("OCR requires a video or image asset.")
        signature = ":".join(
            [
                workspace_id,
                asset_id,
                asset.original_sha256,
                ",".join(normalized_modes),
                language or "auto",
                get_settings().media_ai_speech_model,
                str(get_settings().media_ai_ocr_interval_seconds),
                SPEECH_VERSION,
                OCR_VERSION,
            ]
        )
        job_id = "mediaai_" + hashlib.sha256(signature.encode()).hexdigest()[:24]
        existing = session.get(DurableJob, job_id)
        if existing:
            return get_job_record(job_id, factory=factory)
    return create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "id": job_id,
            "workspace_id": workspace_id,
            "asset_id": asset_id,
            "actor_user_id": actor_user_id,
            "modes": normalized_modes,
            "language": language or "auto",
            "speech_provider": f"faster-whisper@{SPEECH_VERSION}",
            "ocr_provider": f"rapidocr@{OCR_VERSION}",
        },
        max_attempts=2,
        factory=factory,
    )


def run_enrichment_job(
    job_id: str,
    worker_id: str = "media-ai-worker",
    *,
    factory=None,
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    claimed = claim_job(job_id, worker_id, lease_seconds=3600, factory=factory)
    payload = dict(claimed["payload"])
    work = WORK_ROOT / job_id
    try:
        status = provider_status()
        for mode in payload["modes"]:
            if not status[mode]["ready"]:
                raise RuntimeError(
                    f"{status[mode]['provider']} is "
                    + (
                        "switched off. Turn it on from the transcription switch in "
                        "the Library."
                        if status[mode]["prepared"]
                        else "not downloaded yet. Set it up from the transcription "
                        "switch in the Library, or its card in Tools."
                    )
                )
        # Read what the models need, then let the connection go. Transcribing a
        # clip is minutes of inference, and doing it inside the session held a
        # pooled database connection open for every one of them - so a few
        # concurrent jobs could exhaust the pool while none of them were
        # touching the database at all. Nothing below here needs a session
        # until there are results to write.
        audio: Path | None = None
        source: Path | None = None
        with factory() as session:
            asset = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.id == payload["asset_id"],
                    MediaAsset.workspace_id == payload["workspace_id"],
                )
            )
            if not asset:
                raise RuntimeError("Media asset was removed before analysis.")
            if "speech" in payload["modes"]:
                audio = _version_path(session, asset.id, "audio") or _version_path(
                    session, asset.id, "original"
                )
            if "ocr" in payload["modes"]:
                source = _version_path(session, asset.id, "original")
            # Detached, but its loaded values stay readable. The OCR pass wants
            # `media_kind` and nothing else, so this keeps the answer without
            # keeping the row - and without a lazy load firing on a closed
            # session halfway through a render.
            session.expunge(asset)

        drafts: list[tuple[Mode, dict[str, Any]]] = []
        if "speech" in payload["modes"]:
            if not audio:
                raise RuntimeError("The audio version is unavailable.")
            drafts.append(("speech", SPEECH_RUNNER(audio, payload.get("language"))))
        if "ocr" in payload["modes"]:
            if not source:
                raise RuntimeError("The original version is unavailable.")
            drafts.append(("ocr", OCR_RUNNER(asset, source, work)))
        transcript_ids = []
        with factory.begin() as session:
            for kind, draft in drafts:
                existing = session.scalar(
                    select(MediaTranscript).where(
                        MediaTranscript.asset_id == payload["asset_id"],
                        MediaTranscript.job_id == job_id,
                        MediaTranscript.kind == kind,
                    )
                )
                if existing:
                    transcript_ids.append(existing.id)
                    continue
                transcript = MediaTranscript(
                    workspace_id=payload["workspace_id"],
                    asset_id=payload["asset_id"],
                    kind=kind,
                    language=draft["language"],
                    provider=draft["provider"],
                    status="machine",
                    text=draft["text"],
                    segments=draft["segments"],
                    job_id=job_id,
                    created_by=payload["actor_user_id"],
                )
                session.add(transcript)
                session.flush()
                transcript_ids.append(transcript.id)
        return complete_job(
            job_id,
            worker_id,
            {
                "asset_id": payload["asset_id"],
                "transcript_ids": transcript_ids,
                "review_required": True,
            },
            factory=factory,
        )
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=factory)
        raise
    finally:
        if work.is_dir():
            shutil.rmtree(work, ignore_errors=True)


def list_enrichment_jobs(
    workspace_id: str, limit: int = 30, *, factory=None
) -> list[dict[str, Any]]:
    factory = factory or JOB_SESSION_FACTORY
    return list_job_records(workspace_id, JOB_KIND, limit, factory=factory)
