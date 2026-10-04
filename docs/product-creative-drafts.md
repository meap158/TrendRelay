# Product creative drafts

A pending prompt for one Attribution product, filled later into the Library and
linked both ways. Added 2026-10-03. Read this before changing Generate, the
creative HTTP routes, or the MCP tools that fill them.

## Why

Attribution already knew the product and its pictures. The bed flat-lay and the
two video scripts lived only as reference files. An operator, or an external
assistant, needed a place to review the exact prompt, queue it against that
product, and file the finished image or video in the Library without publishing
it or attaching it to a campaign.

Image drafts are still filled from outside. A video draft can be filled by
any video provider that is switched on and whose key Check has accepted, from
the Video generation card in Tools. With nobody ready, the file still comes
from outside. Either way the bytes go through Library ingest, the same way a
campaign post waits for media. The same ready providers also appear on a
Library image, in the editing row. That path files a new video and does not
write a product link.

## Shape

- **`product_creative_recipes.py`** — the only prompt resolver. `bed_flat_lay`
  (image or carousel, background optional), `mannequin_transition` (video,
  background optional), `mirror_selfie` (video, background required, variant
  `female` or `male`). There is no mirror wording without a background. The bed
  prompt names a bed only when background is off. The mannequin prompt includes
  the narrow beige hallway only when background is off.
- **`product_creative_models.py`** — `ProductCreativeDraft` (the stored prompt,
  kind, card count, staged asset ids, `subject_asset_ids`, `listing_fields`,
  status `pending` or `succeeded`), `ProductCreativeDraftProduct` (one row per
  product in the shot, including a single-product draft), and
  `ProductCreativeLink` (one row per product and Library asset). Migrations
  `20261003_0084`, `20261004_0085`, `20261004_0086`, and `20261004_0087`.
  `0087` backfills one member row per existing draft and lets one card be
  linked to every product in the shot. An empty `subject_asset_ids` means the listing gallery is
  the subject, which is what an older draft and an MCP create without a pick
  still do. `listing_fields` is the snapshot of the listing values chosen at
  confirm (`title`, `price`, `description`, `gallery`, `variations`). An empty
  object means none of them were sent. The prompt text does not include them.
- **`product_creative_drafts.py`** — preview does not save. Create stores the
  resolved prompt and commits, because MCP's `_call` closes its session and
  rolls back a flush. Submit ingests through `create_ingest_job` and
  `run_ingest_job` (source type `product-creative`), including video, in the
  same call, then commits the staged ids. A link is written only when
  `len(staged) == card_count`, and that write commits too. A failed or refused
  file writes no link and leaves the draft pending. A carousel's card count is
  2–10 and cannot be lowered; there is no update route.
- **`product_creative_api.py`** — under
  `/api/workspaces/{id}/attribution/creative-drafts`: preview, create, list,
  get, and `POST .../media`. The modal shows `draft.prompt` from that response
  and does not compose a prompt of its own.
- **`integrations/mcp/product_creatives.py`** plus four tools —
  `list_product_creative_drafts`, `get_product_creative_draft`,
  `create_product_creative_draft`, `submit_product_creative_media`. Reads or
  workspace writes. Publish and approve stay refused. SOP
  `attribution.fill-product-creatives`, routed from `SOP/MCP_GUIDE.md`.
- Product reads gain `creative_assets` and `creative_drafts`. Library asset
  reads gain `attribution_products`.

## Web

Attribution → Generate sits in the bulk bar with the other selection actions.
It is enabled for one checked row and for many, and the product detail opens
the same dialog for that one product. One confirmation queues the same recipe
for every selected product; each product keeps its own draft. When two to
eight products are selected, Together is optional: one draft features all of
them, each product's listing pictures stay in the shot, a Library pick is
extra and does not replace those pictures, and the finished file links to
every product. Picture checkboxes sit under Listing fields for one product
and for many, on Single and on Together, and only while Listing pictures is
selected. Each picture starts checked. Uncheck one to leave it out before
queue. That choice is for this draft only. The draft stores the pictures still checked,
and a later read returns those as that product's `product_images`. A product
left out of the choice, and an older draft, keep the whole gallery. The
stored gallery snapshot stays the whole listing. That one draft can take its
file in the dialog. Each-product
bulk still needs a separate file per product, so that dialog does not take a
file. Optional background is a checkbox only for the bed and mannequin
recipes. Mirror shows a required background URL and a woman/man choice, and no
off switch. Success means the asset is in the Library and both sides of the
link show it.

The modal names what the ask attaches. Subject images are chosen from this
workspace's Library with the shared Library picker (`MediaPicker`), up to 8,
in pick order. An each-product queue stores that pick on every draft as
`subject_asset_ids`. Together stores it once, as extra, and does not replace
each product's listing pictures. The background, when one is used, stays an https
URL. Title, description, and listing pictures start on. Price and variations
and stock start off. Turning a chip on or off is remembered in this browser
for the workspace, for Single and Together. A selected field is snapshotted
per product at confirm. What will be sent starts collapsed: the prompt, then
any pictures and listing fields. When more than one product is in the ask,
those pictures and fields are listed under the product they belong to. Each product stays
disabled until at least one Library image is picked. Together can be queued
from the listing pictures. The prompt text does not
depend on those images or on which listing fields are selected.

Each draft line in an expanded product row shows that draft's stored
configuration (background, subject, listing fields, and prompt), loaded by
its id when the row is open. The product list itself stays a short summary,
plus a product count when one draft features several. The expanded row names
those products. Drafts in that list are grouped under Single and Together,
and a product with only one kind still shows that heading. When the table
includes a Together draft, that draft's products stay on consecutive rows
under one heading that names Together, the kind, the recipe, and how many
products. Products whose drafts are only Single follow under a Single
heading. A product that also has a Single draft is still one row, under
Together. A product in two Together drafts is listed once, under the
larger draft. With only Single drafts on screen the table order stays as it is.
View opens the same dialog, read only. Generate media still starts a new
draft. A single draft that still owes a file can take that file from the
review.

The product table filters to those drafts once any product has one. Beside the
listing filter: All, Pending draft (a draft is not succeeded, or still owes a
file), and In Library (a creative is linked). A product can be in both. The
count on a button is how many rows that button will show with the other
filters already on. The Publish offer picker does not grow this control.

## Tests

`test_product_creative_recipes.py`, `test_product_creative_drafts_api.py`, and
`test_mcp_product_creatives.py`. Ingest tests replace `process_media` because
the pinned ffmpeg binary is not in this worktree; the job, the hash, and the
`MediaAsset` row are real. `JOB_SESSION_FACTORY` must point at the test
session or the job writes the wrong database.
