---
id: campaigns.add-post-with-media
action: campaigns.add-post-with-media
title: Add a post with media to a campaign
summary: Upload an image into the media library and propose a draft post from Library assets into a campaign, for the operator to promote.
version: 1
tags: [campaigns, media, upload, posts]
aliases: [upload-image, add-campaign-post, campaigns.upload-media, create-campaign-post]
---
# Adding a post with media to a TrendRelay campaign

This SOP covers bringing an image into the workspace and proposing a post made
from Library media into a campaign. It uses three operations: `upload_image`,
`get_import_status`, and `create_campaign_post`.

## What you can and cannot do here

You can add an image to the media library and create a **draft** post in a
campaign. You cannot publish it, approve it, or move it into the rotation:
every post you create arrives as a draft outside the rotation, and only the
operator can promote it, in the app. This is not a formality to work around -
it is the boundary. End your work by telling the operator a draft is waiting
for their review.

## 1. Bring the image in

Call `upload_image` with one source:

- **A chat attachment.** In clients that support the `openai/fileParams`
  convention (ChatGPT), an image the user attaches arrives automatically as the
  `image` parameter - an object carrying a temporary `download_url`. You do
  not need to read or repeat that URL.
- **A direct URL.** Pass `image_url` with a direct public `https://` link to
  the image file itself. Redirecting links, `http://` links, and links to
  private or local addresses are refused. JPEG, PNG and WebP are accepted, up
  to 25 MB.

Always pass a `title` a person will recognise in the Library. When you know
where the image genuinely came from, record it: `source_url` for the page,
`creator` for who made it, `platform` for the network it came from. Do not
invent provenance, and do not pass the temporary attachment URL as
`source_url` - it expires and identifies nothing.

The upload lands in the media library through the same import pipeline as the
operator's own files - deduplicated by content, kept immutable, audited - and
appears there under the `mcp-upload` source.

## 2. Wait for the asset id

`upload_image` returns either:

- `asset_id` immediately, with `duplicate: true` - the library already holds
  this exact image; use the asset id as it is; or
- a `job_id` - poll `get_import_status` with it until `status` is
  `succeeded` and take the `asset_id`. A `failed` status carries the error to
  read back to the user.

## 3. Propose the post

Call `create_campaign_post` with the campaign id and the Library asset ids:

- One video asset, **or** up to twenty image assets (a carousel) - never a mix.
- Copy fields are optional: `caption`, `title`, `hashtags`, `first_comment`,
  `thread`. A post created without a caption is marked as needing copy, and
  the `campaigns.fill-needs-copy` SOP applies to it.
- The no-links rule applies exactly as it does to every copy write: the
  campaign carries the affiliate link itself and adds its own disclosure.
  Name the product in words; never paste a URL into copy.

Existing Library media can be posted without an upload: find its asset id
through the operator (there is no Library browse over MCP), or use the asset
id an earlier upload returned.

## 4. Say what is waiting

The post is created in the `draft` state, outside the campaign's rotation.
Tell the operator exactly what you created and where: the campaign, the media,
and whether the caption still needs writing. They promote it in the app; you
are done when they know it is waiting.
