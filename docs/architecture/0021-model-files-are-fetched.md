# ADR 0021: Small permissive model files are fetched at setup, pinned by hash

Status: Accepted and built - `scripts/model_assets.py`, called from
`scripts/bootstrap.py`, with YuNet as the first entry.

## Context

Several capabilities here degrade instead of failing when their model is
missing, and that was meant to be a kindness. It became the default state.

The face detector is the clearest case. `cv2.FaceDetectorYN` needs a 227KB ONNX
file that OpenCV does not ship, so the code looked for it at
`.data/models/face_detection_yunet.onnx`, fell back to OpenCV's bundled Haar
cascade when it was absent, and a comment explained that dropping the file in
would switch the better path on. Nobody dropped the file in. Every install ran
the cascade, which:

- finds fewer faces, and misses faces at an angle,
- regularly decides patterned clothing is a face,
- returns **no landmarks**, so an object placed on a face cannot be tilted to
  match the head, cannot be scaled from the eye span, and has to be positioned
  from the detection box alone.

On one test photograph YuNet finds six faces and lands a pair of sunglasses
precisely on each; the cascade finds four, one of which is not a person, and
puts a pair in mid-air. That gap was shipped as the normal experience for
however long, behind an explanation nobody was going to read.

## Decision

**Fetch it.** The file is 227KB, MIT-licensed, redistributable, and has no
acknowledgement gate. There was never a good reason for an install not to have
it, and "documented as a manual step" is the same thing as "absent".

**Pin it by SHA-256, and check before installing.** A model is code in the sense
that matters — it decides what the software does to a photograph of somebody's
face — so fetching one over the network at setup time is only acceptable with a
hash that cannot be talked out of. The file is downloaded to a temporary path,
hashed, and only then moved into place, so nothing ever loads a partial file and
a wrong one never reaches the directory the loader reads.

A hash mismatch is a hard stop, **not** a reason to try the other mirror. Two
official sources disagreeing with the pin is a supply-chain signal, and falling
through would turn it into "keep trying until something passes".

The pinned hash is also the `oid` in opencv_zoo's own Git LFS pointer for the
file — the repository's commitment to the content, not merely what one server
sent. Both mirrors were confirmed to serve exactly those bytes.

**Never fatal.** Every capability that uses one of these already runs without
it. No network, a proxy in the way, or an upstream that has moved leaves the
fallback in place, prints what is lost and how to place the file by hand, and
setup continues. `TRENDRELAY_SKIP_MODEL_DOWNLOAD` turns it off for an
air-gapped machine.

**Run on every setup, not only after a dependency install.** An environment that
was complete before this existed still needs the file, and a machine that was
offline the first time should get it on the next run. Present-and-correct costs
a hash of a 227KB file, so the usual case is free — and it also catches a
truncated earlier download, which is the likely way this goes wrong.

**Only files that clear the bar.** Small, permissively licensed, no gate. That
is a deliberately narrow rule and it excludes most of what this project touches:
InsightFace's weights are non-commercial and gated behind a recorded licence
acknowledgement, and MediaPipe's landmarker bundle is useless without an
optional package, so fetching it for everybody would ship a file almost nobody
can load. Neither is here.

## Consequences

The out-of-the-box experience of every face feature improves without anyone
doing anything, which is the point.

Setup now touches the network for something other than package installation. It
is one 227KB GET from GitHub or Hugging Face, it is bounded and hash-checked,
and it can be turned off — but it is a new thing setup does, and an operator
watching a firewall should know why.

The pin will eventually go stale if upstream replaces the file in place. That
surfaces as a loud, specific refusal to install rather than as a silently
different model, which is the correct failure. Refreshing it is one constant and
one test run.
