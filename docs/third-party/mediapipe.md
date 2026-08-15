# google-ai-edge/mediapipe

- Repository: https://github.com/google-ai-edge/mediapipe
- Installed as: a Python extra (`pip install -e services/api[landmarks]`), from
  the published wheel rather than a source checkout
- License: **Apache-2.0**, for the code *and* for the Face Landmarker model
  bundle
- Commercial use: **permitted**, with no acknowledgement gate
- Status: integrated and optional — the feature it improves works without it

## What it does here

It reads a 478-point face mesh, which is what `face_overlay` uses to decide
where on a face an object hangs and how far the head is tilted. Only a handful
of those points are read — the outer eye corners, the nose tip, the lips and the
chin — because a prop hangs from landmarks, not from a topology.

## Why it is optional

It is the best of three tiers and the only one that costs anything to install.
Below it, OpenCV's YuNet already returns five landmarks with every detection at
no extra cost, and below that the parts of a face are estimated from the
detection box. See ADR 0018 for what each tier can and cannot do; the short
version is that the box tier cannot tilt an object with a tilted head, and the
API reports which tier ran so that limitation is visible rather than mysterious.

A ~60MB wheel and a model download are not worth making mandatory for every
install, most of which will never stick a sticker on a face.

## The model bundle

The Tasks API does not ship weights inside the wheel. Download the Face
Landmarker bundle and put it at:

```
.data/models/face_landmarker.task
```

Unlike YuNet — which is fetched at setup, being 227KB and MIT — this bundle is
not downloaded for you. It is useless without the optional `mediapipe` package,
so fetching it on every install would mean shipping a file almost nobody can
load. Adding it to `scripts/model_assets.py` is one entry if that ever changes.

`GET .../face-overlay/status` reports whether the runtime and the bundle are
both present, and names whichever is missing.

## The contrast with InsightFace, which is the point of recording this

InsightFace is also a face runtime here and is *not* interchangeable with this
one. Its code is MIT but its pretrained models are non-commercial research only,
which is why it sits behind a stored licence acknowledgement (see
`insightface.md` and `insightface-swap-licensing.md`). MediaPipe carries no such
restriction: Apache-2.0 covers the model bundle as well as the code, so nothing
about this dependency is gated.

The two are used for different things and the split is deliberate — MediaPipe
says *where the parts of a face are*, InsightFace says *whose face it is*. Only
the second question needs a licence decision.
