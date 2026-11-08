# SOP: keeping media storage in check

Follow it when the project drive runs low, before adding any new file the media pipeline writes, and before deleting anything under
`.data`. Everything it describes lives under `.data/media`, `.data/productions` and `.data/downloads`. Measurements are from
2026-11-08 and will drift; the commands below re-measure.

## Why this exists

The project drive reached 5.8 GB free of 1 TB while `.data` held about 577 GB. Git was never the cause (`.git` is 0.06 GB, so
pruning branches frees nothing). The space went to the same video bytes stored more than once and to derived files nobody read:

| Cause | Size | What was done |
| --- | --- | --- |
| Downloaded videos copied into the library, so each existed twice or three times | about 159 GB by file size | Hard-linked together; imports now link instead of copy |
| A 16 kHz speech WAV stored per video, 77 of about 10,000 assets ever transcribed | 17.6 GB | Removed; built on request instead |
| Browser proxies that were no smaller than the original they stood in for | 12.2 GB | Removed; kept only where they save space or fix playback |
| Edited and blurred cuts encoded at constant quality with no ceiling, about 7 times the size of their sources | 176 GB | Cap applied to new renders; existing cuts kept (see below) |

Free space went from 5.8 GB to 165.3 GB.

## Read the numbers correctly

Hard links make one stored file appear under several paths. Explorer, `du` and any script that adds up file sizes per path count it
once per path, so a deduplicated `.data` still looks the same size. To see what is really used, count each file once by its
(device, inode) identity, or read the drive's free space. The free space on the drive is the number that matters.

Paths under `.data/downloads/douyin` can pass 260 characters. On Windows, open them with the `\\?\` prefix. PowerShell's
`Get-FileHash` fails on them without it, and a failed hash that is grouped with other failures looks like a huge pile of
duplicates. Hash in Python with the prefix (`scripts/dedupe_media.py` does).

## Routine check

1. Look at free space on the project drive. `.data` is the only place worth looking; `.git` and `node_modules` are small.
2. Report what a dedupe would reclaim: `python scripts/dedupe_media.py`. It changes nothing.
3. If it reports duplicates, `python scripts/dedupe_media.py --apply`. It links each extra copy to the library copy through a
   temporary name and swaps it in, so a failure leaves the original in place. It skips files changed in the last ten minutes and
   pairs on different volumes, and it is safe to run while the app is up. A second run should report zero.
4. Pending data migrations apply with `python scripts/db.py upgrade`; `python scripts/db.py current` shows the head. Database
   writes ship as Alembic migrations, never as direct edits.

New duplicates after this should be rare, because imports link. If the tool keeps finding some, something is copying again.

## What is kept, and why

Do not delete these to save space.

- **Original source clips, including the long ones.** Of 233 videos over ten minutes (87 GB), 221 feed edits, queue items or
  posts, and edited cuts re-render from them. Only 0.9 GB of long clips is unused.
- **Every existing edited and blurred cut.** 1,043 (69.3 GB) are referenced by a publication. The other 1,247 (107.5 GB) have no
  publication record, but that is not the same as unused: queue items point at an asset, and the publish path picks the blurred or
  edited version by kind at the moment it publishes. A cut with no publication record may be the one a scheduled post uses next.
  The decision on 2026-11-08 was to keep all of them.
- **HEVC proxies.** The H.264 proxy is what lets a browser play an HEVC original. It stays however little it saves.
- **Original plus edited together.** Effects keep both the original and the rendered version on purpose. Do not collapse them.

## Rules for the pipeline

These keep the problem from coming back.

1. **Importing a file from inside `.data` links it, it does not copy it.** `media_library._link_or_copy` hard-links a source under
   the project data folder and copies anything else, so a user's own file never shares storage with a hash-addressed original. If
   the volume refuses a link it falls back to a copy.
2. **Nothing may rewrite a linked file in place.** Two paths can share one stored file. Writers must create a new or temporary
   name and swap it in (`replace`, `move`), which rebinds one name and leaves the other alone. A truncating write to an existing
   path would change every path linked to it. A new writer that opens an existing library or download file for writing is a bug.
3. **Do not store what can be rebuilt cheaply.** The speech track is extracted into scratch by `media_library.extract_audio` when
   something asks for the `audio` version, and removed after the response. Do not write derived files back under `.data/media`.
4. **A proxy is kept only if it earns its space.** `media_library.proxy_is_redundant` drops a proxy that is less than 30% smaller
   than an original the browser already plays (H.264 with AAC or MP3 or no audio). A request for a missing `proxy` serves the
   original. The migration `20261108_0107` applies the same rule to stored rows; change both together.
5. **Every cut is encoded under a ceiling.** The two final encodes that produce a cut, the ffmpeg effect chain in `effects.py` and
   `_remux_audio` in `face_blur.py` (shared by blur, overlay, identity and swap), pass
   `max_bitrate=bitrate_ceiling(source)` to `encode_h264`. The ceiling is 1.15 times the source video bitrate, 1.6 times for HEVC
   sources, with a 1.5 Mbps floor. Quality 20 stays the target. A source that cannot be measured is left uncapped. libx264 and
   NVENC hold to the ceiling; QSV, AMF and VideoToolbox take the flags and may ignore them. A new encode that produces a stored
   version should pass a ceiling too.
6. **Say what a new derived file costs before adding it.** Ask whether anything reads it, whether it can be rebuilt on request, and
   how much it will be across the whole library. The speech tracks and proxies were each reasonable per file and 30 GB in total.

## Before deleting anything under `.data`

1. Check what points at it. Look at every table that names the asset or version, and any ID stored inside a JSON column; a check
   that only scans columns with "asset" in the name misses the latter.
2. Prefer a hard link or a regenerable derived file over deleting a source.
3. Data changes go in an Alembic migration scoped as narrowly as possible: a named file, inside the library folder, tolerant of a
   file already gone and of a database without the tables. Look at the migrations `20261108_0106` and `20261108_0107` for the
   shape.
4. Confirm with the operator first. Deleting is a commit, never a quiet change to the worktree.

## Open items

- **Existing cuts are not re-encoded.** The ceiling applies to new renders only. Had it applied to the existing 2,274 cuts, they
  would be at most about 33 GB instead of 176 GB (an estimate from database sizes). Reclaiming that means re-rendering files that
  queue items may still publish and rewriting their stored hashes and sizes. It was declined; reopen it only with a check of which
  unreferenced cuts a pending or scheduled queue item still needs.
- **Unused downloads.** 2,537 Douyin videos (16.1 GB) and 1,931 uploaded images (5 GB) are not referenced by any column whose
  name contains "asset". Do not treat that as safe to delete until IDs inside JSON payloads are checked.
- **Not capped.** AutoCut and subtitle renders still use the old settings. They produce about 0.7 GB.
- **A discarded proxy is still encoded first.** The importer encodes the proxy and then discards it when redundant, because the
  saving is only known afterward.
- **The worker does not hot-reload.** Importer and render changes take effect after the operator restarts it.

## Where the code is

| What | Where |
| --- | --- |
| Link or copy on import, speech extraction, proxy rule | `services/api/src/trendrelay_api/media_library.py` |
| `audio` and `proxy` content fallbacks | `services/api/src/trendrelay_api/media_library_api.py` |
| Bitrate ceiling and encoder arguments | `services/api/src/trendrelay_api/video_encoding.py` |
| Dedupe tool | `scripts/dedupe_media.py` |
| Migrations that cleared old speech tracks and proxies | `services/api/migrations/versions/20261108_0106_*`, `20261108_0107_*` |
| Tests | `test_media_dedupe.py`, `test_speech_track_retention.py`, `test_redundant_proxies.py`, `test_bitrate_ceiling.py` |
