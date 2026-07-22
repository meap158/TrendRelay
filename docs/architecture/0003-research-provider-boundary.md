# ADR 0003: Research provider boundary

## Status

Accepted — 2026-07-22

## Context

Recent-topic providers can access many public sources and optional paid APIs. Some can also read browser sessions or user-level configuration. TrendRelay needs their evidence without coupling the control plane to provider internals or giving a research process unrelated credentials.

## Decision

- Execute Last 30 Days out of process at its exact catalogued revision.
- Consume only its stable agent JSON 1.x export; reject unknown major schema versions.
- Disable browser-cookie extraction in the TrendRelay adapter.
- Construct a subprocess environment from operating-system essentials plus an explicit research-secret allowlist.
- Require installed and active provider state plus explicit confirmation for live research.
- Normalize every result into a workspace-scoped observation while retaining the raw evidence record.
- Store local development jobs under ignored `.data/research/`; migrate the same contract to database-backed durable execution with the control-plane foundation.

## Consequences

TrendRelay can replace or upgrade the provider behind a versioned boundary and can prove which revision produced each job. Some authenticated sources remain unavailable until a user deliberately configures an allowed key. Local JSON/background execution is usable for development but is not considered durable scheduling.
