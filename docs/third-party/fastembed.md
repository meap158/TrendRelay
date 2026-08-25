# qdrant/fastembed (content recognition)

- Repository: https://github.com/qdrant/fastembed
- Pinned version: `0.8.0` (PyPI `fastembed`), on ONNX Runtime `1.28.0`
- Models: `Qdrant/clip-ViT-B-32-vision` and `Qdrant/clip-ViT-B-32-text`
  (OpenAI CLIP ViT-B/32, exported to ONNX; MIT-licensed weights)
- License: Apache-2.0
- Commercial use: allowed
- Status: adapted, for the third reading of a clip - what it shows

CLIP run locally through ONNX Runtime - the same runtime the OCR reader
already shares, so the marginal footprint is the two model halves, about a
third of a gigabyte fetched once at setup. Nothing leaves the machine during
analysis, and the models are public: the downloader deliberately sends no
Hugging Face token, because a stale one on the machine turns a public
download into a 401.

Why it is here: speech and on-screen text answer "what does the clip say";
neither answers "what is this a video *of*". A product held up wordlessly, a
scene that sets the creative's tone, the format of the thing - selfie, demo,
before-and-after - are in no transcript, and they are what search, product
matching and creative analysis actually want from a clip nobody has watched.

Why this one over the alternatives. CLIP compares pictures to *sentences*, so
recognition is a curated vocabulary rather than a fixed label head - teaching
it a new product category is editing a list in `media_vision.py`, not
training a model, and the enrichment job re-reads a clip when the list
changes. Fixed-label classifiers (ImageNet heads, EfficientDet) are lighter
but answer in categories nobody asked about; open-vocabulary taggers with
better accuracy (RAM, SigLIP-large) ship PyTorch or gigabyte-class weights,
which is the same objection the OCR reader raised against PaddleOCR. fastembed
itself is the thinnest maintained wrapper that runs both CLIP halves on the
ONNX Runtime already installed.

## What it does not do

It recognises; it does not read, count or locate. Text on screen belongs to
the OCR reading, and a tag says a product of some kind appears near a moment,
not where in the frame or how many. Scores are relative rather than
calibrated - CLIP's cosine similarities cluster tightly, which is why the
draft keeps them visible for a reviewer instead of pretending they are
percentages. Like every reading in the Library, the result is a machine draft
until somebody reviews it.
