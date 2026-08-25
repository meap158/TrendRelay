# ADR 0011: Govern social publishing as a durable external action

## Status

Accepted. Revised to replace the self-hosted Postiz service with pluggable hosted engines.

## Decision

TrendRelay exposes short-video publishing through an authenticated, workspace-scoped API and `/publish` browser screen. Editors may generate offline previews. Only owners and approvers may discover connected accounts, change engines, save engine credentials, or submit remote work, and every provider-facing request requires an explicit external-action confirmation.

Publishing targets four hosted engines. `PUBLISHING_PROVIDER` names the default, but a single post may address destinations on several at once, chosen per account rather than per network:

| Engine | Auth | Media | Notes |
| --- | --- | --- | --- |
| `bundle_social` | `x-api-key` plus a team ID | Uploads the reviewed local MP4, or ingests a URL | Multi-tenant white-label engine; verbose platform errors |
| `zernio` | Static `Authorization: Bearer sk_…` | Presigned `PUT`, or ingests a URL | Single-tenant; scheduling, drafts, and immediate publishing share one endpoint |
| `buffer` | `Authorization: Bearer …` over GraphQL | None — the caller supplies a public HTTPS URL | Queue semantics; one mutation per channel |
| `woopsocial` | `Authorization: Bearer …` | Multipart upload only, capped at 100 MB per request | Draft/schedule/publish-now are native; reports delivery per destination; the only engine that will validate a post before it exists |

An engine declares the platforms it publishes to, and a request naming an unsupported platform is rejected before any network call.

### Publishing without reporting is not enough

**Every engine must read back what its posts earned, or state in its own definition why its API cannot.** This is a requirement of adding an engine, not a later enhancement, and a test enforces it: `ProviderDefinition.no_metrics_reason` must be set exactly when no reader is registered in `_register_metric_readers` — never both, never neither.

The rule exists because the failure is silent. An engine that publishes but never reports leaves its campaigns reading zero, and a zero on screen is indistinguishable from a post nobody saw. The gap hides precisely where it costs most: in the numbers a campaign is steered by. A missing reader is not a missing feature, it is a wrong answer.

"Not implemented yet" is not an acceptable reason. The reason must belong to the engine's API, and it must carry the evidence that will let the next person re-check it.

| Engine | Reads back | How, and what it costs |
| --- | --- | --- |
| `zernio` | Yes | A dated analytics report, paged. The post is matched on `latePostId`; the report's own `_id` is a per-platform row and matches nothing we store |
| `buffer` | Yes | `post(input: {id})` returns `metrics { type unit value }` — a direct lookup, since Buffer's own post id is what delivery stored. Its `reactions` is preferred over its `likes`, which means only the Facebook Like subcount. Bounded by a hard 250 requests per day for the whole key |
| `bundle_social` | Yes, unverified | `GET /analytics/post` per post *and* network, falling back to the raw platform payload. Their side refreshes every 24h and retains 30 days. Written from their published reference; the configured key is refused with 403 on every path, so it has never run against a live account |
| `woopsocial` | No | Its published OpenAPI 1.0.0 document has no analytics operation, and the words likes, views, shares, impressions and engagement do not occur in it. It reports `DeliveryStatus` — whether the post went out, not how it did |

Two rules govern what a reader may record, and both exist because of a bug that recorded another post's figures as this one's:

- **A failed read is never an observation.** Not found, not reachable, not measured yet, and not enough budget all return `None`, which leaves the window due and retried. Writing zeros instead is permanent: a captured window is never captured again, so a guessed zero outlives the outage that caused it.
- **A figure the network reported is recorded as it stands**, zero included. What the reader must not do is invent the distinction between "reported nothing" and "was not asked".

Measurement is also the lowest-priority caller of any engine. Buffer's allowance is shared between publishing, listing channels, and reading metrics, and only the last of those can wait — so its reader stops while requests remain (`BUFFER_METRICS_RESERVE`) rather than spending the budget a campaign needs to post at all.

Because the engines disagree about media, `PublishRequest` carries both an approved local `video_path` and an optional public `media_url`, and each engine declares two separate capabilities rather than one. `requires_public_media` means it cannot accept an upload at all; `ingests_media_url` means it can fetch a URL instead of being handed the file. The pair matters because one post spans several engines: a URL supplied so Buffer can publish must not excuse WoopSocial, which has no endpoint that takes a URL, from reading the local file.

Credentials are operator-supplied through the Publish screen. The API writes them to the project's local `.env` from a loopback-only, role-gated, explicitly confirmed endpoint, then clears the cached settings. Only fixed, allow-listed keys may be written.

Stored values are exposed in two deliberate shapes, which supersedes the earlier rule that they were never returned. Status payloads carry a preview masked to the last four characters — enough to recognise which key is in place, never enough to use, and a value too short to mask keeps none of itself. A separate endpoint returns one value in full, gated exactly as a write is and restricted to the keys these screens can already write, so it can never become "read any environment variable". The rule changed because a row of empty boxes reads as "nothing saved": five R2 settings were stored with the account ID in the access-key field, and nothing on screen could distinguish them.

Submitted operations use the shared SQL durable-job store with kind `social_publish`, and the resolved engine is frozen into the job payload at creation. Workers claim an expiring lease before calling the engine. The retry budget is one because a timeout after upload or post creation has an uncertain remote outcome; operators must inspect the engine rather than retry blindly.

Media paths are resolved by the API and must be existing MP4 files beneath a configured publishing media root. The local defaults are `.data/downloads`, `.data/media`, and `.data/productions`; deployments may replace them with `PUBLISHING_MEDIA_ROOTS`. This prevents an authenticated LAN client from selecting arbitrary server files.

## Consequences

Publishing survives API restarts and can be processed by the supervised worker without placing provider credentials or bearer tokens in the browser. Previewing never authenticates, uploads, or mutates provider state. No AGPL publishing service is installed, supervised, or embedded any more, which removes the native PostgreSQL, Redis, and Temporal dependencies from startup.

Real execution still depends on a configured engine key, accounts connected inside that engine's own dashboard, rights-approved media, and an explicit user confirmation. Switching engines invalidates previously discovered account IDs, so the interface discards them and requires a refresh.
