# Agent Handover

Last updated: 2026-07-22

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises hot-reload services, reuses healthy backend/frontend processes instead of launching duplicates, and waits for new services to become healthy before starting dependents. `start-electron.bat` delegates to `start.cmd --desktop`; normal startup also applies pending database migrations.
- The pinned `jiji262/douyin-downloader` 2.0.0 provider is integrated as `media.douyin-downloader` and installed locally at revision `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- `npm run douyin --` installs, verifies, and runs single-link or file-based Douyin batches. Downloads, SQLite state, ephemeral configuration, upstream source, dependencies, and credentials remain ignored.
- The pinned `gitroomhq/postiz-agent` 2.0.15 provider is integrated as `social.postiz-agent` at revision `41c5a9dbd6b2776863e7c05c22e7a385c208321c`.
- `npm run postiz --` installs and verifies the provider, performs OAuth/API-key authentication and integration discovery, and previews or executes short-video drafts/schedules for TikTok, Instagram, and YouTube.
- `config/tool-catalog.json`, the `npm run tools --` CLI, the loopback-only lifecycle API, and `/tools` About & Tools page catalogue all six incorporated GitHub projects with pinned revisions, license posture, install state, and activation state.
- The pinned Last 30 Days 3.16.0 source is installed and active locally. `npm run research --`, the research API, and `/research` execute its stable agent JSON 1.x contract and persist workspace-scoped evidence.
- The pinned OpenMontage source is installed and active locally. `npm run studio --` exposes non-executable clip-factory and podcast-repurpose preflights with immutable-source fingerprints, rights records, budget caps, and approval gates.
- Windows exposes exactly two root launchers: `start.cmd` for browser development and `start-electron.bat` for desktop development. Provider/tool operations use npm scripts rather than extra `.cmd` files.
- The pinned Agent Reach 1.5.0 source is installed and active locally. `npm run reach --` and the `/tools` Diagnose action expose 15-channel local-presence diagnostics without upstream execution, network probes, user-config reads, browser-session access, or secret-value exposure.
- Last 30 Days is adapter-ready, while OpenMontage is preflight-ready with runtime execution intentionally blocked.
- MediaCrawler is documented but installation and activation are blocked because its license prohibits commercial use.
- PostgreSQL/pgvector, Redis, and S3-compatible storage remain planned data services when product features require them.
- `Research/` and `References/` are local-only and ignored by Git.
- SQLAlchemy 2 models and Alembic migrations through `20260722_0004` provide user profiles, workspaces, four roles, expiring invitations, device pairings, secret references, and transactional audit events. SQLite is the easy local default; PostgreSQL is the shared/production target.
- The API verifies Supabase asymmetric JWTs through JWKS plus distinct TrendRelay device JWTs; both require issuer, audience, expiry, and subject claims. `/sign-in` implements password sign-in/sign-up, verification redirects, magic links, Google OAuth, password recovery, and global sign-out.
- `/workspaces` lists and creates workspaces, displays members and audit events, and gives owners controls for membership, expiring email-bound invite links, and secret references. Automated transactional-email delivery and optional 2FA remain pending.
- Temporal setup and the publishing UI do not exist yet. A supervised hot-reload SQL worker now provides local durable retry and expired-lease recovery. Last30Days research and OpenMontage preflight proposals now use the leased SQL job store exclusively; no research or production JSON adapter remains.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory.
- Python/FastAPI is the control-plane runtime; Python also powers compute-heavy workers.
- Provider source remains isolated under `.tools/`; core modules depend only on capability contracts.
- Supabase access tokens are verified with asymmetric JWKS only. Workspace authorization is membership-and-role based; no service credential is exposed to the web or Electron renderer.
- Browser components receive an authorized-fetch capability rather than token values. OAuth started in Electron is opened in the system browser; pairing the resulting identity back to Electron remains intentionally disabled until a signed device flow exists.
- Secret records store approved locators only and reject raw values. Governed mutations append audit events in the same transaction.
- Live trend research requires explicit external-action confirmation. Browser-cookie extraction is disabled and the adapter passes only allowlisted research secrets to Last 30 Days.
- OpenMontage proposals require a declared rights basis, immutable source hash, budget cap, and explicit approval. Approval never implies permission to spend, render, or publish.
- Agent Reach diagnostics are local-presence-only. The upstream installer, MCP/skill mutation, browser-cookie import, command execution, live network probes, and user-config access remain outside the trusted adapter boundary.
- Every incorporated GitHub repository must be pinned and recorded in both the machine-readable and human-readable third-party catalogs.
- Tool installation and activation remain separate; source presence never implies credentials, dependencies, or production readiness.
- Postiz is dry-run-first. Uploads and remote drafts/schedules require both `--execute` and `--confirm-external-action`; drafts are the default.
- Postiz operations use content-derived IDs and a local ledger. Duplicate and uncertain retries are blocked pending inspection.
- Douyin batches default to 50 items per selected profile mode. Full crawls require explicit `--limit 0`.
- Cookie values come only from local environment variables or `.env`, are redacted from dry runs, and exist in generated configuration only for the process lifetime.
- SQLite and file-based deduplication remain enabled for repeat and incremental downloads.
- Keep downloaded content as reference media until rights and policy classification permits further use.

## Validation completed

- Pinned upstream checkout resolved exactly to `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- Isolated provider installation succeeded on Python 3.14; `npm run douyin -- check` reported version 2.0.0.
- Fourteen API, wrapper, URL-security, validation, secret-lifecycle, environment-loader, and manifest tests passed.
- Ruff lint and formatting checks passed for the integration code and tests.
- End-to-end `npm run douyin -- batch --file ... --dry-run` parsed copied share text and profile URLs, deduplicated input, applied incremental bounded settings, and produced redacted configuration.
- A live media download was not initiated because no user-authorized Douyin URL was provided.
- Pinned Postiz checkout resolved exactly to `41c5a9dbd6b2776863e7c05c22e7a385c208321c`; the isolated build and `npm run postiz -- check` reported version 2.0.15.
- A three-platform wrapper smoke test produced a draft preview with private/safe defaults and made no provider call.
- No real social upload or post was initiated because credentials, integration IDs, an approved video, and explicit execution confirmation were not supplied.
- `start-electron.bat` repaired the missing Electron 43.1.1 Windows binary through the package-provided installer, then passed desktop-mode validation.
- Unified-runner tests cover healthy-service reuse, unavailable-service startup selection, and missing Electron detection.
- The Last 30 Days pinned checkout was verified as 3.16.0 and activated. CLI and API mock runs completed through agent JSON schema 1.2 and each ingested two workspace-scoped evidence records without external calls.
- The exact OpenMontage checkout was installed and activated. Its two guarded short-form manifests loaded successfully; an owned empty-file smoke fixture completed proposal and approval with a SHA-256 fingerprint and $1 cap while execution remained disabled. No provider call, paid action, or render occurred.
- The `/research` Trend Radar page was visually smoke-tested in the local app; the shared browser API resolver follows loopback or LAN hostnames instead of hard-coding `localhost`, and development CORS accepts private-LAN frontend origins.
- The Agent Reach pinned checkout resolves to `1494c2ab239e7355a77e7cceaf3271453a1f34b5` (upstream 1.5.0). The adapter reports all 15 pinned channels, currently with 3 ready, 1 setup-required, and 11 unavailable local capabilities; no live platform calls were made.
- Migrations `20260722_0001` through `20260722_0004` upgrade a fresh local database to head. Foundation tests cover workspace creation, roles, invitations, owner-only secret references, raw-secret rejection, slug validation, and ordered audit events.
- Browser production builds cover `/sign-in`, `/update-password`, and `/workspaces`; ESLint and TypeScript checks pass. Next.js is updated to 16.2.11, patched PostCSS/Sharp overrides are installed, and `npm audit` reports zero known vulnerabilities.
- Migration `20260722_0002` adds one-time, email-bound invitation tokens with expiry, revocation, replay protection, and transactional acceptance. Development CORS now explicitly permits the browser Authorization header.
- Migration `20260722_0003` and `/device` implement a loopback-only, ten-minute, one-time desktop authorization grant. Device JWTs have a separate token type, audience validation, and an eight-hour default lifetime; production requires `DEVICE_TOKEN_SECRET`.
- Electron keeps the device JWT encrypted with operating-system `safeStorage` in the main process. IPC sender origin, renderer navigation, API origin/path, and HTTP methods are allowlisted; the preload never exposes bearer tokens. `start-electron.bat --check` validates without launching services.
- Migration `20260722_0004` adds shared durable jobs with expiring leases, heartbeats, retry budgets, scheduling, cooperative cancellation, and recovery of abandoned running work. Last30Days and OpenMontage are migrated off JSON.
- The unified runner includes a watch-reloaded durable worker. A live Last30Days mock completed from SQL with no legacy file, an OpenMontage proposal/approval completed its SQL preflight record with no legacy file, and `scripts/worker.py --once` drained zero remaining jobs.
- The complete project suite passes: 69 tests. Tool catalog coverage includes complete listing, explicit confirmation, loopback-only mutation, MediaCrawler license blocking, pinned checkout, activation, and Windows-safe isolated uninstall.
- Production builds for Next.js and Electron, TypeScript checks, ESLint, Ruff, JSON validation, CLI listing, and diff checks pass. The `/tools` page exposes six catalog cards, five locally installed/active providers, guarded lifecycle controls, Agent Reach diagnostics, and the MediaCrawler license block.

## Next recommended action

Add an approved transactional-email delivery adapter for workspace invitations, then evaluate Temporal for multi-host production orchestration. OpenMontage runtime execution still needs dependency isolation, provider authorization, cost reconciliation, output provenance, and AGPL review. Keep MediaCrawler blocked unless written commercial permission is obtained.
