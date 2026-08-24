---
id: campaigns.add-post-with-media
action: campaigns.add-post-with-media
title: Add a post with media to a campaign
summary: Upload an image into the media library and propose a draft post from Library assets into a campaign, for the operator to promote.
version: 4
tags: [campaigns, media, upload, posts]
aliases: [upload-image, add-campaign-post, campaigns.upload-media, create-campaign-post]
---
# Adding a post with media to a TrendRelay campaign

This SOP covers bringing images into the workspace and proposing a post made
from Library media into a campaign - one picture, or several as a carousel. It
uses five operations: `list_campaigns`, `list_library_assets`, `upload_image`,
`get_import_status`, and `create_campaign_post`.

## What you can and cannot do here

You can add an image to the media library and create a **draft** post in a
campaign. You cannot publish it, approve it, or move it into the rotation:
every post you create arrives as a draft outside the rotation, and only the
operator can promote it, in the app. This is not a formality to work around -
it is the boundary. End your work by telling the operator a draft is waiting
for their review.

## 1. Check the campaign can take what you are sending

`list_campaigns` reports `accepts_carousel` for each campaign: whether any of
its connected accounts can post several pictures at once. Whether a gallery is
possible depends on the publishing engine as much as the network - some engines
send no gallery at all - so a campaign on a picture-friendly network may still
have nowhere to put one.

If the campaign you were given is `accepts_carousel: false` and the user wants
a carousel, say so before uploading anything. Uploading first and finding out
afterwards costs the user an import per picture.

## 2. Bring the images in

If the pictures are already in the library - the operator's own downloads, or
something an earlier session uploaded - skip to step 4 and find them with
`list_library_assets`. Otherwise call `upload_image` once per picture, with one
source each:

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

**For a carousel, keep the order.** There is no bulk upload: a carousel of six
pictures is six `upload_image` calls. Track the returned ids in the order the
user gave you the pictures, because that is the order they will swipe through -
`create_campaign_post` uses the order of `asset_ids` as the order of the
carousel.

## 3. Wait for the asset ids

`upload_image` returns either:

- `asset_id` immediately, with `duplicate: true` - the library already holds
  this exact image; use the asset id as it is; or
- a `job_id` - poll `get_import_status` until the import finishes.

**Poll the whole set at once.** Pass every job id for this post as `job_ids`
in one call. Polling is a loop, so checking six pictures one at a time is six
calls per round, several rounds over. Wait until `all_done` is true, then:

- `ready` holds the asset ids, in the order you asked for them - which for a
  carousel is the order they will swipe through, so it can be passed straight
  to `create_campaign_post`.
- `failed` holds any that did not import, each with the error to read back to
  the user. Decide with them whether to post the rest or fix the failure
  first; do not quietly create a carousel a picture short.

## 4. Propose the post

Call `create_campaign_post` with the campaign id and the Library asset ids:

- One video asset, **or** several image assets (a carousel) - never a mix.
  The order of `asset_ids` is the order of the carousel.
- How many pictures a carousel may hold is the receiving network's own figure,
  not one number: X swipes through 4, Instagram and Facebook 10, LinkedIn 20,
  TikTok 35. A post above a destination's limit is still created; that
  destination is named in `carousel_warnings`.
- Copy fields are optional: `caption`, `title`, `hashtags`, `first_comment`,
  `thread`. A post created without a caption is marked as needing copy, and
  the `campaigns.fill-needs-copy` SOP applies to it.
- The no-links rule applies exactly as it does to every copy write: the
  campaign carries the affiliate link itself and adds its own disclosure.
  Name the product in words; never paste a URL into copy.

Existing Library media can be posted without an upload. `list_library_assets`
reads the library newest-first: `query` searches titles and captions, `kind`
narrows to `image`, `video` or `audio`, `collected_within_days` limits it to
what arrived recently, and `offset` pages through it - `more` says whether any
are left. It returns asset ids, which is exactly what this step takes.

Prefer it to re-uploading. Uploading a file the library already holds returns
the existing asset id anyway (the ingest deduplicates by content) but records
provenance that is not true.

The answer carries `carousel_warnings`: any of the campaign's accounts that
cannot post this gallery, and why - an engine that sends none, or a network
whose picture limit this post exceeds. The post is still created; those
accounts simply will not receive it. Read the warnings back to the operator
rather than reporting a reach the post does not have.

## 5. Say what is waiting

The post is created in the `draft` state, outside the campaign's rotation.
Tell the operator exactly what you created and where: the campaign, the media,
and whether the caption still needs writing. They promote it in the app; you
are done when they know it is waiting.
