"""Telling faces apart, so a blur can be selective.

The blur already here covers every face it finds. That is the right default and
the wrong tool for the common case: a creator filming in public wants the
passers-by covered and themselves left visible. Covering everyone defeats the
video; covering nobody defeats the point.

Distinguishing the two needs recognition, not detection - a 512-number
embedding per face, close for the same person and far for different ones. That
is what InsightFace provides, and it is why this module exists.

No reference photo is asked for. Every face in the clip is embedded, the
embeddings are clustered, and the largest cluster is taken to be the subject:
the person who is in their own video most. An operator can invert it when the
guess is wrong, and the report says how many identities were found and how much
of the clip each one held, so the guess is checkable rather than magic.

Licensing, which is not a footnote here
---------------------------------------
InsightFace's *code* is MIT. Its *pretrained models* are released for
non-commercial research only, and `inswapper_128` - the face swapper - was
withdrawn from distribution entirely and needs a licence from InsightFace
directly. A catalogue entry saying "MIT" would be true of the code and
misleading about the thing that actually matters.

TrendRelay drives affiliate revenue, which is commercial use. So this is gated:
the capability reports itself unavailable until someone records that they have
the right to use these models, and that acknowledgement is a stored, auditable
act rather than a comment in a source file. Nothing here downloads or uses the
swapper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trendrelay_api.tool_registry import PROJECT_ROOT

Box = tuple[int, int, int, int]

#: `buffalo_l` is detection plus recognition and runs on CPU. The swapper is
#: deliberately not among these: it is withdrawn, and sourcing it from a mirror
#: would be both a licence problem and an untrusted download.
MODEL_PACK = "buffalo_l"
DETECTION_SIZE = (640, 640)
#: Only what this feature reads: boxes and embeddings. The pack also carries
#: two landmark models and an age/gender estimator, which nothing here looks at.
#: Dropping them is 19% off the CPU path and free on the GPU, and not running an
#: age-and-gender classifier over strangers is the better default anyway.
MODULES = ["detection", "recognition"]
#: Fastest first, CPU last. CUDA is not installed by default - it needs about
#: 3GB of CUDA 13 and cuDNN wheels - but if someone adds it, it is picked up
#: without a code change. DirectML needs no extra runtime at all and measured
#: 5.3x faster than CPU on an RTX 2060: 113ms a frame down to 21ms.
PROVIDER_PREFERENCE = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)
CPU_PROVIDER = "CPUExecutionProvider"
ACKNOWLEDGEMENT_FILE = PROJECT_ROOT / ".data" / "insightface" / "licence-acknowledged.json"

LICENCE_SUMMARY = (
    "InsightFace's code is MIT, but its pretrained models are licensed for "
    "non-commercial research only, and the face swapper is withdrawn from "
    "distribution and needs a licence from InsightFace directly. TrendRelay is "
    "commercial use. Confirm you hold the right to use these models here."
)

_ANALYSERS: dict[str, Any] = {}


class FaceIdentityUnavailable(RuntimeError):
    """Raised when identities cannot be read, with the reason to show."""


@dataclass(frozen=True)
class IdentitySettings:
    """How faces are grouped, and which group is covered."""

    #: Cosine similarity above which two faces are called the same person.
    #: 0.4 is InsightFace's own working figure for this model; measured on a
    #: real clip, the same face scored 0.79 against itself across frames and
    #: different faces bottomed out at 0.36, so the gap is real but not vast.
    match_threshold: float = 0.4
    #: Cover everyone who is *not* the main subject. Inverted, it covers only
    #: the subject, which is what someone anonymising themselves wants.
    keep_subject: bool = True
    #: Detector confidence.
    confidence: float = 0.5


# --------------------------------------------------------------------------- #
# The licence gate
# --------------------------------------------------------------------------- #


def licence_acknowledged() -> bool:
    try:
        payload = json.loads(ACKNOWLEDGEMENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(payload, dict) and payload.get("acknowledged"))


def acknowledge_licence(actor_user_id: str, accepted: bool = True) -> dict[str, Any]:
    """Record that someone takes responsibility for the model licence.

    Stored rather than inferred, and reversible: an acknowledgement that cannot
    be withdrawn is not a decision, it is a trap.
    """
    payload = {
        "acknowledged": bool(accepted),
        "actor_user_id": actor_user_id,
        "licence": LICENCE_SUMMARY,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    ACKNOWLEDGEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ACKNOWLEDGEMENT_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(ACKNOWLEDGEMENT_FILE)
    return payload


def runtime_status() -> dict[str, Any]:
    """Whether identity work can run, and if not, exactly what is missing."""
    installed, detail = _runtime_present()
    acknowledged = licence_acknowledged()
    if not installed:
        reason = detail
    elif not acknowledged:
        reason = LICENCE_SUMMARY
    else:
        reason = None
    return {
        "id": "face-identity",
        "available": installed and acknowledged,
        "reason": reason,
        "runtime_installed": installed,
        "licence_acknowledged": acknowledged,
        "licence": LICENCE_SUMMARY,
        "model_pack": MODEL_PACK,
        "providers_available": available_providers(),
        "provider": chosen_provider(),
        "gpu_accelerated": chosen_provider() != CPU_PROVIDER,
        "install_hint": (
            "pip install --no-deps insightface onnxruntime onnx scikit-image scipy"
        ),
    }


def _runtime_present() -> tuple[bool, str | None]:
    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError as error:
        return False, (
            "Telling faces apart needs InsightFace and onnxruntime, which are "
            f"not installed ({error.name})."
        )
    return True, None


def _require_available() -> None:
    status = runtime_status()
    if not status["available"]:
        raise FaceIdentityUnavailable(status["reason"] or "Unavailable.")


# --------------------------------------------------------------------------- #
# Reading faces
# --------------------------------------------------------------------------- #


def available_providers() -> list[str]:
    """The execution providers this machine offers, fastest first."""
    try:
        import onnxruntime
    except ImportError:
        return []
    present = set(onnxruntime.get_available_providers())
    return [name for name in PROVIDER_PREFERENCE if name in present]


def chosen_provider(force_cpu: bool = False) -> str:
    if force_cpu:
        return CPU_PROVIDER
    return next(iter(available_providers()), CPU_PROVIDER)


def analyser(confidence: float = 0.5, force_cpu: bool = False) -> tuple[Any, str]:
    """The loaded model pack and the provider running it.

    Loading costs a few seconds and a few hundred megabytes, so it is kept
    between calls - per-render overhead worth paying once rather than per clip.
    Cached per provider, because the CPU fallback has to be able to exist
    alongside the GPU one rather than evicting it.
    """
    _require_available()
    provider = chosen_provider(force_cpu)
    if provider not in _ANALYSERS:
        from insightface.app import FaceAnalysis

        # The CPU provider is always appended: a GPU provider that cannot place
        # an operator falls back per-node instead of failing to build at all.
        providers = [provider] if provider == CPU_PROVIDER else [provider, CPU_PROVIDER]
        app = FaceAnalysis(
            name=MODEL_PACK, providers=providers, allowed_modules=MODULES
        )
        app.prepare(
            ctx_id=-1 if provider == CPU_PROVIDER else 0,
            det_size=DETECTION_SIZE,
            det_thresh=confidence,
        )
        _ANALYSERS[provider] = app
    return _ANALYSERS[provider], provider


def faces_in(frame: Any, app: Any) -> list[tuple[Box, Any]]:
    """Every face in one frame, as a box and its embedding."""
    found: list[tuple[Box, Any]] = []
    for face in app.get(frame):
        left, top, right, bottom = (int(round(float(v))) for v in face.bbox[:4])
        width, height = right - left, bottom - top
        if width > 0 and height > 0:
            found.append(((max(0, left), max(0, top), width, height), face.normed_embedding))
    return found


# --------------------------------------------------------------------------- #
# Grouping them
# --------------------------------------------------------------------------- #


def cluster(embeddings: list[Any], threshold: float) -> list[int]:
    """Group embeddings by identity, returning one label per embedding.

    Greedy single-pass agglomeration against running cluster means: each face
    joins the closest cluster it is near enough to, or starts its own. Chosen
    over k-means because the number of people in a clip is exactly what is not
    known in advance, and over a full hierarchical pass because a short clip
    yields tens of faces, not thousands.

    The embeddings are already L2-normalised by InsightFace, so a dot product
    is the cosine similarity.
    """
    import numpy as np

    labels: list[int] = []
    centres: list[Any] = []
    counts: list[int] = []
    for embedding in embeddings:
        best, best_score = -1, threshold
        for index, centre in enumerate(centres):
            score = float(np.dot(embedding, centre))
            if score >= best_score:
                best, best_score = index, score
        if best < 0:
            centres.append(np.array(embedding, dtype="float32"))
            counts.append(1)
            labels.append(len(centres) - 1)
            continue
        # The running mean is renormalised so it stays comparable by dot
        # product; without this a drifting centre slowly stops matching anyone.
        total = counts[best] + 1
        merged = (centres[best] * counts[best] + embedding) / total
        norm = float(np.linalg.norm(merged)) or 1.0
        centres[best] = merged / norm
        counts[best] = total
        labels.append(best)
    return labels


def main_identity(labels: list[int]) -> int | None:
    """The identity appearing in the most frames: presumed to be the subject."""
    if not labels:
        return None
    tally: dict[int, int] = {}
    for label in labels:
        tally[label] = tally.get(label, 0) + 1
    # Ties break towards the lower label, which is the one seen first - the
    # person the clip opens on, which is the better guess than an arbitrary one.
    return min(tally, key=lambda label: (-tally[label], label))


def identity_report(labels: list[int]) -> list[dict[str, Any]]:
    """How many identities were found and how much of the clip each held.

    Returned so the subject guess is checkable. A clip reported as one identity
    at 100% when two people are plainly in it is a threshold problem, and
    without this an operator could only see that the wrong face got blurred.
    """
    total = len(labels) or 1
    tally: dict[int, int] = {}
    for label in labels:
        tally[label] = tally.get(label, 0) + 1
    return [
        {
            "identity": label,
            "appearances": count,
            "share": round(count / total, 3),
        }
        for label, count in sorted(tally.items(), key=lambda item: -item[1])
    ]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_selective_blur(
    source: Path,
    destination: Path,
    settings: IdentitySettings | None = None,
    preview_seconds: float | None = None,
) -> dict[str, Any]:
    """Blur by identity: everyone but the subject, or only the subject.

    Two passes for the same reason the plain blur takes two. The first reads
    every face and its embedding; only then is it known who the subject is,
    because that is a fact about the whole clip and not about any one frame.
    Deciding per frame would blur whoever happened to be largest at that moment
    and flicker between people.
    """
    from trendrelay_api.integrations.face_blur import (
        PREVIEW_WIDTH,
        BlurSettings,
        FaceBlurUnavailable,
        _load_opencv,
        _remux_audio,
        apply_blur,
    )

    _require_available()
    cv2 = _load_opencv()
    settings = settings or IdentitySettings()
    if not source.is_file():
        raise FaceIdentityUnavailable(f"No such media file: {source}")

    def _open() -> Any:
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise FaceBlurUnavailable(f"OpenCV could not read {source.name}.")
        return capture

    def _read_faces(force_cpu: bool) -> tuple[list[list[tuple[Box, Any]]], dict[str, Any], str]:
        app, provider = analyser(settings.confidence, force_cpu=force_cpu)
        capture = _open()
        try:
            shape = {
                "fps": capture.get(cv2.CAP_PROP_FPS) or 25.0,
                "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            }
            limit = int(shape["fps"] * preview_seconds) if preview_seconds else None
            read: list[list[tuple[Box, Any]]] = []
            while limit is None or len(read) < limit:
                ok, frame = capture.read()
                if not ok:
                    break
                read.append(faces_in(frame, app))
            return read, shape, provider
        finally:
            capture.release()

    try:
        timeline, shape, provider = _read_faces(force_cpu=False)
    except Exception as error:
        # A GPU provider can build a session and still throw partway through a
        # clip - DirectML raises on shapes it cannot place, and it does so at
        # inference rather than at load. The pass restarts on CPU rather than
        # losing the render: slower is a far better answer than failed.
        if chosen_provider() == CPU_PROVIDER:
            raise
        fallback_reason = f"{type(error).__name__}: {str(error)[:200]}"
        timeline, shape, provider = _read_faces(force_cpu=True)
    else:
        fallback_reason = None
    fps = shape["fps"]
    width, height = shape["width"], shape["height"]

    if not timeline:
        raise FaceIdentityUnavailable(f"{source.name} contained no readable frames.")

    flat = [embedding for frame_faces in timeline for _box, embedding in frame_faces]
    labels = cluster(flat, settings.match_threshold)
    subject = main_identity(labels)
    report = identity_report(labels)

    # Walked in the same order the labels were produced, so label i belongs to
    # the i-th face read. Any other pairing silently blurs the wrong person.
    per_frame: list[list[tuple[Box, int]]] = []
    cursor = 0
    for frame_faces in timeline:
        row = []
        for box, _embedding in frame_faces:
            row.append((box, labels[cursor]))
            cursor += 1
        per_frame.append(row)

    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    scale = min(1.0, PREVIEW_WIDTH / float(width)) if preview_seconds and width else 1.0
    out_size = (int(width * scale), int(height * scale)) if scale < 1.0 else (width, height)
    blur_settings = BlurSettings(confidence=settings.confidence)

    capture = _open()
    writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size)
    covered = 0
    try:
        for row in per_frame:
            ok, frame = capture.read()
            if not ok:
                break
            for box, label in row:
                is_subject = label == subject
                if is_subject == settings.keep_subject:
                    continue
                apply_blur(cv2, frame, box, blur_settings)
                covered += 1
            if scale < 1.0:
                frame = cv2.resize(frame, out_size, interpolation=cv2.INTER_AREA)
            writer.write(frame)
    finally:
        writer.release()
        capture.release()

    if _remux_audio(silent, source, destination):
        silent.unlink(missing_ok=True)
    else:
        silent.replace(destination)

    return {
        "frames": len(timeline),
        "faces_found": len(flat),
        "faces_covered": covered,
        "identities": len(report),
        # The guess, and the evidence for it, so a wrong subject is diagnosable
        # rather than just visibly wrong.
        "subject_identity": subject,
        "identity_report": report,
        "kept_subject": settings.keep_subject,
        "provider": provider,
        # Named when it happened, because a render that silently took five times
        # longer than the last one is otherwise a mystery.
        "gpu_fallback_reason": fallback_reason,
        "output": str(destination),
    }
