# TrendRelay MCP guide

This file is the control tower and operating entry point for an AI connected to
TrendRelay over MCP. The server reads this exact file into its initialization
instructions and exposes it at `trendrelay://mcp/guide`.

## Route the action first

Identify the intended action before choosing a tool. Do not begin with whichever
write operation looks convenient.

| Intended action | Canonical action | First live operation |
| --- | --- | --- |
| Fill missing Campaigns copy | `campaigns.fill-needs-copy` | `list_posts_needing_copy` |
| Add a post with media (existing asset, new upload, or carousel) | `campaigns.add-post-with-media` | `list_campaigns` |
| Read or change when things post | (no SOP yet) | `list_posting_times` |

For an action not listed here, call `list_sops`. Match its canonical action or
an alias. If no reviewed SOP exists, follow current explicit user direction and
the MCP server's general policy; do not invent a procedure or broaden authority.

## Start here

1. Identify the action using the routing table above.
2. Call `list_sops` to discover reviewed action procedures.
3. Call `get_sop` with the canonical action or read
   `trendrelay://sops/{action}` before using action-specific tools.
4. Read live workspace state immediately before acting. Never substitute memory,
   an old queue, or assumptions from another campaign.
5. Use the narrowest allowed operation that achieves the requested change.
6. Re-read the relevant live state after writing and verify the requested result
   before declaring completion.

For `campaigns.fill-needs-copy`, load the SOP, read the live campaign and each
post's context, write only fields reported as missing, refresh after each batch,
and use a final fresh queue read as completion evidence.

For `campaigns.add-post-with-media`, load the SOP before choosing an intake
tool. Check the campaign and Library first. A new file is one upload call per
file; several image asset ids become a carousel when the draft is created. A
generic artifact or local path from another tool is not automatically a valid
TrendRelay attachment, and a failed requested image must never be silently
replaced with a similar Library asset. For files generated in a client sandbox
or private filesystem with no public URL, encode the file and pass `media_base64`
(or a standard `data:<mime>;base64,<data>` URL) to `upload_media`.

### Quick path: generate an image, then add it to a campaign

1. Call `get_sop` for `campaigns.add-post-with-media`, then read the live
   campaign with `list_campaigns` and check `list_library_assets` for the exact
   image before importing it again.
2. Generate the image with the client's image-generation capability.
3. Call `upload_media` once for that image:
   - In ChatGPT, pass the generated image as the top-level `media` file input
     when the host offers it. TrendRelay declares the official
     `_meta["openai/fileParams"]` contract, so ChatGPT materializes the file as
     `{download_url, file_id, mime_type?, file_name?}`. Pass that object as
     supplied; never invent, shorten, or persist its temporary URL.
   - If the client has the bytes but cannot materialize a file object, pass
     standard base64 in `media_base64`. This is the portable external-client
     fallback and does not need a public URL.
   - If the file already has a direct public HTTPS address, pass `media_url`.
   Send exactly one source field, not the same file in several forms.
4. If the result contains `job_id`, poll `get_import_status` until `all_done`.
   Use the returned `asset_id` / entry in `ready`; a failed import creates no
   campaign attachment and its error must be reported.
5. Create a new draft with `create_campaign_post(asset_ids=[...])`, or add the
   asset to an existing text-first draft with `set_post_media`. Use
   `append=true` only to extend an image carousel. The MCP cannot approve or
   publish the draft; tell the operator it is waiting in Campaigns.

The upload validates the actual file signature on attachment, URL, and base64
routes. JPEG, PNG, and WebP images are capped at 25 MB; MP4, MOV, WebM, and MKV
videos are capped at 512 MB. MIME labels and filename extensions do not
override the bytes.

## Authority and safety

An SOP explains how to use authority already granted by the MCP policy; it does
not grant new authority. TrendRelay MCP may read workspace context, write draft
copy that remains inside the workspace, bring an image into the media library,
propose a draft post into a campaign, and read or change the posting schedule.
It may not sign in, connect an account, approve content, publish, deploy,
delete, or trigger another external side effect reserved for the operator. A
post it creates arrives as a draft outside the rotation; only the operator
promotes it, in the app.

A schedule is configuration, which is why it sits inside that authority:
changing one moves when work a person has already written and already approved
happens, and it cannot make a post exist, send one that would not have gone, or
reach an account nobody connected. It is still a live effect - a campaign's
posts can be moved to a different hour of tonight - so say what is about to be
rescheduled before doing it, and read the schedule back afterwards.

Current explicit user instructions outrank an SOP. Campaign-specific rules and
live post configuration outrank general defaults. When instructions conflict,
follow the priority order in the selected action SOP.

## Working with future SOPs

The canonical SOP root is the repository's `SOP/` directory. Procedures are
discovered recursively from Markdown front matter, so new action files become
available without adding another MCP handler. Never use a similarly named copy
outside this directory as the source of truth.
