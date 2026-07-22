# Agent Handover

Last updated: 2026-07-22

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises hot-reload services, reuses healthy backend/frontend processes instead of launching duplicates, and waits for new services to become healthy before starting dependents. `start-electron.bat` delegates to `start.cmd --desktop`; normal startup also applies pending database migrations.
- The pinned `jiji262/douyin-downloader` 2.0.0 provider is integrated as `media.douyin-downloader` and installed locally at revision `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- The root `/` screen is a compact media-to-publish console. It submits authenticated, role-gated Douyin links to durable `douyin_download` jobs, shows resulting artifacts, and deep-links each file into Studio or Postiz publishing. `npm run douyin --` remains the full CLI. Downloads, SQLite state, ephemeral configuration, upstream source, dependencies, and credentials remain ignored.
- The pinned `gitroomhq/postiz-agent` 2.0.15 provider is integrated as `social.postiz-agent` at revision `41c5a9dbd6b2776863e7c05c22e7a385c208321c`.
- `npm run postiz --` installs and verifies the provider, performs OAuth/API-key authentication and integration discovery, and previews or executes short-video drafts/schedules for TikTok, Instagram, and YouTube.
- `config/tool-catalog.json`, the `npm run tools --` CLI, the loopback-only lifecycle API, and `/tools` About & Tools page catalogue all six managed capability projects with pinned revisions, license posture, install state, and activation state.
- The pinned Last 30 Days 3.16.0 source is installed and active locally. `npm run research --`, the research API, and `/research` execute its stable agent JSON 1.x contract and persist workspace-scoped evidence.
- The pinned OpenMontage source is installed and active locally. `/studio` and `npm run studio --` expose clip-factory/podcast-repurpose preflights plus approved, zero-network local VideoTrimmer jobs with immutable-source checks, manual clip ranges, budget enforcement, verified outputs, and provenance.
- Windows exposes exactly two root launchers: `start.cmd` for browser development and `start-electron.bat` for desktop development. Provider/tool operations use npm scripts rather than extra `.cmd` files.
- The pinned Agent Reach 1.5.0 source is installed and active locally. `npm run reach --` and the `/tools` Diagnose action expose 15-channel local-presence diagnostics without upstream execution, network probes, user-config reads, browser-session access, or secret-value exposure.
- Last 30 Days is adapter-ready. OpenMontage preflight and deterministic local clipping are adapter-ready; paid/networked generation remains intentionally blocked.
- MediaCrawler is documented but installation and activation are blocked because its license prohibits commercial use.
- PostgreSQL/pgvector, Redis, and S3-compatible storage remain planned data services when product features require them.
- `Research/` and `References/` are local-only and ignored by Git.
- SQLAlchemy 2 models and Alembic migrations through `20260722_0005` provide user profiles, workspaces, four roles, expiring invitations, device pairings, secret references, and transactional audit events. SQLite is the easy local default; PostgreSQL is the shared/production target.
- The API verifies Supabase asymmetric JWTs through JWKS plus distinct TrendRelay device JWTs; both require issuer, audience, expiry, and subject claims. `/sign-in` implements password sign-in/sign-up, verification redirects, magic links, Google OAuth, password recovery, and global sign-out.
- `/workspaces` lists and creates workspaces, displays members and audit events, and gives owners controls for membership, expiring email-bound invite links, optional encrypted SMTP delivery, secret references, and TOTP account security. Enrolled AAL1 browser sessions are globally challenged before authenticated screens render; deployments can require AAL2 for governed actions.
- `/publish` provides authenticated Postiz integration discovery, dry-run previews, and governed TikTok/Instagram/YouTube submission. Publishing joins Last30Days research and OpenMontage preflights in the leased SQL job store and supervised hot-reload worker; no research or production JSON adapter remains. Temporal was evaluated and intentionally deferred until measured multi-host, durable-timer/signal, recovery, or workflow-history needs exceed the SQL lease model.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory.
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
- Every managed capability repository must be pinned in the machine-readable catalog and documented in the human-readable catalog; supporting runtime repositories must be lockfile-pinned and documented.
- Tool installation and activation remain separate; source presence never implies credentials, dependencies, or production readiness.
- Postiz is dry-run-first. Uploads and remote drafts/schedules require both `--execute` and `--confirm-external-action`; drafts are the default.
- Postiz operations use content-derived IDs and a local ledger. Workspace publishing jobs are durable but receive one provider attempt because duplicate and uncertain retries require inspection. Only owners and approvers can discover integrations or execute; editors may preview.
- Publishing media must resolve to an existing MP4 beneath `PUBLISHING_MEDIA_ROOTS`; the local defaults are `.data/downloads`, `.data/media`, and `.data/productions`.
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
- Postiz wrapper and governed-adapter smoke tests produced dry-run drafts with private/safe defaults and made no provider call. The authenticated publishing API enforces workspace roles, explicit confirmation, approved media roots, and one-attempt durable execution; `/publish` is included in the production browser build.
- No real social upload or post was initiated because credentials, integration IDs, an approved video, and explicit execution confirmation were not supplied.
- `start-electron.bat` repaired the missing Electron 43.1.1 Windows binary through the package-provided installer, then passed desktop-mode validation.
- Unified-runner tests cover healthy-service reuse, unavailable-service startup selection, and missing Electron detection.
- The Last 30 Days pinned checkout was verified as 3.16.0 and activated. CLI and API mock runs completed through agent JSON schema 1.2 and each ingested two workspace-scoped evidence records without external calls.
- The exact OpenMontage checkout was installed and activated. Its two guarded manifests load successfully. The isolated upstream VideoTrimmer produced and ffprobe-verified a real one-second MP4 from the pinned demo source using locked FFmpeg 6.1.1 binaries. The worker passes no provider credentials, performs no network call, records source/artifact hashes and package/upstream provenance, and reports zero provider cost.
- The `/research` Trend Radar page was visually smoke-tested in the local app; the shared browser API resolver follows loopback or LAN hostnames instead of hard-coding `localhost`, and development CORS accepts private-LAN frontend origins.
- The Agent Reach pinned checkout resolves to `1494c2ab239e7355a77e7cceaf3271453a1f34b5` (upstream 1.5.0). The adapter reports all 15 pinned channels, currently with 3 ready, 1 setup-required, and 11 unavailable local capabilities; no live platform calls were made.
- Migrations `20260722_0001` through `20260722_0005` upgrade a fresh local database to head. Foundation tests cover workspace creation, roles, invitations, owner-only secret references, raw-secret rejection, slug validation, and ordered audit events.
- Browser production builds cover `/sign-in`, `/update-password`, and `/workspaces`; ESLint and TypeScript checks pass. Next.js is updated to 16.2.11, patched PostCSS/Sharp overrides are installed, and `npm audit` reports zero known vulnerabilities.
- Migration `20260722_0002` adds one-time, email-bound invitation tokens with expiry, revocation, replay protection, and transactional acceptance. Development CORS now explicitly permits the browser Authorization header.
- Migration `20260722_0003` and `/device` implement a loopback-only, ten-minute, one-time desktop authorization grant. Device JWTs have a separate token type, audience validation, and an eight-hour default lifetime; production requires `DEVICE_TOKEN_SECRET`.
- Electron keeps the device JWT encrypted with operating-system `safeStorage` in the main process. IPC sender origin, renderer navigation, API origin/path, and HTTP methods are allowlisted; the preload never exposes bearer tokens. `start-electron.bat --check` validates without launching services.
- `/account/security` implements TOTP enrollment, QR/manual-secret setup, challenge-and-verify login, unfinished-factor cleanup, AAL2-only verified-factor removal, and a global enrolled-session gate. API tests cover fail-closed claim defaults and optional governed-action enforcement; migration `20260722_0005` carries browser assurance into device pairings. A fresh SQLite database upgraded from empty to `20260722_0005`, and the production browser build includes `/account/security`. A live enrollment was not attempted because no configured Supabase test account was supplied.
- Migration `20260722_0004` adds shared durable jobs with expiring leases, heartbeats, retry budgets, scheduling, cooperative cancellation, and recovery of abandoned running work. Last30Days and OpenMontage are migrated off JSON.
- The unified runner includes a watch-reloaded durable worker for Douyin acquisition, Last30Days research, OpenMontage rendering, and Postiz publishing. A live Last30Days mock completed from SQL with no legacy file, an OpenMontage proposal/approval completed its SQL preflight record with no legacy file, and `scripts/worker.py --once` drains recoverable work.
- Opt-in workspace invitation email uses standard SMTP with STARTTLS or implicit TLS, HTTPS-only public links outside loopback, a 20-attempt-per-workspace hourly default, metadata-only audits, and an always-available copy-link fallback. Unit and API tests prove delivery behavior and raw-token non-persistence; no real email was sent because SMTP credentials were not supplied.
- The complete project suite passes: 95 tests.
- The practical root console was checked in the running Next.js app through both `localhost` and `127.0.0.1`; the sign-in state exits loading reliably, fits a 1280px viewport without horizontal overflow, and the private-LAN development-origin allowlist matches the unified runner.
- Tool catalog coverage includes complete listing, explicit confirmation, loopback-only mutation, MediaCrawler license blocking, pinned checkout, activation, and Windows-safe isolated uninstall.
- Production builds for Next.js and Electron, TypeScript checks, ESLint, Ruff, JSON validation, CLI listing, and diff checks pass. The `/tools` page exposes six catalog cards, five locally installed/active providers, guarded lifecycle controls, Agent Reach diagnostics, and the MediaCrawler license block.

## Next recommended action

Use the root operations console as the default workflow: fetch authorized Douyin media, prepare or review it in Studio, then send approved files through `/publish`. TikTok acquisition remains visibly unavailable until a reviewed provider is incorporated; Postiz TikTok publishing remains supported for approved local media. Keep the SQL leased queue until ADR 0010 triggers are observed, paid/networked OpenMontage providers disabled pending review, and MediaCrawler blocked unless written commercial permission is obtained.
