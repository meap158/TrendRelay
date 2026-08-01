# ADR 0016: Public Meta Ad Library research boundary

## Status

Accepted.

## Context

TrendRelay already uses Meta Ads Kit for read-only first-party account performance. Competitive
research needs a different capability: searching public Meta Ad Library ads without requiring
the operator to create and verify a Meta developer application.

`promisingcoder/MetaAdsCollector` is MIT-licensed and implements that capability by reproducing
Meta's browser-facing GraphQL flow, dynamically extracting request tokens, and using a
Chrome-like TLS fingerprint.

## Decision

Treat Meta Ads Collector as an isolated, read-only Research provider.

- Pin source and install dependencies in a provider-specific virtual environment under `.tools/`.
- Expose one bounded search contract through the loopback Research API.
- Validate all search/filter inputs and cap each operation at 50 returned ads.
- Require an explicit external-action confirmation before a live public search.
- Return normalized ad evidence; do not persist raw GraphQL responses or provider diagnostics.
- Keep public competitive intelligence distinct from authenticated Meta Ads Kit reporting.
- Expose no media download, webhook, proxy rotation, browser-cookie import, or mutation path.

## Consequences

Research gains public advertiser, creative, placement, and delivery evidence with no API key.
The integration may break when Meta changes its private web contract and must fail closed with a
sanitized message. Reverse engineering does not remove the operator's obligations under Meta's
terms, privacy rules, applicable law, or creative-content rights.
