# ADR 0010: Durable jobs use database leases

## Decision

Research and production execution share a SQL-backed durable-job record. A job stores its workspace key, kind, validated payload, result, lifecycle timestamps, retry budget, cancellation intent, and last bounded error. Workers claim eligible jobs with an identity and expiring lease, heartbeat long work, and may complete or retry only while holding that lease.

Expired running leases are claimable again, making interrupted work recoverable after a process restart. PostgreSQL uses row locking with `SKIP LOCKED` for competing workers; SQLite remains the single-worker local development target. Queued cancellation is immediately terminal, while running cancellation becomes cooperative and resolves at the next worker boundary.

## Consequences

Provider adapters can move off local JSON without coupling their domain result schemas to queue mechanics. Last30Days research and OpenMontage preflight state are migrated to this store and their old JSON paths are removed. OpenMontage rendering remains disabled and will require a separate idempotent execution job when approved for implementation. External side effects must use provider idempotency keys derived from the durable job ID.

The unified development runner supervises a watch-reloaded research worker. It polls eligible SQL records, relies on atomic claims to resolve races with API background dispatch, and reclaims work after lease expiry.