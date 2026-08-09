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
- Affiliate networks receive a sub ID, which is the only field that survives into their own conversion report. Our campaign and platform parameters never reach it: a network has no idea what they are, so without a sub ID no imported conversion has a column identifying the link.
- Sub-ID slots carry fixed meanings, in a constant order — link, content, placement, campaign, date. Networks report them positionally, so a slot holding a placement on one link and a campaign on another produces a column that cannot be grouped by anything. The order also decides what survives on a network offering fewer slots than we have dimensions.
- Slot one always carries a key derived from the tracking code, because it resolves every other dimension from our own database and the conversion importer matches on it. The key is a hash rather than the code itself: `token_urlsafe` puts a `-` or `_` in about 27% of codes and Shopee accepts letters and digits only, while stripping those characters would collide two links onto one key. Deriving it needs no column and no backfill.
- The content dimension is keyed on the media's own hash rather than a plan id, so the same cut reused in another campaign reports under the same value. "Which video sells" is the question that column exists for.
- Values are fixed when a link is minted, never derived at redirect. A sub ID that changed because a campaign was renamed would split one link's history into two columns that cannot be added back together.
- A destination host matching no known network receives no sub IDs at all. Some networks reject a link carrying parameters they do not recognise, so a guessed parameter name does not weaken tracking, it breaks the sale. Every supported network records where its contract came from.
- Confirmed conversion CSV imports are workspace-scoped, idempotent by network and keyed reference hash, and update prior status for refunds or reversals. The latest eligible click within the offer cookie window is attached when available. A row is identified by our tracking code or by the sub ID the network reported it under, since a network's own export contains only the latter.
- Revenue is never summed across currencies. Summaries expose clicks, privacy-safe visitors, approved/pending/reversed conversions, net commission, earnings per click, campaign revenue, and creative-format revenue. Metrics that require platform views remain explicitly unavailable until platform analytics synchronization exists.

## Consequences

The local first release provides an auditable revenue loop without deploying another runtime or collecting raw visitor identity. The `services/link-router` boundary can later be extracted behind the same routes and database contracts when scale or independent deployment requires it.

Click fraud detection, live affiliate reporting adapters, view-based CTR, product-page conversion denominators, automatic broken-link probes, and program-approved destination rotation remain future capabilities. They must not be inferred from the current click and conversion records.
