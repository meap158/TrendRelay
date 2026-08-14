# ADR 0020: Read Shopee offers through the operator's own session, or not at all

Status: Accepted and built - export and link parsing, the cookie session and its
probe, the product bridge, background enrichment, and the Attribution import view.

## Context

Shopee is the network these affiliate links actually come from, and the offer
page is where an operator picks products. Getting from that page to a postable
tracking link meant retyping every product by hand.

Three facts about Shopee shape what is possible, and the first two rule out the
obvious approach:

- Its item APIs answer `403` to anyone who is not signed in.
- Its product page ships as a shell. There is no `og:` metadata, no JSON-LD, no
  `__INITIAL_STATE__`, not even a title - the product is drawn by JavaScript
  after load. Rendering that shell anonymously in a real browser reaches
  "Cần đăng nhập".
- Its affiliate offer page exports a bulk CSV carrying the product id, name,
  shop, price, commission rate, commission amount, product URL and affiliate
  URL. Everything a link needs except the picture.

So the export is the good path and it needs no credentials at all. Only the
images require being signed in, and images are the one field a post cannot do
without.

## Decision

- **No anonymous scraping, and no scraping at all without a session.** There is
  nothing to scrape: the page carries no product data. Anything that appeared to
  work would be reading a login wall.
- **The export is the primary import, and works with no session.** Rows are
  keyed on shop and item so re-importing a re-downloaded export adds only what
  is new. Pasted share links are accepted alongside it and filed under a
  placeholder name that says what is known, because a link nobody has enriched
  yet is still a link worth keeping.
- **A tracking link is minted once per offer and never re-minted.** The first
  link is already in a video somewhere; a second would split that product's
  history in two and the first cannot be recalled.
- **Money is stored in minor units through `money.py`, never in hundredths.**
  Shopee Vietnam sells in dong, which has no subunit, so `price / 100` misstates
  every figure by a hundred. The same ISO 4217 digit table is mirrored in
  `apps/web/lib/minor-units.ts`; the only thing worse than one wrong scale is
  two that differ. No conversion between currencies happens anywhere.
- **Reading a product borrows the operator's own Shopee session**, the way
  Douyin's downloads already do. Cookies only, never a password, stored under
  git-ignored `.data/` on the local machine.
- **A session may be captured by signing in, or pasted as a Cookie header.**
  Capturing is preferred because Shopee stamps expiry dates on those cookies,
  and a pasted header carries none - so only a captured session can be called
  tired before it fails. Pasting stays for machines with no browser runtime or
  no screen. The sign-in window is Shopee's own login page; the app never
  handles the credentials, only the cookies that result.
- **Cookies Shopee rotates are written back.** There is no refresh token and no
  password held anywhere, so a login cannot be renewed without the login. What
  can be done is keeping what Shopee hands back, which is the difference between
  a session that lasts its full term and one that expires early because every
  request replayed its first day.
- **Failures are told apart.** Only authentication-shaped failures say
  "reconnect"; a timeout says it timed out. Reporting every failure as "sign in
  again" teaches people to ignore the one time it is true.
- **A probe walks the whole path and stops at the first failure.** A session
  that is absent, one that is expired, one that cannot reach the page, and a
  page that parses to nothing are four different problems with four different
  fixes, and "the import did not work" is not something anybody can act on.
- **The page read runs in a separate process** with a scoped environment. The
  browser runtime lives in its own virtualenv, the session is handed in on
  stdin rather than read from disk there, and a failing subprocess is quoted
  redacted - a failing subprocess is exactly what ends up in a log.
- **That process re-checks the URL it is given.** It is handed a live session,
  so where it points is not taken on trust from whatever assembled the request.
  The host must match Shopee's own domains anchored at a label boundary;
  `shopee.vn.example.test` reads as Shopee at a glance and a substring check
  would hand it the session.
- **A login wall is reported as its own flag**, not inferred from an empty
  result, because a login wall and a changed layout are different problems.
- **Enrichment is a background job, one per product, capped per import.** A page
  read is a browser render and takes about a minute; an export of two hundred
  would hold an HTTP request open for hours and lose everything if it dropped.
  One job per product means a taken-down page costs that product its image
  rather than costing the run. The cap exists because a queue taking six hours
  to drain is one nobody trusts; the rest keep their export data and enrich on
  the next import.
- **Enrichment fills gaps and never overwrites.** Imports are re-run routinely,
  and a run that undid a corrected name or a chosen image would be worse than
  one that did nothing. Image URLs are accepted only over https, since that URL
  is handed to a publishing engine to go and fetch.
- **Progress is askable separately from the import that queued it.** The import
  returns in a second and the pages take a minute each, so by the time anything
  has gone wrong the response that started it is long gone.

## Consequences

An operator gets from Shopee's offer page to a batch of tracking links by
pasting one export, with no credentials involved. Connecting a session adds the
images and nothing else, which is why it is offered rather than required, and
why every refusal along that path says which step failed instead of reporting
that the import did not work.

The bridge's extraction selectors are written against what Shopee renders today
and are the one part of this that cannot be tested without a live session. They
are also the part most likely to rot: the probe's last stage exists so that when
they do, the answer names which fields stopped being readable rather than
leaving images silently missing.
