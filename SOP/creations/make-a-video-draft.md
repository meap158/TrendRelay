---
id: creations.make-a-video-draft
action: creations.make-a-video-draft
title: Make or continue a video draft
summary: Save an AutoCut or Storytelling video as a resumable draft, edit it across turns, and render it to the Library - the way a campaign post is drafted, so a half-built video is picked up later rather than lost.
version: 1
tags: [creations, drafts, autocut, storytelling, video]
aliases: [create-creation-draft, list-creation-drafts, get-creation-draft, update-creation-draft, render-creation-draft, list-creation-kinds, make-video, autocut, storytelling]
---
# Making or continuing a video draft

A creation draft is a video saved mid-build, the way a campaign post is drafted:
it holds the whole editable spec, so you can save it incomplete, come back a turn
later, and carry it on rather than starting over. Two kinds exist today -
**autocut** (photos and clips cut to a beat) and **storytelling** (a written
script narrated over pictures). Six operations cover the whole flow:
`list_creation_kinds`, `list_creation_drafts`, `get_creation_draft`,
`create_creation_draft`, `update_creation_draft`, and `render_creation_draft`.

## The boundary

You may create, edit, and render a draft. Rendering draws it into a video and
files it in the Library - it publishes nothing. What happens to the finished
video is a person's decision on the campaign path, which stays refused to you.
A draft is safe to leave half-built; only rendering insists on the missing
pieces, and the error says which.

## Finding and continuing a draft

- `list_creation_drafts` returns this workspace's drafts, newest-edited first,
  so a draft from an earlier conversation is found again. Filter by `kind` or
  `status`; page with `limit`/`offset`.
- `get_creation_draft` returns one in full - its `kind`, `title`, `status`, and
  the whole `spec` - so you can see what is set and what is still missing.
- `update_creation_draft` replaces the `title` and/or `spec`. The spec you send
  replaces the stored one and is re-checked, so send the whole spec, not a
  fragment - read it first with `get_creation_draft`, change what you mean to,
  send it back.

## Media

Media is referenced in the spec by Library asset id. Use `list_library_assets`
to find assets the operator already has; use `upload_media` / `upload_image` and
`get_import_status` to bring new media in and get its asset id, exactly as when
building a campaign post. Only images and videos are used; a stray id is dropped.

## The autocut spec

Create with `create_creation_draft(kind="autocut", spec={...})`:

- `asset_ids` (required to render): ordered Library asset ids, up to 40. Order is
  the cut order.
- `template_id` (optional): a cut rhythm; omit to let it be picked from the
  count. `music` (optional): a track filename; omit for the template's own.
- `speed`: 0.5-2.0. `aspect`: `portrait` | `square` | `landscape`.
- `fill`: `cover` (crop to fill) | `blur` (fit whole over a blurred backdrop).
- `caption`: a burned-in hook line, up to 120 characters. `caption_position`:
  `top` | `bottom`.

## The storytelling spec

Create with `create_creation_draft(kind="storytelling", spec={...})`:

- `body` (required to render): the script, up to 20000 characters. The narration
  is synthesised from it and the cuts land on its sentences.
- `asset_ids`: the pictures, in order. `assignments` (optional): a per-sentence
  asset id, to say which picture a sentence opens on.
- `template_id`: `explainer` | `unfolding` | `urgent`. `voice_id`, `model_id`,
  `language_code` (optional): the narration voice; omit for the default.
- `narration_asset_id` (optional): a recorded narration to use instead of
  synthesising one. `aspect` (default `16:9`), `fill`, `subtitles` (on by default).

## Rendering

`render_creation_draft(draft_id)` queues the render through the draft's feature
and moves it to `rendering`; the finished video lands in the Library. If the
spec is not yet renderable - no media, an empty script - the call is refused with
the reason, and the draft is untouched so you can fix the spec and try again.
