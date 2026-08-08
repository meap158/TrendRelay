# Zheng-Chong/CatVTON

- Repository: https://github.com/Zheng-Chong/CatVTON
- Pinned revision: `7818397f25613beedb3d861a34769f607cfcf3b1`
- License: CC BY-NC-SA 4.0
- Commercial use: **blocked** by the licence
- Status: catalogued and licence-checked; no TrendRelay adapter yet
- Inference: 1024x768 in under 8 GB, the lightest credible try-on model

Diffusion virtual try-on: put a specific garment onto a specific person.

Why it is here, and what it costs. Of the try-on models surveyed this is the
only one that fits a 6 GB card at all — IDM-VTON wants 16 GB, and its repository
has not been touched since March 2025. But every major open-source try-on model
in 2026 is released non-commercially, this one included, so shipping it in a
tool that drives affiliate revenue needs a separate agreement with the authors.

It is also images only. Applied per frame to a clip, each frame is generated
independently and the result flickers; temporally consistent video try-on is not
a solved open-source problem. The clothing recolour already in TrendRelay solves
a smaller problem and is stable precisely because it invents nothing.
