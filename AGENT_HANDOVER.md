# Agent Handover

Last updated: 2026-07-22

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises hot-reload services, reuses healthy backend/frontend processes instead of launching duplicates, and waits for new services to become healthy before starting dependents. `start-electron.bat` delegates to `start.cmd --desktop`.
- The pinned `jiji262/douyin-downloader` 2.0.0 provider is integrated as `media.douyin-downloader` and installed locally at revision `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- `npm run douyin --` installs, verifies, and runs single-link or file-based Douyin batches. Downloads, SQLite state, ephemeral configuration, upstream source, dependencies, and credentials remain ignored.
- The pinned `gitroomhq/postiz-agent` 2.0.15 provider is integrated as `social.postiz-agent` at revision `41c5a9dbd6b2776863e7c05c22e7a385c208321c`.
- `npm run postiz --` installs and verifies the provider, performs OAuth/API-key authentication and integration discovery, and previews or executes short-video drafts/schedules for TikTok, Instagram, and YouTube.
- `config/tool-catalog.json`, the `npm run tools --` CLI, the loopback-only lifecycle API, and `/tools` About & Tools page catalogue all six incorporated GitHub projects with pinned revisions, license posture, install state, and activation state.
- The pinned Last 30 Days 3.16.0 source is installed and active locally. `npm run research --`, the research API, and `/research` execute its stable agent JSON 1.x contract and persist workspace-scoped evidence.
- Windows exposes exactly two root launchers: `start.cmd` for browser development and `start-electron.bat` for desktop development. Provider/tool operations use npm scripts rather than extra `.cmd` files.
- OpenMontage and Agent Reach remain source-ready catalog entries; their native adapters and provider dependency setup are pending. Last 30 Days is adapter-ready.
- MediaCrawler is documented but installation and activation are blocked because its license prohibits commercial use.
- PostgreSQL/pgvector, Redis, and S3-compatible storage remain planned data services when product features require them.
- `Research/` and `References/` are local-only and ignored by Git.
- No database migrations, application authentication, Temporal setup, publishing UI, or persisted media-job API exists yet. Research jobs currently use local JSON persistence and in-process background execution.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory.
- Python/FastAPI is the control-plane runtime; Python also powers compute-heavy workers.
- Provider source remains isolated under `.tools/`; core modules depend only on capability contracts.
- Live trend research requires explicit external-action confirmation. Browser-cookie extraction is disabled and the adapter passes only allowlisted research secrets to Last 30 Days.
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
- The `/research` Trend Radar page was visually smoke-tested in the local app; the shared browser API resolver follows loopback or LAN hostnames instead of hard-coding `localhost`, and development CORS accepts private-LAN frontend origins.
- The complete project suite passes: 44 tests. Tool catalog coverage includes complete listing, explicit confirmation, loopback-only mutation, MediaCrawler license blocking, pinned checkout, activation, and Windows-safe isolated uninstall.
- Production builds for Next.js and Electron, TypeScript checks, ESLint, Ruff, JSON validation, CLI listing, and diff checks pass. The `/tools` page was visually verified at 1440 by 1000 with six cards, two installed/active providers, guarded controls, and the MediaCrawler block.

## Next recommended action

Evaluate OpenMontage behind cost, provenance, rights, and approval gates, then add an Agent Reach diagnostics adapter without invoking its system-wide installer. In parallel, begin the first-party workspace/authentication/audit slice and replace local research JSON/background tasks with database-backed durable execution when those foundations are ready. Keep MediaCrawler blocked unless written commercial permission is obtained.
