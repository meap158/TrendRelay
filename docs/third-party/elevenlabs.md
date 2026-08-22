# ElevenLabs

- Service: https://elevenlabs.io
- API: `v2` voice search plus `v1` subscription, model, and text-to-speech endpoints
- Auth: an API key in an `xi-api-key` header — not `Authorization`, and not a bearer token
- Licence: commercial service terms, https://elevenlabs.io/terms-of-use
- Commercial use: **allowed** — recorded by the project owner; see below
- Status: adapted — guided key setup, live quota, regional voice/model selection, and durable generation

Hosted text-to-speech, for voicing a clip from the transcript the Library already
holds. It is the first entry here that is a paid metered service rather than
either a local model or a free API, and almost everything below follows from
that one difference.

## Why it is not shaped like the Library's other models

faster-whisper, RapidOCR and Argos Translate are downloads. They cost a few
hundred megabytes once, then run offline and free, for ever. This costs nothing
to install and money every time it runs, and the words being voiced leave the
machine.

So it is modelled on the publishing engines instead: a key saved from **Tools →
ElevenLabs → Setup**, a reachability probe, and an allowance read from the
service. There is no runtime to prepare and no "download and switch on" button,
because there is nothing to download — **the key is the switch**. The key is
written to the local `.env`, masked in the interface, and never returned to the
browser.

## The allowance is measured, not published

`engine_limits` keeps three confidences apart, and most publishing engines sit at
`published`: a figure read off a pricing page on a date, which can be a year
stale and still get believed. This one does better. `GET /v1/user/subscription`
returns `character_count`, `character_limit`, `tier`, and the next reset, so:

- usage is what it actually is, not what a plan implies;
- the plan is **named by the service**, which no publishing engine here can do —
  theirs has to be inferred from a quota.

Nothing in the integration quotes a price, and the allowance is read live rather
than cached. A cached one is how a batch gets waved through on an allowance that
ran out an hour ago.

## The cost shape that matters

Text-to-speech is metered from the service's live character allowance. Models
may apply a cost multiplier; the picker reads it from `GET /v1/models` and shows
the estimated credits before queuing. It also applies the model's live free- or
paid-plan per-request ceiling instead of assuming every tier accepts the same
script length.

That is why `characters_remaining` is reported before anything can spend it.
The durable job records the source character count, estimated metered cost,
model, language, voice, and voice controls used for the take.

## Voice and model selection

The Library reads the current endpoints rather than maintaining a stale local
voice or model list:

- `GET /v2/voices` is followed through every pagination token. The picker can
  search names, descriptions, labels, accents, and use cases, and filter by
  verified language and locale-derived country or region.
- `GET /v1/models` supplies only text-to-speech-capable models, their supported
  languages, feature flags, cost multiplier, and per-plan request limits.
- The generation request carries the chosen model and spoken language plus
  stability, similarity, speed, optional style, and speaker boost controls.
- Voice catalog metadata may expose a preview URL, but TrendRelay does not load
  it automatically; merely opening Library does not spend quota or fetch audio.

Official references: [voice search](https://elevenlabs.io/docs/api-reference/voices/search),
[models](https://elevenlabs.io/docs/api-reference/models/list),
[subscription](https://elevenlabs.io/docs/api-reference/user/subscription/get),
and [text to speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).

## What is unresolved

**Commercial use is recorded as `allowed` on the project owner's decision.**
Worth writing down because it was not read off a licence the way every other
entry in this catalogue was: the pricing page lists tiers and says nothing about
ownership or commercial use of the output, beyond Enterprise getting "custom
terms". The catalogue records the decision, not a citation, and this line is
where that distinction is kept.

**Voice cloning is deliberately out of scope.** Cloning a real person's voice is
a consent question, not a feature flag, and this repository already treats the
equivalent seriously for faces — see `insightface-swap-licensing.md`. Stock
voices only, unless and until cloning gets its own consent gate.

**Per-tier allowances are not maintained locally.** The plan, used allowance,
limit, and reset are read from the account API. This includes the free plan and
avoids maintaining a ladder of numbers that would become stale.
