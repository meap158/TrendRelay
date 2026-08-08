# Merging Catalog into Attribution

A proposal, and the research behind it. Short version: the merge is the right
call, the data model already assumes it, and there is one thing inside it that
must **not** be merged.

## The decisive finding is in our own schema

Before looking at anyone else, it is worth knowing what we already built.
`Product` is a shared spine, and four separate features already hang off it:

| table | carries | belongs to today's |
|---|---|---|
| `Product` | `catalog_key`, `identifier`, `name`, `brand`, `marketplace`, `product_url`, `image_url` | Opportunities |
| `ProductOffer` | `product_id`, `network`, `merchant`, `affiliate_url`, `commission_bps`, `cookie_days` | Opportunities |
| `WorkEdition` | `product_id`, `identifier`, `product_form`, `assignment` | Catalog |
| `AdSpendEntry` | `product_id`, `work_id`, `campaign_key`, `spend_date`, `currency` | Catalog |
| `TrackingLink` | `product_id`, `offer_id`, `campaign_id`, `destination_url` | Attribution |
| `ClickEvent` / `Conversion` | `product_id`, `offer_id`, `tracking_link_id` | Attribution |

Every one of those rows can already answer "which product is this?" — the
tracking link, the click, the conversion, the ad spend and the book edition all
carry the same `product_id`. **The split across three pages is a UI decision
laid over a model that was designed as one thing.**

Two consequences:

- The merge is not a schema migration. It is mostly moving components and
  writing one query that joins on a key that is already there.
- Every one of those tables is currently **empty** — 0 products, 0 offers, 0
  works, 0 editions, 0 tracking links. There is no data to migrate and nobody
  has built habits around the current layout. This is the cheapest this change
  will ever be.

## What the market does

**Link managers converge on product-first, not link-first.** Lasso's pitch is
"create, manage, and optimize product links", and its differentiators are
product display boxes, link health reports that catch 404s and out-of-stock
items, and link-level click tracking — all of which are properties *of a
product*, surfaced wherever the product appears. Geniuslink solves a narrower
problem, routing one link to the right marketplace per country, which we
already do with `country_destinations`.

The pattern worth taking: **the product is the row, and links, clicks and
revenue are columns on it.** A page that lists links and makes you remember
which product each one serves is the arrangement everyone has moved away from.

**Affiliate dashboards are judged on whether the numbers explain themselves.**
The consistent advice is to state how each figure is calculated — EPC, CR, the
attribution window — in the interface rather than in documentation, and to show
local currency rather than a converted blend. We already do the harder half of
this: clicks are privacy-minimised with workspace-scoped daily HMAC pseudonyms,
conversions match the latest eligible click inside the offer cookie window, and
the measurement notes say so on the page. That copy is an asset; it should
survive the merge intact.

**Last-click is the default everyone ships**, and it is what our cookie-window
matching implements. No change needed; it is worth naming on the page, because
an unstated attribution model is the most common source of disputes.

## Recommendation

Merge, with one boundary held firmly.

### Attribution becomes the product-and-revenue surface

```
Distribution workspace
├── Delivery      accounts and scheduling      (unchanged)
└── Attribution   products, links and revenue  (Catalog folded in)
```

The page becomes one table of **products**, each row expanding to what we know
about it:

- **Identity** — name, brand, marketplace, identifier. For books, the editions
  grouped under a work, which is what `CatalogWork` and `WorkEdition` already
  express; a paperback and a Kindle edition are one row, not two.
- **Where it goes** — the affiliate offers on it, and the tracking links built
  from them, with country destinations and expiry.
- **What it earned** — clicks, conversions, commission.
- **What it cost** — ad spend, and for books the royalties, ROAS, ACoS, TACoS.

Creating a tracking link stops being a form you fill from memory. It becomes an
action on a product row, which is where Lasso's advantage actually comes from.

### The boundary: two revenue streams, never one number

This is the part to get right, and it is the one real argument against merging.

The page would carry two kinds of money that look alike and are not:

- **Affiliate commission** — someone else's product, earned per conversion,
  measured through our own tracking links.
- **Royalties** — our own books, earned per sale, driven by ad spend, and only
  ever knowable from the marketplace's own report.

They have different lifecycles, different confidence, and different currencies.
A single "revenue" figure spanning both would be wrong in a way nobody could see
— which is exactly the failure the per-currency ad-spend buckets were built to
prevent. **Keep them as two ledgers on one page, never one total**, and keep
ROAS/ACoS/TACoS attached to the book ledger where their denominators are real.

### What moves, what stays

| today | after |
|---|---|
| Catalog page | gone; its book table becomes the book ledger inside Attribution |
| Catalog spend import | a tab on Attribution, unchanged in behaviour |
| Attribution "Create a tracking link" form | an action on a product row |
| Attribution conversion import | unchanged, still its own step |
| Opportunities offer catalogue | stays — see below |

**Opportunities keeps its offer import.** It is a different job: deciding what
is worth pursuing, before anything has been published. Attribution answers what
happened after. Merging that in as well would put a research tool inside a
reporting page and give the merged page two audiences.

## Effort, honestly

The schema is done. The work is:

1. A products endpoint that joins `Product` → `ProductOffer` → `TrackingLink` →
   aggregated `ClickEvent`/`Conversion`, plus `WorkEdition` → `AdSpendEntry`
   for the book ledger. One query, all keys present.
2. A product table component with an expanding row, replacing two pages.
3. Moving spend import and conversion import under it as tabs.
4. Nav change: three items to two.
5. Retire `/catalog`, redirecting to `/attribution`.

The translation cost is real — both pages are fully translated in seven
languages, and merged copy is new copy. Reusing the existing `catalog.*` and
`attribution.*` keys wherever the wording survives keeps most of it.

## What I would not do

- **Do not blend the two revenue streams into a headline number.** Stated above,
  worth repeating; it is the only way this merge makes things worse.
- **Do not move Opportunities in.** Different question, different moment.
- **Do not rebuild the measurement notes.** The copy explaining HMAC pseudonyms,
  the cookie window and the missing click-through rate is more valuable than
  most of the UI around it, and it is the thing the market research says most
  tools get wrong.
