---
id: campaigns.fill-needs-media
action: campaigns.fill-needs-media
title: Fill campaign posts that are waiting for their media
summary: Work the needs-media queue continuously, one post at a time - read that post's own brief, generate its scenes in one batch as separate images, check them, upload each, attach them to that exact post, and take the next straight away.
version: 2
tags: [campaigns, media, carousel, needs-media]
aliases: [fill-campaign-needs-media, campaigns.needs-media, generate-carousel-images, attach-post-media]
---
# Filling the media on campaign posts that are waiting for it

This SOP is the other half of `campaigns.fill-needs-copy`. That one writes the
words onto posts that have none; this one puts the pictures or the clip onto
posts whose words are already written and which are waiting on media alone.

Use it when the queue already holds posts with their copy and their working
notes, and the job is to produce the media those notes describe - most often a
set of images, one per scene, that become one post's carousel.

## 1. The queue is a media queue, not a state queue

**`approved` does not mean the post has media.** A post can be approved with
nothing attached: approving it says the words are ready, and where there was no
media to look at there was no media decision to make. The scheduler skips such
a post every pass, silently, whether it is a draft or approved.

So the working queue is the media shape, not the state:

```
list_campaign_posts(campaign_id=..., media="none yet")
```

Do not add `state: "draft"` to that filter. It is the single most misleading
narrowing available here: it hides every approved-but-empty post, which on a
campaign that was written before its pictures existed is most of the backlog.
A run that checked only drafts, found none, and reported the campaign complete
has read past the whole job.

`media` takes `video`, `carousel`, `text only`, or `none yet`. Only `none yet`
is a gap. `text only` is a finished shape - the words are the whole post, by
somebody's decision - and must never be "fixed" by attaching pictures to it.

Page with `limit` and `offset`, following `more` and `next_offset`. Once
writing begins, refresh from `offset: 0`: a post you have just filled leaves
the `none yet` result set, and everything behind it shifts forward.

## 2. What you may change here, and what you may not

`set_post_media` fills a gap. It does not reopen a decision.

| The post | `set_post_media` |
| --- | --- |
| `draft`, no media yet | Allowed - attach it |
| `approved`, no media yet | Allowed - attach it; it joins the rotation as it stands |
| Already carries a video or pictures | Refused - swapping reviewed media is the operator's, in the app |
| `approved` and copy-only (`text only`) | Refused - that shape was chosen deliberately |
| `paused` or `retired` | Refused - neither is a post waiting to be completed |

The one live consequence worth saying out loud: attaching media to an
**approved** post completes it, and the campaign's next pass can then schedule
and publish it without asking anyone again. Nothing else in this procedure has
that effect - a draft stays a draft, waiting for the operator. So say which of
the two you just did when you report, and if the operator has not asked for
approved posts to be completed, ask before starting on those.

This SOP still grants no new authority: it cannot approve, promote, publish, or
change a post that already has its media.

## 3. One post at a time, start to finish

The loop is per post, not per phase. Take one post, finish it, then take the
next:

```
needs-media post
  → read that post's brief
  → generate its scenes in one batch, one image each, never a collage
  → check the set; regenerate only a scene that drifted
  → upload each, keeping the order
  → attach them all to that exact post
  → confirm what landed
  → next post, straight away
```

**Batch within a post, never across posts.** One post's eight scenes are one
batch - ask for all eight at once rather than round-tripping the model eight
times. Five posts' forty scenes are not: generating them together and
attaching them afterwards is the failure this ordering exists to prevent -
scenes drift between posts, an upload fails in the middle and its slot is no
longer identifiable, and nothing on the record says which picture belonged to
which caption. One post's pictures are only ever meaningful next to that post's
words.

Keep the `item_id` of the post you are on in front of you for the whole of its
loop. Every call in it - `get_post_context`, `set_post_media` - takes that id,
and nothing else in the queue should be touched until that post is whole.

**Then take the next one straight away.** The queue is worked continuously:
the moment a post's media is attached and confirmed, fetch the next
`none yet` item and begin its loop. Do not stop to summarise between posts, do
not ask whether to continue, and do not wait for anything but the calls this
loop makes. Report once, at the end of the run - or the moment something is
genuinely blocked, which is a different thing from a scene that came out
wrong.

## 4. Read the post's own brief before making anything

Call `get_post_context(item_id=...)`. What matters here:

- `queue_item.context` - the working notes. This is the brief a previous pass
  left for this one: how many cards, the aspect, the style lock, the scene
  list, what to keep consistent across the set. Follow it exactly. It was
  written about this post, and it is the one thing about the post that lives
  nowhere else.
- `current_copy.caption` - the words the pictures have to belong to. The
  caption is the post; the images illustrate it, not the other way round.
- `campaign` - objective, audience, markets and language, for tone and for
  anything written inside the image.
- `media_kind` - confirm it still reads `none yet`. If it does not, another
  pass has filled it; leave it alone and move on.

If the notes name a number of scenes, that number is the number of images -
not more because a set looked good, not fewer because one was hard. If they
name no number, decide from the caption and say what you decided when you
report.

## 5. Make exactly this post's images

One image per scene, in the order the brief lists them. The order is the swipe
order and the first image is the cover, so the sequence is part of the meaning,
not an implementation detail.

**Separate files, never a collage.** Eight scenes are eight images at the
brief's own aspect - usually 9:16, full-bleed - and not one picture with eight
panels in it. A carousel is swiped: a grid of eight thumbnails is a single card
that shows all of it at once and reads as none of it. An image model asked for
"eight scenes" will happily return one sheet of eight, so say the shape in the
prompt and check it in the result.

**Verify the set before uploading any of it.** Uploading is where a mistake
becomes expensive - an import per picture, then a package to unpick - so look
first, at the whole batch:

- the count matches the brief exactly;
- each file is one scene, not a panel grid or a contact sheet;
- the aspect is the one the brief names, on every file;
- the style lock holds across the set - the same hand, palette and treatment,
  so eight cards read as one post rather than eight;
- anything written inside an image is in the campaign's language and spelled
  correctly.

**When one scene drifts, regenerate that scene.** A single card that came out
wrong - the wrong aspect, a panel grid, a face that does not match the rest -
is one regeneration of that scene, keeping the other seven and their order.
Do not throw the batch away and start the post again over one card, and do not
park the whole post at the first wrong one: both spend work already done.

A scene that will not come right after a couple of attempts is where that
stops. Leave that one post untouched - a set you would not publish is worse
attached than missing - note which post and which scene, and move on to the
next item. One post nobody could finish is not a reason to stop the queue; it
is a line in the report at the end.

How many a carousel may hold is the receiving network's own figure: X swipes
through 4, Instagram and Facebook 10, LinkedIn 20, TikTok 35. `list_campaigns`
reports `accepts_carousel` for the campaign; a post above a destination's limit
is still created, and that destination comes back named in `carousel_warnings`.
Check the ceiling before generating a set that cannot reach the accounts it was
made for.

Nothing here generates images for you. Use the client's own image capability,
then bring the result in through the upload boundary described below and in
`campaigns.add-post-with-media`.

## 6. Upload each image, keeping its place

`upload_media` takes exactly one file per call. There is no bulk upload: eight
scenes are eight calls. Track one result slot per scene, in the brief's order,
even when calls finish out of order.

For an image generated in the client, pass it as the `media` file input when
the host offers one, or send the bytes as standard base64 in `media_base64`.
A local path or another tool's artifact reference is not an attachment. The
full intake rules - every accepted source, the size caps, what happens to a
file that fails - are in `campaigns.add-post-with-media`; read that SOP once
before the first upload of a run rather than discovering a refusal per scene.

An image normally returns its `asset_id` inside the call. A video, or the rare
image another worker claimed first, returns a `job_id` instead: poll
`get_import_status` with every outstanding job id in one call, not one per
round, and wait for `all_done`.

If a scene fails to import, retry that one scene - the same narrow repair a
scene that drifted gets in step 5, for the same reason. Do not attach a short
set, do not substitute a similar Library picture, and do not re-upload the
seven that landed. If it will not import after a couple of attempts, name the
post and the scene, leave the post as it is, and carry on with the rest of the
queue rather than stopping the run for it.

## 7. Attach them to that exact post

With every asset id in hand and in order:

```
set_post_media(item_id=<this post>, asset_ids=[<scene 1>, <scene 2>, ...])
```

That replaces the post's package outright, which is what a freshly generated
set wants. To grow a carousel one picture at a time instead - a scene arriving
per upload - call `set_post_media` with `append: true` and that one asset id;
it joins the end, which is the order it will be swiped, and you do not need to
resend what is already attached.

A video stands alone: it cannot be appended, and a post is one clip **or** a
set of pictures, never a mix.

The answer carries `carousel_warnings` - the campaign's accounts that cannot
take this gallery, and why - and a note saying whether the post is now waiting
for the operator (it was a draft) or has joined the rotation as it stands (it
was already approved). Read both back rather than reporting a reach the post
does not have.

## 8. Confirm, then move on

Re-read the post - `get_post_context`, or the next page of
`list_campaign_posts` - and check `media_kind` now reads `carousel` (or
`video`). Only then start the next post.

When the run ends - not between posts - say plainly: how many posts you filled,
how many pictures each got, which posts joined the rotation because they were
already approved, which are still drafts waiting for the operator, and every
post left unfinished with the scene that stopped it. A fresh
`list_campaign_posts(media="none yet")` is the completion evidence; the number
it returns is the backlog that remains.
