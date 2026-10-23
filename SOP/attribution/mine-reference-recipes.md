---
id: attribution.mine-reference-recipes
action: attribution.mine-reference-recipes
title: Mine reference posts into Attribution recipes
summary: Study ranked SocialMiner reference exports and annotate recurring patterns to create original Attribution media recipes.
version: 1
tags: [attribution, recipes, references]
aliases: []
---
# Mine reference posts into Attribution recipes

Turn a SocialMiner export of reference accounts into new **Generate media**
recipes for Attribution: studied in rank order, annotated in a fixed
vocabulary, aggregated per account, and written as original recipe entries
with filter chips. Run it every time a new export arrives; each run is one
**batch**.

The rules in `docs/reference-catalogue.md` bind every step. In short: the
reference pictures stay on this machine in git-ignored `References/`, nothing
they show is copied, and a recipe describes a recurring pattern in our own
words. No account, person, brand or signature look is named or traced.

## 0. Before you start

- The export: SocialMiner's `.xlsx` (Posts sheet) or `.json`. Note its path.
- The batch name: the export's date, e.g. `2026-10-23`. Everything for the
  batch lives in `References/brand-pages/<batch>/`.
- Fetch the same day. The picture links in an export are signed and expire
  within days.

## 1. Rank and fetch

```
python scripts/reference_mining.py rank  "<export.xlsx>" --batch <batch>
python scripts/reference_mining.py fetch --batch <batch>          # resumable
python scripts/reference_mining.py sheets --batch <batch>
```

- `rank` keeps one row per post, groups accounts by `sourceId` (display names
  vary), drops repeated slides (an export can list one picture twice in two
  renditions), orders every post by engagement and records each post's
  **percentile within its own account**.
- `sheets` writes one numbered contact sheet per post
  (`sheets/posts/<rank>_<postId>.jpg`) and per-account **profile grid** sheets
  (`sheets/grids/`, three across, newest top-left, 3:4 tiles as a profile
  shows them today), which is where a row theme or a picture split across
  several posts shows up.
- Reels keep only a cover picture in the export; judge their motion from the
  caption and transcript, and weigh them less.

## 2. Annotate every post, highest engagement first

```
python scripts/reference_mining.py overview --batch <batch> --start 1 --end 200
```

Each overview page holds four posts with their rank, account, kind, slide
count, engagement and in-account percentile. Annotate in rank order and write
one JSON per slice into `References/brand-pages/<batch>/annotations/`
(a list of objects, any file name). Large batches are split into rank slices
across several agents; every agent gets this section and the vocabulary file,
and nothing else to invent.

**Vocabulary.** `scripts/reference_annotation_vocab.json` is fixed for the
batch. Every enum field takes only its values. If a post needs a value that is
missing, stop and add it to the vocabulary in its own commit first; an
annotation never carries a private tag.

One object per post:

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

- `slides`: one entry per slide, in order, every slide.
- `outfitCount`: distinct outfits across the post. `togetherInFrame`: true when
  one picture shows two or more garments or people's outfits side by side.
- `apparelUsable`: could this pattern sell a garment, a sleepwear set or an
  outfit of ours? Food-only, text-only, stage and group shots are false.
- `reuse` 1-5: how well the pattern would carry our products with our
  character. 5 = a clear, repeatable recipe; 1 = nothing to take.
- `structure`, `props`, `styling`, `recipeIdea` are free text in our own words:
  no names, handles, brands, logos or quoted captions.
- Do not describe faces, bodies or a person's identity; describe the pose,
  framing, set and light.

Check the slice before handing it in:

```
python scripts/reference_mining.py validate --batch <batch>
```

It lists every value outside the vocabulary and every post not yet annotated.
The batch is annotated when it reports 0 problems and 0 missing.

## 3. Aggregate

```
python scripts/reference_mining.py aggregate --batch <batch> --percentile 70
```

Counts, among posts in the top 30% **of their own account** that are usable for
apparel with `reuse` of 3 or more: content types, settings, poses, framings,
the order of framings across a carousel, one versus several outfits, together
versus one product a frame, and how many accounts share each value.
Engagement is never compared across accounts of different sizes.

## 4. Choose recipes

A pattern becomes a recipe only when it passes all three:

1. **It recurs**: several strong posts show it, ideally in two or more accounts.
   A pattern from one post is a copy of that post, not a recipe.
2. **It is distinct**: no existing recipe, hand-written or mined, has the same
   kind, the same setting, the same pose family and the same card structure.
   `services/api/tests/test_product_creative_library.py` enforces this.
3. **It is safe to generate**: an adult, the garment opaque and modestly framed
   (no chest close-ups), no text or logos, no real place's signage, the
   product picture as the sole authority for the garment.

Keep the evidence (the ranks behind each recipe) in
`References/brand-pages/<batch>/recipes-evidence.md`. It stays local: it names
the accounts.

## 5. Write the entries

New recipes go in
`services/api/src/trendrelay_api/product_creative_library/batch-<batch>.json`,
one file per batch, under `"recipes"`. An entry:

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

The assembly in `product_creative_library.py` adds the rest itself: the
product-authority sentence, the covered and silent lines, the no-text line,
the character or default model, the background switch and the group wording.
Do not repeat them in an entry. Keep each wording under 4,000 characters; the
tests check it.

Facet values for chips come only from `vocabulary.json`. A new value goes
there first, with all seven labels.

## 6. Database and checks

- The draft table's `valid_product_creative_recipe` check lists every recipe
  id. A batch adds a migration that widens it with the new ids (copy the newest
  `services/api/migrations/versions/*_mined_recipes_*.py`) and the same ids in
  `product_creative_models.py`. Until the operator's runner applies it, a new
  recipe previews but cannot be queued. Tell the operator it is waiting.
- Run `python -m pytest services/api/tests/test_product_creative_library.py
  services/api/tests/test_product_creative_recipes.py` and the web tests
  (`npm test` in `apps/web`).
- Preview two or three of the new recipes through
  `POST /api/workspaces/<ws>/attribution/creative-drafts/preview` and read the
  prompt the way a reviewer will.

## 7. Hand-off

- Commit the batch file, the migration and the model's check together, with a
  message naming the batch and how many recipes it adds. Never commit anything
  under `References/`.
- Note the batch in `AGENT_HANDOVER.md`: export path, posts annotated, recipes
  added, and what is waiting on the operator.
