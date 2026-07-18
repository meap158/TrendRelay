# Agent Handover

Last updated: 2026-07-19

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises backend and frontend hot-reload processes; `start.cmd --desktop` can include Electron.
- The pinned `jiji262/douyin-downloader` 2.0.0 provider is integrated as `media.douyin-downloader` and installed locally at revision `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- `douyin.cmd` installs, verifies, and runs single-link or file-based Douyin batches. Downloads, SQLite state, ephemeral configuration, upstream source, dependencies, and credentials remain ignored.
- PostgreSQL/pgvector, Redis, and S3-compatible storage remain planned data services when product features require them.
- `Research/` and `References/` are local-only and ignored by Git.
- No database migrations, authentication, Temporal setup, publishing connector, or product UI for media jobs exists yet.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory.
- Python/FastAPI is the control-plane runtime; Python also powers compute-heavy workers.
- Provider source remains isolated under `.tools/`; core modules depend only on capability contracts.
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

## Next recommended action

Connect the `media.douyin-downloader` manifest to a persisted media-job API and desktop queue. Capture upstream completion metadata into TrendRelay asset records, hash downloaded originals, and add rights classification before making media available to creative workflows.
