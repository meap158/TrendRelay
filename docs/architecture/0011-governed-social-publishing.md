# ADR 0011: Govern social publishing as a durable external action

## Status

Accepted.

## Decision

TrendRelay exposes Postiz short-video publishing through an authenticated, workspace-scoped API and `/publish` browser screen. Editors may generate offline previews. Only owners and approvers may discover connected accounts or submit remote work, and every provider-facing request requires an explicit external-action confirmation.

Submitted operations use the shared SQL durable-job store with kind `social_publish`. Workers claim an expiring lease before invoking Postiz. The retry budget is one because a timeout after upload or draft creation has an uncertain remote outcome; operators must inspect Postiz rather than retry blindly. Postiz's content-derived operation ledger remains a second provider-boundary safeguard against duplicate or uncertain execution.

Media paths are resolved by the API and must be existing MP4 files beneath a configured publishing media root. The local defaults are `.data/downloads`, `.data/media`, and `.data/productions`; deployments may replace them with `PUBLISHING_MEDIA_ROOTS`. This prevents an authenticated LAN client from selecting arbitrary server files.

## Consequences

Publishing can survive API restarts and can be processed by the supervised worker without placing provider credentials or bearer tokens in the browser. Previewing never authenticates, uploads, or mutates provider state. Real execution still depends on locally configured Postiz credentials, connected integration IDs, rights-approved media, and an explicit user confirmation.
