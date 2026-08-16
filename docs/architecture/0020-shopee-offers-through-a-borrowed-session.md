# ADR 0020: Import Shopee offers through operator-exported files

Status: Accepted and built. This 2026-08-15 revision supersedes the automated
session workflow described in the historical implementation notes below.

## Current decision

Shopee's own Product Offer CSV export is the supported bulk boundary. The
operator opens Shopee in their normal browser, signs in directly with Shopee,
selects at most 100 products, exports the `.csv`, and gives that file to
TrendRelay. TrendRelay previews and validates the batch before it mints any
tracking links.

The import does not capture a cookie, automate a logged-in browser, or depend
on passing Shopee's CAPTCHA. This is a reliability boundary: a verification
challenge is specifically designed to stop automation and cannot be treated as
a normal setup step. The API retains the older session bridge for compatibility
and investigation, but its probes are headless and never open a window. A
blocked probe reports that the CSV export is required. The product UI does
not advertise the bridge or automatically enqueue product-page reads after an
CSV import. A browser opens only after the operator explicitly selects
**Open Shopee Product Offer**.

The file path accepts at most 100 products and 5 MB over the browser API,
decodes UTF-8 CSV with or without a byte-order mark, supports Vietnamese and
English headings, and previews readable, new, existing, duplicate, and
problematic rows. `.xlsx` remains a compatibility input and its decompressed
size is bounded. A few HTTPS Shopee product links remain a secondary input.

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

So the export is the good path and it needs no credentials at all. What a
session buys is the download step and the pictures: signed in, the same offer
data can be read from the page directly, and the product pages that carry the
images become readable. Images are the one field a post cannot do without,
which is why a session is worth offering - and why nothing here requires one.

## Superseded implementation notes

- **No anonymous scraping, and no scraping at all without a session.** There is
  nothing to scrape: the page carries no product data. Anything that appeared to
  work would be reading a login wall.
- **Three ways in, one destination.** A pasted link, a downloaded CSV, and the
  offer page read directly all become the same rows and go through the same
  importer. That is what makes them safe to mix: one deduplication rule, so a
  product that arrived by link and later by export is one product, and a batch
  re-run adds only what is new.
- **The offer page is read as JSON, not as markup.** It is a JavaScript
  application whose generated class names change without notice, so its DOM is
  the wrong surface. What is recorded instead is the payload its own front end
  fetches, and the endpoint serving that payload is not pinned either - Shopee
  versions those paths, and a hard-coded one fails silently and looks like an
  empty account. Every JSON response from the affiliate host is walked and
  anything offer-shaped is kept.

  **Measured against the live site, this does not currently reach the offers,
  and a CAPTCHA is not the only reason.** Probed on 2026-08-16 with a valid
  session, in the browser runtime, against `/offer/product_offer`:

  - Headless, the read is refused with a verification challenge, as the
    reliability boundary above predicts.
  - Headful, the same read passes: no challenge, no login wall, exit 0. So the
    challenge is triggered by headlessness rather than by the session.
  - Headful *and unblocked*, it still harvested zero offers. Seven JSON
    responses arrived and every one was configuration or account state -
    `config/website`, `user/status`, `user/profile`,
    `user/check_program_permission`, `offer/checkInAmsWhiteList`,
    `version.json`, `inbox_message/unread_num`. The product list was rendered
    on screen throughout and never appeared among them.

  The field-name mapping was therefore never reached, and correcting it would
  fix nothing. Whatever carries the list is not a JSON response on that host
  within the window the bridge watches. That is the open question if anyone
  revisits this; until it is answered, the bridge cannot read offers at all and
  the CSV export is not a fallback but the only path.
- **"Nothing found" is told apart from "not signed in" and from "the payload
  changed".** Only the last of those means going back to the CSV, and reporting
  all three the same way would send somebody to fix the wrong thing.
- **Money from Shopee's own APIs has its scale detected, not assumed.** Amounts
  arrive in a convention its front end divides before display, and the same
  field arrives already divided on other payloads. Commission rates arrive as a
  fraction, a percentage, or scaled, and are read by magnitude on the grounds
  that no real rate exceeds 100%. A figure a hundred thousand times out is the
  one error nobody catches on a screen, because it merely looks like a big
  number.
- **Exporting files nothing.** Looking at what is on offer and committing to it
  are different acts, and creating a product and minting a tracking link for
  every row as a side effect of wanting a spreadsheet would be a surprise
  nobody asked for. A hundred rows at a time, matching what Shopee's own page
  hands out, because the reason to export is to work on the rows elsewhere and
  a file that took ten minutes to assemble is one nobody waits for.
- **The legacy TrendRelay-generated export is a hand-written workbook.** A
  `.xlsx` is a zip of XML and one sheet of values is small enough to write
  directly. That compatibility endpoint writes IDs as text so opening the file
  in Excel cannot turn item 57860887539 into 5.78609E+10. Shopee's own CSV is
  different: TrendRelay reads its UTF-8 text directly rather than asking Excel
  to guess its encoding or coerce identifiers.
- **The CSV export needs no session, and stays the path when there is none.**
  Rows are keyed on shop and item so re-importing a re-downloaded export adds
  only what is new. Pasted share links are accepted alongside it and filed under
  a placeholder name that says what is known, because a link nobody has enriched
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
choosing one export, with no credentials shared with TrendRelay. The additional
download step is intentional: it keeps the durable path independent of
Shopee's changing anti-bot checks. Product images are imported when the export
contains them; TrendRelay does not promise to scrape missing images afterward.

The retained bridges are the part of this that cannot be tested without a live session:
the product one reads a rendered page, and the offer one recognises records by
the field names Shopee happens to use. Both are written against what Shopee
serves today, and both are the part most likely to rot. That is what the probe's
last stage and the "payload changed" answer are for - when they do rot, the
failure names which fields stopped being readable, rather than leaving images
silently missing or an account looking empty.
