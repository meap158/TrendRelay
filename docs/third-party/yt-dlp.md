# yt-dlp integration (TikTok)

**Repository:** https://github.com/yt-dlp/yt-dlp
**Licence:** Unlicense (public domain) — commercial use allowed, no attribution required.
**Adopted for:** the TikTok provider in the Downloads tab.
**Surface:** `download` · **Runs:** network

## Why this rather than our own extractor

TikTok's playback URLs are signed, and the signing changes. Writing that
ourselves would mean owning a moving target permanently and re-learning it every
time TikTok moved it — the same treadmill the Douyin work already documents.
yt-dlp extracts TikTok videos, channels and collections when TikTok exposes the
needed channel metadata, is public domain,
and its extractor is repaired by a community within days of a break. Depending
on it is the whole point; reimplementing it would be choosing the maintenance.

It also covers roughly a thousand other sites, so the next provider after TikTok
is likely a declaration in `download_providers.py` rather than a new integration.

## What it can and cannot do here

Measured against the extractor list and by fetching real sources
(2026-09-06, yt-dlp 2026.08.19):

| Source | Extractor | Verified |
| --- | --- | --- |
| Single video | `TikTok` | Downloads, with metadata sidecar |
| Channel | `tiktok:user` | Works — requires an explicit impersonation target, see below |
| Collection | `tiktok:collection` | Extractor present |
| Liked videos | — | Not offered: private on TikTok |
| Sound / tag | `tiktok:sound`, `tiktok:tag` | **Marked broken upstream**, not offered |

The provider declares only `post` and `mix` modes for exactly this reason. A
control that always fails is worse than an absent one.

## Impersonation is not optional

This is the finding worth carrying forward.

TikTok refuses the download to a client that cannot present a browser's TLS
fingerprint. The failure is asymmetric and therefore misleading:

- Reading a link **works** without impersonation — metadata resolved 3/3 in
  testing, so a link looks perfectly recognised.
- Fetching it **fails every time**, with
  `ERROR: [TikTok] <id>: Unexpected response from webpage request`.

Someone seeing that would reasonably go and debug their link. So
`tiktok.provider_status()` reports `ready: False` when impersonation is
unavailable, and says which install fixes it.

Two ways it can be unavailable, and they look identical from the outside:

1. `curl_cffi` is absent.
2. `curl_cffi` is installed but **does not match the yt-dlp build**. It imports
   fine; `--list-impersonate-targets` then reports every target `unavailable`.
   This is what the development machine had — `curl_cffi 0.15.0` against yt-dlp
   `2026.03.17` — and it behaves exactly as if nothing were installed.

Hence the catalogue pins the pair together as `yt-dlp[default,curl-cffi]` with a
`minimum_version`, and the status probe asks yt-dlp what it can actually drive
rather than asking Python what imports.

With a matching pair, the log line reads
`[TikTok] <id>: Downloading webpage with challenge cookie` and the download
completes.

## How it is invoked

### "Unable to extract secondary user ID" was our request, not the channel

Reported as a broken channel. It was not.

A TikTok channel page fetched **without a browser fingerprint** comes back
missing the secondary user id the `tiktok:user` extractor needs. Having
`curl_cffi` installed is not enough — yt-dlp has to be *told* to use it, and
left to choose for itself it fetched the channel page as a plain HTTP client.

Intermittent, which is what disguised it: one early run succeeded, which made
the fault look like the channel rather than the request. Measured properly:

| channel | without `--impersonate` | with `--impersonate chrome` |
| --- | --- | --- |
| `@ai_videos_tiktok` | 0/3 | **4/4** |
| `@tiktok` | 0/1 | **3/3** |

So `_command_for` names the target explicitly whenever the install has one. It
is added only when a target exists, because naming one yt-dlp cannot provide is
a hard error before it fetches anything.

The Chrome scroll-and-copy **Import links** path still exists and still works,
but it is a fallback for a genuinely withheld channel, not the answer to this
error. Prescribing manual work for a fixable request was the wrong advice.

### A post can have no video at all

TikTok sometimes serves a post whose entire format list is one entry, `audio`.
yt-dlp downloads it and exits 0 — correctly, it fetched the only thing offered.

For a request that asked for video that is not success. The mp3 was then
filtered out downstream by the requested kinds, so the batch saved nothing,
reported nothing, and looked like a download that quietly did not happen.
`_video_actually_arrived` compares what landed against what was asked for and
returns a non-zero code with the reason, while the run is still in scope.

Confirmed on `@ai_videos_tiktok/video/7321826489580686594`: one format, `audio`,
no video stream, three cover thumbnails — while its sibling
`7239628786919034117` on the same channel offers `h264_540p` and downloads
normally.

`integrations/tiktok.py` builds one command per source and answers the same
`(exit code, detail)` contract the Douyin fetch answers, so the surrounding job
runner — scanning the output folder, fingerprinting, de-duplicating, handing
files to the Library, honouring a cancel between sources — is shared rather than
written a second time.

Notable flags:

- `--download-archive` gives incremental runs for free; a repeated fetch skips
  what it holds and a timed-out profile resumes where it stopped.
- `--write-info-json` leaves a metadata sidecar, so ingest reads title and
  creator without asking TikTok again.
- `--ignore-errors` keeps one bad post from ending a profile.
- `--no-update` because the version is pinned by the catalogue.
- `--keep-video` accompanies `--extract-audio`, which would otherwise discard
  the picture it was asked to keep alongside the track.

## Environment note

This machine prints an unrelated `RequestsDependencyWarning` (urllib3/chardet
mismatch) to stderr on every Python start. It is not from yt-dlp, but it lands
in the same pipe: reading stdout and stderr together turns valid JSON into a
parse error. Both `describe_source` and the version probe read **stdout only**
for that reason.
