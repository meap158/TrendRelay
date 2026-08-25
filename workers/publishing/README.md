# Publishing worker

Durable, idempotent upload and polling operations with capability checks, retries, audit events, and manual fallbacks.

Work is executed against whichever hosted engine is active — Bundle.social, Zernio, Buffer, or WoopSocial — and every job records the engine it resolved to, so a later engine switch never re-routes an in-flight delivery. Explicitly confirmed MP4 drafts and schedules are supported for the platforms each engine advertises. Engine credentials live only in the local `.env`; job state and audit-safe deduplication live in the shared SQL job store under kind `social_publish`.

The same worker pass reads engagement back. Every engine must supply a metrics reader or record why its API cannot — a rule the test suite enforces, because an engine that publishes without reporting leaves a campaign reading zeros that look like a result. A read that fails leaves the window due rather than filling it, and measurement yields to publishing when an engine's request budget runs short. See [ADR 0011](../../docs/architecture/0011-governed-social-publishing.md).
