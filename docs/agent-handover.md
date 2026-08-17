# Agent handover

Written for the next agent working in this repository. It is not a summary of
the code - the code and the ADRs say what they do. It records the working
agreements the operator has set, the traps that have already cost time, the
state of the running system, and what is genuinely unfinished.

Read the "Live system" section first. Some of it is posting to real accounts.

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

**The campaign timeline is one list.** Delivered and planned posts are
normalised into a flat `TimelineEntry` and grouped by day. Keep it that way -
they were two lists with two designs, and the delivered half did not even name
the engine.

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
Deliberately **not** built. `delivery: "now"` is now selectable up front, which
covers the simple case. A per-row button is harder than it looks: a scheduled
row means the engine already holds that post with a due time, so publishing it
then must modify the existing job rather than create a second one. Needs:
- an endpoint beside `approve` / `dismiss`, accepting only `queued` and
  `failed` (never `running` - that is the duplicate race; never `succeeded`);
- a verified answer to whether Zernio can update a scheduled post, as Buffer
  can;
- the same confirmation gate `approve` uses.

Do not ship this without the per-engine answer. Double-posting to a real
account is the failure the runner's circuit breaker exists to prevent.

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
