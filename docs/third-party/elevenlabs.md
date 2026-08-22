# ElevenLabs

- Service: https://elevenlabs.io
- API: `v1`, at `https://api.elevenlabs.io/v1`
- Auth: an API key in an `xi-api-key` header — not `Authorization`, and not a bearer token
- Licence: commercial service terms, https://elevenlabs.io/terms-of-use
- Commercial use: **allowed** — recorded by the project owner; see below
- Status: key and quota only; nothing generates speech yet

Hosted text-to-speech, for voicing a clip from the transcript the Library already
holds. It is the first entry here that is a paid metered service rather than
either a local model or a free API, and almost everything below follows from
that one difference.

## Why it is not shaped like the Library's other models

faster-whisper, RapidOCR and Argos Translate are downloads. They cost a few
hundred megabytes once, then run offline and free, for ever. This costs nothing
to install and money every time it runs, and the words being voiced leave the
machine.

So it is modelled on the publishing engines instead: a key in `.env`, a
reachability probe, and an allowance read from the service. There is no runtime
to prepare and no "download and switch on" button, because there is nothing to
download — **the key is the switch**.

## The allowance is measured, not published

`engine_limits` keeps three confidences apart, and most publishing engines sit at
`published`: a figure read off a pricing page on a date, which can be a year
stale and still get believed. This one does better. `GET /v1/user/subscription`
returns `character_count`, `character_limit` and `tier`, so:

- usage is what it actually is, not what a plan implies;
- the plan is **named by the service**, which no publishing engine here can do —
  theirs has to be inferred from a quota.

Nothing in the integration quotes a price, and the allowance is read live rather
than cached. A cached one is how a batch gets waved through on an allowance that
ran out an hour ago.

## The cost shape that matters

Text-to-speech is billed **per character**. This library holds around eighteen
hundred clips, so a batch action across it would exhaust a month in one click —
Creator, the tier whose figure is published, includes 220k characters.

That is why `characters_remaining` is reported from stage one, before anything
can spend it. Any generation path added later owes a pre-flight count against it
rather than discovering the ceiling by hitting it.

## Models

| Model | Languages | Note |
| --- | --- | --- |
| `eleven_v3` | 70+ | |
| `eleven_multilingual_v2` | 29 | the API default |
| `eleven_flash_v2_5` | 32 | ~75ms; "50% lower price per character for API generations" |

`eleven_turbo_v2_5` is superseded by Flash.

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

**Per-tier allowances are mostly unpublished.** Only Creator's 220k characters is
stated. Which is a reason to read the allowance from the API rather than to
maintain a ladder of numbers that would be half guesses.
