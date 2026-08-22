# How a campaign turns media into posts

Working notes on the Campaigns implementation, end to end. Untracked on purpose:
this is a map for whoever is holding the feature, not a document the repository
promises to keep true. Where it disagrees with the code, the code is right.

Read `docs/agent-handover.md` first if you have not - some of this is live.

---

## The shape in one paragraph

A campaign holds a **queue of packages** (media plus copy), a set of
**destinations** (accounts, each on a connection), and a **schedule** borrowed
from the workspace. A tick asks one question per running campaign: is a posting
slot due, is there an approved package that has rested long enough, and is the
destination under its cap? If so it composes the post, attaches the affiliate
link where that link can be clicked, freezes the result as an **execution**, and
hands it to the publishing engine. The timeline is that story told back, past
and future in one list.

---

## 1. Content in

**Where:** the Content tab, "Choose from library".

The picker lists the media library - videos *and* pictures. It used to be video
only, in two places: the request asked for `mediaKind: "video"`, and the filter
state opened pre-set to video. Both had to go; fixing one left pictures still
invisible.

The rule for what a selection becomes is deliberately one sentence:

> A video is a post. Pictures chosen together are one carousel.

It is stated in the form's heading before the button is pressed ("Carousel of 3
pictures"). Anything cleverer - per-picture posts, mixed packages - is a rule
somebody has to be *told*, and this is the screen where a wrong guess becomes
real posts.

**Copy is optional.** Media is usually chosen before anybody has written for it.
A package with no copy stores `PLACEHOLDER_BODY` and reports `needs_copy: true`,
so the interface can mark what still needs writing rather than showing the
placeholder as though somebody meant it. Every network refuses a caption-less
post, which is why it is a stand-in and not an empty string.

**Data:** `CampaignQueueItem` carries either `video_path` *or* `image_paths`
(ordered - the first picture is the cover), never both and never neither. That
is validated at the edge, not discovered by an engine mid-publish.

Still unread by the composer, though the columns exist: `made_with_ai`,
`visibility`, `topic`, `youtube_category_id` on the package; `subreddit`,
`board`, `board_name` on the destination.

---

## 2. Products

Already built, and better than it looks. `offer_mode` is `smart`, `manual` or
`none`. Smart scores workspace offers against the campaign's own evidence - its
name, objective, audience, and the approved copy - and stores the reasoning on
the item as `offer_match`, including which terms matched and how confident it
is. Low-confidence matches stay review-only and are not attached automatically.

The link that ends up in the caption is the **network's own** affiliate URL, not
a TrendRelay redirect. See ADR 0022: the `/c/` redirector is retired, so
anything measuring internal clicks reads zero by construction.

---

## 3. Expansion - how one package becomes many posts

This is the part people mis-predict, so it is now stated in a sentence above the
switch:

> 2 packages go to 2 accounts, up to 4 posts a day (2 per account, your daily
> cap). Each post carries its affiliate link where that link can be clicked.

Every number in it was already on screen, in three separate tiles. It says "up
to" because three things pull the real number down: `min_recycle_days` (a
package must rest before it repeats on a destination), `daily_cap_per_account`,
and how many packages are approved at all.

Roughly: `posts/day ≈ min(posting_slots, daily_cap) × destinations`.

Placement per destination decides *where* the link goes - caption, first comment
or bio - and `auto` lets the network's own behaviour decide. That choice is on
the destination row, because a link that is clickable on Threads is not
clickable on TikTok.

---

## 4. Scheduling and delivery

Three levers, and they are routinely confused with each other:

| Lever | Question it answers | Values |
|---|---|---|
| `enabled` | Does this campaign run at all? | on / off |
| `authority` | What needs a human first? | assist, auto_draft, run_by_exception, autonomous |
| `delivery` | What happens when it posts? | draft, schedule, now |

`enabled` is also the **circuit breaker**: the runner sets it false by itself
after repeated auth refusals or uncertain deliveries, because posting into that
state is how duplicates are made. It is labelled "Campaign is running" - it was
"Post automatically", which read as a second approval step.

Switched off, the outlook is empty and says why. That is a real gate, not
cosmetic: it used to forecast a week of posts that nothing would carry out.

`delivery` defaults to **schedule**. It used to default to draft, which made the
campaign look broken - approve a package, switch it on, watch the posting time
pass, and the post sits in the engine waiting for an approval nobody mentioned.
The control lives beside the run switch for the same reason: one says whether
the campaign runs, the other says what running does.

**Freezing.** `plan_campaign` composes a `ScheduledPost`; `campaign_runner`
writes a `PublicationExecution` carrying the media path, hash, caption, replies
and destination. The point is that what was composed and what is published
cannot drift apart - a render finishing later cannot swap the file under a plan
that was already previewed.

---

## 5. The timeline

One list, past to future, grouped by day. Delivered jobs and forecast posts are
normalised into a flat `TimelineEntry` - fully populated, null where a half has
nothing to say - and rendered by one row component. They were two lists with two
designs, and the delivered half did not even name the engine that carried it.

Each row answers four questions: **when, where, through which engine, and has it
gone out.** Whether it posted is a badge, not which box it landed in.

The player is `controlsList="nodownload"`: Chrome puts a save button in its own
video controls, and a preview of a post is not a file on offer.

---

## 6. Known sharp edges

- **Platform limits are not checked before delivery.** A too-wide video fails at
  Buffer, repeatedly, with the engine's own error on the row. A carousel aimed
  at a network that cannot take one would fail the same way. `photo_carousel_
  platforms` exists in Publish and the campaign path does not read it.
- **No per-row "Publish now".** `delivery: "now"` is selectable up front, which
  covers the simple case without risk. A per-row button must modify the job the
  engine already holds rather than create a second one - see the handover.
- **No carousel has actually posted.** The path is built and tested end to end
  but has never been exercised against a live engine. Watch the first one.
- **Nothing generates copy.** Placeholder only, by decision; MCP later.

---

## 7. Where things live

```
services/api/src/trendrelay_api/
  autopilot_models.py        queue items, destinations, autopilot settings
  campaign_scheduler.py      plan_campaign - what should post next, and why
  campaign_runner.py         executions, delivery, the circuit breaker
  campaign_autopilot_api.py  the endpoints the panel talks to
  publication_models.py      the frozen record of one publication
apps/web/app/campaigns/
  page.tsx                   campaign list, header, status actions
  autopilot-panel.tsx        everything else: settings, picker, timeline
```

Migrations `0035`-`0037` cover carousels and the parity columns.
