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
    """Which identity is replaced, and how sure the detector has to be."""

    #: Cosine similarity above which two faces are the same person. Shared with
    #: the identity blur so "the subject" means the same thing in both.
    match_threshold: float = 0.4
    #: Replace the main subject. Inverted, it replaces everyone *but* them,
    #: which is the bystander case.
    swap_subject: bool = True
    confidence: float = 0.5


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


def install_hint() -> str:
    return (
        "Record what permits this - a commercial licence from InsightFace "
        "(contact@insightface.ai), or non-commercial research use - put the "
        f"model file in {MODEL_DIR}, and name what the footing rests on. "
        "Nothing here downloads a model."
    )
