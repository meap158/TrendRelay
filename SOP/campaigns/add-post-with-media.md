---
id: campaigns.add-post-with-media
action: campaigns.add-post-with-media
title: Add a post with media to a campaign
summary: Upload a video or image into the media library and propose a draft post from Library assets into a campaign - whole, or a piece at a time - for the operator to promote.
version: 6
tags: [campaigns, media, upload, posts]
aliases: [upload-image, add-campaign-post, campaigns.upload-media, create-campaign-post, set-post-media]
---
# Adding a post with media to a TrendRelay campaign

This SOP covers bringing media into the workspace and proposing a post made
from it into a campaign - one video, one picture, or several pictures as a
carousel - sent whole, or assembled a piece at a time. It uses six
operations: `list_campaigns`, `list_library_assets`, `upload_media`,
`get_import_status`, `create_campaign_post`, and `set_post_media`.

`upload_media` takes video and images alike. `upload_image` still exists and
takes pictures only; prefer `upload_media` unless you have a reason not to,
so that one tool covers whatever the user attaches.

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

## 2. Bring the media in

If it is already in the library - the operator's own downloads, or something an
earlier session uploaded - skip to step 4 and find it with
`list_library_assets`. Otherwise call `upload_media` once per file, with one
source each:

- **A chat attachment.** In clients that support the `openai/fileParams`
  convention (ChatGPT), a file the user attaches arrives automatically as the
  `media` parameter - an object carrying a temporary `download_url`. You do
  not need to read or repeat that URL.
- **A direct URL.** Pass `media_url` with a direct public `https://` link to
  the file itself. Redirecting links, `http://` links, and links to private or
  local addresses are refused. MP4, MOV and WebM video up to 512 MB are
  accepted, and JPEG, PNG and WebP images up to 25 MB.

A video is one file and one post. There is no such thing as a carousel of
clips: a campaign package is one video **or** a set of pictures, so upload the
clip, wait for its asset id, and go straight to step 4.

Always pass a `title` a person will recognise in the Library. When you know
where the file genuinely came from, record it: `source_url` for the page,
`creator` for who made it, `platform` for the network it came from. Do not
invent provenance, and do not pass the temporary attachment URL as
`source_url` - it expires and identifies nothing.

The upload lands in the media library through the same import pipeline as the
operator's own files - deduplicated by content, kept immutable, audited - and
appears there under the `mcp-upload` source.

**For a carousel, keep the order.** There is no bulk upload: a carousel of six
pictures is six `upload_media` calls. Track the returned ids in the order the
user gave you the pictures, because that is the order they will swipe through -
`create_campaign_post` uses the order of `asset_ids` as the order of the
carousel.

## 3. Wait for the asset ids

`upload_media` returns either:

- `asset_id` immediately, with `duplicate: true` - the library already holds
  these exact bytes; use the asset id as it is; or
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

## 4a. Or build the post a piece at a time

Everything above assumes you have the whole post before you start. Often you do
not: the user sends the words now and the clip when they find it.
`set_post_media` is the other half of `create_campaign_post`, and between them a
post can be assembled in either order.

**Media is never required to start.** Words alone are enough to create the post,
and so is media alone - whichever half you have, the other can follow. That
makes text the one thing you can always act on the moment the user gives it to
you, without waiting for a file. So:

- **Text first.** Call `create_campaign_post` with a `caption` and an empty
  `asset_ids`. The post is created and marked as awaiting media - the scheduler
  passes over it, with a note, until something is attached. Upload the file when
  it arrives, then call `set_post_media` with the post's id and the asset ids.
- **Media first.** Call `create_campaign_post` with `asset_ids` and no caption.
  It is marked as needing copy, and `campaigns.fill-needs-copy` applies. Write
  the words later with `write_post_copy`.

`set_post_media` takes the same packages this SOP describes - one video, or a
set of pictures in swipe order - and answers with the same `carousel_warnings`
a whole-package create does. So a gallery attached on a second visit is told
the moment it outgrows an account, rather than at publish time.

Only a **draft** can be changed this way. Once the operator has promoted a post
into the rotation they approved it with its media in view, and changing what
publishes underneath that decision is theirs to make in the app.

## 5. Say what is waiting

The post is created in the `draft` state, outside the campaign's rotation.
Tell the operator exactly what you created and where: the campaign, the media,
and whether the caption still needs writing. They promote it in the app; you
are done when they know it is waiting.
