---
id: attribution.fill-product-creatives
action: attribution.fill-product-creatives
title: Fill pending Attribution product creatives
summary: Work the pending product-creative queue one draft at a time. Read the stored prompt and the product's image references, generate that file outside TrendRelay, and submit it. A carousel stays unlinked until every card has landed. Nothing is published.
version: 1
tags: [attribution, media, creatives, library]
aliases: [fill-product-creatives, attribution.needs-creatives, generate-product-creative]
---
# Filling pending Attribution product creatives

This is the product-side pair of `campaigns.fill-needs-media`. There the queue
is campaign posts waiting for pictures. Here the queue is Attribution products
with a reviewed prompt and no finished file yet.

Use it when an operator, or an earlier turn, queued an image, a carousel, or a
video for one product and the job is to produce that file and file it in the
Library, linked back to the product.

TrendRelay does not call an image or video model. The pixels are produced by
the client, then submitted. Confirming a draft only stores the prompt.

## 1. The queue is the pending drafts

```
list_product_creative_drafts()
```

Pending is the default. Each row names the product, the kind (`image`,
`carousel`, or `video`), the recipe, the card count, and how many files are
still owed. Page with `limit` and `offset`. Once a draft succeeds it leaves
the pending result set, so refresh from `offset: 0` after a fill.

Open one draft before generating anything:

```
get_product_creative_draft(draft_id=...)
```

That read returns the prompt stored when the draft was confirmed, the product
image references, the background reference when one was attached, the kind,
the card count, and what is still owed. Use that prompt. Do not rewrite it,
and do not recompute a prompt of your own. A later read returns the same
stored text.

## 2. What you may change here, and what you may not

`submit_product_creative_media` files one finished creative. It does not
publish, approve, schedule, or attach the asset to a campaign.

| The draft | Submit |
| --- | --- |
| `pending`, files still owed | Allowed — ingest this one file |
| Carousel, fewer files than `card_count` | Allowed — the draft stays pending and writes no new product link |
| `card_count` met by this file | The Library asset is linked both ways and the draft is succeeded |
| Already `succeeded` | Refused — the draft is filled |
| No file, or more than one source | Refused — there is no way to mark a draft complete without media |
| A file the draft's kind does not accept | Refused — no new link, draft stays pending |

Image and carousel drafts accept an image. A video draft accepts a video.
Send exactly one source: the `media` file, a public https `media_url`, or
`media_base64`. A refused or failed file writes no link.

The link is two-way and only complete. The product's read lists the Library
asset, and the asset's read lists the product, once the draft holds every
card it asked for. A two-card carousel that has one image ingested is still
pending, and neither side shows a new link.

## 3. Recipes

The recipe was chosen when the draft was queued. Read it; do not switch it.

| Recipe | Kind | Background |
| --- | --- | --- |
| `bed_flat_lay` | image or carousel | Optional. Off names the bed. On uses the attached background and the stored prompt does not say bed. |
| `mannequin_transition` | video | Optional. Off is the narrow beige hallway. On opens on the attached background and drops the hallway. |
| `mirror_selfie` | video | Required, with `variant` `female` or `male`. A mirror draft cannot be queued without a background image. |

When `subject_assets` is non-empty, those Library image ids are the subject,
in that order, at most 8. Read them with `get_asset_thumbnails`. When
`subject_assets` is empty, `product_images` (the listing gallery) is the
subject, and a product with no image cannot be queued. `listing_fields` is a
snapshot from confirm. Use a key only when it is present: `title`, `price`,
`description`, `gallery`, `variations`. An empty object means none of those
were attached. Do not read the live listing to fill in a missing key. One
draft is one garment: the product's own piece, not a collage of unrelated
tops.

A carousel's `card_count` is fixed at confirm, from 2 to 10. It cannot be
lowered. Generate that many separate images and submit them one at a time,
in order. Do not submit one picture that contains every card as panels.

## 4. One draft, then the next

1. Read the draft and generate the file the stored prompt describes. If
   `subject_assets` is non-empty, those Library images are the subject
   (`get_asset_thumbnails`). If it is empty, use `product_images`. Use
   `background_reference` when it is set. Use `listing_fields` for the keys
   that are present, and leave out any key that is absent.
2. `submit_product_creative_media` once for that file. Video ingest finishes
   in the same call; do not poll `get_import_status` for it.
3. Read the draft again. `owed` is what is left. `linked` is true only when
   the set is complete.
4. Take the next pending draft.

Finish one draft before starting another. Do not publish the asset, and do
not create a campaign post from it unless the operator asked for that as a
separate action.
