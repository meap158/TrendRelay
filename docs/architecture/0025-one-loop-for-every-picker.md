# ADR 0025: Every picker reads its origin through one shared loop

Status: Accepted and built - `apps/web/lib/use-library-assets.ts`,
`apps/web/lib/asset-filters.ts`, `apps/web/app/ui/asset-filters.tsx`,
`apps/web/app/publish/offer-rows.ts`.

## Context

TrendRelay's tabs pick from each other: Publish and Campaigns pick media from
the Library, Publish and Discover pick offers from Attribution, the Library
hands media to Campaigns. Each picker had grown its own copy of its origin's
machinery, and an audit found them drifted four ways: the campaign media
browser had paging and honest counts but the Publish picker stopped silently
at forty rows with no total and no stale-response guard; the Library page had
sort but no paging; the effect gallery asked for more rows than the endpoint
allows and rendered the 422 as an empty dropdown; the Attribution table
filtered products through an untested inline copy of the rules the offer
picker's tested module already had; and the campaign create and settings
dialogs searched the same offer catalogue two different ways.

The failure mode is structural, not individual: a feature added to an origin
tab lands in whichever pickers somebody remembers, and every picker born as a
copy is born already behind.

## Decision

**The loop is shared; the surface is not.** What every media picker needs -
the filter model and its one serialiser (`lib/asset-filters.ts`), the fetch
loop with its debounce, generation guard, offset paging, dedupe merge, facets
and totals (`lib/use-library-assets.ts`), and the filter control
(`ui/asset-filters.tsx`) - lives in `lib/` and `ui/` and is composed by each
surface. What surfaces genuinely disagree about - what selecting means (one
clip, an ordered carousel, a bulk set), what they open on, which kinds they
can use - stays theirs, passed as the hook's `baseline` and `keep` and their
own selection state. The Publish `MediaPicker` and the campaign media browser
both read through the hook; a capability added to it reaches every picker at
once.

**Both select-all reaches are the hook's, with stated ceilings.**
`fetchAllMatching` walks whole assets to `SELECT_ALL_ASSET_CEILING` for a
composer that needs rows; `fetchMatchingIds` takes the server's
`/assets/ids` fast path (its own `MAX_SELECTABLE`) for a page that acts on
ids. The two ceilings had drifted apart as private constants; they are now
named exports beside the loop they bound, and the interface states them
rather than stopping quietly.

**Rules live in tested modules, at every grain a surface asks them.**
`offer-rows.ts` answers "does this offer match" for the picker and now "does
this product match" for the Attribution table, with tests pinning the answers
the two must agree on. The rule generalises: when a picker and its origin ask
the same question of different shapes, the module grows a matcher per grain
rather than a surface growing a copy.

**A picker is added by composing, not copying.** A new surface that lists
Library media starts from `useLibraryAssets` + `AssetFilters`; one that lists
offers starts from `offer-rows.ts`. A feature added to an origin tab is added
to the shared module first, where every picker inherits it.

## Consequences

- Publish's picker gained the debounced search, stale-response guard, full
  pages, totals and Load more it lacked; the campaign browser shed ~150 lines
  of private machinery and kept every feature; the gallery dropdown loads
  again and surfaces a refused read instead of rendering its silence.
- Known remaining seams, recorded rather than hidden: the Library page itself
  still runs its own loop (with sort and ids-select-all the hook already
  supports - adopting it is mechanical); offer pickers are fed by two
  different catalogues (`/attribution/products` rich vs
  `/opportunities/offers` flat) and the campaign "Add products" surface is
  still bespoke - reconciling those catalogues is the precondition for one
  offer browser; thumbnails are being unified separately through
  `lib/media-preview`.
- The audit that found the drift is repeatable: compare each picker's feature
  set against its origin tab and against `use-library-assets` - anything an
  origin has that the shared modules lack is the gap to close next.
