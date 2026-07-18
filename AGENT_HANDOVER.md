# Agent Handover

Last updated: 2026-07-19

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises backend and frontend hot-reload processes; `start-electron.bat` is the one-click Electron launcher and delegates to `start.cmd --desktop`.
- The pinned `jiji262/douyin-downloader` 2.0.0 provider is integrated as `media.douyin-downloader` and installed locally at revision `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- `douyin.cmd` installs, verifies, and runs single-link or file-based Douyin batches. Downloads, SQLite state, ephemeral configuration, upstream source, dependencies, and credentials remain ignored.
- The pinned `gitroomhq/postiz-agent` 2.0.15 provider is integrated as `social.postiz-agent` at revision `41c5a9dbd6b2776863e7c05c22e7a385c208321c`.
- `postiz.cmd` installs and verifies the provider, performs OAuth/API-key authentication and integration discovery, and previews or executes short-video drafts/schedules for TikTok, Instagram, and YouTube.
- PostgreSQL/pgvector, Redis, and S3-compatible storage remain planned data services when product features require them.
- `Research/` and `References/` are local-only and ignored by Git.
- No database migrations, application authentication, Temporal setup, publishing UI, or persisted media-job API exists yet.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory.
- Python/FastAPI is the control-plane runtime; Python also powers compute-heavy workers.
- Provider source remains isolated under `.tools/`; core modules depend only on capability contracts.
- Postiz is dry-run-first. Uploads and remote drafts/schedules require both `--execute` and `--confirm-external-action`; drafts are the default.
- Postiz operations use content-derived IDs and a local ledger. Duplicate and uncertain retries are blocked pending inspection.
- Douyin batches default to 50 items per selected profile mode. Full crawls require explicit `--limit 0`.
- Cookie values come only from local environment variables or `.env`, are redacted from dry runs, and exist in generated configuration only for the process lifetime.
- SQLite and file-based deduplication remain enabled for repeat and incremental downloads.
- Keep downloaded content as reference media until rights and policy classification permits further use.

## Validation completed

- Pinned upstream checkout resolved exactly to `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- Isolated provider installation succeeded on Python 3.14; both `douyin.cmd check` and `npm run douyin -- check` reported version 2.0.0.
- Fourteen API, wrapper, URL-security, validation, secret-lifecycle, environment-loader, and manifest tests passed.
- Ruff lint and formatting checks passed for the integration code and tests.
- End-to-end `douyin.cmd batch --file ... --dry-run` parsed copied share text and profile URLs, deduplicated input, applied incremental bounded settings, and produced redacted configuration.
- A live media download was not initiated because no user-authorized Douyin URL was provided.
- Pinned Postiz checkout resolved exactly to `41c5a9dbd6b2776863e7c05c22e7a385c208321c`; the isolated build and both `postiz.cmd check` and `npm run postiz -- check` reported version 2.0.15.
- The complete project suite passes: 24 tests covering existing scaffolding plus Postiz schema, safe payload defaults, dry-run, confirmation, simulated external calls, cleanup, and idempotency.
- A three-platform wrapper smoke test produced a draft preview with private/safe defaults and made no provider call.
- No real social upload or post was initiated because credentials, integration IDs, an approved video, and explicit execution confirmation were not supplied.
- `start-electron.bat` launcher validation passed through the unified runner with backend, frontend, and desktop services enabled.

## Next recommended action

Authenticate Postiz, connect the intended social accounts, and run `postiz.cmd integrations` to capture their integration IDs. Then test an approved MP4 as a private/draft post before enabling an explicit schedule. In parallel, connect both provider manifests to persisted media and publication job APIs with rights classification and audit events.
