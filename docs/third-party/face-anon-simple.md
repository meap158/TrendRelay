# hanweikung/face_anon_simple

- Repository: https://github.com/hanweikung/face_anon_simple
- Paper: *Face Anonymization Made Simple* (WACV 2025)
- Pinned revision: `c36f276352873827e9d559ee8d130b7563491171`
- License: **AGPL-3.0**
- Commercial use: conditional — see the isolation note below
- Status: integrated and gated; runs on Python 3.12, **waiting on a Hugging Face licence acceptance** — see below
- Media: **still images only**

## What it does

It replaces a face with a *generated* one that keeps the original expression,
head pose and gaze. The shot still reads as a person reacting; it just is not
that person any more.

This is the distinction that matters, and it is why this is the version of the
technology worth having: it is **not** a face swap. No second person's likeness
is involved, and the face that comes out belongs to nobody. Blurring says
"someone was here and you may not see them"; this says nothing at all.

Weights come from three repositories — `hkung/face-anon-simple` for the UNet and
the two ReferenceNets, `stabilityai/stable-diffusion-2-1` for the VAE and
scheduler, and `openai/clip-vit-large-patch14` for the image encoder.

## Why it runs in its own process

AGPL-3.0 copyleft reaches whatever the licensed code is combined with, and
TrendRelay is not AGPL. So this is installed as its own checkout with its own
virtual environment and invoked as a subprocess. The API package never imports
it; they exchange argv, files and JSON on stdout. That is the difference between
*using a program* and *linking a library*, and it is the same arrangement the
Douyin downloader already uses for an unrelated reason.

That arrangement is a deliberate choice rather than legal advice, which is why
the capability reports itself unavailable until an operator records that they
accept running an AGPL tool this way.

## Stills only, and why video is refused rather than attempted

The model generates each face independently at 512×512. Run it across a clip and
every frame receives a *different* invented face, so the result strobes. That is
not a wiring gap that a loop would close — it is the same temporal-consistency
problem that rules out per-frame diffusion generally.

So video is refused with a message pointing at the identity-aware blur, which
does hold steady across a clip. Producing a flickering video and calling it
anonymised would be worse than declining.

## Resolved: it needs Python 3.10–3.12, not the API's 3.14

The integration is complete and the isolation is in place. It does not yet run
here, and the reason is a hard dependency deadlock rather than anything fixable
in the wiring:

- The repository vendors its own fork of **diffusers 0.25.1** under `src/`, and
  that fork is coupled to that exact version's internals. On diffusers 0.39 it
  fails importing `PositionNet`, which was renamed after 0.25.1.
- diffusers 0.25.1 and the fork both need **huggingface_hub < 0.26**, because
  they call `cached_download`, removed in 0.26.
- The only `transformers` installable on Python 3.14 is 5.x, which requires
  **huggingface_hub >= 1.5**.

Those two constraints are mutually exclusive. Satisfying the repository means
`transformers 4.46.1`, whose `tokenizers` dependency has no Python 3.14 wheel.
The project's own `environment.yml` pins Python 3.8.18 for exactly this reason.

The fix was an interpreter, not a patch. The tool's virtualenv is now built on a
uv-managed standalone Python 3.12 — no system install, nothing on PATH — and the
pinned stack installs into it cleanly: torch 2.4.1+cu124, diffusers 0.25.1,
transformers 4.46.1, huggingface_hub 0.25.2. Because the tool already runs as an
isolated subprocess, it never had to share the API's interpreter: the AGPL
boundary and the version boundary turned out to be the same boundary.

## Still blocked: Stable Diffusion 2-1 is gated

The model builds on `stabilityai/stable-diffusion-2-1`, and Stability has gated
that repository behind licence acceptance. An unauthenticated fetch answers 401,
which diffusers reports as *"not a valid model identifier"* — a message that
sends you hunting for a typo instead of a login.

Accepting a model licence is the operator's to do, so:

1. Accept the terms at https://huggingface.co/stabilityai/stable-diffusion-2-1
   while signed in to your own Hugging Face account.
2. Create a read token and set `HF_TOKEN`.

The integration detects this case and says exactly that rather than surfacing
the raw error. `openai/clip-vit-large-patch14` and `hkung/face-anon-simple`
itself are both ungated and download fine.

Patching forward instead was tried and abandoned: the `cached_download` shim in
`scripts/patches/` cleared one wall and the next appeared immediately. Chasing
renamed internals through a vendored fork is unbounded work with a silent-wrong
failure mode at the end of it.

## Notes from installing it

- `pip install torch` on Windows gives a **CPU-only** build. Diffusion on CPU is
  minutes per face; the CUDA build has to come from PyTorch's own index
  (`--index-url https://download.pytorch.org/whl/cu126` matches torch 2.13 and
  supports Turing).
- There is no `from_pretrained` shortcut for this pipeline. The pipeline class
  and its ReferenceNet live in the checkout and the weights come from three
  separate repositories, so the loading code follows the project's own
  `demo.ipynb` rather than a guessed convenience wrapper.
- Three UNet-sized networks load at once. In fp32 they do not fit a 6 GB card
  alongside the VAE and CLIP encoder, so inference runs in fp16, with
  `enable_model_cpu_offload()` available for cards that still cannot hold it.
- The Hugging Face cache is redirected to `.data/models/huggingface` on the
  project drive. The three repos come to roughly ten gigabytes and this
  machine's `C:` had 8 GB free — left at the default the download would have
  died most of the way through with a disk error rather than anything about
  models.
- uv caches to `C:` as well, and torch filled it: the drive hit 86 MB free
  before `uv cache clean` recovered 4.7 GB. Set `UV_CACHE_DIR` to the project
  drive before installing this stack.
