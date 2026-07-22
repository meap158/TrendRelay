# ADR 0010: Durable jobs use database leases

## Decision

Research and production execution share a SQL-backed durable-job record. A job stores its workspace key, kind, validated payload, result, lifecycle timestamps, retry budget, cancellation intent, and last bounded error. Workers claim eligible jobs with an identity and expiring lease, heartbeat long work, and may complete or retry only while holding that lease.

Expired running leases are claimable again, making interrupted work recoverable after a process restart. PostgreSQL uses row locking with `SKIP LOCKED` for competing workers; SQLite remains the single-worker local development target. Queued cancellation is immediately terminal, while running cancellation becomes cooperative and resolves at the next worker boundary.

## Consequences

Provider adapters can move off local JSON without coupling their domain result schemas to queue mechanics. Last30Days is migrated to this store. OpenMontage still needs an idempotent worker and explicit migration before its old file store is removed. External side effects must use provider idempotency keys derived from the durable job ID.
