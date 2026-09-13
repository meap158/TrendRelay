# editly — evaluated as a reference, not adopted

**Repository:** https://github.com/mifi/editly
**Licence:** MIT — commercial use allowed.
**Evaluated for:** Storytelling and AutoCut rendering (Ken Burns, transitions, audio).
**Outcome:** kept as a design reference. Not installed, not a dependency.

## What it is

A declarative video editor in Node.js. A JSON5 spec of clips and layers is
rendered frame by frame — images and text through `canvas`/Fabric.js, transitions
through headless WebGL (`gl` plus the `gl-transitions` shader collection) — and
the raw frames are piped into ffmpeg for encoding.

## Why it is not a dependency

Measured on 2026-09-13, not assumed.

**It does not install here.** `npm install editly` fails compiling `gl`
(headless-gl), with Visual Studio 2019 Build Tools present, on Node 22.21.0:

```
error MSB8036: The Windows SDK version 10.0.19041.0 was not found.
```

`gl` is hard-pinned to one Windows SDK, and `canvas` is a second native build
(Cairo) behind it. Every machine that renders would need Build Tools, that exact
SDK and a working node-gyp. On Linux, headless-gl additionally wants a GL
context (xvfb or EGL) on the render host.

**It has stopped moving.** The last stable release is v0.14.2 (December 2022).
The only later build is `v0.15.0-rc.1` (January 2025), which never became a
release. No commits since May 2025; 80 open issues.

**It would be a second render path.** Storytelling already renders through
AutoCut's renderer (`autocut/renderer.py`) — one ffmpeg filter graph for the
motion, the transitions and the encode. Adding editly would fork that path into
a Node frame-by-frame renderer beside a Python ffmpeg one, with every fix to be
made twice and two looks drifting apart.

**It is slower by construction.** Frames are rasterised in JavaScript and sent
through a pipe; ours are computed inside ffmpeg.

## What we already do better

The Ken Burns move — the reason this was looked at — is already in place, and
ahead of editly's:

| | editly | TrendRelay renderer |
| --- | --- | --- |
| Zoom | `zoomDirection` in/out | `zoompan` with `motion.zoom` |
| Pan | left/right | `motion.pan_x` and `motion.pan_y`, any direction |
| Framing | centre crop, "stretch" resize | **subject-aware**: cover-crop around `focus_x`/`focus_y` |

A centred crop cuts the subject off a portrait photo in a landscape frame. Ours
does not, and editly has no equivalent.

## What is worth taking from it

Each of these is reachable in the renderer's own ffmpeg — the bundled
`ffmpeg-static` 6.1.1 at `node_modules/ffmpeg-static/ffmpeg.exe` — with no new
dependency.

### 1. Mixing narration with music (the largest gap)

Our renderer maps **one** audio stream straight through (`-map N:a -shortest`).
Storytelling hands it the narration, so a narrated story has no music bed at
all: it is the voice or the track, never both.

editly mixes layers with `amix` and per-layer `weights`, and normalises with
`dynaudnorm`. It does **not** duck. The industry treatment for a voice over a
bed is to duck the bed while the voice speaks, and ffmpeg has it natively:

- `sidechaincompress` — the music compressed by the narration's own level, so
  it dips under speech and recovers in the gaps, rather than a fixed volume
  that is either too loud under the voice or too quiet between lines.
- `loudnorm` — EBU R128 normalisation, the broadcast and platform standard,
  in preference to editly's `dynaudnorm`.
- `amix` with `weights` and `normalize=0`. `amix` scales every input down as
  inputs are added (`normalize` defaults to true), which quietly halves a voice
  the moment a music bed joins it. editly's audio code carries a comment linking
  that exact problem; `normalize=0` with explicit weights is the direct fix, and
  this ffmpeg supports it.

### 2. Transition vocabulary

We map four names onto `xfade`: cut, crossfade (`fade`), whip (`slideleft`),
zoom (`smoothup`). This ffmpeg's `xfade` offers **59** transitions. editly's
breadth comes from ~70 GLSL shaders, but most templates need a handful chosen
well, not a random one — editly's own default is `random`, which is not a
choice an editor makes.

Worth adding: the other three directions of the whip (`slideright`,
`slideup`, `slidedown`), `fadeblack`/`fadewhite` for section breaks,
`dissolve`, `circleopen`, `zoomin`.

### 3. Eased transitions

editly shapes transition progress with an easing curve, and its directional
slides use `easeOutExpo` — fast off the mark, soft landing — which is what makes
a whip read as a whip rather than a slow slide.

`xfade` itself is linear, but `transition=custom` takes an `expr` over the
progress `P`, so an easing can be written straight into the graph. Reserve it
for the directional moves; a crossfade is right linear.

### 4. Audio crossfades that follow the picture

When two clips that keep their own sound overlap in a transition, editly
crossfades the audio over the same window with `acrossfade`, with separate
out/in curves (`c1`, `c2`; default `tri`). A picture that dissolves over a hard
audio cut sounds like an edit mistake. Relevant only where clip audio is kept.

### 5. Smaller conventions

- The last clip takes no transition — there is nothing to transition into.
- A "dummy" transition: audio crossfades while the picture cuts. Useful for
  keeping sound continuous across a hard cut.
- Default transition length 0.5 s; Storytelling's are 0.35–0.4 s.

## The music tip in its README

editly's README suggests finding music on a YouTube channel or the YouTube audio
library, downloading it with youtube-dl, and pointing `--audio-file-path` at the
file. Its three links, checked on 2026-09-13:

| Link | Status | What it actually is |
| --- | --- | --- |
| `youtube.com/channel/UCht8qITGkBvXKsR1Byln-wA` | Live | **@audiolibrary_** — a verified third-party record label (703K subscribers) curating "no-copyright / royalty-free" music. **Not** YouTube's own library, despite the shared name. |
| `youtube.com/audiolibrary/music?nv=1` | **404** | Dead. YouTube's library now lives in YouTube Studio, behind a Google sign-in. |
| `github.com/ytdl-org/youtube-dl` | Live | Last release 2021.12.17. We already use its maintained fork, yt-dlp. |

Not adopted as a sourcing method, for reasons that matter more here than in
editly's own setting:

- **The two "libraries" are different things.** Tracks from a third-party label
  carry that label's and each artist's terms, usually with attribution. The
  similar name is exactly how the two get confused.
- **"Free for YouTube videos" is not "free for this app's posts".** TrendRelay
  publishes affiliate content to TikTok, Facebook and Instagram. That is
  commercial use, on platforms a YouTube licence was not written for.
- **The official library cannot be reached on demand.** It sits behind a Google
  sign-in in Studio, so an editing flow has no way to fetch from it.
- **Ripping from a YouTube upload** takes the file without its terms. The file
  then says nothing about where it came from or what it may be used for.

What an on-demand music source needs instead is a licence that travels *with*
the file. Openverse (`api.openverse.org/v1/audio/`) is one such source: its
`license_type=commercial` filter returned 240 results for "upbeat", each with
its licence (CC0, CC BY 4.0), origin, duration and a ready-made attribution
string — the attribution a CC BY track then owes in the post's caption.

## Not worth taking

- Text layers (`title`, `news-title`, `slide-in-text`) rendered through Fabric.js
  on a canvas. Captions here are already drawn by the renderer's own karaoke
  path; a second text system would be the fork this document argues against.
- `random` transitions.
- The JSON5 spec format — our plans are typed Python (`CutPlan`), and a second
  schema for the same thing is two sources of truth.
