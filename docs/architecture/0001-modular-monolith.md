# ADR 0001: Begin with a modular monolith

Status: Accepted — 2026-07-18

## Context

TrendRelay spans trend ingestion, affiliate intelligence, media processing, generation, publishing, and attribution. These areas need clean boundaries, but an early product lacks scaling evidence for independently deployed business services.

## Decision

The Go control plane begins as a modular monolith. Domain modules communicate through explicit interfaces. Expensive or failure-prone workloads run in isolated workers, and platform integrations run as capability-based plugins. PostgreSQL is the system of record; binary assets live in object storage.

## Consequences

- Product transactions and local development remain simple.
- Provider code can evolve independently without entering the core.
- Modules can be extracted when measured load, ownership, or reliability needs justify it.
- Service-shaped directories are boundaries and intentions, not a mandate to deploy every directory independently.
