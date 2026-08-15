# Agent Handover

Last updated: 2026-08-09

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises hot-reload services, reuses healthy backend/frontend processes instead of launching duplicates, and waits for new services to become healthy before starting dependents. Reused services require consecutive failed probes before shutdown. Browser mode opens `http://127.0.0.1:3001/` automatically only after readiness; `start-electron.bat` delegates to `start.cmd --desktop`; normal startup also applies pending database migrations.
- The pinned `jiji262/douyin-downloader` 2.0.0 provider is integrated as `media.douyin-downloader` and installed locally at revision `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- Local development uses a loopback-only `local-admin@trendrelay.local` AAL2 bypass by default, automatically creates one Local Workspace owner membership, and marks the UI with a Local admin badge. It is unavailable to LAN clients and outside the development environment; set `LOCAL_AUTH_BYPASS=false` to exercise real authentication locally.
- The global toolbar exposes job updates through an outlined notification bell rather than a Jobs text button. Each `job_id:status` transition is an unread notification, displayed in a wrapping vertical list with per-row and mark-all-read controls. Read keys are bounded and stored locally per user; reading never deletes durable job history.
- All nine primary tabs now share a compact sticky page-context treatment beneath the global toolbar. Existing workspace selectors remain visible where the page header owns them; Library keeps only Creative intelligence, Media Library, its purpose, and Workspace sticky, while processing/transcription status remains ordinary scrolling content. Desktop sticky side panels are offset below the persistent context, and the shared header adapts below the two-row mobile navigation.
- The media console now owns Douyin authentication setup: **Connect Douyin** starts a loopback-only, owner-confirmed connection process, installs isolated Playwright/Chromium login support if missing, opens a visible Douyin window, polls for the required session cookies without terminal input, stores them only in ignored `.data/douyin/cookies.json`, and refreshes readiness. Chromium is never used for media downloading.
- The root `/` screen is a focused batch Douyin downloader. It extracts and deduplicates supported links from pasted share text, previews detected video/profile/collection sources, ignores unsupported URLs, exposes profile mode and 10/20/50/100 per-source limits progressively, submits one explicit durable `douyin_download` action without a redundant confirmation dialog, and filters the live queue by active/completed/attention state. Its primary download action is disabled only during submission; incomplete input or provider setup produces a specific inline correction and missing input returns focus to the link field. The label sits outside the focused textarea shell, Home opens directly into the link composer and live queue without a redundant workflow strip, and history uses five-row progressive disclosure while active work remains expanded. Completed batches link to their folder, Library/Studio preparation, campaign planning, and publishing. `npm run douyin --` remains the full CLI. Downloads, SQLite state, ephemeral configuration, upstream source, dependencies, and credentials remain ignored.
- Fresh Windows clones use a four-stage `start.cmd` setup with actual Node/Python version checks, reproducible `npm ci`, and `scripts/bootstrap.py` for visible, retried, time-bounded API dependency installation. The lockfile and Windows CI are pinned to npm 11.16 so its stricter manifest/lock consistency checks match current clean machines. A version-pinned npm `allowScripts` policy covers the six reviewed native build/media dependencies, while `scripts/check_node_dependencies.mjs` detects and repairs incomplete JavaScript installs instead of trusting the presence of `node_modules`. A manifest/Python fingerprint skips unchanged work, interrupted setup is safely resumable, `--check` is download-free, and CI verifies the same dependency probe.
- Douyin Library ingestion reads provider sidecar metadata to retain the channel name, source caption, publication time, and item-specific video URL. Duplicate refreshes backfill missing provenance without overwriting reviewed values; the detail view links the creator name to the originating Douyin profile when the batch retained that profile URL, shows the original video as an accessible Douyin icon action, and opens the selected asset's containing folder through the guarded local API.
- Next.js serves `apps/web/app/icon.svg` as the product favicon: a green rounded square with a white rising four-node relay path, designed to remain distinct at browser-tab sizes.
- Social publishing is hosted-API only. `services/api/src/trendrelay_api/integrations/publishing.py` implements four interchangeable engines behind one adapter: Bundle.social (`x-api-key` + team ID, uploads the local MP4), Zernio (`Bearer sk_…` at `https://zernio.com/api/v1`, presigned upload then `POST /posts`), Buffer (GraphQL `createPost` at `https://api.buffer.com`, which requires an already-public media URL), and WoopSocial (`Bearer` at `https://api.woopsocial.com/v1`, multipart `POST /media` then `POST /posts`). `PUBLISHING_PROVIDER` selects the active engine. No AGPL publishing service, PostgreSQL, Redis, or Temporal is installed or supervised any more.
- Whether an engine is handed the local file is two capabilities, not one. `requires_public_media` says it cannot take an upload at all (Buffer); `ingests_media_url` says it can fetch a URL instead of being given the file. WoopSocial can do neither but the upload, which matters because one post spans several engines: a public URL supplied so Buffer can work must not leave WoopSocial with nothing to send. Its single-request upload is capped at 100 MB and refused here, by name and size, rather than at the engine.
- WoopSocial reports delivery per destination - status, external URL and error for each account - which no other engine here does. Its `WOOPTEST` sandbox platform is deliberately unmapped so it cannot appear as a destination, and the platform on each post body is read back from the account because their `LINKEDIN` and `LINKEDIN_PAGES` are both `linkedin` to us and the body rejects the wrong one.
- Engine API keys are entered on `/publish` and written back to the project `.env` by `env_store.py` through a loopback-only, owner/approver, explicitly confirmed endpoint. Only fixed allow-listed keys are writable, cached settings refresh immediately, and stored values are never returned — the API exposes configured booleans and the variable name only.
- `config/tool-catalog.json`, the `npm run tools --` CLI, the loopback-only lifecycle API, and `/tools` catalogue all seven managed capability projects. The Tools page is also the provider setup hub: Douyin owns automatic cookie capture, Meta Ads uses a confirmed fixed-command authentication launcher, Last 30 Days exposes only configured optional secret names, Agent Reach provides local diagnostics, and no-auth tools link to their operational surface. Compact, collapsed Meta Marketing API and Amazon Creators API access guides link to official credential setup pages; Opportunities links directly to the Amazon guide.
- The pinned Last 30 Days 3.16.0 source is installed and active locally. `npm run research --`, the research API, and `/research` execute its stable agent JSON 1.x contract and persist workspace-scoped evidence.
- The pinned OpenMontage source is installed and active locally. `/studio` and `npm run studio --` expose clip-factory/podcast-repurpose preflights plus approved, zero-network local VideoTrimmer jobs with immutable-source checks, manual clip ranges, budget enforcement, verified outputs, and provenance.
- Windows exposes three deliberate root entry points: `start.cmd` for browser development, `start-electron.bat` for desktop development, and `update.cmd` for a guarded fast-forward-only pull. The updater refuses dirty worktrees, detached HEAD state, and branches without an upstream. Provider/tool operations use npm scripts rather than extra `.cmd` files.
- The pinned Agent Reach 1.5.0 source is installed and active locally. `npm run reach --` and the `/tools` Diagnose action expose 15-channel local-presence diagnostics without upstream execution, network probes, user-config reads, browser-session access, or secret-value exposure.
- The pinned Meta Ads Kit source is installed and active at `0879bb4566a836670f33beb509ff7d8d4779849e`; its isolated `@vishalgojha/social-flow` 0.2.17 runtime provides the `social` command. The Python adapter exposes only read-only account/campaign/ad/fatigue reports, and `/research` presents it as first-party validation alongside Last30Days execution and Agent Reach diagnostics.
- The pinned Meta Ads Collector 1.4.0 source is installed and active at `0ffb2fb1af94eae6542b328ab3ae31fc1c9a5897`. `/research` embeds its no-key public Ad Library search as competitive creative intelligence with bounded normalized results, a fixed isolated bridge, and no cookies, proxy controls, media downloads, webhooks, or mutations.
- Last 30 Days is adapter-ready. OpenMontage preflight and deterministic local clipping are adapter-ready; paid/networked generation remains intentionally blocked.
- MediaCrawler is a catalogued source-checkout provider, installable and activatable from `/tools` and off by default. Its upstream README asks against large-scale crawling, so its adapter keeps collection bounded and operator-initiated rather than scheduled.
- TrendRelay core still uses SQLite locally and plans PostgreSQL/pgvector plus S3 for shared deployment. No embedded database, queue, or scheduler ships with the app.
- `Research/` and `References/` are local-only and ignored by Git.
- SQLAlchemy 2 models and Alembic migrations through `20260726_0010` provide user profiles, workspaces, four roles, expiring invitations, device pairings, secret references, and transactional audit events. SQLite is the easy local default; PostgreSQL is the shared/production target.
- The API verifies Supabase asymmetric JWTs through JWKS plus distinct TrendRelay device JWTs; both require issuer, audience, expiry, and subject claims. `/sign-in` implements password sign-in/sign-up, verification redirects, magic links, Google OAuth, password recovery, and global sign-out.
- `/workspaces` lists and creates workspaces, displays members and audit events, and gives owners controls for membership, expiring email-bound invite links, optional encrypted SMTP delivery, secret references, and TOTP account security. Enrolled AAL1 browser sessions are globally challenged before authenticated screens render; deployments can require AAL2 for governed actions.
- `/publish` picks an engine, configures its keys, discovers connected accounts, produces dry-run previews, and submits governed work. Engine cards and per-platform destination cards carry inline SVG marks from `apps/web/app/publishing-icons.tsx` so operators can tell Zernio, Buffer, and Bundle.social — and each destination — apart at a glance. Publishing joins Last30Days research and OpenMontage preflights in TrendRelay's leased SQL job store and supervised hot-reload worker.
- TikTok Discovery reads TikTok Creative Center's public trend tabs. Creative Center is client-rendered and its `creative_radar_api` answers `40101 no permission` to unsigned callers, so plain HTTP cannot read it: `scripts/tiktok_creative_bridge.py` renders the page in an isolated Playwright runtime and `integrations/tiktok_creative.py` normalizes the result. Two extractors run per render - rows carrying `data-index`, then a generic repeating-sibling detector that names no CSS class - with a rendered-text scanner as a third fallback. Metrics arrive in three layouts (`303.7K` + `Posts`, `Video views` + `79M`, and the combined `787.7K followers`) and all three are parsed. Anonymous visitors get three hashtag rows and four video cards before a login wall, which is reported as a note rather than hidden. The song and creator tabs are retired upstream and are declared unavailable rather than silently redirected. Nothing is ever fabricated: an empty render raises and the API answers 503.
- `/campaigns` provides persistent workspace campaigns and a timezone-aware content calendar. Plans bind approved media to captions, hashtags, affiliate links, disclosure, platform deep links, and posting times; owner/approver decisions lock content. Approved plans can hand off to the active publishing engine or produce an idempotent, audited manual ZIP under `.data/manual-packages/`.
- Opportunity scoring - workspace-scoped products and affiliate offers, idempotent CSV import, persisted evidence, deterministic score `v1` with nine visible contributions, research-job provenance, and draft campaign creation linked back to the opportunity and primary offer - is still in force, but no longer has a page of its own. The scoring sits on Discover under the research it scores, the products and offers sit in Attribution, and `/opportunities` redirects (see the Attribution bullet below).
- `/library` is the persistent creative-intelligence layer: durable SHA-256-deduplicated ingestion, hash-addressed immutable originals, FFmpeg thumbnail/proxy/audio derivatives, authenticated content, reviewed transcript/OCR records, versioned creative recipes, rights review, search, and publishable Studio/Campaign handoffs. Contextual counted facets filter by media type, channel, source, and usage rights; each facet ignores only its own selection, full filtered totals are calculated before pagination, and results can be grouped with exact counts without duplicating immutable assets. Douyin and authenticated OpenMontage outputs queue into it automatically.
- `/attribution` is the first-party revenue workbench: governed transparent links, HTTPS and parameter-collision controls, country routing, privacy-minimized click events, idempotent conversion CSV imports, campaign/creative-format summaries, and explicit measurement limitations. Public `/c/{code}/info` reveals the destination before `/c/{code}` records and redirects.
- Shopee Product Offer import now has two complete entry paths in Attribution: a real `.xlsx` file exported by Shopee's **Hoa hồng Sản phẩm / Product Offer** page, and a session-backed direct read capped at the same 100 products (five pages of 20). The workbook reader accepts shared or inline strings, preserves sparse cells and long IDs, rejects more than 100 data rows, and the API integration test proves one workbook creates 100 products and 100 tracking links. TrendRelay's English export headings now round-trip through its own importer. Cookie capture and offer reads share `.data/shopee/browser-profile`; the offer window is visible because Shopee sent fresh/headless browsers to `/verify/traffic/error`, and it waits up to two minutes for an operator to complete `/verify/` before continuing. On 2026-08-15 the stored cookie passed the local session probe, but Shopee presented a CAPTCHA to the newly created profile; the post-verification five-page live pull still requires one operator-completed check to prove against the real account.

- Douyin batches download one source at a time. `run_download_job` invokes `scripts/douyin.py batch` per URL and hands each finished source to the Media Library before starting the next, so files and ingest jobs appear while the batch is still running. Previously every URL went in one invocation and the pinned tool enumerated everything before writing a file. Three layers stop an item being fetched or ingested twice: the downloader's own de-duplicating database plus incremental flags, `_new_artifacts` recording every media path already collected (only unseen files are fingerprinted, so re-scanning after each source stays cheap), and the library's sha256 import de-dupe. A source yielding nothing new is counted, not fatal; a source that fails hard lands in `source_errors` and the batch continues. The worker heartbeats between sources to hold its lease.
- `/discover` opens on a trend rather than empty space. The last TikTok category, region and period persist in `localStorage` under `trendrelay.discover.tiktok` and are restored on the next visit, falling back to the first live category. Retired Creative Center tabs (Songs, Creators) stay in the registry so the adapter can explain itself but are filtered out of the quick links. The adapter's cache decides whether a revisit costs a fresh render.
- The Discover feed shows only what a provider returned. The former `AFFILIATE_STARTERS` cards carried invented conversion rates and commissions under an "Affiliate Signals" source and were shown whenever a provider returned nothing; they are gone and the empty state names the real sources instead.

- Attribution is the product-and-revenue surface. Catalog and Opportunities are
  retired into it: `/catalog` redirects to its Books tab, `/opportunities`
  redirects to Discover carrying `trend`, `source`, `title`, `url` and `job`
  through. Its four tabs are Products (one row per product, expanding to its
  offers, links, clicks and commission), Links, Books (ad economics, which only
  mean anything one level above a product) and Imports (conversion CSV and the
  offer CSV that creates products in the first place). Attribution is now a
  top-level nav item between Library and Publish; the Publish and Discover
  section strips render nothing, having one destination each. Opportunity
  scoring lives on Discover, under the research it scores.
- Publishing addresses several accounts on several engines in one post.
  Destinations are chosen per account rather than per network, so two TikTok
  accounts on two engines are two destinations; `PublishRequest` rejects a
  repeated account, not a repeated network. Every capability question - caption
  and title limits, threading, first comments, approval holds, whether media
  must be public - is asked of the engine delivering that destination, never of
  one active engine. Engines carry an explicit "Use for publishing" switch and
  one of six states (ready, off, no key, key refused, unreachable, no channels),
  each with the engine's own message and a next step. The account load decides
  whether an engine works; the credential probe is only consulted before any
  load has run.
- Campaigns run as standing programmes. `campaign_autopilot.py` decides link
  placement and composes captions, `campaign_scheduler.py` decides what to post
  and why, `campaign_runner.py` is the only part that creates anything
  irreversible, and the durable worker ticks every switched-on campaign once a
  minute. Three tables: `campaign_autopilot`, `campaign_destinations` (each with
  its own tracking link, which is what makes destinations comparable) and
  `campaign_queue_items` (recycling, so a posted item goes to the back rather
  than being consumed). The panel on `/campaigns` gates its switch behind a
  readiness checklist and previews the next day's posts before anything exists.

- Engine cards say what is connected and on which plan. Each engine reports its
  connected channels rather than the platforms it supports - the supported list
  was the same eight or twelve icons on every card whether an account was
  attached or none. No engine exposes which plan an account is on: there is no
  endpoint that names a tier and no field on any response that carries one, so
  `engine_limits.infer_plan` reads it off the limits they do report. Buffer's
  30-day request quota separates Free/Essentials/Team and arrives in
  `RateLimit-Policy`, not `RateLimit` - the latter is the 15-minute window,
  which is 100 on every tier and would call a Team account Free. Against the
  live key Buffer sends only the policy header, reporting 3,000: Free, measured.
  A quota matching no published figure leaves the plan unnamed rather than
  rounded, and the answer carries the same measured/counted/published mark as
  every figure beside it.
- An engine with no quota left stops offering destinations. Its accounts are
  marked unavailable rather than dropped, because a destination that vanishes
  reads as a disconnected account, and each carries how much went against the
  quota. Only a measured or counted figure can block - a published one is a
  pricing-page scrape with no usage and must never refuse a post - and only
  allowances that actually stop a post count, which is why "3 of 3 connected
  accounts" does not: that is room for more accounts, not room to post. A page
  reached by two engines routes around the exhausted one.
- Media hosting is checked rather than trusted. `media_hosting.probe` signs a
  request against the bucket, writes a small object and fetches it back through
  the public base URL with no credentials - the hop an engine makes, and the
  only one that proves public access, which is a separate switch from the API
  token. Each stage names the setting it clears; a public URL answering 200 with
  different bytes fails rather than passes. Saving refuses the two paste errors
  the form invites (the S3 endpoint as the secret, the account ID as the access
  key) and leaves anything wrong-but-plausible to the check.

- Affiliate networks are sent a sub ID, which is the only field that survives
  into their own conversion report. `attribution_subids.py` holds the contract
  per network and the slot map, which is a constant: networks report sub IDs
  positionally, so a slot that meant a placement on one link and a campaign on
  another would produce a column that cannot be grouped. Slot one always carries
  a key derived from the tracking code, because it resolves every other
  dimension from our own database and the conversion importer matches on it -
  `token_urlsafe` puts a `-` or `_` in 27% of codes and Shopee accepts letters
  and digits only, so the key is hashed rather than stored or stripped. Values
  are fixed when the link is minted, since one that changed with a campaign
  rename would split a link's history in two. A host matching no known network
  gets nothing at all: a guessed parameter name can break the sale rather than
  merely weaken tracking.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory. Its "Interface work" rules apply to every change that touches the UI: the right control for the interaction, built once in `apps/web/app/ui/`, native semantics kept, logical properties, all seven dictionaries, and layout verified at real widths with real content rather than assumed.
- Python/FastAPI is the control-plane runtime; Python also powers compute-heavy workers.
- Provider source remains isolated under `.tools/`; core modules depend only on capability contracts.
- Supabase access tokens are verified with asymmetric JWKS only. Workspace authorization is membership-and-role based; no service credential is exposed to the web or Electron renderer.
- Browser components receive an authorized-fetch capability rather than token values. OAuth started in Electron opens in the system browser; the signed one-time device authorization flow pairs the approved identity back to Electron, whose main process encrypts the distinct device token with operating-system `safeStorage`.
- TOTP MFA is optional at the account level. Missing assurance claims are AAL1; `REQUIRE_AAL2_FOR_GOVERNED_ACTIONS` can enforce AAL2 for owner controls, provider publishing, and pairing approval. Paired device tokens preserve the approving browser session's assurance.
- Secret records store approved locators only and reject raw values. Governed mutations append audit events in the same transaction.
- Invitation email is opt-in, owner-only, TLS-only, and rate-limited. The token digest commits before SMTP; raw tokens are never queued, stored, logged, or audited, and delivery failure preserves the copy-link fallback.
- Live trend research requires explicit external-action confirmation. Browser-cookie extraction is disabled and the adapter passes only allowlisted research secrets to Last 30 Days.
- OpenMontage proposals require a declared rights basis, immutable source hash, budget cap, and explicit approval. Rendering requires a second confirmed action, stays local and zero-network, uses fixed output roots, and never implies permission to publish.
- Agent Reach diagnostics are local-presence-only. The upstream installer, MCP/skill mutation, browser-cookie import, command execution, live network probes, and user-config access remain outside the trusted adapter boundary.
- Meta Ads Kit is read-only in TrendRelay. Briefings are loopback-only and confirmed; commands are constructed from fixed report templates, provider stderr is sanitized, credentials remain in the isolated CLI profile, and pause/resume/budget/create/upload/delete operations are absent. Any spend-impacting capability requires a new ADR and approval design.
- Meta Ads Collector is a separate public-research boundary. Searches are loopback-only, explicitly confirmed, capped at 50 ads, and normalized before returning to the browser. Its reverse-engineered browser-facing GraphQL transport is accepted as operationally fragile; failures stay sanitized and no provider credentials or account mutations are in scope.
- Meta setup prefers the tool-owned OAuth launcher; raw or temporary access tokens are never pasted into TrendRelay. Amazon setup is documentation-only until a reviewed Creators API adapter exists: do not accept Amazon credentials yet, do not start a Product Advertising API integration, and continue using SiteStripe links or CSV imports.
- Every managed capability repository must be pinned in the machine-readable catalog and documented in the human-readable catalog; supporting runtime repositories must be lockfile-pinned and documented.
- Tool installation and activation remain separate; source presence never implies credentials, dependencies, or production readiness.
- Tool setup reports expose readiness, dependency state, and credential names only. They never return secret values. Interactive setup actions are loopback-only, explicitly confirmed, and selected from fixed tool/action mappings rather than user-supplied commands.
- Publishing is dry-run-first. TrendRelay never accepts social-platform passwords and never returns an engine credential to the browser; social accounts are connected inside the engine's own dashboard. Drafts are the default and scheduling is opt-in.
- An engine declares the platforms it supports, and a request naming an unsupported platform is rejected before any network call. Because the engines disagree about media, a request carries both an approved local `video_path` and an optional public `media_url`: Buffer requires the URL, Zernio prefers it and otherwise uploads, and Bundle.social always uploads the reviewed local file.
- Publishing operations use content-derived IDs and freeze the resolved engine into the job payload, so switching engines never re-routes in-flight work. Workspace publishing jobs are durable but receive one provider attempt because duplicate and uncertain retries require inspection. Only owners and approvers can discover integrations, change engines, save keys, or execute; editors may preview.
- Publishing media must resolve to an existing MP4 beneath `PUBLISHING_MEDIA_ROOTS`; the local defaults are `.data/downloads`, `.data/media`, and `.data/productions`.
- Douyin batches default to 50 items per selected profile mode. Full crawls require explicit `--limit 0`. Downloads use only the pinned API provider; browser fallback has been removed. The optional login browser exists only to capture cookies. Empty provider output is a failed job, while historical zero-artifact successes are labeled `empty` in the UI.
- Cookie values come from the app-controlled Douyin connection flow, local environment variables, or `.env`; values are redacted from status/dry runs and exist in generated download configuration only for the process lifetime. Starting connection requires an owner, governed assurance, explicit confirmation, and a loopback request.
- SQLite and file-based deduplication remain enabled for repeat and incremental downloads.
- Keep downloaded content as reference media until rights and policy classification permits further use.
- Library originals are immutable. Only owned, licensed, and public-domain assets may enter campaigns; owner/approver rights changes require confirmation, evidence, governed assurance, and an audit. Automatic transcription remains visibly unavailable until a reviewed provider is incorporated.
- Attribution links are transparent, HTTPS-only, and fail closed. Existing affiliate parameters are preserved; configured campaign/platform parameters cannot collide. No raw IP, full referrer path, fingerprint, or network order reference is stored. Production requires `ATTRIBUTION_HASH_SECRET`; currency totals are never combined.

- Usage rights are retired as a product concept. Removing the controls left the classification enforcing itself with nothing able to satisfy it: every import defaulted to `unknown`, which is not publishable, so the Library detail pane hid its Studio/Campaign/Publish links and `campaigns_api` rejected every plan with a 409. The campaign gate, the `/assets/{id}/rights` endpoint and `RightsUpdate`, the publishable-rights import gate, the `rights_status` filter and `rights` facet, `rights_status`/`publishable` on the asset view, `PUBLISHABLE_RIGHTS` and the `RightsStatus` literal are all removed. The `media_assets` columns stay with their `unknown` default as inert provenance; dropping them would need a migration and would discard history for no functional gain.
- Buffer needs per-network metadata or it refuses the post. `InstagramPostMetadataInput` declares `type: PostType!` and `shouldShareToFeed: Boolean!`, Facebook declares `type: PostTypeFacebook!`, and YouTube requires a title on create. `_buffer_metadata` supplies them: Instagram and Facebook publish as Reels, matching the other two engines for short-form video, YouTube takes the title or the caption trimmed to 100 characters, and the AI-disclosure toggle reaches Instagram and TikTok through `isAiGenerated`. Enum values are bare GraphQL tokens, never quoted strings.
- Error banners must stay readable. `.registry-error` painted `#ffc1af` on `#f8d7da`, a contrast ratio of 1.16:1, which made engine failures such as a bundle.social 403 invisible. It is `#721c24` at 8.25:1, and the two blocked badges sharing that pink wash moved from 3.33:1 to 5.81:1.

- Affiliate link placement is decided by the network, not by a setting. A URL in
  an Instagram or TikTok caption is not a link - it renders as plain text - and
  a link in a first comment now costs Instagram reach and gets the comment
  hidden. So: caption where a link is clickable and unpenalised, bio for
  Instagram and TikTok with the tracking link on the profile, and a
  first-comment path that exists but is deliberately unused unattended. Every
  destination carries the reason alongside the decision.
- The disclosure leads every caption and is not configurable. It is required
  near the endorsement, no later than the link, prominent, and on every post. A
  post with an offer and no disclosure is refused at the setting and at the
  post.
- Ranking is by measured earnings per click and refuses to rank below five
  settled conversions, reporting why rather than printing a figure built on
  luck. Every fourth slot explores, because always posting to the current leader
  guarantees the others never gather the evidence that would overturn it.
- DM automation is out of scope by choice. It converts several times better than
  a bio link on Instagram and it is the fastest way to get an account restricted
  when driven from a tool.
- Autopilot never approves content, writes copy, or invents a posting schedule.
  A workspace with no slots posts nothing and says so.

## Validation

- TikTok Discovery rewrite (2026-08-04): the previous adapter fetched the page, read only its `<title>`, and returned three hard-coded rows labelled as Creative Center data; it also blocked the event loop and pointed at URLs that now redirect. Replaced with a rendered-page collector, verified live against both working tabs - hashtags returned `#spidermanbrandnewday` at 303.7K posts / 944.2M views, videos returned four creator cards with followers and view counts. Both extractors and the text fallback were checked to agree on the same render. 38 parser tests run offline against two recorded fixtures of real markup, covering metric layouts, column reordering, malformed bridge output, caching, retired categories and credential scoping. The API answers 503 for a retired tab and 422 for bad input.
- Publishing-engine polish (2026-08-04): the bundle.social payload was corrected against the published OpenAPI schema - it had been sending `socialAccountIds` (not in the schema; the API selects by `socialAccountTypes`), omitting the required top-level `title`, and using `privacyLevel`/`brandContentToggle`/lowercase `privacy` values the API does not accept, so every bundle.social publish would have failed. Reddit and Pinterest now collect the subreddit and board those engines require. Contract tests assert each corrected field.
- Publishing-engine migration (2026-08-03): `npm test` passes 177 tests, including a new provider suite covering the Bundle.social upload/post pair, Zernio presign-then-`POST /posts` for both scheduled and draft deliveries, Buffer's per-channel GraphQL mutations and surfaced `MutationError` text, unsupported-platform rejection, and a `.env` writer suite. ESLint, TypeScript, and Ruff pass. An end-to-end run through the real ASGI app saved a Zernio key, confirmed it reached a scratch `.env`, confirmed the secret was not echoed in the response, rejected an unconfirmed save (400) and an unknown engine (422), and persisted an engine switch to `PUBLISHING_PROVIDER`. Every Postiz validation entry below is superseded: that service no longer exists in the tree.
- Postiz Winget recovery tests reproduce `0x8A15004B`, verify community-source isolation, one refresh retry, and non-blocking core startup. The focused launcher/Postiz suite passes 24 tests, and the full lint, TypeScript, and 185-test Python validation passes.
- Favicon QA confirmed that Next.js discovers the SVG as image/svg+xml, exposes /icon.svg as a static route, and passes ESLint, TypeScript, and the production web build.
- The collapsed Meta and Amazon access guides were browser-tested under local admin at desktop and 390px widths. Both expose four concise steps and official provider links, Amazon's unsupported-adapter warning is visible, Opportunities links back to the Amazon guide, there is no horizontal overflow, and the browser console is clean. ESLint, TypeScript, and the production web build pass.
- Douyin console browser QA verified a separate field label and accessible shell focus ring with no textarea outline overlap, five initial history rows, expandable artifacts and handoffs, five-at-a-time history loading, no duplicate help rail, no console warnings, and no horizontal overflow at a 390 px viewport.
- Download-action QA verified that the empty form keeps **Start download** enabled, clicking it shows a specific supported-link message and focuses the textarea, and a valid `v.douyin.com` link remains ready without starting a live download. ESLint and TypeScript pass.
- Native Postiz validation completed: PostgreSQL, Redis, Temporal, the backend, orchestrator, and frontend all reported healthy.
- The app-managed Postiz local-session route set its auth cookie and landed at `/launches` without a login form. The Publish screen reported `Local service ready`, and its **Open local Postiz** action completed successfully.
- Live browser QA opened Tools without sign-in, rendered all provider setup cards, then opened embedded Postiz. Clicking unconfigured Reddit and Instagram Standalone connectors stayed on `/launches` and showed the setup warning; no external OAuth URL containing `undefined` opened.
- The one-click runner remained healthy through a real WatchFiles backend reload after Windows child services were isolated with `CREATE_NO_WINDOW`; backend and web both returned 200 afterward. Focused Postiz/API tests pass (20), along with Ruff, explicit Python compilation, ESLint, and TypeScript checks.
- The local Postiz API key validated. Integration discovery correctly returns an empty list until platform accounts are connected through their OAuth flows.

- Pinned upstream checkout resolved exactly to `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- Isolated provider installation succeeded on Python 3.14; `npm run douyin -- check` reported version 2.0.0.
- Fourteen API, wrapper, URL-security, validation, secret-lifecycle, environment-loader, and manifest tests passed.
- Ruff lint and formatting checks passed for the integration code and tests.
- End-to-end `npm run douyin -- batch --file ... --dry-run` parsed copied share text and profile URLs, deduplicated input, applied incremental bounded settings, and produced redacted configuration.
- A live media download was not initiated because no user-authorized Douyin URL was provided.
- Pinned Postiz checkout resolved exactly to `41c5a9dbd6b2776863e7c05c22e7a385c208321c`; the isolated build and `npm run postiz -- check` reported version 2.0.15.
- Postiz wrapper and governed-adapter smoke tests produced dry-run drafts with private/safe defaults and made no provider call. The authenticated publishing API enforces workspace roles, explicit confirmation, approved media roots, and one-attempt durable execution; `/publish` is included in the production browser build.
- No real social upload or post was initiated because credentials, integration IDs, an approved video, and explicit execution confirmation were not supplied.
- `start-electron.bat` repaired the missing Electron 43.1.1 Windows binary through the package-provided installer, then passed desktop-mode validation.
- Unified-runner tests cover healthy-service reuse, unavailable-service startup selection, and missing Electron detection.
- The Last 30 Days pinned checkout was verified as 3.16.0 and activated. CLI and API mock runs completed through agent JSON schema 1.2 and each ingested two workspace-scoped evidence records without external calls.
- The exact OpenMontage checkout was installed and activated. Its two guarded manifests load successfully. The isolated upstream VideoTrimmer produced and ffprobe-verified a real one-second MP4 from the pinned demo source using locked FFmpeg 6.1.1 binaries. The worker passes no provider credentials, performs no network call, records source/artifact hashes and package/upstream provenance, and reports zero provider cost.
- The consolidated `/research` inspiration radar was visually smoke-tested in the local app at desktop and 390px mobile widths. Recent topic evidence, public competitor creative, and first-party Meta signals render in one filterable card feed; an empty workspace defaults to six usable starter patterns rather than an empty state. Trend and public-ad search share one mode-switching query bar, account validation and activity are compact disclosures, and a source-health drawer links directly to tool management. Local-admin access and browser console checks passed; the shared browser API resolver follows loopback or LAN hostnames instead of hard-coding `localhost`, and development CORS accepts private-LAN frontend origins.
- The Agent Reach pinned checkout resolves to `1494c2ab239e7355a77e7cceaf3271453a1f34b5` (upstream 1.5.0). The adapter reports all 15 pinned channels, currently with 2 ready, 1 setup-required, and 12 unavailable local capabilities; no live platform calls were made.
- Meta Ads Kit resolves exactly to `0879bb4566a836670f33beb509ff7d8d4779849e`; isolated Social Flow 0.2.17 version/help checks pass locally on Node 22 despite its declared newer engine warning. Adapter/API tests prove strict account/preset validation, fixed read-only commands, winner/bleeder/fatigue synthesis, loopback confirmation, provider-error redaction, and harmonized research status. No Meta account was authenticated and no live briefing or mutation was run.
- Meta Ads Collector resolves exactly to `0ffb2fb1af94eae6542b328ab3ae31fc1c9a5897`; its isolated runtime imports successfully and a live one-result public `coffee` query completed with one request, one normalized ad, and no account/API credential. Twelve focused adapter/API tests, Ruff, TypeScript, ESLint, JSON validation, diff checks, and the full Next.js production build pass.
- Migrations `20260722_0001` through `20260726_0009` upgrade a fresh local database to head. Foundation tests cover workspace creation, roles, invitations, owner-only secret references, raw-secret rejection, slug validation, and ordered audit events.
- Browser production builds cover `/sign-in`, `/update-password`, and `/workspaces`; ESLint and TypeScript checks pass. Next.js is updated to 16.2.11, patched PostCSS/Sharp overrides are installed, and `npm audit` reports zero known vulnerabilities.
- Migration `20260722_0002` adds one-time, email-bound invitation tokens with expiry, revocation, replay protection, and transactional acceptance. Development CORS now explicitly permits the browser Authorization header.
- Migration `20260722_0003` and `/device` implement a loopback-only, ten-minute, one-time desktop authorization grant. Device JWTs have a separate token type, audience validation, and an eight-hour default lifetime; production requires `DEVICE_TOKEN_SECRET`.
- Electron keeps the device JWT encrypted with operating-system `safeStorage` in the main process. IPC sender origin, renderer navigation, API origin/path, and HTTP methods are allowlisted; the preload never exposes bearer tokens. `start-electron.bat --check` validates without launching services.
- `/account/security` implements TOTP enrollment, QR/manual-secret setup, challenge-and-verify login, unfinished-factor cleanup, AAL2-only verified-factor removal, and a global enrolled-session gate. API tests cover fail-closed claim defaults and optional governed-action enforcement; migration `20260722_0005` carries browser assurance into device pairings. A fresh SQLite database upgraded from empty to `20260722_0005`, and the production browser build includes `/account/security`. A live enrollment was not attempted because no configured Supabase test account was supplied.
- Migration `20260722_0004` adds shared durable jobs with expiring leases, heartbeats, retry budgets, scheduling, cooperative cancellation, and recovery of abandoned running work. Last30Days and OpenMontage are migrated off JSON.
- The unified runner includes a watch-reloaded durable worker for Douyin acquisition, Media Library ingestion, Last30Days research, OpenMontage rendering, and Postiz publishing. A live Last30Days mock completed from SQL with no legacy file, an OpenMontage proposal/approval completed its SQL preflight record with no legacy file, and `scripts/worker.py --once` drains recoverable work.
- Opt-in workspace invitation email uses standard SMTP with STARTTLS or implicit TLS, HTTPS-only public links outside loopback, a 20-attempt-per-workspace hourly default, metadata-only audits, and an always-available copy-link fallback. Unit and API tests prove delivery behavior and raw-token non-persistence; no real email was sent because SMTP credentials were not supplied.
- Local-admin behavior was smoke-tested in the running browser app: login was bypassed on loopback, Local Workspace was created with owner role, the Local admin badge rendered, and no browser console errors were reported. LAN/production fail-closed behavior is unit-tested.
- Notification-center QA uses the production CSS and list markup at desktop and 390px widths with deliberately unbroken URL/error strings. The notification list explicitly resets the global `ol` auto-fit columns, gap, and background to one full-width column; panel and row client/scroll widths match, the bell has a visible resting outline, and mark-all-read retains all rows while clearing the badge. Live QA in the running local-admin app confirmed 15 full-width vertically stacked rows at desktop and narrow widths with no horizontal overflow. The fixed heading stays in place while the 1,478px list scrolls independently inside its 558px viewport; scrolling moved the list to 430px without moving the page. ESLint, TypeScript, and the production web build pass.
- Automatic Douyin connection tests cover loopback owner authorization, explicit confirmation, isolated subprocess startup, automatic cookie detection without stdin, atomic local cookie/status writes, and secret-free public status. The visible login window itself was not completed against a real account during automation.
- Douyin provider 2.0.0 passed installation checks and redacted dry-run configuration. Tests cover cookie sources, missing-cookie refusal, absence of browser fallback, artifact hashing, and rejection of zero-exit runs that save no media. A live network download was not run because valid cookies and a user-authorized source URL were not supplied.
- Douyin provenance validation passes 19 focused job/ingestion tests plus Ruff and ESLint. The production web build passed with the channel and icon treatment; the final single-primary-link refinement was rechecked with the focused Python suite, Ruff, and ESLint.
- Library categorization validation passes the three focused Media Library API tests, Ruff, ESLint, TypeScript, diff checks, and the production web build. Live local-admin QA confirmed exact workspace and filtered totals above the page limit, contextual media/channel/source/rights counts, exact channel grouping, zero console warnings/errors, and no horizontal overflow at desktop or 390 px; the mobile facet stack was reduced from one column to a compact two-column layout.
- The complete project suite passes: 138 tests (65 API and 73 root integration tests).
- Media Library validation covers import confirmation and loopback enforcement, SHA-256 idempotency, workspace search, reviewed enrichment and recipe derivation, rights audits, file-hash campaign blocking, automatic Douyin/OpenMontage handoff, missing-content handling, and a real pinned-FFmpeg derivative run producing an original, thumbnail, proxy, and audio file. The API suite passes 62 tests, the root integration suite passes 73 tests, and web/desktop production builds pass. Local-admin browser QA confirms active navigation, ready local processing, the honest transcription status, no console errors, no horizontal overflow at 1280 px or 700 px, and the responsive single-column breakpoint.
- Campaign/calendar validation covers workspace isolation, archived-campaign locking, timezone-aware plans, approval content lock, explicit and loopback-only export, complete ZIP contents, audit events, and idempotent package reuse. A fresh SQLite database upgrades through `20260726_0009`; the production web build includes `/campaigns` and `/library`. Browser QA created and refreshed campaigns under isolated local-admin data with no console errors, while the API suite exercised approval and export without external publication.
- The practical root console was checked in the running Next.js app through both `localhost` and `127.0.0.1`; the sign-in state exits loading reliably, fits a 1280px viewport without horizontal overflow, and the private-LAN development-origin allowlist matches the unified runner.
- Tool catalog coverage includes complete listing, explicit confirmation, loopback-only mutation, pinned checkout, activation, and Windows-safe isolated uninstall.
- Production builds for Next.js and Electron, TypeScript checks, ESLint, Ruff, JSON validation, CLI listing, and diff checks pass. The `/tools` page exposes the catalog cards, the locally installed/active providers, guarded lifecycle controls, and Agent Reach diagnostics.
- Tool-setup API tests cover sanitized Douyin readiness, credential-name-only Last 30 Days reporting, explicit launcher confirmation, and rejection of unknown actions. The Next.js production build, TypeScript, ESLint, and Ruff checks pass for the guided setup interface.

- Opportunity validation covers required/optional CSV fields, monetary normalization, restrictions, idempotent re-import, workspace isolation, research evidence handoff, exact scoring contributions, ranked listing, affiliate URL propagation, and opportunity-linked campaign creation. Browser QA imported an offer and produced a persisted nine-factor opportunity card under local admin; its isolated demo records were removed afterward.

- Attribution validation covers confirmation and role gates, HTTPS-only destinations, country routing, parameter preservation and collision rejection, visitor-query isolation, privacy HMACs, workspace isolation, click matching, idempotent approval/reversal imports, unavailable-offer failure, and multi-currency summaries. A fresh SQLite database upgrades through `20260726_0010`; the web/desktop build includes `/attribution`; local API and route health checks return 200. The in-app browser could not revisit the loopback page because its URL policy blocked the action, so no new visual claim is made for this slice.

- Sticky page-context validation: ESLint passes and the Next.js production compiler completes. The production build then stops on the pre-existing Media Library `Uint8Array<ArrayBufferLike>[]`/`BlobPart[]` TypeScript error at `apps/web/app/library/page.tsx:134`; this sticky-header change does not touch that helper. The in-app browser could not access loopback (`ERR_BLOCKED_BY_CLIENT`), so no new visual-runtime claim is recorded.

- `update.cmd --no-pause` stopped before mutation against the current dirty workspace, then passed local-only end-to-end validation against a temporary tracked remote in both already-current and one-commit-behind states. The latter fast-forwarded exactly one commit through `git pull --ff-only --prune`; the temporary repositories were removed afterward.

- Publishing-engine session (2026-08-05): `npm run check` passes 231 tests with ESLint, TypeScript and Ruff. New suites cover the three engines' payloads, the `.env` writer, the Douyin streaming batch (interleaving, no-double-ingest, keep-going-on-failure) and Buffer's per-network metadata. The streaming tests were confirmed to fail when the batch is collapsed back into a single invocation, so they detect the regression rather than merely passing.
- Live checks this session: a Zernio key saved through the API reached a scratch `.env` without the secret appearing in the response, an unconfirmed save returned 400 and an unknown engine 422, and an engine switch persisted to `PUBLISHING_PROVIDER`. The Library assets endpoint returned 694 assets with no `publishable` or `rights_status` key and facets of channels/platforms/media_kinds only, and selecting an asset rendered all three handoff links. A Library import that previously failed `422 literal_error` now reaches business logic. TikTok Creative Center parsed live hashtag and video rows with posts and views intact. Twenty live Ad Library cards measured 0.00px between the Research and Source centres, identical bar geometry on every card, and no overflow.
- Two environment traps cost time and are worth knowing. Next dev HMR appends updated CSS after existing rules, so cascade results are wrong until a hard reload; a padding fix appeared broken twice before reloading proved it correct. And `document.hasFocus()` is false in a hidden browser pane, so `:focus` styling cannot be verified there at all.
- Engine/quota/hosting session (2026-08-09): the API suite passes 624 tests with TypeScript, ESLint, the production web build and 959/959 strings translated. 34 failures in `test_face_blur`, `test_face_swap`, `test_face_identity` and `test_effect_render` pre-date the session and are `insightface`/`onnxruntime` missing from the venv, not regressions. New suites cover plan inference per engine, the 30-day-window rule that stops a Team account reading as Free, exhaustion (including the two things that must never block a publish), route selection around a spent engine, and the storage access check.
- Live checks this session: Buffer returned `RateLimit-Policy` with a 3,000-request 30-day quota and no `RateLimit` header at all, so the plan reads Free by measurement; the R2 access check reached stage 2 and reported the refused key, which turned out to be the account ID saved as the access key ID. Forcing an engine into an exhausted state marked its three pages unavailable with the count attached while Zernio's TikTok stayed available, then the force was reverted.
- **UI changes this session were not verified by eye, and that is a real gap.** The browser automation could not get any page past its loading state: chunks fetch 200, HMR connects, no console errors, and zero API calls follow - the visible text is server-rendered, so React never hydrated. That is consistent with Chrome starving an occluded window of scheduler work, and it is not something waiting or reloading resolves. The web app has no automated tests of its own (`npm test` runs pytest), so `tsc`, ESLint and `next build` are the only gates a UI change passes. Anything shipped today that touches rendering deserves a look in a real, focused browser window.
- Two tests were reading the developer's own `.env` and passed or failed depending on who ran them: both assert Buffer refuses a local file with no public URL, which stops being true the moment R2 is configured. `test_buffer_requires_a_public_media_url` and the autopilot preview test now pin `media_hosting.status` instead.
- The backend reloader had stopped working: edits to the API, and a touch of `main.py`, left a worker from hours earlier still serving, so a change had to be chased by killing the process. A fresh process with identical arguments reloads correctly, which puts the fault in the watch being lost rather than never set up. `scripts/dev.py` now runs the backend with `WATCHFILES_FORCE_POLLING=1`; that is a mitigation, not a root cause.
- Restarting the backend repeatedly trips `dev.py`'s restart guard (5 in 60s) and takes the whole runner down with it, orphaning the frontend. Worth knowing before reaching for a restart to work around something else.

## Session close, 2026-08-10

Since this file is no longer committed, everything durable from this session was
written into `README.md` and `docs/architecture/` instead - ADR 0011 for the
fourth engine, the two media capabilities and the reversal on returning
credential values, ADR 0015 for the sub-ID rules. What follows is only state.

Done: sub IDs for affiliate networks, WoopSocial as a fourth engine, its
validate pre-check and 100 MB guard, the two-capability media rule, a Pinterest
board picker, masked credentials with a gated reveal across all three
credential surfaces, and the reloader mitigation.

`.venv/Scripts/python.exe -m pytest services/api/tests tests` passes 857.
Ruff, TypeScript, ESLint, the production web build and 974/974 translations are
clean. Note the interpreter: the system Python has no `insightface`, and running
the suite with it fails 34 face and effect tests that pass in the venv. `npm
test` uses the right one.

Two things are waiting on the operator rather than on code, and both are in
"Next recommended action" below: the R2 credentials, and looking at any of this
session's interface work in a real browser. Nothing shipped today has been seen
by eye. The automation tab never completes React hydration - chunks fetch 200,
HMR connects, no API call follows - and a clean restart of both dev servers
reproduced it exactly, which rules the servers out and leaves the browser
environment as the cause.

The dev runner currently running was started from an agent session and will stop
when that session does. Start `npm run dev` yourself before relying on it.

## Next recommended action

Fix the R2 credentials, then take the campaign autopilot through one real
end-to-end run. The order matters now: the credentials block the run.

**The blocker.** The saved R2 settings are in the wrong fields.
`R2_ACCESS_KEY_ID` holds the account ID - byte-identical to `R2_ACCOUNT_ID` -
and `R2_SECRET_ACCESS_KEY` holds `https://405e37fc…`, the S3 API endpoint URL.
Everything on the R2 bucket page is a 32-character hex string or a URL and the
fields do not say which is which, so this is the mistake the form invites. The
access check on `/publish` reports it in one line; saving now refuses both
shapes outright. The replacements come from R2 → API → Manage API tokens, with
Object Read & Write on the bucket. Until this is fixed, media hosting cannot
work, and Buffer - which has no upload endpoint and fetches the file when the
post goes out - cannot publish a local clip at all.

Then the run itself, each step being where a real problem would surface:

1. **Set posting slots** on `/publish`. Autopilot refuses to invent a schedule,
   so with none it posts nothing and says exactly that.
2. **Check the engines.** Bundle.social still answers HTTP 403. Buffer returns
   three channels (Facebook `Naceto Books`, Instagram and Threads
   `halcyonbooks.official`) on a measured Free plan. Zernio's key works and now
   has one TikTok channel, `Tiêu Dùng Thông Minh 24h`. WoopSocial is built but
   has never been given a key, so nothing about it has met the live API - and
   it is the one engine that takes an upload and so does not need R2 at all,
   which makes it the shortest path to a first real post while the storage
   credentials are still wrong.
3. **Add a destination** on `/campaigns`, activate the campaign, queue a clip
   from the Library and approve it.
4. **Preview** before switching on. It creates nothing and mints no tracking
   code, and it is the first place a composed caption is seen whole.
5. **Switch on with delivery = draft** and confirm a draft appears in the
   engine's own dashboard. Nothing reaches an audience at this setting.

What to watch for, since none of it has met a live engine:

- `campaign_runner` calls `create_publish_job`, which builds a preview and can
  raise on validation. A destination that an engine refuses is reported in
  `last_note` and skipped rather than stopping the run - correct behaviour that
  has never actually been seen happen.
- A queue item's title now reaches the engines, so Reddit and Pinterest have the
  field they require, but no post has been sent to either.
- The disclosure and the link are composed per network at post time. The first
  live post is the first time that composition meets a real caption limit.
- Nothing has ever been out of quota, so the destinations that grey out when an
  engine is spent have only been seen by forcing the condition, never by
  reaching it.

Also outstanding, unrelated:

- Three truncated MP4s from 2026-08-05 (`moov atom not found`, 2.5-5.8 MB, still
  on disk) sit permanently in the Library's "Needs attention". Deleting media is
  destructive, so they have been left for the operator to decide on.
- `C:` is at roughly 4.2 GB free, down from 4.9 GB, and is the best available
  explanation for the instability described under Validation.
- Arabic layout on Library and Publish deserves a look from someone who reads it.
