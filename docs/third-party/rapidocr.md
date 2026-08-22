# RapidAI/RapidOCR

- Repository: https://github.com/RapidAI/RapidOCR
- Pinned version: `3.9.2` (PyPI `rapidocr`), on ONNX Runtime `1.28.0`
- License: Apache-2.0
- Commercial use: allowed
- Status: adapted, for the on-screen text half of a transcript

The PP-OCR detection and recognition models exported to ONNX and run through
ONNX Runtime, which is the same shape of dependency as the rest of the media
analysis runtime: a wheel and a model file, no service to keep running and no
request leaving the machine.

Why it is here: a short video says one thing out loud and shows another. Prices,
product names, the hook burned into the first frame and the call to action in
the last are often typed rather than spoken, so a transcript built only from
audio misses the part a creative recipe most needs. This reads the frames.

Why this one over the alternatives. PaddleOCR is the upstream these models come
from and is the more complete toolkit, but it pulls in PaddlePaddle - a second
deep-learning framework alongside the CTranslate2 stack faster-whisper already
installs, for one feature. Tesseract is lighter still and markedly worse on the
stylised, low-contrast, overlaid text that short-form video is made of, which is
precisely the case here. EasyOCR is comparable in accuracy but ships a PyTorch
dependency, which is the same objection as Paddle at a larger download.

## What it does not do

It reads text; it does not judge it. Watermarks, usernames, interface furniture
from whichever app the clip was captured in, and subtitles somebody else burned
in all come back looking exactly like the creator's own copy. Frames are sampled
on an interval rather than decoded whole, so a caption on screen for less than
that interval can be missed entirely.

Everything it produces is a draft. Like the speech transcriber, its output is
recorded as a machine transcript and has to be reviewed before it becomes part
of a creative recipe.
