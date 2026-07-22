# TrendRelay

TrendRelay is an affiliate trend-to-content orchestrator. It connects trend discovery, demand validation, affiliate matching, creative research, content production, approval, publishing, and revenue attribution in one workflow.

## Project goal

Turn fragmented research and publishing tools into a repeatable, evidence-backed loop:

`Discover → validate demand → match offers → analyze content → create variations → approve → publish → measure revenue → learn`

The first usable release prioritizes reliable research, media handling, publishing, and attribution before costly AI video generation.

## Technology stack

| Layer | Technology |
| --- | --- |
| Web | Next.js, React, TypeScript |
| Desktop | Electron and electron-vite with a hardened renderer boundary |
| Control plane | Python, FastAPI, Uvicorn, Pydantic |
| Authentication | Supabase Auth browser PKCE/TOTP MFA flows, API-side asymmetric JWKS verification, and paired device JWTs |
| Shared contracts | TypeScript schemas and Pydantic models |
| Data | SQLAlchemy and Alembic; SQLite locally, PostgreSQL with pgvector for shared/production use |
| Cache and coordination | No local dependency; Redis is a production candidate only when shared coordination is proven |
| Object storage | Ignored local media today; S3-compatible storage is the shared/production target |
| Durable workflows | Leased SQL job queue and supervised Python worker; Temporal deferred until multi-host workflow evidence exists |
| Media | FFmpeg, ffprobe, and isolated Python workers |
| Media download provider | Pinned `jiji262/douyin-downloader` integration |
| Social publishing provider | Pinned `gitroomhq/postiz-agent` integration |
| Trend research provider | Pinned `mvanhorn/last30days-skill` 3.16.0 adapter |
| Video production | Pinned `calesthio/OpenMontage` preflights plus isolated local VideoTrimmer execution |
| Research channel diagnostics | Pinned `Panniantong/Agent-Reach` registry with side-effect-free local checks |
| Tool governance | FastAPI lifecycle API, pinned JSON catalog, and Next.js About & Tools page |
| AI workers | Python provider adapters; ComfyUI connectors remain planned |
| Local development | npm workspaces and a Python virtual environment |
| Production direction | Managed services and Terraform; orchestration only when justified |

## Repository structure

- `apps/web` — compact operations console for media acquisition, Trend Radar, Studio, publishing, workspaces, and About & Tools
- `apps/desktop` — Electron shell for local media and browser-assisted workflows
- `scripts/dev.py` — unified hot-reload supervisor for local development
- `scripts/reach.py` — sanitized Agent Reach channel diagnostics
- `config/tool-catalog.json` — machine-readable registry of every incorporated GitHub project
- `start.cmd` — one-click browser-app launcher with dependency bootstrap and hot reload
- `start-electron.bat` — one-click Electron launcher with backend and frontend hot reload
- `services/api` — Python/FastAPI control plane (modular monolith), SQLAlchemy models, and Alembic migrations
- `services/link-router` — first-party attribution redirect boundary
- `workers` — isolated trend, media, AI, and publishing runtimes
- `packages` — shared UI, schemas, TypeScript SDK, and workflow definitions
- `plugins` — replaceable provider implementations
- `infra` — infrastructure guidance and future Terraform modules
- `docs` — architecture decisions and capability contracts

The core depends on capabilities, never platform-specific business logic. See [SOP.md](./SOP.md) for engineering rules and [AGENT_HANDOVER.md](./AGENT_HANDOVER.md) for current state.

## Quick start

Prerequisites: Node.js 22+ with npm 10+, and Python 3.12+.

### Windows — easiest

Double-click `start.cmd` for the browser app or `start-electron.bat` for the Electron app. These are the only Windows launcher files. Both validate prerequisites, create `.venv`, install dependencies, apply database migrations, and hand off to the unified runner. The runner supervises the API, web app, and leased SQL worker with hot reload. Desktop startup also repairs a missing Electron runtime automatically. Healthy backend or frontend processes are reused, preventing duplicate Next.js server failures. Logs stay in one terminal with prefixes, servers bind to the LAN, and code changes hot reload automatically.

### Command line

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "services/api[dev]"
npm install
.\.venv\Scripts\python.exe scripts\dev.py
# Add --desktop to also launch Electron.
```

Initialize or update the local database with `npm run db -- upgrade`. API documentation is available at `http://localhost:8080/docs` during development. Open `http://localhost:3000/` for the media-to-publish operations console. Paste Douyin source links there, monitor durable downloads, and hand resulting files directly to Studio or Postiz publishing. Trend Radar remains at `/research`, governed local production at `/studio`, and provider management at `/tools`.

The first product vertical slice now includes database-backed Supabase authentication, workspace and role management, append-only audit events, secret references, and the governed plugin registry.

## Browser authentication

Configure the same Supabase project on both sides of the application:

```dotenv
SUPABASE_URL=https://PROJECT.supabase.co
AUTH_AUDIENCE=authenticated
NEXT_PUBLIC_SUPABASE_URL=https://PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

Then open `/sign-in`. The browser supports email/password sign-in and account creation, verification redirects, magic links, Google OAuth, password recovery, and global sign-out. Authenticated workspace screens are at `/workspaces`. Renderer components receive an authorized API helper rather than raw tokens; the Python API still verifies every request independently. Google OAuth launched from Electron is handed to the system browser.

Authenticated browser users manage optional TOTP authenticator factors at `/account/security`. Enrollment keeps the QR secret in browser memory, verifies a six-digit challenge, and upgrades the session to AAL2. The global session gate redirects an enrolled AAL1 session to its factor challenge after password, magic-link, or OAuth authentication. Set `REQUIRE_AAL2_FOR_GOVERNED_ACTIONS=true` to make FastAPI require the verified `aal2` JWT claim for owner controls, Postiz execution/integration discovery, and desktop pairing approval. Paired device JWTs preserve the assurance level of their browser approval; older or claim-less tokens safely remain AAL1 and must be paired again after enabling AAL2 enforcement.

The device-authorization API, `/device` approval screen, and Electron broker complete secure desktop pairing. Pairings start and exchange only from loopback, expire after ten minutes, store only a device-code digest, and issue a distinct eight-hour app JWT after explicit one-time approval. Electron encrypts that token with operating-system `safeStorage` in its main process and exposes only status, pairing, sign-out, and fixed-origin authorized-request capabilities to the isolated renderer. Local development generates its signing secret under ignored `.data/`; production must set `DEVICE_TOKEN_SECRET`. Start or validate the desktop path with `start-electron.bat` or `start-electron.bat --check`.

## Authenticated workspace foundation

Run `npm run db -- upgrade` to apply the current migration. Local development defaults to ignored `.data/trendrelay.db`; set `DATABASE_URL` to PostgreSQL for shared or production environments. Configure `SUPABASE_URL` and `AUTH_AUDIENCE` in local `.env` to enable bearer-token verification through Supabase's asymmetric JWKS endpoint.

Authenticated endpoints under `/api/workspaces` create and list workspaces, manage owner/editor/approver/analyst membership, issue and revoke expiring email-bound invitations, register secret references, and read the workspace audit trail. Invitation tokens are returned once, stored only as SHA-256 digests, and accepted only by a signed-in account with the invited email. The UI always generates a copyable link and can optionally send it through configured SMTP. Email delivery supports encrypted STARTTLS or implicit TLS only, commits the token digest before sending, records metadata-only attempt/outcome telemetry, and is rate-limited per workspace. A provider failure leaves the one-time link available; raw tokens are never queued, audited, or stored. Configure `PUBLIC_WEB_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_FROM_EMAIL`, and optional `SMTP_USERNAME`/`SMTP_PASSWORD` in local `.env`. `PUBLIC_WEB_URL` must use HTTPS except for loopback development, and `SMTP_SECURITY` must be `starttls` or `ssl`. Secret-reference requests accept only approved secret-store locators; raw credentials are never accepted. Mutations and their audit records commit in one database transaction.

## Durable execution

Migration `20260722_0004` adds a shared database job queue with expiring worker leases, heartbeats, bounded retries, scheduling, cancellation intent, and structured payload/result storage. PostgreSQL supports competing workers through row locking; SQLite is the single-worker local default. Douyin acquisition, Last30Days research, OpenMontage preflights and local renders, and Postiz publishing operations use this durable queue. The research and production adapters write no legacy JSON state. The supervised durable worker polls recoverable jobs, retries eligible research/render failures within their bounds, processes publishing operations once, and reclaims expired leases after process restarts; `python scripts/worker.py --once` provides a deterministic operational drain.

## Managed open-source tools

Every incorporated GitHub repository is documented in [the third-party catalog](./docs/third-party/README.md). Managed capability providers are pinned in `config/tool-catalog.json`, while supporting runtime packages are pinned in `package-lock.json`. The About & Tools page shows each managed provider's repository, revision, license posture, capabilities, installation state, and activation state. Source and runtime state stay ignored under `.tools/` and `.data/`.

Use the UI at `http://localhost:3000/tools`, or the local CLI:

```powershell
npm run tools -- list
npm run tools -- install last30days-skill --confirm-external-action
npm run tools -- activate last30days-skill
npm run tools -- deactivate last30days-skill
npm run tools -- uninstall last30days-skill --confirm-external-action
```

Installation fetches an exact reviewed revision; activation separately makes it eligible for orchestration. Source-ready tools still require a TrendRelay capability adapter and their documented upstream dependency/credential setup before production use. Lifecycle mutations are loopback-only. MediaCrawler remains visible but cannot be installed or activated because its current license prohibits commercial use.

## Trend Radar research

TrendRelay integrates the MIT-licensed `mvanhorn/last30days-skill` at its exact pinned revision. The adapter uses the stable agent JSON 1.x contract, disables browser-cookie extraction, passes only allowlisted research credentials, and persists normalized workspace evidence and execution state in the SQL durable-job store.

Install and activate the provider from About & Tools or the CLI:

```powershell
npm run tools -- install last30days-skill --confirm-external-action
npm run tools -- activate last30days-skill
npm run research -- check
```

Run confirmed live research, optionally selecting sources and depth:

```powershell
npm run research -- run "portable espresso makers" --mode quick --confirm-external-action
npm run research -- run "AI video tools" --source reddit --source youtube --mode deep --confirm-external-action
npm run research -- list
```

Free upstream sources require no key. Optional research keys are documented in `.env.example`; unrelated provider credentials are never passed to the research process. Use `--mock` for deterministic local verification without network calls. The `/research` page provides the same flow and displays source status plus ingested evidence.
## Douyin batch downloader

TrendRelay integrates the MIT-licensed `jiji262/douyin-downloader` provider at a pinned revision. Its source, dependencies, cookies, database, and downloaded media stay outside Git under `.tools/` and `.data/`.

Install and verify the provider:

```powershell
npm run douyin -- install
npm run douyin -- check
```

Download a single video or profile:

```powershell
npm run douyin -- batch "https://www.douyin.com/video/VIDEO_ID"
npm run douyin -- batch "https://www.douyin.com/user/SEC_UID" --mode post --limit 50
```

Run a newline-delimited batch with deduplication and incremental updates:

```powershell
npm run douyin -- batch --file .\douyin-urls.txt --mode post --mode mix --limit 50 --incremental
```

The main operations console provides the simpler authenticated path: choose a workspace, paste one or more Douyin video/profile/share links, set a bounded item count, and monitor the durable media queue. Completed artifacts include size and SHA-256 provenance plus direct **Prepare** and **Publish** handoffs that prefill Studio or Postiz. The supervised worker resumes queued downloads after restarts. TikTok acquisition is visibly disabled until a reviewed provider is incorporated; Postiz can still publish approved media to connected TikTok accounts.

The CLI default limit is 50 items per selected profile mode; use `--limit 0` only for an intentional full crawl. The GUI is deliberately bounded to 100. Use `--dry-run` to inspect redacted configuration without downloading. For browser fallback, run `npm run douyin -- install --browser`, then add `--browser-fallback` to the batch command.

Optional authenticated access reads `DOUYIN_*` variables from the environment or local `.env`; values are never printed and the generated runtime configuration is deleted after each run. Downloads are written to `.data/downloads/douyin/`. Only download and reuse content when permitted by platform terms and applicable rights.

## OpenMontage local production

TrendRelay uses the pinned AGPL-3.0 OpenMontage source for governed short-form planning and deterministic local clipping. Open `/studio` to create an immutable-source preflight, record owned/licensed/public-domain rights, set a budget cap, approve the plan as an owner or approver, and submit manual clip ranges. Rendering invokes the upstream `VideoTrimmer` in a scrubbed subprocess with no provider credentials or network requirement.

The locked `ffmpeg-static@5.3.0` and `@derhuerst/ffprobe-static@5.3.0` packages provide FFmpeg/ffprobe 6.1.1. Clips are re-encoded for keyframe-safe boundaries, probed for a valid video stream and duration, hashed, and written only beneath `.data/productions/openmontage/`. Durable render records preserve the source hash, exact OpenMontage revision, package versions, artifact hashes, and zero actual provider cost. Rendering does not publish; `/publish` remains a separate governed Postiz action.

Use the browser Studio or the CLI:

```powershell
npm run tools -- install openmontage --confirm-external-action
npm run tools -- activate openmontage
npm run studio -- runtime
npm run studio -- propose "Three launch clips" --source .\source.mp4 --rights owned --pipeline clip-factory --platform tiktok --clips 3 --budget 1 --confirm-external-action
npm run studio -- approve PRODUCTION_ID --approved-by WORKSPACE_OWNER --confirm-external-action
npm run studio -- render PRODUCTION_ID --workspace local --segment "Hook:0:15" --segment "Proof:30:50" --confirm-external-action
```

The upstream base module auto-loads its own `.env`; TrendRelay deliberately replaces that base only inside the isolated trimmer process so secrets cannot cross this boundary. Paid/networked providers, automatic generation, and arbitrary upstream workflows remain disabled. Distribution must satisfy OpenMontage AGPL-3.0 and the packaged media binaries' GPL-3.0-or-later obligations; see [OpenMontage](./docs/third-party/openmontage.md) and [FFmpeg static runtime](./docs/third-party/ffmpeg-static.md).

## Agent Reach channel diagnostics

TrendRelay incorporates the MIT-licensed Agent Reach 1.5.0 source at revision `1494c2ab239e7355a77e7cceaf3271453a1f34b5`. Its 15-channel registry covers GitHub, X, YouTube, Reddit, Facebook, Instagram, Bilibili, Xiaohongshu, LinkedIn, Xiaoyuzhou, V2EX, Xueqiu, RSS, Exa, and general web reading.

Install and activate the pinned source, then inspect local readiness:

```powershell
npm run tools -- install agent-reach --confirm-external-action
npm run tools -- activate agent-reach
npm run reach -- check
npm run reach -- channels
```

The About & Tools page also provides a Diagnose action. Diagnostics only inspect local file, package, executable, and configured secret-name presence. They do not execute discovered commands, probe the network, read Agent Reach user configuration, inspect browser sessions, or expose secret values. A ready result is a local prerequisite signal, not proof that a live or authenticated platform request will succeed. The upstream system installer, MCP/skill mutations, and cookie import remain disabled.
## Social publishing with Postiz

TrendRelay integrates the AGPL-3.0-licensed `gitroomhq/postiz-agent` at an exact revision. The isolated provider supports connected social accounts; the TrendRelay adapter exposes audited MP4 drafts and schedules for TikTok, Instagram, and YouTube. Authenticated users work at `/publish`: editors can create dry-run previews, while owners and approvers can discover integrations and explicitly submit remote operations. Submitted work is workspace-scoped in the SQL durable queue and can continue in the supervised worker after an API restart.

Install, authenticate, and discover integration IDs:

```powershell
npm run postiz -- install
npm run postiz -- check
npm run postiz -- auth-login
npm run postiz -- auth-status
npm run postiz -- integrations
```

OAuth device login is preferred. As an alternative, set `POSTIZ_API_KEY` and optionally `POSTIZ_API_URL` in local `.env` for a self-hosted Postiz instance.

Preview a multi-platform short-video draft without network calls:

```powershell
npm run postiz -- short-video --video .\.data\media\approved-clip.mp4 --caption "Launch caption" --date "2026-07-20T10:00:00+07:00" --target tiktok=TIKTOK_ID --target instagram=INSTAGRAM_ID --target youtube=YOUTUBE_ID
```

To create the remote draft, append `--execute --confirm-external-action`. To schedule it, also append `--schedule` and use a future timezone-aware date. TikTok defaults to `SELF_ONLY` upload mode with duet, stitch, and comments off; YouTube defaults to private. Override these settings only intentionally with the documented command flags.

The API accepts existing MP4 files only from `PUBLISHING_MEDIA_ROOTS`, which defaults to `.data/downloads`, `.data/media`, and `.data/productions`. Every execution hashes its media and payload into an operation ID recorded at `.data/postiz/operations.json`. Durable publishing jobs have a one-attempt retry budget because a provider timeout can leave an uncertain remote outcome. Duplicate and uncertain retries are refused; inspect Postiz before resolving an uncertain operation. Only publish approved media for which you hold the necessary rights.

## Local-only material

`Research/` and `References/` are intentionally ignored. They may contain working notes or licensed/source material and are not part of the distributable repository.

## Documentation rule

This README is a living entry point. Any change to the project goal, stack, structure, setup, major commands, or release scope must update this file in the same atomic commit.
