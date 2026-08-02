# ADR 0011: Govern social publishing as a durable external action

## Status

Accepted. Revised to replace the self-hosted Postiz service with pluggable hosted engines.

## Decision

TrendRelay exposes short-video publishing through an authenticated, workspace-scoped API and `/publish` browser screen. Editors may generate offline previews. Only owners and approvers may discover connected accounts, change engines, save engine credentials, or submit remote work, and every provider-facing request requires an explicit external-action confirmation.

Publishing targets one of three hosted engines, selected by `PUBLISHING_PROVIDER`:

| Engine | Auth | Media | Notes |
| --- | --- | --- | --- |
| `bundle_social` | `x-api-key` plus a team ID | Uploads the reviewed local MP4 | Multi-tenant white-label engine; verbose platform errors |
| `zernio` | Static `Authorization: Bearer sk_…` | Presigned `PUT`, then a public URL | Single-tenant; scheduling, drafts, and immediate publishing share one endpoint |
| `buffer` | `Authorization: Bearer …` over GraphQL | None — the caller supplies a public HTTPS URL | Queue semantics; one mutation per channel |

An engine declares the platforms it publishes to, and a request naming an unsupported platform is rejected before any network call. Because the engines disagree about media, `PublishRequest` carries both an approved local `video_path` and an optional public `media_url`; Buffer requires the URL, Zernio prefers it and otherwise uploads, and Bundle.social always uploads the reviewed local file.

Credentials are operator-supplied through the Publish screen. The API writes them to the project's local `.env` from a loopback-only, role-gated, explicitly confirmed endpoint, then clears the cached settings. Only fixed, allow-listed keys may be written. Stored values are never returned: the API reports configured booleans and the environment-variable name.

Submitted operations use the shared SQL durable-job store with kind `social_publish`, and the resolved engine is frozen into the job payload at creation. Workers claim an expiring lease before calling the engine. The retry budget is one because a timeout after upload or post creation has an uncertain remote outcome; operators must inspect the engine rather than retry blindly.

Media paths are resolved by the API and must be existing MP4 files beneath a configured publishing media root. The local defaults are `.data/downloads`, `.data/media`, and `.data/productions`; deployments may replace them with `PUBLISHING_MEDIA_ROOTS`. This prevents an authenticated LAN client from selecting arbitrary server files.

## Consequences

Publishing survives API restarts and can be processed by the supervised worker without placing provider credentials or bearer tokens in the browser. Previewing never authenticates, uploads, or mutates provider state. No AGPL publishing service is installed, supervised, or embedded any more, which removes the native PostgreSQL, Redis, and Temporal dependencies from startup.

Real execution still depends on a configured engine key, accounts connected inside that engine's own dashboard, rights-approved media, and an explicit user confirmation. Switching engines invalidates previously discovered account IDs, so the interface discards them and requires a refresh.
