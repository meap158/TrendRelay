"""Face swapping, behind a licence you have to actually hold.

InsightFace's swap models are licensed for non-commercial research, and sold
separately for anything else. `inswapper_128` was also withdrawn from public
distribution, so every circulating copy is a re-upload its authors did not
sanction - which makes obtaining one a judgement about your own footing rather
than something an app should decide for you.

So this downloads nothing and ships no mirror. It runs a model file *you*
placed there, on a footing *you* recorded - commercial licence or research use
- and says which, rather than falling back to anything.

That is not a technicality. The model turns a real, identifiable person's video
into footage of someone who was never there, and this product republishes other
people's clips. A licence from InsightFace is what makes the tool legitimate;
it is not what makes the output honest, and nothing here can supply that part.

What it does when it is licensed
--------------------------------
The same two-pass shape as the identity blur, for the same reason: who the
subject is is a fact about the whole clip, not about a frame. Faces are detected
and embedded, embeddings are clustered into identities, and the swap is applied
to one chosen identity across the clip. Deciding per frame would swap whoever
was largest at that moment and flicker between people.

The heavy lifting is already done and tested by `face_identity` - detection,
embeddings, clustering, GPU provider selection with a CPU fallback. This adds
the swap step and the gate, and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trendrelay_api.tool_registry import PROJECT_ROOT

#: Where a licensed model goes. Under `.data`, which is git-ignored: licensed
#: weights are somebody's property and must not be committed, and the failure
#: mode for weights in a repository is that nobody notices until it is public.
MODEL_DIR = PROJECT_ROOT / ".data" / "face-swap"
LICENCE_FILE = MODEL_DIR / "licence.json"
#: Portraits to swap *in*, dropped here by the operator. Beside the model and
#: under `.data` for the same reason: a photograph of somebody's face is not
#: repository content, and the failure mode for one committed by accident is
#: that nobody notices until it is public.
FACES_DIR = MODEL_DIR / "faces"
FACE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
#: Accepted names, newest first. InsightFace's current models are better than
#: the withdrawn 128 baseline: higher resolution, steadier identity across a
#: clip. If they license you something else, add it here rather than renaming
#: the file to match - the name is the record of what you are running.
KNOWN_MODELS = ("inswapper-512-live.onnx", "inswapper_512.onnx", "inswapper_128.onnx")

#: The two footings this can legitimately stand on, and what each one commits
#: the operator to. Kept apart because they are genuinely different permissions
#: and recording the wrong one is worse than recording nothing: a false record
#: is what gets relied on later.
LICENCE_BASES: dict[str, str] = {
    "commercial": (
        "A commercial licence held from InsightFace (contact@insightface.ai). "
        "Permits commercial use of the licensed model. The weights remain "
        "InsightFace's property and are not redistributable."
    ),
    "research": (
        "Non-commercial research use, under the research licence InsightFace's "
        "swap models carry. Permits research and evaluation only - not a "
        "commercial product, a paid service, or client work. If this project "
        "starts earning, this record stops being true and a commercial licence "
        "is required."
    ),
}
DEFAULT_BASIS = "commercial"

LICENCE_SUMMARY = (
    "Face swapping needs a footing recorded before it runs: either a "
    "commercial licence from InsightFace, or non-commercial research use "
    "under the licence the models already carry. TrendRelay will not download "
    "a model or fetch one from a third-party mirror either way - place the "
    f"file in {MODEL_DIR} yourself."
)


class FaceSwapUnavailable(RuntimeError):
    """Raised when swapping cannot run, with the reason to show."""


@dataclass(frozen=True)
class SwapSettings:
    """Which identity is replaced, with what, and how sure the detector is."""

    #: The portrait to put on, named by its file stem in `FACES_DIR`. A name
    #: rather than a path: a recipe is stored and re-run, and an absolute path
    #: in a stored recipe is both a portability problem and a way to read a
    #: file nobody meant to offer.
    source_face: str = ""
    #: Cosine similarity above which two faces are the same person. Shared with
    #: the identity blur so "the subject" means the same thing in both.
    match_threshold: float = 0.4
    #: Replace the main subject. Inverted, it replaces everyone *but* them,
    #: which is the bystander case.
    swap_subject: bool = True
    confidence: float = 0.5


# --------------------------------------------------------------------------- #
# The face being swapped in
# --------------------------------------------------------------------------- #


def available_faces() -> tuple[dict[str, Any], ...]:
    """Portraits the operator has made available, newest name order.

    Read on every describe and every validation rather than cached, so a
    portrait dropped in the folder is selectable without a restart - and, more
    to the point, so validation cannot fall behind the list offered.
    """
    if not FACES_DIR.is_dir():
        return ()
    found = [
        {
            "value": item.stem,
            "label": item.stem.replace("_", " ").replace("-", " ").strip() or item.stem,
            "group": "Faces",
        }
        for item in sorted(FACES_DIR.iterdir())
        if item.is_file() and item.suffix.lower() in FACE_SUFFIXES
    ]
    return tuple(found)


def face_file(name: str) -> Path | None:
    """The portrait a name refers to, or nothing.

    Resolved by matching the catalogue rather than by joining the name onto a
    path, so a name carrying `..` or an absolute path selects nothing instead
    of reaching a file outside the folder.
    """
    wanted = (name or "").strip()
    if not wanted:
        return None
    for item in sorted(FACES_DIR.iterdir()) if FACES_DIR.is_dir() else []:
        if item.is_file() and item.suffix.lower() in FACE_SUFFIXES and item.stem == wanted:
            return item
    return None


def reference_face(name: str, confidence: float = 0.5) -> Any:
    """The face to swap in, read from its portrait.

    The largest face in the picture, because a portrait with a bystander in it
    should still give the portrait's subject. Refused rather than guessed when
    there is no face at all: swapping in nothing produces a clip that looks
    untouched, which reads as a broken render rather than a bad input.
    """
    from trendrelay_api.integrations import face_identity

    path = face_file(name)
    if path is None:
        offered = ", ".join(item["value"] for item in available_faces())
        raise FaceSwapUnavailable(
            f"No portrait named {name!r} in {FACES_DIR}. "
            + (f"Available: {offered}." if offered else "The folder is empty.")
        )
    cv2 = _load_cv2()
    picture = cv2.imread(str(path))
    if picture is None:
        raise FaceSwapUnavailable(f"{path.name} could not be read as an image.")
    app, _provider = face_identity.analyser(confidence)
    faces = app.get(picture)
    if not faces:
        raise FaceSwapUnavailable(
            f"No face was found in {path.name}. A clear, front-facing portrait "
            "works best."
        )
    return max(faces, key=lambda face: _area(face.bbox))


def _area(bbox: Any) -> float:
    left, top, right, bottom = (float(value) for value in bbox[:4])
    return max(0.0, right - left) * max(0.0, bottom - top)


def _load_cv2() -> Any:
    from trendrelay_api.integrations.face_blur import _load_opencv

    return _load_opencv()


# --------------------------------------------------------------------------- #
# The licence, and the model it lets you run
# --------------------------------------------------------------------------- #


def model_path() -> Path | None:
    """The licensed model on this machine, if one has been placed."""
    for name in KNOWN_MODELS:
        candidate = MODEL_DIR / name
        if candidate.is_file():
            return candidate
    return None


def licence_record() -> dict[str, Any] | None:
    try:
        payload = json.loads(LICENCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("licensed") else None


def record_licence(
    actor_user_id: str,
    reference: str,
    licensed: bool = True,
    basis: str = DEFAULT_BASIS,
) -> dict[str, Any]:
    """Record what permits this to run, and who says so.

    Two footings, because there are two. A commercial licence is bought from
    InsightFace. Non-commercial research use is already permitted by the
    licence the models carry, and needs no purchase - but it is a claim about
    what this project *is*, and a project that starts earning has quietly
    stopped meeting it.

    `reference` is whatever identifies the footing - an order number or contract
    id for a commercial licence; for research use, the institution, grant or
    project it is being done under. Either way it is stored so that "are we
    allowed to run this" has an answer that is not somebody's memory.
    """
    if basis not in LICENCE_BASES:
        raise ValueError(
            f"Unknown licence basis {basis!r}: expected one of {', '.join(LICENCE_BASES)}."
        )
    if licensed and not reference.strip():
        # Refused for research too. "We are a research project" with nothing
        # naming the project is the record that turns out to be worthless
        # precisely when somebody asks.
        raise ValueError("Record what the licence rests on, not just that it does.")
    payload = {
        "licensed": bool(licensed),
        "basis": basis,
        "reference": reference.strip(),
        "actor_user_id": actor_user_id,
        "supplier": "InsightFace (contact@insightface.ai)",
        "terms": LICENCE_BASES[basis],
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    temporary = LICENCE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(LICENCE_FILE)
    return payload


def runtime_status() -> dict[str, Any]:
    """Whether swapping can run, and if not, precisely what is missing."""
    from trendrelay_api.integrations import face_identity

    model = model_path()
    licence = licence_record()
    identity = face_identity.runtime_status()

    if not identity["runtime_installed"]:
        reason = identity["reason"]
    elif licence is None:
        reason = LICENCE_SUMMARY
    elif model is None:
        reason = (
            f"No swap model is present. Put the model file in {MODEL_DIR}. "
            "TrendRelay does not download one and does not ship a mirror: "
            "inswapper_128 was withdrawn by its authors, so obtaining a copy "
            "is a judgement about your own footing, and the app should not "
            "make it on your behalf."
        )
    elif not available_faces():
        # The last thing missing, and the only one the operator supplies per
        # use rather than once. Named as its own step so "nothing to swap in"
        # never reads as "the model is broken".
        reason = (
            "No portrait to swap in. Put a clear, front-facing photograph of "
            f"the face you have permission to use in {FACES_DIR}."
        )
    else:
        reason = None

    return {
        "id": "face-swap",
        "available": reason is None,
        "reason": reason,
        "model": model.name if model else None,
        "model_dir": str(MODEL_DIR),
        "licence_recorded": licence is not None,
        "licence_reference": (licence or {}).get("reference"),
        # Which footing, and what that footing actually permits. Shown rather
        # than reduced to "licensed", because research use and a commercial
        # licence allow different things and the difference is the point.
        "licence_basis": (licence or {}).get("basis"),
        "licence_terms": (licence or {}).get("terms"),
        "licence_summary": LICENCE_SUMMARY,
        "licence_bases": dict(LICENCE_BASES),
        "provider": identity.get("provider"),
        "gpu_accelerated": identity.get("gpu_accelerated", False),
        # Said plainly, because it is the part a licence does not settle.
        "consent_note": (
            "A swapped face is synthetic media of a real, identifiable person. "
            "Whether the footage may be republished that way is a separate "
            "question from whether the model is licensed."
        ),
    }


def _require_available() -> Path:
    status = runtime_status()
    if not status["available"]:
        raise FaceSwapUnavailable(status["reason"] or "Unavailable.")
    model = model_path()
    assert model is not None  # runtime_status already proved it
    return model


# --------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------- #


_SWAPPER: Any = None


def swapper() -> Any:
    """The loaded swap model, kept between calls.

    Loaded from the local file only. `get_model` will happily fetch from the
    internet when handed a bare name; it is given an absolute path and
    `download=False` so a missing licence can never turn into a silent download
    of the very weights this module refuses to use.
    """
    global _SWAPPER
    model = _require_available()
    if _SWAPPER is None:
        from insightface import model_zoo

        from trendrelay_api.integrations import face_identity

        _SWAPPER = model_zoo.get_model(
            str(model),
            download=False,
            download_zip=False,
            providers=[face_identity.chosen_provider(), face_identity.CPU_PROVIDER],
        )
    return _SWAPPER


def render_swapped(
    source: Path,
    destination: Path,
    settings: SwapSettings | None = None,
    preview_seconds: float | None = None,
) -> dict[str, Any]:
    """Replace one person's face across a clip, leaving everyone else alone.

    Two passes, for the reason the identity blur takes two: who the subject is
    is a fact about the whole clip, not about any one frame. The first pass
    reads every face and its embedding; only then can they be grouped into
    people and the subject identified. Deciding per frame would swap whoever
    was largest at that moment and flicker between people mid-clip.

    Unlike the blur, the first pass keeps the detected faces themselves rather
    than boxes: the swap needs the landmarks to align what it pastes, and a box
    cannot say which way a head is turned.
    """
    from trendrelay_api.integrations import face_identity
    from trendrelay_api.integrations.face_blur import (
        PREVIEW_WIDTH,
        FaceBlurUnavailable,
        _remux_audio,
    )

    model = _require_available()
    settings = settings or SwapSettings()
    cv2 = _load_cv2()
    if not source.is_file():
        raise FaceSwapUnavailable(f"No such media file: {source}")

    # Read before the long pass, so a missing portrait fails in a moment rather
    # than after every frame of the clip has been analysed.
    replacement = reference_face(settings.source_face, settings.confidence)

    def _open() -> Any:
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise FaceBlurUnavailable(f"OpenCV could not read {source.name}.")
        return capture

    def _read_faces(force_cpu: bool) -> tuple[list[list[Any]], dict[str, Any], str]:
        app, provider = face_identity.analyser(settings.confidence, force_cpu=force_cpu)
        capture = _open()
        try:
            shape = {
                "fps": capture.get(cv2.CAP_PROP_FPS) or 25.0,
                "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            }
            limit = int(shape["fps"] * preview_seconds) if preview_seconds else None
            read: list[list[Any]] = []
            while limit is None or len(read) < limit:
                ok, frame = capture.read()
                if not ok:
                    break
                read.append(list(app.get(frame)))
            return read, shape, provider
        finally:
            capture.release()

    try:
        timeline, shape, provider = _read_faces(force_cpu=False)
    except Exception as error:
        # A GPU provider can build a session and still throw partway through a
        # clip. Restarting on CPU loses time; not restarting loses the render.
        if face_identity.chosen_provider() == face_identity.CPU_PROVIDER:
            raise
        fallback_reason = f"{type(error).__name__}: {str(error)[:200]}"
        timeline, shape, provider = _read_faces(force_cpu=True)
    else:
        fallback_reason = None

    if not timeline:
        raise FaceSwapUnavailable(f"{source.name} contained no readable frames.")

    fps, width, height = shape["fps"], shape["width"], shape["height"]
    flat = [face for frame_faces in timeline for face in frame_faces]
    labels = face_identity.cluster([face.normed_embedding for face in flat],
                                   settings.match_threshold)
    subject = face_identity.main_identity(labels)
    report = face_identity.identity_report(labels)

    # Walked in the order the labels were produced, so label i belongs to the
    # i-th face read. Any other pairing swaps the wrong person's face, which is
    # the one failure here that is worse than not rendering at all.
    per_frame: list[list[tuple[Any, int]]] = []
    cursor = 0
    for frame_faces in timeline:
        row = []
        for face in frame_faces:
            row.append((face, labels[cursor]))
            cursor += 1
        per_frame.append(row)

    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    scale = min(1.0, PREVIEW_WIDTH / float(width)) if preview_seconds and width else 1.0
    out_size = (int(width * scale), int(height * scale)) if scale < 1.0 else (width, height)

    engine = swapper()
    capture = _open()
    writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size)
    swapped = 0
    try:
        for row in per_frame:
            ok, frame = capture.read()
            if not ok:
                break
            for face, label in row:
                if (label == subject) != settings.swap_subject:
                    continue
                # paste_back so the result is the whole frame with the new face
                # composited in, rather than the aligned crop on its own.
                frame = engine.get(frame, face, replacement, paste_back=True)
                swapped += 1
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
        "faces_swapped": swapped,
        "identities": len(report),
        # The guess and the evidence for it, so the wrong person being swapped
        # is diagnosable rather than only visible.
        "subject_identity": subject,
        "identity_report": report,
        "swapped_subject": settings.swap_subject,
        # What was put on, and what did the putting. This output is synthetic
        # media of a real person; a render that cannot say which face it used
        # or which model made it is not one anybody can account for later.
        "source_face": settings.source_face,
        "model": model.name,
        "provider": provider,
        "gpu_fallback_reason": fallback_reason,
        "output": str(destination),
    }


def install_hint() -> str:
    return (
        "Record what permits this - a commercial licence from InsightFace "
        "(contact@insightface.ai), or non-commercial research use - put the "
        f"model file in {MODEL_DIR}, and name what the footing rests on. "
        "Nothing here downloads a model."
    )
