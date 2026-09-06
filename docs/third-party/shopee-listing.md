# Shopee listing reader

How TrendRelay reads a Shopee product listing, what it can and cannot get,
and the probe record behind those choices. The reader lives in
`services/api/src/trendrelay_api/integrations/shopee_listing.py` and is
driven by the `shopee_enrich` worker jobs
(`services/api/src/trendrelay_api/shopee_enrichment.py`).

## What works, and what refuses (probed 2026-09-06)

- `GET https://shopee.vn/api/v4/item/get` and `/api/v4/pdp/get_pc` refuse an
  anonymous caller outright: HTTP 403, `error: 90309999`,
  `redirect_to_error_page: true` - Shopee's bot check, on every product tried.
- The product page itself (`https://shopee.vn/product/{shop_id}/{item_id}`)
  answers a plain GET with browser-like headers: HTTP 200, ~900KB, and embeds
  the product-details module's entire initial state in a
  `<script type="text/mfe-initial-data" data-module="<base64>">` tag whose
  decoded module id is `pcmall-productdetailspage`.
- Both public URL forms carry the ids: `/product/{shop}/{item}` and the SEO
  slug `...-i.{shop}.{item}`.

## What a signed-out page says

Present and captured whole: title, full description, the complete image
gallery, brand, live discount percent, category trail, attributes, tier
variations with their option thumbnails, model names, shop vouchers (code,
minimum spend, value - VND at Shopee's internal 100000 multiplier), shop
location, video flag, original listing date.

Withheld and recorded as withheld, never guessed: price, stock, sold count,
rating. These arrive as nulls in the anonymous state and load after login on
the page itself. The reader lists them in `withheld_signed_out`; the price
of record is the export's. No evasion is implemented or intended - the same
posture as the Douyin wall.

## How it behaves

- One polite request per product, 2.5 seconds apart
  (`LISTING_DELAY_SECONDS`), drained by the worker's own listing lane beside
  the rest of the pass.
- A page that comes back without the product state (the challenged shell)
  raises `ListingUnavailable`; the enrichment job then falls back to the
  signed-in browser bridge (`integrations/shopee_session.fetch_product`),
  which yields an image and a name only.
- The distilled record is stored on `products.listing` with
  `listing_fetched_at` beside it; refreshes replace the snapshot whole.
  Bounds are sanity rails only (60 images, 40k description characters).
- Image hashes become URLs through
  `https://down-vn.img.susercontent.com/file/{hash}` - the host the page
  itself uses.

## Relation to ADR 0020

ADR 0020 records that the affiliate *offer list* cannot be scraped - headless
is challenged, headful never receives the list - and the `.xlsx` export is
the only working path for offers. That still holds. This reader answers a
different question: given a product URL the export already named, what does
its public listing page say. The two are complementary: the export supplies
identity, price and commission; the listing supplies everything else.
