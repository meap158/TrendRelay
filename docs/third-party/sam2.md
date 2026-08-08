# facebookresearch/sam2

- Repository: https://github.com/facebookresearch/sam2
- Pinned revision: `2b90b9f5ceec907a1c18123530e92e794ad901a4`
- License: Apache-2.0
- Commercial use: allowed
- Status: catalogued and licence-checked; no TrendRelay adapter yet

Promptable segmentation — a point, box or mask says what to segment — and it
propagates that mask across video frames, which is the part that matters here.

Why it is here: it is the cleanest licence of the segmentation options and the
only one that tracks a mask through a clip rather than solving each frame
independently. Its cost is that it needs a prompt, so it suits an operator
choosing a region far more than it suits an automatic pass.
