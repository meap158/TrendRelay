# ADR 0010: Durable jobs use database leases

## Decision

Research and production execution share a SQL-backed durable-job record. A job stores its workspace key, kind, validated payload, result, lifecycle timestamps, retry budget, cancellation intent, and last bounded error. Workers claim eligible jobs with an identity and expiring lease, heartbeat long work, and may complete or retry only while holding that lease.

Expired running leases are claimable again, making interrupted work recoverable after a process restart. PostgreSQL uses row locking with `SKIP LOCKED` for competing workers; SQLite remains the single-worker local development target. Queued cancellation is immediately terminal, while running cancellation becomes cooperative and resolves at the next worker boundary.

## Consequences

Provider adapters can move off local JSON without coupling their domain result schemas to queue mechanics. Last30Days research and OpenMontage preflight state are migrated to this store and their old JSON paths are removed. OpenMontage rendering remains disabled and will require a separate idempotent execution job when approved for implementation. External side effects must use provider idempotency keys derived from the durable job ID.

The unified development runner supervises a watch-reloaded durable worker for research and publishing. It polls eligible SQL records, relies on atomic claims to resolve races with API background dispatch, and reclaims work after lease expiry.

Temporal was evaluated after the leased queue became operational and is deliberately deferred. Temporal requires a separate managed or self-hosted service plus worker deployment and workflow-versioning operations; adding that control plane to the current single-machine path would weaken the one-click startup without solving an observed limitation. Adopt the Temporal Python SDK only after at least one of these conditions is demonstrated: multiple independently deployed worker pools need task-queue routing; a versioned multi-step workflow needs durable timers, signals, or compensation beyond one job transaction; recovery objectives exceed the SQL lease model; or operators need cross-service workflow history that the audit/job records cannot provide. Any migration must preserve existing durable job IDs as provider idempotency keys and run both models during a measured cutover.

Reference: [Temporal deployment options](https://docs.temporal.io/).