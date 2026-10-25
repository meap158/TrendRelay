# Reference catalogue for carousel and lookbook styles (PM-233)

Purpose: learn what works in other brands' carousels and lookbooks, as data, and turn it into ORIGINAL TrendRelay characters and
templates. The catalogue stores observations in our own words. It never stores or republishes the other brands' images.

Status (2026-10-25): the SocialMiner batch `2026-10-23` has 1,852 annotated
posts across five accounts. Validation reports zero invalid or missing
annotations. Its aggregate includes 425 apparel-usable posts in the top 30%
of their own account with reuse scores of at least 3. Three extraction passes
produced 58 mined recipes; evidence and deferred patterns are recorded in
`References/brand-pages/2026-10-23/recipes-evidence.md` (local only). Batch
`2026-10-25` adds two image recipes written from single reference posts the operator
picked, each kept to its pattern in our own words.

Batch `2026-10-26` adds eight hand-written starter recipes for products that
are not clothing; they are not mined.

The current catalogue has 81 distinct recipes: 13 built-ins, 60 from
references and 8 starters. The picker filters by type: 16 support Image, 52 Carousel, and 16 Video;
some built-ins support several types, so those counts overlap. These are
implemented reusable patterns, not an estimate of every pattern the export
could support. Further extraction must establish recurrence and a distinct
setting, pose, framing, and card structure under
`SOP/attribution/mine-reference-recipes.md` rather than inflate the count with
near-duplicates.

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
Fields filled from the SocialMiner JSON export (`posts[]`, from `src/core/models.ts` in SocialMiner, read 2026-10-21):
- `postId` = `externalId` (the `id` is `instagram:<externalId>`); `url` links back to the post.
- `postedAt` = `timestamp` (Unix ms; check `timestampPrecision`, "relative" values are approximate).
- `kind` = `mediaType` (`image`, `carousel`, `reel`, `video`); `slideCount` = the length of `mediaUrls` for a carousel.
- `engagement` = `reactions` (likes and the other counts) plus `commentCount`; `viewCount` for reels.
- `captionPattern` is derived from `text` and `hashtags`.
Everything else (`structure`, `framing`, `poses`, `productsPerFrame`, `togetherSet`, `palette`, `styling`, `notes`) is our own
annotation after looking at the images locally. `mediaUrls` are only used to fetch images into `References/brand-pages/` for that
local viewing; they are never stored in the catalogue entry. For videos the export keeps only the cover image.

Convert an export into skeletons (facts filled, annotations empty; image URLs are not copied):
`python scripts/catalogue_from_socialminer.py export.json --source hemell_official --out catalogue-hemell.json`

## From references to recipes
`SOP/attribution/mine-reference-recipes.md` is the one procedure for turning reference media into recipes, whether a whole
export is mined or the operator shares single posts. Its annotation format (`scripts/reference_annotation_vocab.json`) is the
one recipe work uses; the per-post entry above predates it.

PM-233 first planned three original characters and three FrameMotion carousel templates from this catalogue; the recipe
library replaced the templates.

## Open items
- Continue future extraction from the existing batch annotations, aggregates,
  local contact sheets, and recipe evidence; the initial export and annotation
  pass are complete.
- Owner: confirm that "pribng" meant pricing; if so add a price-positioning column per page (price band shown, if any).
- Confirm on the first real export that Instagram carousels list every slide in `mediaUrls` (SocialMiner's docs say they do).
- Decide how TrendRelay invokes FrameMotion (package import vs `npm run carousel`).
