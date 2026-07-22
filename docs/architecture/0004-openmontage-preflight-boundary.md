# ADR 0004: OpenMontage preflight before execution

## Status

Accepted — 2026-07-22

## Context

OpenMontage is an agent-driven production framework with local and paid media providers, extensive dependencies, human checkpoints, and AGPL-3.0 obligations. Activating its source must not silently authorize spending, rendering, content reuse, or publishing.

## Decision

- Initially expose only the pinned `clip-factory` and `podcast-repurpose` manifests.
- Require a user-confirmed local source file and explicit rights basis.
- Fingerprint the immutable input and bind approval to its SHA-256 and budget cap.
- Preserve every upstream human-approval gate in the proposal.
- Keep approved proposals non-executable until a separate runtime adapter provides isolated dependencies, scoped provider authorization, cost reservation/reconciliation, artifact provenance, and license review.

## Consequences

TrendRelay can safely plan and audit short-form production now without creating the impression that source activation enables costly operations. Rendering remains a deliberate later capability with its own implementation and confirmation boundary.
