# opencv/opencv_zoo — YuNet face detector

- Repository: https://github.com/opencv/opencv_zoo
- Model: `models/face_detection_yunet/face_detection_yunet_2023mar.onnx`
- Paper: Wu, Peng, Yu, *YuNet: A Tiny Millisecond-level Face Detector*,
  Machine Intelligence Research (2023)
- License: **MIT** — stated in `models/face_detection_yunet/LICENSE`
  ("All files in this directory are licensed under MIT License")
- Commercial use: **permitted**, with no acknowledgement gate
- Installed as: a 227KB file fetched at setup into
  `.data/models/face_detection_yunet.onnx`
- Status: integrated and fetched by default

## What it does here

It is the face detector behind everything in the editing suite that looks for a
face: the blur, the identity-aware blur's fallback path, the garment recolour's
torso estimate, and the object overlay. `cv2.FaceDetectorYN` runs it directly —
the model is the only part OpenCV does not ship.

It returns five landmarks per detection alongside the box, which is what lets an
object placed on a face tilt with the head. Nothing else available without an
extra install does that.

## Why it is fetched rather than described

It was a documented manual step, and the result was that no install had it.
Every machine ran the fallback — OpenCV's bundled Haar cascade — which finds
fewer faces, regularly calls patterned clothing a face, and returns no landmarks
at all. On one test photograph YuNet finds six faces and places an object
precisely on each; the cascade finds four and puts one in mid-air.

A 227KB MIT-licensed file with no gate attached is not a thing to make somebody
go and get. See ADR 0021.

## The pin

Both official sources serve identical bytes:

```
sha256  8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
size    232589
```

That hash is also the `oid` recorded in opencv_zoo's own Git LFS pointer for the
file, so it is the repository's commitment to the content rather than what one
server happened to send. It is verified before the file is moved into place, and
a mismatch installs nothing.

Note for anyone changing the source: the file is stored in Git LFS, so
`raw.githubusercontent.com` returns a 130-byte pointer rather than the model.
The fetcher uses `media.githubusercontent.com` and the `opencv` organisation's
Hugging Face mirror.

## Why the 2023 revision and not the 2026 one

opencv_zoo now recommends `face_detection_yunet_2026may.onnx`, which has dynamic
input dimensions for OpenCV 5's ONNX Runtime engine. `services/api` pins
`opencv-python-headless>=4.10,<5`, and OpenCV 4 infers on the exact shape it is
handed. The 2026 file is the right choice for a runtime this project does not
install; a test asserts the two stay in step, so moving to OpenCV 5 will fail
loudly here rather than quietly picking the wrong model.
