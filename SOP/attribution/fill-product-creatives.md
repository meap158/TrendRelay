---
id: attribution.fill-product-creatives
action: attribution.fill-product-creatives
title: Fill pending Attribution product creatives
summary: Work the pending product-creative queue one draft at a time. Read the stored prompt and the product's image references, generate that file outside TrendRelay, and submit it to the Library before the task is done. A carousel stays unlinked until every card has landed. Nothing is published.
version: 7
tags: [attribution, media, creatives, library]
aliases: [fill-product-creatives, attribution.needs-creatives, generate-product-creative]
---
# Filling pending Attribution product creatives

This is the product-side pair of `campaigns.fill-needs-media`. There the queue
is campaign posts waiting for pictures. Here the queue is Attribution products
with a reviewed prompt and no finished file yet.

Use it when an operator, or an earlier turn, queued an image, a carousel, or a
video for one product, or one shot of several products, and the job is to
produce that file and file it in the Library, linked back to every product on
the draft.

TrendRelay does not call an image model. A video is generated and filed by
`scripts/fill_creative_videos.py`, and section 5 is the whole of that
procedure. Confirming a draft only stores the prompt.

## The file is not done until it is in the Library

Generating the picture or the video is one step. The same task then uploads
that file into the Library. A file that exists only on disk, in the chat, or
on a still-pending draft is unfinished.

1. `submit_product_creative_media` with the finished file. Send exactly one
   source. Image and carousel drafts take an image. A video draft takes a video.
2. Read the draft again. Done means `status` is `succeeded`, `owed` is 0,
   `linked` is true, and the response names a Library asset id.
3. A together draft submits once. That one asset is linked to every product
   in `products`.

Report the creative done only after that read. Queueing with
`create_product_creative_draft` stores the prompt and does not ingest.

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

For `character_editorial_carousel`, use the portrait and character sheet returned
with the draft to lock identity. Listing images are the product authority; the
character sheet outfit is replaced by the product. Research informs poses and
framing, without treating the Instagram model as the generated character.
Generate five separate finished images in the stored card order, never a grid
or contact sheet. Submit each image through the same draft; reread after each
upload to establish the next unfilled card and avoid duplicate submissions.


The recipe was chosen when the draft was queued. Read it; do not switch it.

No recipe requires a background, and every recipe can feature a Library
character. `hikaru_lookbook` and `hikaru_walk` show Hikaru only when no
character is chosen; with one, the stored prompt describes that character
instead and carries no Hikaru profile.

Every video wording says nobody speaks or mouths words, and that the clip has no dialogue, narration, or voice-over. Image and carousel wordings do not. When a video draft features a Library character, that same sentence follows the character line.

| Recipe | Kind | Background |
| --- | --- | --- |
| `bed_flat_lay` | image or carousel | Optional. Off names the bed. On uses the attached background and the stored prompt does not say bed. |
| `mannequin_transition` | video | Optional. Off is the narrow beige hallway. On opens on the attached background and drops the hallway. |
| `mirror_selfie` | video | Optional, with `variant` `female` or `male`. Off is a tall mirror in a bright bedroom. On uses the attached background as the room. With a character, the character takes the mirror's place, face still hidden behind the phone. |
| `mannequin_walkaround` | video | Optional. Off is the white boutique corner with the black pedestal. On uses the attached background. |
| `styled_flat_lay` | image or carousel | Optional. Off is the round cream ottoman. On uses the attached background. |
| `flat_lay_reveal` | video | Optional. The same styled flat lay as a handheld rise from the fabric to the whole outfit. |
| `sleepwear_lookbook` | carousel | Optional. Card 1 is the hanger, card 2 the fabric, later cards the model. Only the model's room follows the background. |
| `character_editorial_carousel` | carousel, exactly 5 cards | Optional. Requires a selected Library character. Same adult identity, set and lighting across hero, seated, detail, side and front views. At most 5 products; one product per frame in input order. |
| `held_up_garment` | video | Optional. Off is a warm room with walnut panelling, the holder's face out of frame. On uses the attached background. With a character, the character holds the garment, face visible. |
| `hikaru_lookbook` | image or carousel | Optional. Shown as Model lookbook. The stored prompt carries the model's full character profile, including a real person on camera. The garment is opaque: no nipples through the fabric, and no camel toe. Keep her face, hair, and build the same on every card. |
| `ceiling_fan_transition` | video | Optional. Off is the living-room rug in slatted sunlight. She lies on the centre line, head toward the top of the frame, and presses the remote once. The camera stays fixed. One blade covers her completely and reveals one whole outfit. With one product that blade waits until late in the clip, and that pass is the only change. No text on screen. The garment is opaque. |
| `hanger_rail_reveal` | video | Optional. Off is the dark wooden rail against a white wall. A hand lifts the top away to show the bottoms, then the fabric in close-up. |
| `hikaru_walk` | video | Optional. Shown as Model walk. Off is a Tokyo street at night. She stops and looks into the camera with her mouth closed. Nobody speaks. The stored prompt carries the model's full character profile, including a real person on camera. The garment is opaque: no nipples through the fabric, and no camel toe. |

When `subject_assets` is non-empty, those Library image ids are the subject,
in that order, at most 8. Read them with `get_asset_thumbnails`. When
`subject_assets` is empty, `product_images` (the listing gallery) is the
subject, and a product with no image cannot be queued. `listing_fields` is a
snapshot from confirm. Use a key only when it is present: `title`, `price`,
`description`, `gallery`, `variations`. An empty object means none of those
were attached. Do not read the live listing to fill in a missing key. One
draft is the product's own item - one garment, accessory, bottle or pack -
not a collage of unrelated ones. A product that is not clothing keeps its
label and printed text exactly as in its pictures: never retype, translate,
or invent lettering on it, and add none beside it. A draft can instead feature up to eight products in one shot. Read
`products`. Each product's `product_images` stay the subject, and
`subject_assets` are extra when present. `product_images` is the set the
operator kept. When that choice was left unset, it is the full listing
gallery. Do not add a listing picture that is not in `product_images`.
Each item in `products` names that product and carries its own `product_images`
and `listing_fields`. Keep a picture or a field with the product it is listed
under. The top-level `product_images` and `listing_fields` repeat only the
first product.
Submit the one file once; it links to every product in `products`.

A carousel's `card_count` is fixed at confirm, from 2 to 10. It cannot be
lowered. Generate that many separate images and submit them one at a time,
in order. Do not submit one picture that contains every card as panels.

## 4. Generation order: finish one draft, then the next

Choose the next draft in this order (operator instruction, 2026-10-25):

1. Prioritize templates/recipes with no completed media yet. Check completed
   drafts and linked Library media; a pending prompt is not existing media.
2. Within that priority, prefer drafts featuring a selected character, then
   character drafts among the remaining templates.
3. Alternate products between completed drafts when eligible drafts are
   available at the same priority. Rotate products without splitting a draft.

Completion takes precedence once a draft has started. Generate, review, and
submit every remaining card in stored order, then verify `status: succeeded`,
`owed: 0`, `linked: true`, and Library asset ids before selecting another
draft. For a five-card draft, finish all five; never generate one of five and
move to another draft or product to satisfy the rotation. Resume an already
started draft before opening a fresh one. If a provider or reference blocker
prevents completion, record the draft id, completed cards, remaining cards,
and reason before moving on; preserve its progress and do not claim it done.

Apply the priorities when choosing the next whole draft, not the next image.

1. Read the draft. A video draft is filled by the command in section 5, which
   submits the file itself. For an image or a carousel, generate the file
   the stored prompt describes. When
   `products` lists more than one product, each product's `product_images`
   are the subject and `subject_assets` are extra. Otherwise, if
   `subject_assets` is non-empty, those Library images are the subject
   (`get_asset_thumbnails`). If it is empty, use `product_images`. Do not
   add a listing picture that is absent from `product_images`. Use
   `background_reference` when it is set. When `products` lists more than one
   product, use each product's own `listing_fields` for the keys present on
   that product. Do not give one product another product's title, price,
   description, or pictures. Otherwise use the draft's `listing_fields` for
   the keys that are present, and leave out any key that is absent.
2. For an image or a carousel, `submit_product_creative_media` once for that
   file. This upload is part of the task, including when you queued the draft
   yourself in the same turn. A video whose section 5 command printed `OK` is
   already submitted. Do not submit it again.
3. Read the draft again. `owed` is what is left. `linked` is true only when
   the set is complete. An image or a video is done when `status` is
   `succeeded` and the Library asset id is known. A carousel stays on this
   draft until `owed` is 0.
4. Take the next pending draft.

Finish one draft before starting another. Do not publish the asset, and do
not create a campaign post from it unless the operator asked for that as a
separate action.

## 5. Video

Run one of these commands from the repository root. Do this instead of calling
a video tool, writing a prompt, or starting the API, the worker, or Grok Build.
The operator asking for the clip is the confirmation. Run the command. Do not
ask again, and do not choose among providers.

`setup-zdr` is once per machine, and only for the Grok video tools. `fill`
does not read its file. If the command prints `present`, stop.

```
python scripts/fill_creative_videos.py setup-zdr
```

A video draft that is already pending:

```
python scripts/fill_creative_videos.py fill --draft <draft_id>
```

A recipe the operator asked for, with no draft yet. Repeat `--recipe` for
several. The command runs them one after another.

```
python scripts/fill_creative_videos.py fill --product <product_id> --subject-asset <asset_id> --recipe <recipe>
```

The subject is one clean Library photograph of the product. Pass a photograph
of the product itself. A listing card that carries a text panel, a logo, or a
second inset photo is copied into the clip, lettering included.

What the command does:

- Talks to `http://127.0.0.1:8011`. When exactly one workspace answers, it
  uses that workspace. When the command asks for `--workspace`, pass the id
  it printed.
- Creates a draft with the background off, listing fields `title`,
  `description`, and `gallery`, and that one Library subject. The first
  subject is sent as a reference, not as a locked first frame. A draft
  cannot gain a subject after confirm.
- Reads the stored prompt. A ceiling-fan prompt has to include the single
  late pass. A model lookbook or model walk prompt has to include the opaque garment and the
  real-person lines. A miss means the API is still serving the previous
  recipe module. The command waits briefly for the dev supervisor to reload
  `services/api/src`, then fails with `stale`. Run the same command again.
  Leave the prompt as stored.
- Sends one xAI request at a time. The clip is 10 seconds and vertical. A
  recipe that describes a longer sequence still gets those 10 seconds. The
  request carries a presigned upload into the Publish bucket at
  `product-clips/<draft_id>.mp4`.
- Saves `.data/creative-videos/<draft_id>.mp4` and submits that file. A later
  `fill --draft` for the same id submits the local file, or the object
  already in the bucket, and does not send another request.
- Prints `OK <recipe> <draft_id> <asset_id>` when the draft is `succeeded`,
  `owed` is 0, and `linked` is true. That line is the creative being done.
- Prints `FAIL <recipe> <draft_id> <code> <message>` otherwise, and exits
  non-zero if any draft failed. `moderation` discards that draft and is final
  for it. Leave the prompt alone, and do not run that recipe again.
- Does not publish, and does not attach the asset to a campaign.

A draft of several products is refused before anything is sent. Its file
still comes from outside, as sections 1–4 describe. An image or a carousel
does too. The in-app Generate button stays hidden until Tools has a saved
key, the switch on, and a passing check. This command does not turn those on.

### Register externally generated characters

After generating a new fictional character through the imagegen workflow, register its reviewed portrait and consistency sheet before using it in Attribution. POST `/api/workspaces/{workspace_id}/characters/register/images` with name, appearance, outfit, portrait_base64 and sheet_base64. This imports both originals into Library and creates a ready character without another image-provider call. For references already in Library, POST `/characters/register` with portrait_asset_id and sheet_asset_id instead. Both references must be distinct images in the same workspace. Repeating the same pair returns the existing character. Verify GET `/characters` includes its ready record and both asset IDs before claiming it is available in Generate a creative. Preserve fictional provenance in appearance; do not relabel real-person references as fictional.


Character creation and appearance revisions must follow [Create or revise a photorealistic character](../creations/create-character.md), including distinct identities, portrait-first consistency sheets, Library registration and ready-picker verification.

