# ADR 0022: Posts carry the network's own affiliate link; internal tracking is retired for now

Status: Accepted and built - `offer_link_url` in `campaign_autopilot_api.py`
is the one place a post's link comes from. Supersedes the posting half of
ADR 0015; the redirector and its models remain for hand-made links.

## Context

ADR 0015 gave every campaign post a first-party tracking link: a `/c/{code}`
redirect minted per execution, carrying sub-IDs (content hash, platform,
campaign) so that clicks and imported conversions could answer "which video
sells". The design was sound for a hosted product. This is not a hosted
product yet.

The redirect URL is built from `attribution_public_url`, which defaults to
`http://localhost:8080`. Every caption the autopilot composed therefore
carried a link that resolves only on the operator's own machine while the
TrendRelay API happens to be running. A reader on Facebook clicking it gets
nothing; Shopee sees no visit; no commission is ever paid on it. The
machinery measured everything except reality.

Meanwhile the links imported into Attribution are Shopee's own short links
(`https://s.shopee.vn/2gAN9f0Ef6`). Shopee already counts every click and
every commission against that link in its own report. The tracking the
operator actually uses exists on the network's side, whole, with nothing to
deploy.

## Decision

**A post's link is the offer's `affiliate_url`, verbatim.** Caption, first
comment, thread reply, or bio - the same URL, the one the network pays on.
`offer_link_url` validates it (https, no credentials) and hands it over;
nothing is minted, nothing is written, previews show exactly the URL that
will be published.

**Internal per-post tracking is retired, not removed.** The `/c/` redirector,
`TrackingLink`, sub-ID assignment, and the click/conversion models all stay:
hand-made links in Attribution still work, links already in the wild still
redirect, and old executions still reconcile. What is gone is the campaign
path that minted them (`link_url_for`, `mint_post_link`) - restorable from
history when there is a public host to mint against.

## Consequences

- Per-post and per-account click measurement inside TrendRelay stops
  accruing: executions record `tracking_link_id: None`, so the scheduler's
  performance ranking sees no new evidence and, by its own evidence bars,
  declines to rank rather than ranking on stale data. Revenue truth lives in
  Shopee's report.
- The bio placement loses its one distinguishing property (a stable minted
  URL the profile could keep pointing at) and gains a simpler one: the
  profile points at the offer's own link, which does not change either.
- The Publish preview's attribution note now recognises the network's short
  links (`attribution_shopee.SHORT_HOSTS`) as tracked, because they are -
  by the network.
- Reversal is one function: give `link_for` back a minter when
  `attribution_public_url` is a host the public can reach.
