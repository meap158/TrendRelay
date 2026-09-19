---
id: campaigns.fill-needs-media
action: campaigns.fill-needs-media
title: Fill campaign posts that are waiting for their media
summary: Work the unfinished-media queue continuously, one post at a time - read that post's own brief, put its card count on the post as media_target, generate its scenes one call per scene as separate images made beside the cards already on that post, check and upload each card as it arrives, attach them to that exact post until it is complete, and take the next straight away.
version: 7
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

So the working queue is the media, not the state:

```
list_campaign_posts(campaign_id=..., media="unfinished")
```

Do not add `state: "draft"` to that filter. It is the single most misleading
narrowing available here: it hides every approved-but-empty post, which on a
campaign that was written before its pictures existed is most of the backlog.
A run that checked only drafts, found none, and reported the campaign complete
has read past the whole job.

`media` takes `video`, `carousel`, `text only`, `none yet` or `unfinished`.
`unfinished` is the backlog and the one to work from: no media at all, or
fewer files than the post's own `media_target`. `none yet` is the narrower
literal case - nothing attached - and stopped being the whole backlog the
moment a post could say how much it is waiting for, because a carousel of
three cards out of eight lists as a `carousel` and is still unpublishable.
`text only` is a finished shape - the words are the whole post, by somebody's
decision - and must never be "fixed" by attaching pictures to it.

Every post in the listing carries `media_count`, `media_target` and
`media_complete`, so "waiting for its first card" and "waiting for its last"
are told apart before either is opened.

Page with `limit` and `offset`, following `more` and `next_offset`. Once
writing begins, refresh from `offset: 0`: a post you have just finished leaves
the `unfinished` result set, and everything behind it shifts forward.

## 2. What you may change here, and what you may not

`set_post_media` fills a gap. It does not reopen a decision.

| The post | `set_post_media` |
| --- | --- |
| `draft`, media unfinished | Allowed - attach it |
| `approved`, media unfinished | Allowed - attach it; it joins the rotation once its media is complete |
| Media complete already | Refused - swapping reviewed media is the operator's, in the app |
| `approved` and copy-only (`text only`) | Refused - that shape was chosen deliberately |
| `paused` or `retired` | Refused - neither is a post waiting to be completed |

"Unfinished" is the post's own answer. Without a `media_target` it means
nothing is attached, and the first thing attached finishes the post. With one
it means the post holds fewer files than it says it is waiting for, and it
stays fillable until it holds them all - which is what lets a carousel be
delivered a card at a time without the post going out half-built.

The one live consequence worth saying out loud: **completing** an approved
post's media puts it in the rotation, and the campaign's next pass can then
schedule and publish it without asking anyone again. Nothing else in this
procedure has that effect - a draft stays a draft, waiting for the operator.
So say which of the two you just did when you report, and if the operator has
not asked for approved posts to be completed, ask before starting on those.

This SOP still grants no new authority: it cannot approve, promote, publish, or
change a post that already has its media.

## 3. One post at a time, start to finish

The loop is per post, not per phase. Take one post, finish it, then take the
next:

```
unfinished post
  → read that post's brief
  → put its number of cards on the post as media_target
  → per scene, in the brief's order:
       look at the cards already on this post
       generate one image beside them, never a collage
       check that card; regenerate it if it drifted
       upload it, and attach it with append: true
  → confirm the post is complete
  → next post, straight away
```

**One post's scenes, never across posts.** Generate for the post you are on
and for no other. Five posts' forty scenes made together and attached
afterwards is the failure this ordering exists to prevent - scenes drift
between posts, an upload fails in the middle and its slot is no longer
identifiable, and nothing on the record says which picture belonged to which
caption. One post's pictures are only ever meaningful next to that post's
words.

Inside the post the scenes are made one at a time as well - one generation
call per card, not one call for the set. Step 5 says why: asking for all of
them at once is itself what returns one sheet with all of them on it.

Keep the `item_id` of the post you are on in front of you for the whole of its
loop. Every call in it - `get_post_context`, `set_post_media` - takes that id,
and nothing else in the queue should be touched until that post is whole.

**Then take the next one straight away.** The queue is worked continuously:
the moment a post's media is attached and confirmed, fetch the next
`unfinished` item and begin its loop. Do not stop to summarise between posts, do
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
- `media_complete` - confirm it still reads false. If it is true, another pass
  has finished this post; leave it alone and move on. `media_count` and
  `media_target` say where a part-filled post got to, and which cards are
  still owed.
- `attached_media` - the cards already on the post, in posting order, each
  with the Library id `get_asset_thumbnails` takes. On a post an earlier pass
  left part-filled these are where the set's look actually lives, and they
  outrank any description of it - including the brief's, where the two
  disagree. Step 5 says how they are used.

If the notes name a number of scenes, that number is the number of images -
not more because a set looked good, not fewer because one was hard. If they
name no number, decide from the caption and say what you decided when you
report.

**Put that number on the post before you make anything.**

```
set_post_media_target(item_id=<this post>, media_target=<the brief's number>)
```

Before, not with the first card: a post that takes its target alongside its
first picture spends a moment holding one card and waiting for nothing, and
on an approved post that moment is one the scheduler can publish in. The same
number can also arrive at `create_campaign_post`, when this pass is the one
creating the post, or as `media_target` on a `set_post_media` call. It
is what makes a part-filled post safe: a post that says it is waiting for
eight files is skipped by the scheduler and stays in the `unfinished` queue
until it holds eight, so cards may be attached as they are made and nothing
publishes a set that is still arriving. A post with no target is finished by
the first file attached to it, which on an approved post means published.

The number may be raised later - a brief that grew - but never lowered:
lowering it declares a short set finished, which is the operator's call in the
app. Both writes refuse the attempt, and so does a target under what the post
already holds.

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

**One generation call per scene.** Eight cards are eight separate asks, each
for a single picture - never one call for the set, never a count parameter of
eight. Saving the round trips is exactly what produces the panel sheet: many
image tools read the whole conversation around the request rather than a
prompt field alone, so an ask with all eight scenes in view is an ask that
describes one image with eight scenes in it. The brief is the same hazard.
`get_post_context` returns the entire sequence - "8 cards", "carousel",
"scene 3 of 8" - and generating immediately after reading it leaves all of
that sitting beside the request. Read the brief once, take its locks out of it
privately - who the people are, what they wear, where they are, which props
recur - and then ask for one scene from the locks plus that scene's own
action, with the other seven left out of the request altogether.

**Ask for a picture in the words of a picture.** "Carousel", "8-card",
"panel", "comic strip", "storyboard" and "scene 1/8" all name a layout, and a
model that is handed a layout draws one. Ask for what the card actually is:
one full-frame vertical illustration, a single camera view, no borders, no
inset pictures, no typography beyond the words the brief puts inside the
image. The count is yours and the brief's to keep; it does not belong in the
request.

**Carry the same guard into every request.** The two failures above are the
ones that recur, so they are said again each time rather than once at the
start of a post - by the eighth ask, the first ask is the oldest thing in the
room. Something to this effect, with the locks from this post's own brief
filled in:

```
Draw only the cast, clothing, location, props, style and palette this post's
brief defines. Do not borrow people, rooms, furniture, signs, food, wardrobe,
lighting or poses from an earlier picture or from another post; an earlier
picture that came out wrong is not a reference. One single full-frame image
at the brief's aspect: no collage, split panel, contact sheet, storyboard,
inset, border or montage. No words inside the picture unless this scene names
the words it carries. Invent no extra characters or props - where the brief
is silent, keep it plain rather than filling it in.
```

It says what to draw before it says what not to, because a request that is
mostly prohibitions is a request whose subject is the prohibited thing.

**Separate files, one continuous story.** Separate describes the files, not the
pictures. Eight cards that each stand alone and share only a palette are eight
illustrations; a carousel is swiped, so each card has to carry over from the one
before it. The same people in the same clothes, the same place, the same light,
the same props with the same marks on them: the white bowl handed over in card
four is the bowl being washed in card six, and the corridor is that corridor
throughout. The style lock in the brief is the surface of this; continuity is
the substance, and it is what makes the set a post rather than a gallery.

That has to be written into every request, not hoped for. A model carries
nothing deliberate from one call to the next, so the recurring people, setting
and props - age, build, hair, glasses, clothing, the colour of the thing being
passed around - are described in **every** scene's ask, in the same words, and
in the brief's own wording where the brief fixes them: a paraphrase is how a
character quietly becomes a different person halfway through a swipe. What is
repeated is the lock, not the sequence. This card's action is the only action
in the request; the seven other scenes stay out of it, including the one
before this card, which is described only to the extent the picture shows it.

**The reference is this post's own cards, and nothing else.** From the second
card on, the post itself holds the answer to what the next one has to look
like. `get_post_context` lists what is attached, in posting order, with each
file's Library id:

```
attached_media: [{position: 1, asset_id: "asset_...", ...}, ...]
get_asset_thumbnails(asset_ids=["asset_..."])
```

Fetch the last one - the card this scene follows - and make the next card
beside it. Fetch an earlier one too where the scene needs it: card one for a
face that has drifted, or the card a returning prop was last seen in. They are
this post's cards, they were checked before they landed, and they are on the
record rather than in a conversation, so they say the same thing on the tenth
card as on the second.

Three things are never the reference, for the same reason in three forms:

- **another post's card**, however good it looked. It carries another post's
  cast, room and light into this one, which is drift with a respectable
  excuse. A set that looked right elsewhere is not this set;
- **a generation that came out wrong.** Throw it away and ask again from the
  lock. Do not hand it back with "keep the style, change the scene", and do
  not show it to say what to avoid - a tool that can see it will take it as
  the thing being edited, and the wrong picture becomes the one being redrawn;
- **a card that has not been checked yet.** It is a candidate, not a fact;
  until it passes step 5's gate and is attached, the post does not have it.

The first card of a post has no anchor - there is nothing attached yet - so
the brief's locks are the whole of it, and it is checked hardest of the set.
Everything after it has one, which is why a post is filled in order.

**Check each card as it arrives, before it is uploaded.** A card is looked at
the moment it is made, while the next one has not been asked for yet: a wrong
card caught here costs one regeneration, and the same card found at the end of
the post costs an import to unpick as well. Against the brief, every time:

- it is this post's scene, and the scene that was asked for;
- the cast and their clothes are the lock's, not a neighbouring post's;
- the location and the carried props are the ones the set has been using;
- the style and palette match the cards already made;
- one frame - no panels, no borders, no inset pictures, no contact sheet;
- the aspect is the one the brief names;
- nothing is written inside it that this scene did not ask for, and what is
  written is in the campaign's language and correctly spelled.

A card that misses any line is not uploaded. Regenerate it and look again.

**Then verify the set before attaching it.** The cards pass one at a time and
still have to hold together, so read them in swipe order before the post is
touched:

- the count matches the brief exactly;
- the style lock holds across the set - the same hand, palette and treatment,
  so eight cards read as one post rather than eight;
- the continuity holds too - the same faces, clothes, place and props from card
  to card, and each scene recognisably following the last.

**When one scene drifts, regenerate that scene.** A single card that came out
wrong - the wrong aspect, a panel grid, a face that does not match the rest, a
prop that changed colour on the way through - is one fresh ask for that scene,
from the locks the surviving cards agree on and with the single-view guard
said again in full, keeping the other seven and their order. Do not throw the
set away and start the post again over one card, and do not park the whole
post at the first wrong one: both spend work already done.

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

Upload each card as it passes its check, rather than holding the set to the
end. An uploaded picture is in the Library and survives whatever happens to
the session that made it, so a run that dies at scene six leaves five cards
that can still be used; a run that held all six in the session leaves nothing.
Uploading is not attaching, and the two are worth keeping apart in your head:
a Library asset belongs to nobody until step 7 puts it on a post, and an
abandoned post's uploads are Library clutter to mention in the report, not a
half-finished post. Where the post carries a `media_target`, attach each card
as it uploads and the two steps run together - the post itself then holds the
progress, which is sturdier than a list of asset ids in a session.

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

**What the target decides is whether a card may be attached alone.** A post
carrying a `media_target` is unfinished until it holds that many files, so
each card can be attached as it passes - `append: true` with that one asset id
- and the post stays out of the rotation and in the `unfinished` queue the
whole way. That is the flow to prefer: nothing is held in a session that might
end, and the post's own record says how far it got.

Without a target, an approved post is attached **once**, with the whole set.
There the first attach is the only attach: it completes the post, which joins
the rotation there and then, and every later call is refused with "This post
is already approved with its media decided. Ask the operator to change it in
the app." A set delivered a card at a time to such a post becomes a one-card
post, live, that nobody but the operator can repair - and it has left the
backlog, so no later pass will even find it. Either put a target on the post
first, which is the point of step 4, or hold the cards in the Library until
the set is complete and attach them in one call.

A post that came out short is left as it is and named in the report. With a
target it is simply still unfinished and the next pass can carry on filling
it; without one, a partial attach spends the only attach the post had. Never
lower a target to make a short set look finished - `set_post_media` refuses
it, and the record would read as a post briefed for the number it settled for.

A video stands alone: it cannot be appended, and a post is one clip **or** a
set of pictures, never a mix.

The answer carries `carousel_warnings` - the campaign's accounts that cannot
take this gallery, and why - and a note saying whether the post is now waiting
for the operator (it was a draft) or has joined the rotation as it stands (it
was already approved). Read both back rather than reporting a reach the post
does not have.

## 8. Confirm, then move on

Re-read the post - `get_post_context`, or the next page of
`list_campaign_posts` - and check `media_complete` now reads true, with
`media_count` equal to the brief's number. `media_kind` reading `carousel`
says only that pictures are attached, which a post holding one of eight says
too. Only then start the next post.

When the run ends - not between posts - say plainly: how many posts you filled,
how many pictures each got, which posts joined the rotation because they were
already approved, which are still drafts waiting for the operator, and every
post left unfinished with the scene that stopped it. A fresh
`list_campaign_posts(media="unfinished")` is the completion evidence; the number
it returns is the backlog that remains.
