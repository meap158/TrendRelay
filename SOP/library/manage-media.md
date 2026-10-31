---
id: library.manage-media
action: library.manage-media
title: Inspect and manage Library media through MCP
summary: Use the Library GUI filters, asset context, edit recipes and processing jobs without bypassing workspace or provider confirmation rules.
version: 1
tags: [library, media, effects]
aliases: [library.manage]
---
# Library media management

Start with `library_list_assets` for the full GUI filter set. Unlike the compact
`list_library_assets` helper, this supports creator/platform, campaign inclusion
or exclusion, character, recipe, processing state, attached products, hashes,
sort order and offset. `limit` is 1 to 100. Use `library_list_asset_ids` only when
the action needs the complete filtered selection rather than another page.

Read `library_get_asset` for versions, transcripts, analysis, attribution and
product/campaign links. `get_asset_thumbnails` remains the bounded MCP image
preview. Do not claim an asset is usable because a list row exists: read its
versions and processing status when those determine the operation.

The `Library GUI` tools use the GUI's body models and handlers. All mutations
and provider reads require top-level `confirm_external_action=true` after user
consent. The top-level value controls any nested confirmation field. The local
operator's workspace role and governed-assurance checks still apply. A boolean
does not establish an MFA session.

Use `library_list_effects` and `library_get_recipe` to inspect edits, then
`library_save_recipe` to save a reviewed ordered stack. Rendering is separate:
`library_submit_render` or `library_submit_batch_render` queues the durable
work, while `library_list_effect_render_jobs` reports its progress. Confirm
before rendering, discarding cuts, clearing history, cancelling or reprioritising
jobs. Original Library assets remain subject to the application's own immutable
media rules.

Caption style/track inspection, reviewed transcript enrichment, transcription,
translation, voice generation, image-to-video generation, licensed music search
and import, bulk actions and archive preparation use their corresponding GUI
tools. Some actions contact providers or spend quota. State that consequence
and obtain consent before invoking them. A returned queued job is not proof
that a file has finished. Re-read the processing or generation record.

The GUI's local-file and folder imports are loopback-only and stay local.
A path on a different app's computer is not a server path. Remote clients
use `upload_media` with transferable bytes or an accepted public
URL, then read `get_import_status`; never invent a server path.

Use `read_library_media` for original, proxy, thumbnail, audio or edited files,
and `read_library_caption` for paths from the caption file listing. Downloads
return embedded MCP resources, capped at 16 MiB. Use `preview_library_effect`
with explicit confirmation for an effect-stack preview as MCP image content.
Larger streams, specialized face/sprite previews and archive downloads use the
authenticated GUI. Archive preparation returns the GUI's download reference.
Browser multipart media uploads use the MCP byte/URL upload flow instead.
Face-swap licence attestations and account
sign-in stay with the operator. Do not record consent or licence ownership on
their behalf.

For product/media links or Attribution drafts, read
`attribution.manage-library-data`. For adding media to a campaign or Publish,
use the posting SOP; Library processing itself does not approve or publish.

The same group includes the Library's shared dialogs: `autocut_` for template
planning, preview/render and jobs; `storytelling_` for outlines, matching,
narration renders and confirmed B-roll/voice provider work; `carousel_` for
text-slide rules/preview/save; `hook_` for hook review and renders; `creation_`
for saved drafts and their media; and `studio_` for production edits, approval
and renders. Inspect the live typed schema and follow the corresponding
creative SOP before generating or approving output. Rendering is not posting.
