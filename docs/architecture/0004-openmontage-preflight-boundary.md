# ADR 0004: Governed OpenMontage preflight and local rendering

## Status

Accepted and extended — 2026-07-22

## Context

OpenMontage is an agent-driven production framework with local and paid media providers, extensive dependencies, human checkpoints, and AGPL-3.0 obligations. Activating its source must not silently authorize spending, rendering, content reuse, publishing, or access to provider credentials. Its base module also auto-loads an upstream `.env`, which is outside TrendRelay's trusted execution boundary.

## Decision

- Expose only the pinned `clip-factory` and `podcast-repurpose` manifests for production planning.
- Require a user-confirmed local source file and explicit rights basis.
- Fingerprint the immutable input and bind approval to its SHA-256 and budget cap.
- Preserve human approval before any render job is submitted.
- Permit only explicit manual clip ranges through the upstream deterministic `VideoTrimmer`.
- Run trimming in a subprocess with a scrubbed environment and a compatibility shim that prevents upstream `.env` loading.
- Use locked GPL media binaries, fixed ignored output roots, verified MP4 video streams/durations, artifact hashes, bounded retries, and durable SQL leases.
- Record zero provider cost and exact OpenMontage/media package provenance.
- Keep paid or networked providers, automatic generation, arbitrary commands, and publishing disabled.

## Consequences

TrendRelay can safely produce deterministic local clips without provider credentials or network calls. The `/studio` surface and CLI separate proposal, approval, and rendering. Distribution must satisfy OpenMontage AGPL and packaged FFmpeg GPL obligations. Provider-backed generation remains a future capability with independent authorization, cost reconciliation, provenance, and review.
