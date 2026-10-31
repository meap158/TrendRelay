---
id: attribution.manage-library-data
action: attribution.manage-library-data
title: Inspect and manage Attribution data and product media
summary: Use the Attribution GUI tools for tracking, conversions, product tags, stored listings and complete product-creative workflows.
version: 1
tags: [attribution, products, library]
aliases: [attribution.manage]
---
# Attribution and product media

Use `attribution_attribution_summary`, `attribution_attribution_products`,
`attribution_list_tracking_links` and `attribution_list_conversions` for the
GUI's stored attribution data. `list_products`, `get_product_details` and
`get_product_attribution` remain the compact, task-oriented alternatives. Do
not combine currencies or treat imported conversions as verified revenue.

Read `attribution_list_campaign_tags` before changing which campaigns may
promote products. `attribution_tag_products_to_campaigns` and
`attribution_untag_products_from_campaigns` use offer IDs and campaign IDs.
Product/media attachment is a separate relation: use
`creative_attach_media_products` with Library asset IDs and product IDs, or
`creative_detach_media_product` to remove one link. Read the asset back to
verify the intended relation changed.

All GUI mutation tools require top-level `confirm_external_action=true` after
user consent, including local tracking-link edits and CSV conversion imports.
Nested booleans cannot grant consent. The GUI's workspace, role, validation
and state rules apply. Other workspaces' IDs are not valid selections.

Shopee import preview reads the supplied source without connecting an account.
Confirmed import, offer fetch, listing refresh and session probe can contact
Shopee or queue listing reads. Obtain permission first. Read the enrichment
jobs and stored product listing afterwards. Session status exposes readiness,
not cookies; sign-in, credential storage and disconnect remain local-only.
Use `export_shopee_workbook` with explicit confirmation to fetch offers and
receive the GUI's XLSX export as an embedded MCP resource (16 MiB maximum).
The browser offer-page opener remains local-only.

The `creative_` tools expose the product-creative GUI's preview, filtered draft
listing, read, create, media submit, discard, provider availability and video
generation/progress. For actual creative completion, follow
`attribution.fill-product-creatives`: read the stored prompt and exact selected
references, submit ordered media and re-read the draft. A carousel stays
incomplete until all requested cards exist. Never mark one complete based on
the number of local files or a queued generation job.

Generation may spend provider quota and requires explicit confirmation.
`create_product_creative_draft` and `submit_product_creative_media` remain
compatible task helpers; the GUI counterparts provide the full modal fields.
The MCP connection acts for the local operator and cannot claim an AAL2
session. Installations requiring MFA refuse governed operations over this
connection. Account credentials and real-world licence attestations are not
delegated by this procedure. Nothing here publishes a campaign post.

The character picker is available through `character_read_characters` and
`character_read_character`. Confirmed `character_` GUI tools register reviewed
assets, create or retry a character drawing and remove a character. Read the
character-creation SOP before those actions; stored appearance/reference
requirements remain authoritative. A provider retry is not permission to
resubmit previously refused equivalent content.
