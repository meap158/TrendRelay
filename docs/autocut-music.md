# AutoCut template music

Each AutoCut template rides a track. The tracks are the audio of the
operator's own @ai_videos_tiktok reference videos - the hand-made
image-to-video montages this feature automates - extracted and
loudness-normalised into `.data/autocut/audio/<template>.m4a` by
`scripts/autocut_extract_music.py`. That directory is git-ignored data; the
script is the reproducible record.

## Why these references

Each template was mapped to the reference whose tempo (measured by
`autocut.beat_analysis`) matches its designed cadence and whose length is
enough to back a full render. Measured 2026-09-07:

| Template | Reference | BPM | Length |
| --- | --- | --- | --- |
| steady-two | 7227691789480742150 | 94.0 | 19.4s |
| rapid-one | 7230618538464120069 | 126.5 | 13.7s |
| build-up | 7228481119539219717 | 115.0 | 29.8s |
| breathe | 7227711908386753798 | 88.0 | 23.4s |
| punch | 7225539850458516741 | 140.0 | 24.5s |

The full reference sweep (18 videos, 60-148 BPM) is in the extraction
script's neighbours; these five span the templates' range.

## Regenerating

`python scripts/autocut_extract_music.py` reads the reference paths from the
workspace database, re-extracts, and re-normalises. Nothing reaches the
network. Re-run it after adding references or re-tuning the mapping; adjust
`MAPPING` in the script to point a template at a different track.

## What the references' transitions taught the templates

Measured 2026-09-07 (frame-difference around each scene cut, `analyze` in
the scratchpad): the hand-made montages **hard-cut far more than they
dissolve**. The fast montages (≈126 BPM, ~14 cuts) are pure hard cuts on
near-static frames - no dissolve, almost no motion between cuts. The ~94 BPM
workhorse cadence is also all hard cuts. Only the slow, editorial references
dissolve: the calmest one crossfaded every change. Between-cut motion rose
as the cadence slowed - the quick ones sit still, the slow ones drift.

So the templates: `rapid-one` and `steady-two` hard-cut (matching the
evidence), `breathe` crossfades (matching the slow references), and
`build-up`/`punch` use a whip/zoom as deliberate stylistic accelerators on
top of that base. Motion scales with the hold - most on `breathe`, least on
`rapid-one` - the way the references do.

## Absence is honest

A template whose file is missing renders silent and is *labelled* silent -
in the template list, the plan preview, and the render. AutoCut never lays
no track and calls the result beat-synced; `beat_synced` is true only when a
real grid was read from a real track.
