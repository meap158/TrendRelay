---
id: attribution.mine-reference-recipes
action: attribution.mine-reference-recipes
title: Turn reference media into Attribution recipes
summary: Make original Generate media recipes from reference posts - a whole SocialMiner export mined for recurring patterns, or single posts the operator shares - written as library entries with a migration, checks and a preview.
version: 2
tags: [attribution, recipes, references]
aliases: [reference-to-recipe, media-to-recipe, create-recipe-from-reference]
---
# Turn reference media into Attribution recipes

This is the one procedure for every new **Generate media** recipe made from
reference material. It has two intake paths that meet at the same steps:

- **A. An export:** a SocialMiner export of reference accounts, ranked,
  annotated and aggregated, so that a recipe stands for a pattern that recurs.
- **B. Shared posts:** one or a few posts the operator shows in chat (a
  screenshot, a picture, a clip) and asks to be turned into recipes.

Path A runs steps A1-A3, path B runs B1; both continue at step 4.

Each run is one **batch**, named by date, e.g. `2026-10-23` for an export or
`2026-10-25` for posts shared that day. Its local evidence lives in
`References/brand-pages/<batch>/` (git-ignored).

The rules in `docs/reference-catalogue.md` bind every step. In short: the
reference pictures stay on this machine, nothing they show is copied, and a
recipe describes a pattern in our own words. No account, person, brand,
costume or signature look is named or traced.

New recipes are always library entries (step 5). The thirteen hand-written
recipes in `product_creative_recipes.py` are not a place to add more.

## A1. Rank and fetch an export

Note the export's path (`.xlsx` Posts sheet or `.json`) and fetch the same day:
the picture links are signed and expire within days.

```
python scripts/reference_mining.py rank  "<export.xlsx>" --batch <batch>
python scripts/reference_mining.py fetch --batch <batch>          # resumable
python scripts/reference_mining.py sheets --batch <batch>
```

- `rank` keeps one row per post, groups accounts by `sourceId` (display names
  vary), drops repeated slides, orders posts by engagement and records each
  post's **percentile within its own account**.
- `sheets` writes one numbered contact sheet per post
  (`sheets/posts/<rank>_<postId>.jpg`) and per-account **profile grid** sheets
  (`sheets/grids/`, three across, newest top-left, 3:4 tiles), which is where a
  row theme or a picture split across several posts shows up.
- Reels keep only a cover picture; judge their motion from the caption and
  transcript, and weigh them less.

## A2. Annotate every post, highest engagement first

```
python scripts/reference_mining.py overview --batch <batch> --start 1 --end 200
```

Each overview page holds four posts with their rank, account, kind, slide
count, engagement and in-account percentile. Annotate in rank order and write
one JSON per slice into `References/brand-pages/<batch>/annotations/` (a list
of objects, any file name). Large batches are split into rank slices across
several agents; every agent gets this section and the vocabulary file, and
nothing else to invent.

**Vocabulary.** `scripts/reference_annotation_vocab.json` is fixed for the
batch. Every enum field takes only its values. If a post needs a missing
value, add it to the vocabulary in its own commit first.

```json
{
  "rank": 5, "postId": "DVnjXa7EmLW", "account": "<account>",
  "contentType": "lookbook_editorial",
  "apparelUsable": true,
  "outfitCount": 4,
  "togetherInFrame": false,
  "slides": [
    {"framing": "waist_up", "pose": ["lean_wall", "hold_prop"], "setting": "cafe_restaurant"},
    {"framing": "three_quarter", "pose": ["over_shoulder"], "setting": "window_light_room"}
  ],
  "lighting": "soft_daylight",
  "mood": ["elegant_mature", "candid_lifestyle"],
  "structure": "cover lean with cup; profile by curtain; full length by railings; seated at window",
  "props": ["takeaway cup", "structured handbag"],
  "styling": ["blouse under pinafore", "denim with frilled top", "cap"],
  "gridRole": "standalone",
  "textOverlay": "none",
  "reuse": 5,
  "recipeIdea": "one model, four looks, one location each across a cafe and its street, quiet magazine feel"
}
```

- `slides`: one entry per slide, in order. `outfitCount`: distinct outfits.
  `togetherInFrame`: one picture shows two or more garments side by side.
- `apparelUsable`: could this pattern sell a garment of ours? Food-only,
  text-only, stage and group shots are false.
- `reuse` 1-5: how well the pattern would carry our products with our
  character. 5 = a clear, repeatable recipe; 1 = nothing to take.
- Free-text fields are in our own words: no names, handles, brands, logos or
  quoted captions. Describe pose, framing, set and light, never faces, bodies
  or identity.

`python scripts/reference_mining.py validate --batch <batch>` lists every value
outside the vocabulary and every post not yet annotated. The batch is annotated
at 0 problems and 0 missing.

## A3. Aggregate

```
python scripts/reference_mining.py aggregate --batch <batch> --percentile 70
```

Counts, among posts in the top 30% **of their own account** that are usable
for apparel with `reuse` 3 or more: content types, settings, poses, framings,
the order of framings across a carousel, one versus several outfits, together
versus one product a frame, and how many accounts share each value. Engagement
is never compared across accounts of different sizes.

## B1. Read a shared post

For each post the operator shares:

1. Ignore the platform chrome: profile picture, account name, counters,
   buttons, captions, stickers.
2. Write down, in our own words, what makes the picture work: the set, the
   light, the camera height and lens feel, the pose, the framing, how the
   garment is shown, and the mood.
3. Strip everything that belongs to the post rather than the pattern: the
   costume or outfit (our product replaces it), branded props or phone cases,
   any face, hair or identity, any text.
4. Decide the kind: the post's own kind unless the operator asks for another
   (a story frame becomes an `image` when they ask for a single image).

A sensual reference is translated through light, silhouette, pose and mood,
never through exposure: the garment stays opaque, a person is never shown
undressed, and a shadow or silhouette stays a plain, featureless shape.

## 4. Choose recipes

A pattern becomes a recipe only when it passes all three:

1. **It recurs** across several strong posts, ideally in two or more
   accounts. On path B the operator may waive this by asking for the post as
   a recipe; record the waiver in the evidence file.
2. **It is distinct**: no existing recipe, hand-written or mined, has the same
   kind, settings, poses, framings and number of beats.
   `services/api/tests/test_product_creative_library.py` enforces this; a
   product-only picture has no poses, so its framing and setting must differ.
3. **It is safe to generate**: an adult, the garment opaque and modestly
   framed (no chest close-ups), no text or logos, no real place's signage, the
   product picture as the sole authority for the garment.

Write the evidence to `References/brand-pages/<batch>/recipes-evidence.md`:
for path A the ranks behind each recipe and the deferred patterns, for path B
what each post was, what was taken and what was left out. It stays local.

## 5. Write the entries

New recipes go in
`services/api/src/trendrelay_api/product_creative_library/batch-<batch>.json`,
one file per batch, under `"recipes"`, with an `about` line saying where they
came from. A later pass over the same export is its own file
(`batch-<batch>b.json`); never edit a committed batch or its migration.

The running API reads that folder as it reloads, and a malformed entry stops
it from starting. Draft the file under `.data/` first, run the library test
against it (point `LIBRARY_DIR` at the draft), and move it into the folder only
once it loads.

| Field | What it holds |
| --- | --- |
| `id` | snake_case, unique across every recipe, never reused |
| `kinds` | exactly one of `image`, `carousel`, `video` |
| `cast` | `person` (a model or the chosen Library character) or `none` |
| `together` | with several products: `card` = one product per card or shot, `frame` = all in each picture |
| `scene` | the set when no background image is attached, in one or two sentences |
| `camera` | optional: lens, height, light, colour grade |
| `beats` | carousel: 2-10 card descriptions, in order. Image or video: exactly one shot or motion description |
| `layout_note` | optional, carousel only: replaces the "separate cards, never a collage" line, for layouts that must line up across cards |
| `facets` | chip values from `product_creative_library/vocabulary.json`: `pose`, `framing`, `setting`, `mood`, `layout` |
| `label`, `help` | the name and one-line description in all seven locales (`en vi ja zh ru fr ar`) |
| `preview` | optional: one of the generic preview drawings |

Writing the wording:

- The assembly in `product_creative_library.py` adds the product-authority
  sentence, the covered and silent lines, the no-text and no-other-people
  line, the cast or character, the expression line, the background switch and
  the group wording. Do not repeat them in an entry.
- No `she`, `her`, `he` or `his` anywhere in the file: a chosen character
  records no gender, so beats say "they" or name no one. Only the cast line
  describes the person.
- A picture with no visible person, even one with a person's shadow or hand at
  the edge, is `cast: none`; that refuses a character instead of appending one
  after "no other people in the picture".
- End an image or video beat with what happens when several products are
  attached ("With several products, ..."), matching `together`.
- Keep each assembled wording under 4,000 characters; the tests check it.
- Facet values come only from `vocabulary.json`. A new value goes there first,
  with all seven labels.

## 6. Database and checks

- The draft table's `valid_product_creative_recipe` check lists every recipe
  id. Each batch adds a migration that widens it: copy the newest
  `services/api/migrations/versions/*_mined_recipes_*.py`. The test reads the
  newest file by name, so the new one keeps `_mined_recipes_` in its name and
  sorts last. Move the previous `_BATCH` into `_OLD` under a comment naming its
  batch, put the new ids in `_BATCH`, and set `revision` and `down_revision`.
- Add the same ids to the check in `product_creative_models.py`, under a
  comment naming the batch and the migration.
- Until the operator's runner applies the migration, a new recipe previews
  but cannot be queued. Tell the operator it is waiting.
- Run `python -m pytest services/api/tests/test_product_creative_library.py
  services/api/tests/test_product_creative_recipes.py` and the web tests
  (`node --test "lib/**/*.test.ts"` in `apps/web`).
- Preview each new recipe through
  `POST /api/workspaces/<ws>/attribution/creative-drafts/preview`, with and
  without a character for a `person` recipe, and read the prompt the way a
  reviewer will.

## 7. Hand-off

- Update the status in `docs/reference-catalogue.md`. Take the counts from the
  live `GET /api/workspaces/<ws>/attribution/creative-recipes`, counting each
  recipe's `facets.format` values, rather than adding by hand.
- Commit the batch file, the migration, the model's check and the status
  together, with a message naming the batch and how many recipes it adds.
  Never commit anything under `References/`.
- Note the batch in `AGENT_HANDOVER.md`: the source (export path or shared
  posts), recipes added, and what is waiting on the operator.
