# Licensing a swap model from InsightFace

TrendRelay will not run a swap model it was not given. `.data/face-swap/` is
empty by design, nothing downloads into it, and the capability reports itself
unavailable until both a model file and a licence reference are present.

This is the route to filling it legitimately.

## Why not a mirror

`inswapper_128` was withdrawn from public distribution by its authors. Every
copy still circulating is an unauthorised re-upload, and the weights it
re-uploads are licensed for non-commercial research — which affiliate revenue
is not. "Commonly used" describes how many people are doing it, not whether the
licence permits it.

The withdrawn model is also the weakest thing on offer. InsightFace now sells
`inswapper-512-live` and the Cyn/Dax series: higher resolution, better identity
retention, cleaner hair and boundary blending, and steadier results across a
clip rather than a still. The mirror route ends at the 2023 baseline.

## Download mirrors are a different question

Two things get called "a mirror" and only one of them is a licensing problem.

A **delivery mirror** — `hf-mirror.com`, ModelScope — serves the same files from
the same publishers, and exists because Hugging Face is slow or unreachable from
much of the world. It changes where a download comes from and nothing about
whether you may use what arrives. Set `HF_ENDPOINT`, or record one locally:

```python
from trendrelay_api.integrations import face_anon
face_anon.save_hf_endpoint("https://hf-mirror.com")
```

It must be https — weights fetched over plain HTTP can be altered in transit,
and a tampered model fails silently rather than loudly. The setting lives in
`.data/face-anon/hf-endpoint`, git-ignored like the token.

A **re-upload** of a withdrawn model is not that. The file is the same bytes,
but the publisher took it down and the licence never permitted commercial use;
serving it from somewhere else changes neither. The gates in `face_swap.py`
answer that question and the endpoint setting does not touch them — there is a
test asserting exactly that, because the distinction is easy to lose.

## Who to contact

- **Email:** contact@insightface.ai
- **Licensing page:** https://www.insightface.ai/solutions/face-swapping
- **Model licensing:** https://www.insightface.ai/services/models-commercial-licensing

## A draft enquiry

Adjust the volumes — they change which product they quote, and a local model
licence, an SDK and a hosted API are priced very differently.

> Subject: Commercial licence enquiry — inswapper for short-form video
>
> Hello,
>
> I run a small commercial content operation and would like to license an
> inswapper model for face swapping in short-form vertical video.
>
> Usage: roughly [N] clips per month, each under 60 seconds, produced for
> affiliate marketing. Output is published to public social platforms.
>
> Deployment: currently local inference on a single Windows workstation with an
> NVIDIA RTX 2060 (6 GB), running ONNX Runtime with the DirectML provider. I am
> open to a hosted API instead if that suits the licence better, or fits the
> hardware better than local weights would.
>
> I would like to understand:
>
> 1. Which model you would recommend for this use — I understand
>    inswapper-512-live supersedes the older 128 baseline.
> 2. Licence terms and pricing for commercial use at this volume.
> 3. Whether local weights or your API is the appropriate route given the GPU.
> 4. Any restrictions on subject matter or consent that come with the licence,
>    since the source footage features people other than the operator.
>
> Thank you,
> [name] — [company] — [country]

Point 4 is not padding. It is the question whose answer determines whether the
licence covers what you actually intend to do, and it is better asked now than
discovered later.

## Recording the licence

Once you hold one:

```python
from trendrelay_api.integrations import face_swap
face_swap.record_licence("your-user-id", reference="INV-2026-0042")
```

Put the model file they supply in `.data/face-swap/`. Both that directory and
the licence record are git-ignored: licensed weights are somebody else's
property and must never be committed.

## What a licence does not settle

A swapped face is synthetic media of a real, identifiable person. The source
clips here are downloaded from other creators, so the people in them did not
agree to appear in your advertising wearing someone else's face.

The licence answers whether you may run the model. It does not answer whether
the footage may be published that way — that is platform policy, publicity and
likeness rights, and in several markets synthetic-media disclosure law. The
status report keeps the two apart on purpose, because a solved licence question
reads very easily as a solved question.
