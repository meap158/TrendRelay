# Agent handover

Written for the next agent working in this repository. It is not a summary of
the code - the code and the ADRs say what they do. It records the working
agreements the operator has set, the traps that have already cost time, the
state of the running system, and what is genuinely unfinished.

Read the "Live system" section first. Some of it is posting to real accounts.

## Fix: Caption language dropdown resets modal scroll and vanishes (2026-09-14)

In the Captions modal (`CaptionEditor`), the language dropdown was broken: opening it
caused the dialog to jump to the top (scroll reset), and on preview errors the dropdown
disappeared entirely. Two root causes:
1. CSS `overflow: visible` on `.ui-dialog:has(.search-select-popover)` destroyed the scroll
   container of `.ui-dialog-body`, snapping `scrollTop` to 0.
2. `setPreview(null)` on error nulled `sourceLanguage`, emptying `availableTranslations`
   and unmounting the `<Select>` component.

Fixed by: extending the overflow-containment CSS to `.ui-dialog-wide`, adding
`data-contain-select-popovers` to the caption editor root, preserving the last good
preview on errors, mirroring `knownLanguage` into `detectedLanguage` state for render-safe
access, normalizing language-code matching, and keeping the `<Select>` always mounted.

## Attribution products table & toolbar layout optimization (2026-09-14)

In the Attribution products view (`ProductTable`), the toolbar and table rows were redesigned to eliminate unpredictable wrapping and awkward element placements:
- Structured the toolbar into three distinct, stable tiers (`.product-toolbar` grid with 8px gap):
  1. Search tier: search input with leading search icon, quick-clear button, and `.product-listing-filter` tabs.
  2. Filter tier: Campaign, File, Creator, Sub ID, Date filters, and "Clear filters" action.
  3. Bulk action tier: subdivided into selection pills, action buttons, campaign tag group with vertical divider, and clear selection button.
- Moved the disclosure chevron (`.product-disclosure`) to the leading edge before the product thumbnail (`.product-thumb`), rotating smoothly in place on expansion.
- Wrapped offer counts and publish link counts in rounded pill badges (`.product-count-badge`).
- Framed the table scroll container with a subtle border and radius (`.product-table-scroll`).
- Fixed duplicate `.product-campaign-tags` rules, retaining green pill badges using design tokens.

## Batch transcribe mode explanations on hover (2026-09-14)

In the Media Library "Transcribe" batch dialog (`BatchTranscribe`), each of the three reading modes
("Transcribe speech", "Read on-screen text", "Recognise what it shows") now features a rich hover
tooltip (`Tooltip side="bottom"`), a subtle info icon (`Info`) on the right of each card that
highlights on hover, and native `title` fallbacks explaining what the mode does:
- Speech: Faster-whisper audio soundtrack speech-to-text into timestamped drafts.
- OCR: RapidOCR visual frame scanning for titles, prices, stickers, and burned-in text.
- Vision: Local CLIP AI recognition across video frames for subjects, products, scenes, and actions.

## Optional supplementary first comments via MCP for organic destinations (2026-09-14)

When campaign destination link placement is set to "No affiliate link" (`link_placement: "none"`),
external AI agents connecting via MCP previously saw `needs.first_comment = false` and
`follow_up_deliverable = false`, causing them to refuse to draft first comments.
The contract now marks first comments as optional (never blocking in `needs`), but explicitly
accepted (`accepts_first_comment: true`, `first_comment_optional: true`, `follow_up_deliverable: true`)
for supplementary information such as sizing advice, care tips, styling notes, or engagement prompts
(never affiliate URLs).

## WoopSocial TikTok publishing & privacy level mapping (2026-09-14)

Uploading/publishing to TikTok via WoopSocial previously failed with `api.woopsocial.com: HTTP 400`
because WoopSocial's live API contract requires specific TikTok fields for `DIRECT_POST`:
`postMode="DIRECT_POST"`, `privacyLevel` (`PUBLIC_TO_EVERYONE` or `SELF_ONLY`),
`allowComment=True`, `allowDuet=not carousel`, `allowStitch=not carousel`, `isYourBrand=False`,
`isBrandedContent=False`, `autoAddMusic=carousel`, and `isAiGeneratedContent=bool(made_with_ai)`.
In `services/api/src/trendrelay_api/integrations/publishing.py`, `_woopsocial_account_entry` now
centralizes these fields across both `_woopsocial_publish` and `_woopsocial_validate`.
`privacyLevel` automatically maps from `request.visibility` (`public` -> `PUBLIC_TO_EVERYONE`,
`private` -> `SELF_ONLY`). `_error_message` was enhanced to parse provider `error_message`
and detailed `validationErrors` / `errors` so field-level failures are transparent.

## On-demand music library (2026-09-13)

All four stages are built, tested and committed. Migration 0068 is applied to
the live database. The media worker runs the render job code from its own
process and does not hot-reload: **restart it** before the next AutoCut or
Storytelling render, or a job queued with a Library track will run the old
code, ignore `music_asset_id` and file the video without its credit.

### What the operator asked for and decided

- Assess editly (github.com/mifi/editly) for Storytelling. Decision: **use it
  as a reference, not a dependency.** Written up in
  `docs/third-party/editly.md` and catalogued in `config/tool-catalog.json`
  as `editly`, `evaluated-not-adapted` (its native `gl` build fails on this
  machine with MSB8036, no stable release since v0.14.2 in Dec 2022, and it
  would fork our ffmpeg render path). Storytelling already has Ken Burns via
  `autocut/renderer.py` `zoompan`, with subject-aware framing editly lacks.
- editly's README tip (rip music from a YouTube channel with youtube-dl) was
  validated and **not adopted**: the channel UCht8qITGkBvXKsR1Byln-wA is a
  third-party label (@audiolibrary_), not YouTube's library; the
  `youtube.com/audiolibrary/music` link is a 404 (the library now lives in
  YouTube Studio behind sign-in); ripping loses the licence terms, and our
  posts are commercial and go to TikTok/Facebook/Instagram.
- Build an **Audio library filled on demand while editing**, sourced from
  Openverse (`api.openverse.org/v1/audio/`). Licence rule chosen by the
  operator: **CC0 + CC BY only, credit added automatically.** BY-NC (no
  commercial use), BY-ND (cutting a track is adaptation), BY-SA (would bind
  the finished video) and the Public Domain Mark are refused.

### Stage A - data and API (done, tests green)

- Migration `20260913_0068_a_library_asset_carries_its_licence.py`:
  `media_assets.license` (SPDX id, indexed), `license_url`, `attribution`
  (the caption credit line; null when none is owed). Upgrade/downgrade
  round-trip verified on a scratch database; applied to the live database on
  2026-09-13 while the API was down.
- `integrations/openverse_music.py`: `search`, `track` (reads the licence back
  by id; raises `LicenceRefused`), `download` (bounded 40 MB, audio content
  types, `.part` then rename, named `<openverse id><suffix>`), `credit_line`
  (`Music: "Title" by Creator (CC BY 4.0)`, deliberately no links - networks
  treat caption links as spam). The licence allow-list is enforced twice: in
  the query and on every row returned.
- `media_library.create_ingest_job` takes `license`/`license_url`/
  `attribution` and carries them into the asset. On a duplicate file a licence
  is filled in, never replaced.
- `media_library_api.py`: `GET .../media/library/music/search?q=&page=`
  (membership) and `POST .../media/library/music/imports` body
  `{track_id, confirm_external_action}` (owner/editor/approver; 400 without
  confirmation; 422 refused licence; 404 unknown id; 502 Openverse down).
  The request carries only the id - title, creator and licence come from
  Openverse, so a browser cannot relabel CC BY as CC0. Files save to
  `.data/downloads/music/<workspace_id>/` (inside an approved media root).
  Asset views now include `license`, `license_url`, `attribution`.
- Tests: `tests/test_openverse_music.py` (29), `tests/test_media_library_music_api.py`
  (7; imports helpers from `test_media_library_api`). Live checks done during
  the build: Openverse returned only cc0/by 3.0/by 4.0 for the filtered query,
  a Jamendo mp3 downloaded anonymously, and `/audio/{id}/` read-back works.
  The Library held 0 audio assets at the time; AutoCut template tracks under
  `.data/autocut/audio` have no recorded licence.

### Stage C - music under narration (done)

- `autocut/renderer.py`: `RenderRequest.music_path` is a second audio input.
  With a voice in `audio_path` the two are mixed by `build_audio_graph` (pure,
  tested): both to 48 kHz stereo; voice padded and trimmed to the plan so it
  is the clock; bed at `MUSIC_BED_GAIN` 0.25, `atrim` to the plan, `afade`
  out over the last 1.5 s, `sidechaincompress` with the voice as sidechain
  (threshold 0.02, ratio 8, attack 20 ms, release 400 ms), `amix
  duration=first normalize=0` → `[aout]`. The music input is `-stream_loop -1`
  so a short track fills a long narration; `atrim` ends the graph, so the
  encode stops with the video (verified with the bundled ffmpeg 6.1.1 on
  synthetic inputs). A bed given without a voice is treated as the one track.
  `loudnorm` was left out on purpose: single-pass it is heavy and the fixed
  gain plus ducking read well; revisit if operators report level problems.
- `autocut/jobs.py`: `MusicChoice` (asset id, path, attribution) and
  `music_from_library(session, workspace_id, asset_id)` - audio only, this
  workspace only. `build_plan(music_path=)` reads the beats from the chosen
  file; `enqueue_render(music_asset=)` stores `music_asset_id` and
  `music_attribution` on the payload and `run_render_job` hands the credit to
  `create_ingest_job(attribution=)`, so the finished video owes it.
- `storytelling/jobs.py`: `enqueue_render(music_asset_id=)`; resolved at run
  time like the pictures; a track that has left the Library fails the job with
  "That music is no longer in the Library" rather than rendering silent.
  `autocreate.py` passes it through.
- APIs: `PlanRequest.music_asset_id` (AutoCut plan/preview/render) and
  `RenderBody.music_asset_id` (Storytelling render/autocreate); 422 when the id
  is not an audio asset of this workspace. `creation_drafts.py` specs carry
  `music_asset_id`; an AutoCut draft whose track has gone falls back to the
  template's file.

### Stage B - automatic credit (done)

- `media_library.attribution_for(session, workspace_id, asset_id)` is the one
  read: does this asset owe a credit line.
- `campaign_autopilot.compose(credit=)` / `compose_products(credit=)` /
  `compose_for_post(credit=)`: the credit sits after the words and links and
  before the hashtags, on every placement branch, added once (`_with_credit`
  skips a line already present in any part). `with_credit(caption, credit)`
  does the same for a caption somebody typed.
- Passed at every composer call site: `campaign_scheduler.plan_campaign`
  (`frozen.asset_id`), `campaign_runner.recompose_held`
  (`execution.asset_id`) and `publish_queue_item_now` (`frozen.asset_id`),
  and the editor preview in `campaign_autopilot_api` (`draft.asset_id`) - so
  what the editor shows is what the scheduler publishes.
- Manual publish: `publishing_api._with_media_credit` appends the credit to
  `PublishRequest.caption` for both `/preview` and `/jobs`, before the
  network limits are checked, so a credit that overruns a limit is refused
  with the reason rather than published cut off.

### Stage D - UI (done)

- `apps/web/app/library/music-picker.tsx`: `MusicPicker` (current choice with
  licence badge and credit note; behind a button, two tabs: "In the Library"
  through `useLibraryAssets` pinned to audio, and "Find more" searching
  `.../music/search` with a native `<audio preload="none">` preview per row
  and an Add button that POSTs `{track_id, confirm_external_action: true}` -
  the click is the confirmation). After an add the Library list is re-read at
  4 s and 10 s so the track appears without reopening; a duplicate with an
  asset id is chosen immediately. `loadMusicChoice` restores a draft's track
  by id. Strings under the `music` group in all seven locales.
- Wired into `autocut-dialog.tsx` (Music field; the empty row names the
  template's own track; plan/preview/render/draft carry `music_asset_id`) and
  `storytelling-dialog.tsx` (a `story-music` block under the pacing grid;
  render/autocreate/draft carry it). Styles: `.music-picker*` in
  `media-library.css` (shared), `.story-music` in `storytelling.css`.

### Still open

- The Library page itself does not yet show an asset's licence or credit;
  the picker and the asset view carry them.
- Ducking levels are constants in `renderer.py`; no operator control.
- **Worth taking from editly later** (details in `docs/third-party/editly.md`):
  more `xfade` transitions, eased whips via `xfade=transition=custom` with an
  easeOutExpo expression, `acrossfade` curves, no transition after the last
  clip, 0.5 s default transition length.

## Shopee listing counts corrected (2026-09-06)

User requested truthful notification/Attribution counts and complete, efficient
fetch batches. The notification used only 250 recent job rows to calculate a
495-product batch, falsely claiming the others were never queued. All 495 jobs
existed. `shopee-listing-e6DNi7A0` had 494 succeeded jobs and one failed job;
one of those successes stored an empty product-state shell. Thus that refresh
had 493 verified fetches, one empty response, and one failure. The catalog has
494 valid snapshots out of 495 products (the failed refresh retained an older
valid snapshot). Never conflate historical batch results with current catalog
coverage, or count a nonempty JSON wrapper as a fetched listing.

Changes: `shopee_enrichment.recent_jobs` retains all active jobs and returns
workspace-scoped full-batch summaries independent of the history cap. Frontend
`listing-batch-progress.ts` displays fetched/pending/failed/missing-data counts;
single notifications also reject empty successes. The parser rejects empty or
wrong-product state; workers retry responses without usable listing data and
preserve previous valid snapshots. Attribution summaries/full reads suppress
legacy empty listings, and its page refreshes as batch results change. Enqueue
now queues all eligible requested products by default (removed silent 100 cap),
atomically; worker rate limiting and bounded retries remain. Operational progress
no longer silently truncates to 200 jobs.

Recovery was scoped to the affected workspace and two products. Retry
`shopee-enrich-m4UAUu7URI0` succeeded for the previously failed refresh of
`product_cf153492a929447ebffdaad28ea5f7be`. Retry `shopee-enrich-8BY7-3bozsQ`
targets `product_28bcc2ed19714b3a995361c26241b1ce`; last observed running in the
existing worker after an initial sandbox network refusal. An independent public
page check outside the sandbox confirmed Shopee still returned no actual product
data at `https://shopee.vn/product/1102885844/55502053067`. Do not count that
product as fetched or claim full completion. Recheck job state before retrying.
No servers/workers were restarted. No historical jobs/listings were deleted.

Verification: 70 targeted Python tests, 3 frontend progress regressions,
TypeScript and targeted ESLint passed. Node test subprocesses were sandbox-blocked;
running `node --experimental-strip-types lib/listing-batch-progress.test.ts`
from `apps/web` runs the same tests in-process. Changes coexist with a very large
pre-existing dirty worktree; do not stage or revert unrelated edits.

## Douyin download investigation: resume here (2026-09-05)

The requested outcome is to recover the profile's available videos, not merely
make a small batch report success. Source: `https://v.douyin.com/PTWjhbbFNY4/`,
creator `塔塔Thalia`, profile sec_uid
`MS4wLjABAAAAde6Wgqq4LSxzTsQueTz3BgTXSWQ7JhhBqn3IyZIaxWE`.
Workspace: `ws_03c59534908647d892d8e0dab62780e8`.

Verified state: Library originally had three videos because failed earlier
runs retained media without finalizing imports. Sibling-run reconciliation
recovered 40 batch files: 39 by this creator plus one credited to `14cc`.
Latest profile runs `download_f02063a692691da8` and
`download_8d0d4aa2b8664fcd` have 40 artifacts. A subsequent manual 20-link batch,
`download_062a9ad83033bbce`, completed with three artifacts, bringing this
creator's Library count to 42. The profile declares 369 posts; completion is
not established. All paths and counts need revalidation before acting.

Final follow-up state (after the bookmarklet capture and retries): the original
384-link pass produced 299 artifacts with 45 source errors; an incremental
retry recovered 40, and a final retry recovered the remaining five. The last
retry has 344 cumulative artifacts and zero source errors. Provider storage now
contains 366 unique posts for this creator and the Library contains 366 creator
video assets; the profile declares 369, so three posts were not exposed by the
provider/session. All related Library-ingest jobs are terminal (no queued jobs).

Anonymous cookie capture now saves automatically, waits briefly for tokens to
settle and closes; explicit `require_login` keeps the login window open. Cookie
values are only in ignored `.data/douyin/cookies.json`; never print them.
Sign-in belongs in the left connection panel. Each batch retains **Fetch
missing**, including signed-out sessions, with provider/Library deduplication.
A short response alone no longer sets `requires_sign_in`.

The user reports variable results in signed-out Chrome. The controlled browser
showed a service error on one load and 20 profile video links followed by
`登录后查看更多作品` on another. Earlier API probes returned HTTP 403. Those are
session-specific observations, not proof that all anonymous sessions have a
fixed 40-post ceiling. Stop at explicit login/access checks. No evasion needed
or implemented. Do not repeat empty polling or restart servers/workers.

The manual fallback is **Import links** on each expanded download batch. Its
bookmarklet is `apps/web/lib/douyin-bookmarklet.ts`, shown in a shared Dialog
from `apps/web/app/dashboard.tsx`. It collects only loaded profile video anchors,
excludes hidden/footer links, canonicalizes/deduplicates and stores the capture
per profile in browser localStorage. Run it after each manual scroll to retain
virtualized links. It copies links; clipboard failure opens selectable text.
Nothing is transmitted automatically. Captures over 400 links are refused
without truncation because DownloadRequest currently accepts at most 400 URLs.
The Import links dialog now accepts the pasted result directly. It validates
direct HTTPS video URLs, canonicalizes/deduplicates them, refuses more than 400
without truncation, and queues a scoped incremental child job. The child keeps
the parent's source group, media-kind selection, and provenance, so Fetch
missing and Open Library continue to operate on the cumulative batch. The API
checks workspace membership/role and governed assurance before accepting the
import; no clipboard contents or browser cookies are transmitted automatically.
Fetch missing on a grouped batch retries the union of manually captured direct
video links through that same parent-group endpoint; a profile-only batch still
uses the original profile URL.

The first bookmarklet selected whole-page anchors and could include unrelated
footer videos. The current v2 storage key avoids reusing those captures.
Six tests execute the actual generated code in a DOM fixture and cover scope,
newlines, repeated capture, corrupted/blocked storage and clipboard rejection.
Live Chrome bookmark installation/copy QA still remains.

Another confirmed bug: mixed batches silently omitted empty/code-3 responses
from `source_errors`, explaining the unhelpful “Fetched 3” success record for
the 20-link trial. The worker now preserves each failed/empty source's detail;
the dashboard displays these batches as Needs attention with expandable
reasons. Historical job results were not rewritten. A retry can now capture
the missing evidence for the other 17 links. Do not claim all 20 downloaded.

Verification: six bookmarklet execution tests, three import-link frontend tests,
ten focused worker/API tests, TypeScript and focused ESLint pass. Worker tests now isolate their connection
status file in tmp_path. Approved pytest execution may be needed because of
Windows temp-directory permissions. A synthetic test status write was restored
to the previously verified anonymous-connected state; no cookies were changed.

Next steps: verify bookmarklet on the user's working Chrome profile and inspect
outcomes of future captured-link retries. Per-batch paste/import is implemented
at `POST /api/workspaces/{workspace_id}/media/downloads/{job_id}/import-links`;
the endpoint enforces membership/assurance, strict direct-video URL validation,
deduplication, the 400-link cap, and cumulative provenance. All 369 remains
unverified; no stealth or login-gate bypass is intended.
Preserve unrelated dirty worktree edits. The larger local log is in the ignored
root `AGENT_HANDOVER.md`.

---

## 1. Working agreements

These come from the operator directly. They are not preferences to weigh; they
are how work happens here.

**Never start, stop or restart the dev servers.** They belong to the operator.
Verify against whatever is already running - `curl` the port, drive the browser
pane - and if something is down, say so rather than fixing it by restarting.

**Never `git add -A`, and never stage a whole file you did not write alone.**
Another agent works in this repository at the same time. Staging a file whole
sweeps their in-progress work into your commit. This has happened three times:
an attribution wording change, a set of effect translations, and once in the
other direction, when their `git commit` picked up work staged in the shared
index and filed it under their message.

The reliable defence is to **stage and commit in one step**, not minutes apart.
When a file holds both your work and theirs, rebuild the blob from `HEAD` plus
only your hunk and write it into the index directly:

```bash
git show HEAD:path/to/file > /tmp/base   # then apply only your change to it
git hash-object -w --stdin < /tmp/rebuilt
git update-index --cacheinfo 100644,<blob>,path/to/file
```

Assert the line-count delta matches what you added before staging. A content
diff is not enough: an added line can be textually identical to one already
elsewhere in the file, and the check will undercount.

**Do not use bash heredocs for code containing backslashes.** `\\` and `\n` are
corrupted. This has broken a `.tsx` file badly enough to take the whole dev
server down with a parse error, and silently mangled a Python `"\n".join(...)`
into a literal newline. Use `Write` / `Edit`, or a heredoc only for text with no
escapes.

**Commit atomically, with a message that explains why.** The repository's
history is written in prose and explains reasoning, not mechanics. Match it.

**Secrets live in the git-ignored `.env`**. Never commit one, and never print
one into a message that lands in a log or on a screen.

**Confirm before outward actions.** Anything that publishes, posts, sends or
spends is the operator's decision. Say what it will do and wait.

---

## 2. Live system - read before changing anything

**The campaign is switched on and publishing to real accounts.** As of the last
session its `delivery` is `schedule`, which means posts go out rather than
landing as drafts. Two destinations: `halcyonbooks.official` (Threads, via
Buffer) and `Tiêu Dùng Thông Minh 24h` (TikTok, via Zernio). Eleven executions
delivered.

**One clip fails every time it is tried.** Buffer refuses it: *"Video width must
be no more than 1920px for Threads."* It has failed at least three times and
will keep failing until the video is resized or pulled from the queue. Nothing
in the app catches this before delivery - see the roadmap.

**One account is at its daily cap**, which is why the outlook can read zero
upcoming while the campaign is healthy.

**Migrations are applied by `start.cmd`, not by `scripts/dev.py`.** Telling the
operator to run `python scripts/dev.py` skips them. That mistake produced two
"the app is broken" reports in one session - `no such table:
publication_executions`, then `no such column: campaign_autopilot.post_language`
- both of which were just an un-upgraded database. Check `python scripts/db.py
current` against `services/api/migrations/versions/` before diagnosing anything
stranger.

---

## 3. Traps that have already cost time

**CSS specificity, twice.** `.autopilot-destinations li > div { display: grid }`
catches any new `div` wrapper added inside those rows. A shared `Button` also
carries styles that beat a plain attribute selector. When a rule does not take,
do not guess - ask the page which rule actually won:

```js
[...document.styleSheets].flatMap(s => [...s.cssRules])
  .filter(r => r.selectorText && el.matches(r.selectorText) && r.style.display)
```

**The shared `Button` deliberately omits `className`.** That is how "a button
looks the same everywhere" is enforced. Hook position and size with a `data-`
attribute instead; do not restyle it.

**i18n keys are not unique by name.** `intro` exists in several sections, and
`addAccount` exists in both the publish and campaigns blocks. A sweep that
matched on a bare key name once deleted `tools.intro` and `library.intro`.
Anchor edits inside the enclosing block, or on a key that exists nowhere else.

**`monkeypatch.delenv` records nothing for a key that was absent**, so a key a
test creates afterwards survives teardown and the next test reads it as
configured. Three unrelated suites failed only when run together because of
this. Fixtures that write environment values must restore the environment by
hand.

**A download manager on the operator's machine looks exactly like an app bug.**
Expanding "See exactly what posted" on the campaign timeline was reported as
triggering a download of the clip. It is Internet Download Manager's Chrome
extension capturing the stream: it grabs any progressive `video/*` response, and
expanding the row is simply when the player starts fetching. Everything on our
side was verified correct first - `FileResponse` sends no `content-disposition`
(no `filename` is passed, checked against the Starlette source), the type is
`video/mp4`, range requests answer `206` with exact byte counts, the file is
faststart with `moov` before `mdat`, `controlsList="nodownload"` is in both the
source and the running dev bundle, and nothing anywhere calls `a.download`.

Fixed in the app, in `campaigns/timeline-player.tsx`: the clip is read with
`fetch` and played from a blob, so there is no request in the browser's download
path for a grabber to see. The objection to this was that it pulls the whole file
before the first frame - true, but against an API on loopback that is a disk read
rather than a transfer, and the blob arrives complete so seeking still works. An
`IntersectionObserver` on the wrapper holds the fetch until the row is open,
since the content of a closed `details` has no layout box; without that a long
timeline would read every clip off disk at once.

`controlsList="nodownload"` is kept but was never sufficient. It governs the
controls Chrome draws, not what an extension does with the request underneath
them.

**The full test suite is not a reliable signal on its own** while another agent
is editing. Failures have appeared and vanished between consecutive runs,
including a syntax error in a file being saved mid-run. Re-run before believing
a red suite is yours, and check `git status` for who owns the file.

---

## 4. Architecture worth knowing

**ADR 0022 - posts carry the network's own link.** The internal `/c/` redirector
is retired. Nothing mints tracking links any more; captions carry Shopee's own
`s.shopee.vn` URL. Anything in the interface that offers to copy or disable a
tracking link is describing machinery that no longer runs, and click counts are
structurally zero.

Attribution has since been swept for exactly that: the header's Active links,
Clicks and visitor tiles, the campaign panel's link and click metrics and its
per-link clicks chart are gone, and the page no longer requests
`/attribution/links` at all. What remains is deliberate. The backend still has
`POST .../attribution/links` (`attribution_api.py:365`), the conversions CSV
import (`:515`) and the `/c/{code}` redirector itself (`:910`), none of them
reachable from the interface - links already published still resolve, and old
executions still reconcile. Do not treat those endpoints as dead code to remove;
do not wire them back into the interface either. Note that the conversions
import requires a pre-existing `TrackingLink`, so commission figures can only
move by way of an out-of-band call.

**Publishing connections.** An *engine* is capabilities (Buffer, Zernio,
Bundle.social, WoopSocial). A *connection* is one login to an engine, and there
may be many. The trick that made this cheap: a connection's id defaults to its
engine's id, so every destination, slot and execution written before connections
existed still resolves with no migration. Credentials resolve through a
`ContextVar` holding the connection in force, guarded by engine id.

**Connection ids are capped at 32 characters** to fit the `provider` columns.
SQLite would accept longer silently; Postgres would not.

**An engine that cannot be read back does not ship.** Adding an engine means
adding a metrics reader in `_register_metric_readers`, or writing the reason its
API cannot support one into `ProviderDefinition.no_metrics_reason` - exactly one
of the two, enforced by a test. Publishing without reporting is not a missing
feature, it is a campaign reading zero and looking measured. Two rules bind the
readers: a failed read returns `None` so the window stays due, and a reported
figure is stored as it stands. A captured window is never captured again, so a
guessed zero outlives whatever caused it - which is how sixty-nine executions
came to hold another post's figures. WoopSocial is the one engine with no
reader, and its own OpenAPI document is the evidence.

**The campaign timeline is one list.** Delivered and planned posts are
normalised into a flat `TimelineEntry` and grouped by day. Keep it that way -
they were two lists with two designs, and the delivered half did not even name
the engine.

**Caps and rest days are per account, and that costs a cross-campaign query.**
"Posts per account per day" and the rest window read `PublicationExecution`
across the whole workspace, not just this campaign's queue. They have to: a
destination is unique per `(campaign_id, provider, integration_id)`, so one
account can sit in several campaigns, and counting only the campaign's own posts
multiplied the operator's number by however many campaigns pointed at it. Rest
is matched on `asset_id`, the identity that survives being queued twice; an item
with only a raw path falls back to the per-campaign check rather than being
blocked on a guess. If you add another cadence rule, ask it of the account.

**Three separate levers, often confused:** the run switch (`enabled` - also the
circuit breaker the runner trips after repeated auth failures or uncertain
deliveries), `authority` (what needs a human), and `delivery` (draft, schedule
or now). Renaming the switch to "Campaign is running" was to stop it reading as
a second approval step.

---

## 5. Roadmap

Roughly in the order that makes each next piece easier.

### Catch platform limits before delivery, not after
The 1920px failure above is the shape of this. Publish already knows a great
deal about what each platform accepts; the campaign scheduler does not consult
it. The same gap means **a carousel aimed at a network that cannot take one
fails at delivery rather than being caught at selection** - `photo_carousel_
platforms` exists and is unread by the campaign path. Highest value: it stops
posts failing in the operator's account.

### Finish Publish parity
Columns exist and are unread by the composer:
- On the package: `made_with_ai`, `visibility`, `topic` (Threads),
  `youtube_category_id`.
- On the destination: `subreddit`, `board`, `board_name` (Pinterest).

Wire each through the API request, the scheduler's `ScheduledPost`, the frozen
execution and the engine request. The carousel is already done end to end and is
the pattern to copy.

### Per-row "Publish now" on the timeline
Built for the half that is safe, and only that half. A *planned* row is a
forecast - the engine holds nothing - so its Publish now creates the first and
only job, through `publish_queue_item_now` scoped to that row's
`destination_id`, and the next plan reflows the remaining posts into the freed
slot. Posts can also be **locked to one slot** (`pinned_slot` on the queue
item, the `/slots` and `/queue/{id}/slot` endpoints, MCP's `get_day_slots` /
`pin_post_slot`): a locked post is spent nowhere else, takes its slot ahead of
the rotation, and reflow moves around it. Locks work on drafts too, and each
lock reserves its slot against the next, so a batch of drafts spreads across
days one `pin_post_slot` call at a time. When a draft's id has been lost
between conversations, MCP's `list_campaign_posts` pages the whole queue
whatever the state - with media-shape and caption-search filters - so the id
is recoverable rather than a dead end.

Still deliberately not built: publish-now on a row an engine already holds
(scheduled/queued executions). That must modify the existing job rather than
create a second one, and needs the per-engine answer on updating a scheduled
post before it is safe. Double-posting to a real account is the failure the
runner's circuit breaker exists to prevent.

### Copy generation
Packages accept media without copy and carry `PLACEHOLDER_BODY`, reporting
`needs_copy`. The operator's plan is to expose generation over MCP later. Until
then the placeholder is deliberate - do not wire an AI dependency without
asking.

### Shopee offer reading
Probed against the live site and **it does not work**, for two reasons rather
than one. Headless is refused with a verification challenge; headful passes but
the product list never appears among the JSON responses - only configuration and
account endpoints do. The field mapping is therefore never reached and fixing it
would change nothing. The `.xlsx` export is the only path that works. This is
recorded in ADR 0020.

### Shopee listing reading (2026-09-06) - this one works
A different question from the offer list above, with the opposite answer:
given a product URL the export already names, its public page answers an
anonymous GET and embeds the whole product state (gallery, description,
variations with thumbnails, attributes, discount, vouchers, location).
Price/stock/sold/rating are withheld signed-out and recorded as withheld -
the export prices the product. Reader: `integrations/shopee_listing.py`;
jobs: `shopee_enrichment.py` (`shopee_enrich` kind, batch-marked for the
bell); storage: `products.listing` + `listing_fetched_at`; probe record and
details in `docs/third-party/shopee-listing.md`. The Attribution table
filters by with/without listing and renders a Shopee-like panel per row from
the product's own listing endpoint.

### Worker lanes (2026-09-06)
`process_available` ran one kind at a time in a fixed order, so any long
queue starved everything after it (99 media ingests once held 415 two-second
listing reads at "waiting"). Listing reads now run in their own thread lane
beside the pass (pooled x2, self-refilling, joined at pass end), and media
ingests moved from a serial loop onto `run_job_batch` with a refill. Leases
make cross-thread claims safe. If another queue starves the same way, a lane
is the established answer.

### Smaller, known
- The Library's own video player still offers a download; that is intentional
  there, unlike the campaign timeline's.
- `campaign_destinations.provider` and friends are `String(32)`; anything
  writing a connection id must respect that.

---

## 6. Verify like this

The operator values evidence over assertion, and has caught claims that were
not checked.

- **Prefer measuring to reading.** Layout bugs were solved by reading
  `getBoundingClientRect()` off the live page, not by studying CSS.
- **Prove a test fails without the fix.** Re-introduce the bug, watch it go
  red, restore. Two tests written this way turned out not to catch what they
  claimed until that check was run.
- **Assert your own edits landed.** A `replace` that silently matched nothing
  once produced a confident "fixed" message and no change at all.
- **Report what happened, including the wrong turns.** Three of this session's
  useful findings came from a diagnosis being wrong the first time.
