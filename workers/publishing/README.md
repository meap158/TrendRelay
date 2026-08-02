# Publishing worker

Durable, idempotent upload and polling operations with capability checks, retries, audit events, and manual fallbacks.

Work is executed against whichever hosted engine is active — Bundle.social, Zernio, or Buffer — and every job records the engine it resolved to, so a later engine switch never re-routes an in-flight delivery. Explicitly confirmed MP4 drafts and schedules are supported for the platforms each engine advertises. Engine credentials live only in the local `.env`; job state and audit-safe deduplication live in the shared SQL job store under kind `social_publish`.
