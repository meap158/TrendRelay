# Meta Ads Collector

- Repository: <https://github.com/promisingcoder/MetaAdsCollector>
- Pinned revision: `0ffb2fb1af94eae6542b328ab3ae31fc1c9a5897`
- Version: `1.4.0`
- License: MIT
- TrendRelay status: embedded public-ad research adapter

## Purpose

Meta Ads Collector searches Meta's public Ad Library without a developer token. TrendRelay
uses it in Research for bounded keyword and competitor-page collection. Results include public
creative text and media links, advertiser identity, delivery dates, placements, and any spend
or impression ranges Meta returns.

This provider complements rather than replaces Meta Ads Kit:

- Meta Ads Collector provides public competitive evidence and requires no account connection.
- Meta Ads Kit reads an explicitly connected advertiser account for first-party performance.

## Installation

```powershell
npm run tools -- install meta-ads-collector --confirm-external-action
npm run tools -- activate meta-ads-collector
```

The trusted installer checks out the exact revision under `.tools/`, creates an isolated Python
runtime, and installs the pinned source plus its `curl_cffi` transport dependency there. Source,
dependencies, transient sessions, and provider output remain outside Git.

## Boundary

- Search is loopback-only and requires explicit confirmation.
- Each request is limited to 50 returned ads.
- Inputs are schema validated; no command or path is accepted from the browser.
- The adapter executes a fixed local bridge and never uses a shell.
- Raw provider diagnostics and response bodies are not returned on failure.
- No ad-account credential, Meta developer token, browser cookie, or mutation capability exists.
- Media links remain remote public evidence; TrendRelay does not download or claim rights to them.

The upstream transport reverse-engineers Meta's browser-facing GraphQL requests and uses
browser-like TLS fingerprints. This is operationally fragile and can stop working when Meta
changes tokens, document IDs, or automated-access controls. Use must still comply with
applicable law, Meta terms, privacy obligations, and content rights.
