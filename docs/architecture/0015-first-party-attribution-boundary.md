# ADR 0015: Use transparent first-party links and privacy-minimized attribution

Status: Accepted

## Context

TrendRelay must connect trends, opportunities, campaigns, creatives, publications, clicks, products, conversions, and commission. Direct affiliate URLs cannot provide stable campaign/platform sub-IDs, country routing, disclosure metadata, click records, or broken-offer behavior. A generic open redirect would create phishing and misleading-cloaking risk, while storing raw visitor identifiers or affiliate order references would collect unnecessary personal and commercial data.

## Decision

- The FastAPI control plane exposes a deliberately narrow public boundary at `/c/{short-code}`. Production may route a dedicated hostname to only `/c/*`; `ATTRIBUTION_PUBLIC_URL` controls generated URLs.
- Authenticated, governed creation resolves the destination only from a workspace campaign, publication plan, or available affiliate offer. Destinations must be credential-free HTTPS URLs. Visitor-supplied query parameters are never forwarded.
- Existing affiliate query parameters are preserved. Configured campaign and platform parameter names must differ, cannot collide with any base or country destination, and are appended without overwriting existing values.
- Explicit two-letter country destinations are supported from trusted edge country headers. No automatic offer rotation occurs; program-rule review is required before changing a destination.
- `/c/{short-code}/info` exposes the final destination host, disclosure, status, and expiry. Disabled, expired, broken, and unavailable-offer links return `410` instead of silently rotating or redirecting.
- Click records contain workspace/campaign/plan/offer/product provenance, time, coarse country, referrer origin only, coarse user-agent family, and a workspace-scoped daily HMAC visitor pseudonym. Raw IP addresses, full referrer paths, full user-agent strings, and URL query strings are not stored.
- Development creates a persistent ignored attribution HMAC secret under `.data/`; production must set `ATTRIBUTION_HASH_SECRET`. Affiliate conversion references are stored only as keyed hashes.
- Confirmed conversion CSV imports are workspace-scoped, idempotent by network and keyed reference hash, and update prior status for refunds or reversals. The latest eligible click within the offer cookie window is attached when available.
- Revenue is never summed across currencies. Summaries expose clicks, privacy-safe visitors, approved/pending/reversed conversions, net commission, earnings per click, campaign revenue, and creative-format revenue. Metrics that require platform views remain explicitly unavailable until platform analytics synchronization exists.

## Consequences

The local first release provides an auditable revenue loop without deploying another runtime or collecting raw visitor identity. The `services/link-router` boundary can later be extracted behind the same routes and database contracts when scale or independent deployment requires it.

Click fraud detection, live affiliate reporting adapters, view-based CTR, product-page conversion denominators, automatic broken-link probes, and program-approved destination rotation remain future capabilities. They must not be inferred from the current click and conversion records.
