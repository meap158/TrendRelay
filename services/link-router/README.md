# Link router

The first-party redirect boundary is implemented by `trendrelay_api.attribution_api` and exposed at `/c/{short-code}`. A dedicated production hostname can route only `/c/*` to this API surface; `ATTRIBUTION_PUBLIC_URL` controls generated links.

The boundary resolves only governed HTTPS destinations already stored in a workspace tracking-link record. It preserves the affiliate URL's existing query parameters, appends configured campaign/platform sub-ID parameters without overwriting existing values, ignores visitor-supplied query parameters, supports explicit country destinations, records privacy-minimized click events, and returns `410` for disabled, expired, broken, or unavailable offers. `/c/{short-code}/info` exposes the destination host and disclosure so the redirect is not misleading cloaking.

Raw IP addresses, full referrer paths, full user-agent strings, and affiliate conversion references are not stored. When `ATTRIBUTION_HASH_SECRET` is configured, visitor IPs become daily HMAC pseudonyms and conversion references become keyed hashes. The authenticated workspace API owns link creation/status, conversion CSV imports, and revenue summaries.