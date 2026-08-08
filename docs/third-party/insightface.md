# deepinsight/insightface

- Repository: https://github.com/deepinsight/insightface
- Pinned revision: `7fadd420c2351d0ffa8cac403421c1a3ed733365`
- Installed as: a Python extra (`pip install -e services/api[faces] --no-deps`),
  not a source checkout. The runtime comes from the published wheel; the
  `buffalo_l` models download from InsightFace on first use.
- License: MIT for the code. The pretrained models, including `inswapper_128`,
  are restricted to non-commercial research use.
- Commercial use: **conditional** — needs a licence from InsightFace directly
- Status: adapted, behind a licence gate

## What it does here

It powers `selective_face_blur`: every face in a clip is embedded as 512
numbers, the embeddings are clustered, and the largest cluster is taken to be
the subject. The blur then covers everyone *except* that person — or only that
person, inverted — which is what a creator filming in public actually needs.
Covering every face defeats the video; covering none defeats the point.

No reference photo is required, and the render reports how many identities were
found and what share of the clip each held, so the subject guess is checkable
rather than magic.

Measured on a real 6-second clip: 185 frames, 70 faces, 5 identities, about 33
seconds on CPU. That is roughly five times slower than real time, so it suits a
deliberate render rather than a live preview.

## The licence, which is the whole reason this is gated

The split is what matters and it is easy to get wrong. The repository's code is
MIT, which reads as permissive until you notice the weights are not:
`inswapper_128` is non-commercial, was withdrawn from public distribution, and
commercial use needs a licence from InsightFace. A catalogue that recorded only
"MIT" would be actively misleading, which is why the licence field names both.

TrendRelay drives affiliate revenue, so this is commercial use. The capability
therefore reports itself unavailable until an operator records that they hold
the right to use these models. That acknowledgement is stored with the terms it
accepted, names who accepted it, and can be withdrawn — an acknowledgement that
cannot be withdrawn is not a decision, it is a trap.

The gate holds at the recipe layer, not just in the editor, because a render job
can be posted straight to the API without the interface ever asking.

`inswapper_128` is not used, not downloaded, and not reachable from here.

## Also evaluated and not adopted

- `hanweikung/face_anon_simple` (WACV 2025) — anonymises by generating a
  replacement face rather than swapping in a real one, which is the safer
  capability. AGPL-3.0, so its copyleft reaches anything it is linked into, and
  it handles images only.
- `s0md3v/roop` — archived in March 2026 by its author over the technology's
  second-order effects. Development continues in forks.

## One packaging trap

InsightFace depends on `opencv-python`. That is the GUI build and a *different*
distribution from the `opencv-python-headless` this project uses; installing it
plainly puts both in the environment, both providing `cv2`, and pip resolves it
to a 5.x that this project's own constraint excludes. It silently replaced the
OpenCV the editing suite was verified against. Install with `--no-deps`.
