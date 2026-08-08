# deepinsight/insightface

- Repository: https://github.com/deepinsight/insightface
- Pinned revision: `7fadd420c2351d0ffa8cac403421c1a3ed733365`
- License: MIT for the code. The pretrained models, including `inswapper_128`,
  are restricted to non-commercial research use.
- Commercial use: **blocked** by the model terms
- Status: catalogued and licence-checked; no TrendRelay adapter yet

Face detection, embedding and swapping. The detection and embedding models are
excellent and would improve on the OpenCV cascade currently doing face blur.

The split matters more than the headline. The repository's code is MIT, which
reads as permissive until you notice the weights are not: `inswapper_128` is
non-commercial, was withdrawn from public distribution, and commercial use needs
a licence from InsightFace directly. A catalogue that recorded only "MIT" would
be actively misleading, which is why the licence field here names both.

Also evaluated and not catalogued:

- `hanweikung/face_anon_simple` (WACV 2025) — anonymises by generating a
  replacement face rather than swapping in a real one, which is the safer
  capability. AGPL-3.0, so its copyleft reaches anything it is linked into, and
  it handles images only.
- `s0md3v/roop` — archived in March 2026 by its author over the technology's
  second-order effects. Development continues in forks.
