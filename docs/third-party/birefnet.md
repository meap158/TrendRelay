# ZhengPeng7/BiRefNet

- Repository: https://github.com/ZhengPeng7/BiRefNet
- Pinned revision: `25cb9309bacf3dde954e4584594e16e142c51de5`
- License: MIT for the code. The pretrained weights carry no stated terms.
- Commercial use: conditional — see below
- Status: catalogued and licence-checked; no TrendRelay adapter yet
- Inference: about 5.5 GB at 1024x1024, so it fits a 6 GB card with little room

High-resolution binary segmentation: it answers "what is the foreground" without
being prompted, which is the opposite of what SAM 2 does.

Why it is here: the clothing recolour currently estimates a torso from the
detected face and moves only saturated pixels inside it. That works and is
cheap, but it is an estimate — a real subject mask would let the recolour follow
the actual garment edge, and would also give background replacement.

The conditional rating is deliberate. The code is MIT, but the repository does
not state terms for the trained weights, and MIT on the code does not extend to
them. InsightFace is the cautionary case: MIT code, non-commercial weights.
Confirm the weight terms before shipping anything that depends on them.
