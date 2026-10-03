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

TrendRelay does not call an image or video model. The pixels are made outside
and submitted, the same way a campaign post waits for media.

## Shape

- **`product_creative_recipes.py`** — the only prompt resolver. `bed_flat_lay`
  (image or carousel, background optional), `mannequin_transition` (video,
  background optional), `mirror_selfie` (video, background required, variant
  `female` or `male`). There is no mirror wording without a background. The bed
  prompt names a bed only when background is off. The mannequin prompt includes
  the narrow beige hallway only when background is off.
- **`product_creative_models.py`** — `ProductCreativeDraft` (the stored prompt,
  kind, card count, staged asset ids, `subject_asset_ids`, status `pending` or
  `succeeded`) and `ProductCreativeLink` (one row per product and Library
  asset). Migrations `20261003_0084` and `20261004_0085`. An empty
  `subject_asset_ids` means the listing gallery is the subject, which is what
  an older draft and an MCP create without a pick still do.
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
for every selected product; each product keeps its own draft. A finished file
is submitted only while a single product is open, because each product needs
its own file. Optional background is a checkbox only for the bed and mannequin
recipes. Mirror shows a required background URL and a woman/man choice, and no
off switch. Success means the asset is in the Library and both sides of the
link show it.

The modal names what the ask attaches. Subject images are chosen from this
workspace's Library with the shared Library picker (`MediaPicker`), up to 8,
in pick order, and the same pick is stored on every selected product's own
draft as `subject_asset_ids`. The background, when one is used, stays an https
URL. Title, price, description, listing pictures, variations, and stock are
listed as not sent. Queue stays disabled until at least one Library image is
picked. The prompt text does not depend on those images.

## Tests

`test_product_creative_recipes.py`, `test_product_creative_drafts_api.py`, and
`test_mcp_product_creatives.py`. Ingest tests replace `process_media` because
the pinned ffmpeg binary is not in this worktree; the job, the hash, and the
`MediaAsset` row are real. `JOB_SESSION_FACTORY` must point at the test
session or the job writes the wrong database.
