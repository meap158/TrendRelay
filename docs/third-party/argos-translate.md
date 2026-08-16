# argosopentech/argos-translate

- Repository: https://github.com/argosopentech/argos-translate
- Pinned version: `1.11.0` (PyPI `argostranslate`)
- License: MIT or CC0, at the user's choice (dual licensed)
- Commercial use: allowed
- Status: adapted, for subtitle translation

Offline neural machine translation built on CTranslate2 - the same inference
runtime faster-whisper already installs - so it adds a package rather than a
second stack. Language pairs are downloaded as individual packages and run
locally; nothing is sent anywhere during a translation.

Why it is here: the transcriber can already read speech in whichever language
it was spoken, and a subtitle in that language only serves an audience who
already understood the video. This is the piece that lets a Vietnamese clip
carry English captions, or the reverse.

Why this one over the alternatives. NLLB-200 translates more accurately and
covers far more languages, but it is CC-BY-NC and would have been the second
non-commercial entry in this catalogue, capping a use TrendRelay has not ruled
out. Opus-MT is small and fast but needs a separate model per direction, with
uneven coverage across the rarer pairs. Argos is the only one whose licence is
unambiguous in both directions, and it shares the runtime already present.

## What it does not do

Translation destroys word-level timing. Word order changes between languages,
so the per-word spans the transcriber measured no longer correspond to anything
in the translated text. Styles that highlight the word currently being spoken
therefore fall back to showing the whole cue on a translated track - the timing
of the cue itself is still exact, because it is inherited rather than
re-derived.
