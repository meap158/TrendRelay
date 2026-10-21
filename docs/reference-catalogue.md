# Reference catalogue for carousel and lookbook styles (PM-233)

Purpose: learn what works in other brands' carousels and lookbooks, as data, and turn it into ORIGINAL TrendRelay characters and
templates. The catalogue stores observations in our own words. It never stores or republishes the other brands' images.

Status: schema only. No page has been catalogued yet; that waits on a SocialMiner export (PM-234) of the reference pages:
rilly_emulie_official, anmille.official, hemell_official (Instagram).

## Rules (read first)
1. Reference images stay local and git-ignored (`References/brand-pages/`, already covered by the repo's `/References/` ignore, never committed, never uploaded, never used to train or
   fine-tune a model, never fed to an image model as an image prompt).
2. A catalogue entry describes a post in words and numbers. It must not be detailed enough to recreate a look-alike of a real
   person or of a brand's signature character. Describe a pose as "weight on one leg, hand on hip", not as "the pose of model X".
3. New characters and templates are original: new faces, names, outfits and colour palettes, none of them tracing or closely
   following a reference. If a result looks like a specific reference, change it.
4. Products shown in "together" templates are OUR products from our catalogue only.
5. Data comes from the owner mining the pages in their own signed-in browser (SocialMiner), at human pace. No bulk scraping and no
   workaround for a login or bot check.

## One catalogue entry per post
```json
{
  "source": "hemell_official",
  "postId": "from the SocialMiner export",
  "postedAt": "2026-09-09",
  "kind": "single | carousel | reel",
  "slideCount": 5,
  "engagement": { "likes": 0, "comments": 0 },
  "structure": ["cover: full-length look", "detail close-up", "second colour way", "flat lay", "call to action"],
  "framing": ["full length", "waist up", "detail"],
  "poses": ["weight on one leg, hand on hip", "walking, mid-stride"],
  "productsPerFrame": [1, 1, 1, 3, 0],
  "togetherSet": true,
  "palette": ["warm neutral", "off-white backdrop"],
  "styling": ["layering", "oversized knit over slip dress"],
  "captionPattern": "short line + 3 hashtags",
  "notes": "free text, our own words"
}
```
Fields `postId`, `postedAt`, `kind`, `slideCount`, `engagement` come from the export; the rest are our own annotations. The exact
export field names are to be confirmed when the first export exists.

## From catalogue to original assets
1. Aggregate: which `structure`, `framing` and `productsPerFrame` patterns recur in the higher-engagement posts. Engagement is
   not controlled for follower count, so use it only to rank posts within the same page.
2. Original characters: three, each with our own look sheet (face, hair, body type, signature outfit, palette). Poses come from
   a pose library written in words and drawn fresh.
3. Templates: a single-product template (cover, detail, colour ways, call to action) and two "together" templates for 2-4 of our
   products. Each template is a data entry in FrameMotion's carousel style registry (`src/lib/carousel-styles.ts`, PM-208) plus
   a slot layout.
4. Acceptance (PM-233): catalogue for 3 pages with 20+ posts each, 3 original characters, 3 templates.

## Open items
- Owner: run the SocialMiner mine of the three pages and give the export path.
- Owner: confirm that "pribng" meant pricing; if so add a price-positioning column per page (price band shown, if any).
- Decide how TrendRelay invokes FrameMotion (package import vs `npm run carousel`).
