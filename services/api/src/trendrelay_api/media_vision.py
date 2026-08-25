"""Recognising what a clip shows: the subject, the scene, the product, the shape.

The third reading of a media item, beside what was said (speech) and what was
written (on-screen text). Speech and OCR answer "what does the clip say";
neither answers "what is this a video *of*" - and that is the question search,
product matching and creative analysis actually ask of a clip nobody has
watched yet.

How it reads
------------
CLIP, run locally through ONNX Runtime by way of `fastembed` - the same
runtime the OCR reader already shares, Apache-2.0 end to end, and nothing
leaves the machine during analysis. CLIP was trained to place pictures and
sentences in one space, so recognition is comparison: each sampled frame is
embedded once, each entry of a curated vocabulary is embedded once per
process, and a frame's tags are the vocabulary entries nearest to it. That
zero-shot shape is the point - the vocabulary below is a starting point, and
growing it is editing a list rather than training a model.

Like every reading here, the result is a *machine draft*: stored beside the
speech and OCR drafts, shown in the same review section, and never treated as
truth until somebody looks at it.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

#: The pip distribution and the pinned version, quoted in the provider string
#: the way the other readers quote theirs.
VISION_PACKAGE = "fastembed"
VISION_VERSION = "0.8.0"

#: The two halves of CLIP ViT-B/32, as fastembed names them. 512 dimensions
#: each, about a third of a gigabyte together, downloaded once at setup.
VISION_MODEL = "Qdrant/clip-ViT-B-32-vision"
TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"

#: A frame's tags are the vocabulary entries scoring at least this cosine
#: similarity. CLIP similarities are relative rather than calibrated - 0.30 is
#: a confident match, 0.20 is background - so the floor keeps weak guesses out
#: of the draft without demanding certainty the model cannot express.
MIN_SCORE = 0.20

#: How many entries a single frame may contribute. A frame is one picture; six
#: things is already a generous reading of one picture.
TAGS_PER_FRAME = 6

#: How many tags a whole clip keeps after aggregation. Enough to describe a
#: busy clip, few enough that the draft reads as a description rather than as
#: the vocabulary echoed back.
TAGS_PER_CLIP = 18


#: What the reader looks for, grouped by the question each group answers.
#:
#: `subject` is who or what is on camera, `scene` is where, `product` is what
#: is being shown or sold, and `format` is the shape of the creative - the
#: distinctions the campaign metadata fields downstream actually care about.
#:
#: Written as noun phrases because they are completed into "a photo of ..."
#: prompts, which is the phrasing CLIP was trained against. Adding a line here
#: is the whole cost of teaching the reader a new thing to look for; the
#: enrichment job's identity includes a digest of this list, so an edited
#: vocabulary re-reads a clip instead of returning the stale draft.
VOCABULARY: tuple[tuple[str, str], ...] = (
    # --- who or what is on camera ---
    ("a woman talking to the camera", "subject"),
    ("a man talking to the camera", "subject"),
    ("a person dancing", "subject"),
    ("a person singing", "subject"),
    ("a person exercising", "subject"),
    ("a person eating food", "subject"),
    ("a person cooking", "subject"),
    ("a person applying makeup", "subject"),
    ("a person trying on clothes", "subject"),
    ("a person unboxing a package", "subject"),
    ("a person typing on a laptop", "subject"),
    ("a person driving a car", "subject"),
    ("hands demonstrating a product", "subject"),
    ("a couple together", "subject"),
    ("a child", "subject"),
    ("a baby", "subject"),
    ("a group of friends", "subject"),
    ("a dog", "subject"),
    ("a cat", "subject"),
    ("an animated cartoon character", "subject"),
    # --- where the clip happens ---
    ("a bedroom", "scene"),
    ("a living room", "scene"),
    ("a kitchen", "scene"),
    ("a bathroom", "scene"),
    ("an office", "scene"),
    ("a gym", "scene"),
    ("a restaurant or cafe", "scene"),
    ("a shop interior", "scene"),
    ("a city street", "scene"),
    ("a beach", "scene"),
    ("a park or garden", "scene"),
    ("mountains or countryside", "scene"),
    ("a car interior", "scene"),
    ("a photography studio backdrop", "scene"),
    ("a warehouse or factory", "scene"),
    ("a classroom", "scene"),
    ("a stage or concert", "scene"),
    # --- what is being shown or sold ---
    ("clothing or fashion apparel", "product"),
    ("a dress", "product"),
    ("shoes or sneakers", "product"),
    ("a handbag or purse", "product"),
    ("jewelry", "product"),
    ("a watch", "product"),
    ("sunglasses or eyewear", "product"),
    ("skincare products", "product"),
    ("makeup or cosmetics", "product"),
    ("perfume", "product"),
    ("hair styling products", "product"),
    ("a smartphone", "product"),
    ("headphones or earbuds", "product"),
    ("a laptop or computer", "product"),
    ("a camera", "product"),
    ("home appliances", "product"),
    ("kitchen gadgets or cookware", "product"),
    ("furniture or home decor", "product"),
    ("bedding or pillows", "product"),
    ("cleaning products", "product"),
    ("toys", "product"),
    ("baby products", "product"),
    ("pet supplies", "product"),
    ("packaged food or snacks", "product"),
    ("drinks or beverages", "product"),
    ("fresh food dishes", "product"),
    ("dietary supplements or vitamins", "product"),
    ("sports equipment", "product"),
    ("outdoor or camping gear", "product"),
    ("stationery or art supplies", "product"),
    ("books", "product"),
    ("plants or flowers", "product"),
    ("a car or motorcycle", "product"),
    ("phone cases or accessories", "product"),
    # --- the shape of the creative ---
    ("a selfie video", "format"),
    ("a product close-up", "format"),
    ("a tutorial or demonstration", "format"),
    ("a before and after comparison", "format"),
    ("a phone screen recording", "format"),
    ("text on a plain colored background", "format"),
    ("a slideshow of photos", "format"),
    ("an interview", "format"),
    ("a vlog filmed outdoors", "format"),
    ("a cinematic advertisement", "format"),
    ("a live stream with chat overlay", "format"),
    ("a gaming video", "format"),
)


def vocabulary_digest() -> str:
    """A fingerprint of the list above, for the enrichment job's identity.

    The vocabulary is part of what the reading means: the same clip read
    against an extended list is a different draft, so the digest joins the
    job signature and an edit here re-reads instead of echoing the old draft.
    """
    joined = "\n".join(f"{label}\t{category}" for label, category in VOCABULARY)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _quiet_hub() -> None:
    """Download public models anonymously, whatever tokens the machine holds.

    The models are public and need no account - but `huggingface_hub` sends
    any token it finds lying around, and a *stale* token turns every download
    into a 401 for a reason no error message names. Found the hard way on the
    first machine this ran on.
    """
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    # Windows without developer mode cannot make the symlinks the cache
    # prefers; the degraded copy works and the warning would land in a job log
    # nobody can act on.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, Any] = {}


def _model_cache_dir() -> Path:
    from trendrelay_api.media_ai import MODEL_ROOT

    return MODEL_ROOT / "fastembed"


def models_cached() -> bool:
    """Whether both halves of CLIP are already on disk.

    The hub cache keeps each model under a `models--{owner}--{name}` folder;
    an ONNX file somewhere inside it is what separates "downloaded" from "a
    folder the download died in".
    """
    root = _model_cache_dir()
    for model in (VISION_MODEL, TEXT_MODEL):
        folder = root / ("models--" + model.replace("/", "--"))
        if not folder.is_dir() or not any(folder.rglob("*.onnx")):
            return False
    return True


def _engines() -> tuple[Any, Any]:
    """The CLIP halves, loaded once per process.

    Behind a lock for the same reason the OCR engine is: two enrichment jobs
    arriving together must not both pay the model load, or race the download.
    """
    with _ENGINE_LOCK:
        if "vision" not in _ENGINES:
            from trendrelay_api.media_ai import _runtime_path

            _runtime_path()
            _quiet_hub()
            from fastembed import ImageEmbedding, TextEmbedding

            cache = str(_model_cache_dir())
            _ENGINES["vision"] = ImageEmbedding(VISION_MODEL, cache_dir=cache)
            _ENGINES["text"] = TextEmbedding(TEXT_MODEL, cache_dir=cache)
        return _ENGINES["vision"], _ENGINES["text"]


_LABEL_CACHE: dict[str, Any] = {}


def _label_matrix() -> Any:
    """Every vocabulary entry embedded and normalised, once per process."""
    import numpy as np

    digest = vocabulary_digest()
    if _LABEL_CACHE.get("digest") != digest:
        _, text = _engines()
        prompts = [f"a photo of {label}" for label, _category in VOCABULARY]
        matrix = np.array(list(text.embed(prompts)), dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        _LABEL_CACHE.update({"digest": digest, "matrix": matrix})
    return _LABEL_CACHE["matrix"]


def download_models() -> None:
    """Fetch both CLIP halves, for the setup job. Import cost only afterwards."""
    _engines()


def vision_draft(asset: Any, source: Path, work: Path) -> dict[str, Any]:
    """Read what the clip shows, in the shape every machine draft takes.

    Frames are sampled exactly as the OCR reader samples them - same interval,
    same ceiling, same scaling - so "the frame the text was read from" and
    "the frame the scene was recognised in" are the same moment of the clip.

    `segments` carries one entry per sampled frame with that frame's own tags
    and timestamp, which is what lets a reviewer see *when* the product
    appears and not merely that it does. `text` is the clip-level summary: the
    aggregated tags joined into one searchable line.
    """
    import numpy as np

    from trendrelay_api.config import get_settings
    from trendrelay_api.media_ai import _extract_ocr_frames

    frames = _extract_ocr_frames(asset, source, work)
    vision, _text = _engines()
    labels = _label_matrix()

    embedded = np.array(list(vision.embed([str(frame) for frame in frames])), dtype=np.float32)
    embedded /= np.linalg.norm(embedded, axis=1, keepdims=True)
    similarities = embedded @ labels.T

    interval_ms = round(get_settings().media_ai_ocr_interval_seconds * 1000)
    segments: list[dict[str, Any]] = []
    for index in range(similarities.shape[0]):
        order = np.argsort(-similarities[index])[:TAGS_PER_FRAME]
        found = [
            {
                "label": VOCABULARY[at][0],
                "category": VOCABULARY[at][1],
                "score": round(float(similarities[index][at]), 4),
            }
            for at in order
            if float(similarities[index][at]) >= MIN_SCORE
        ]
        segments.append(
            {
                # The first sampled frame is at t=0 for an image and near it
                # for a clip; ffmpeg's fps filter emits the first frame of
                # each interval, so the index maps back to the interval start.
                "timestamp_ms": index * interval_ms if asset.media_kind != "image" else 0,
                "labels": found,
            }
        )

    tags = aggregate_tags(segments)
    return {
        "text": ", ".join(item["label"] for item in tags[:TAGS_PER_CLIP]),
        # The tags are English because the vocabulary is, whatever language
        # the clip speaks - "und" would claim nobody knows.
        "language": "en",
        "provider": f"{VISION_PACKAGE}@{VISION_VERSION}:clip-vit-b-32",
        "segments": segments,
    }


def aggregate_tags(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One list for the whole clip, out of the per-frame readings.

    Ranked by how much of the clip carries the tag and then by how strongly,
    because "seen on every frame at 0.24" describes the clip better than
    "seen once at 0.31". Each tag keeps the moment it first appeared, which
    is what lets a product tag point at the frame to check it against.
    """
    found: dict[str, dict[str, Any]] = {}
    for segment in segments:
        for item in segment.get("labels", []):
            entry = found.setdefault(
                item["label"],
                {
                    "label": item["label"],
                    "category": item.get("category", ""),
                    "frames": 0,
                    "score": 0.0,
                    "first_ms": segment.get("timestamp_ms", 0),
                },
            )
            entry["frames"] += 1
            entry["score"] = max(entry["score"], float(item.get("score", 0.0)))
    ranked = sorted(found.values(), key=lambda entry: (-entry["frames"], -entry["score"]))
    return ranked[:TAGS_PER_CLIP]
