# Campaign autopilot

Turning Campaigns from a folder of one-off plans into a standing programme that
feeds connected accounts on a cadence and attaches an affiliate link where that
link can actually be clicked.

## What exists already

Almost all of the machinery. This is mostly wiring, plus two decisions that have
to be made explicitly rather than left to a checkbox.

| piece | where | state |
|---|---|---|
| Accounts across several engines, one post to many | `integrations/publishing.py` | done |
| `first_comment`, and which networks accept one | `FIRST_COMMENT_PLATFORMS` | done |
| Recurring time slots | `PublishingSlot` | done |
| Durable leased job queue and a worker loop | `jobs.py`, `scripts/worker.py` | done |
| Tracking links, clicks, conversions, EPC | `attribution_api.py` | done |
| Products and offers | `attribution_products.py` | done |
| Approved media with a blurred cut | `media_library.py` | done |
| **A campaign that posts by itself** | — | missing |
| **A rule for where the link goes** | — | missing |

Today a `PublicationPlan` is one video at one time, approved by hand and
exported as a manual package. It never reaches the publishing engines at all.

## The first decision: where the link goes

The obvious design — a field on the campaign for the affiliate URL, appended to
every caption — produces posts that cannot convert on the two networks this app
downloads from.

**A URL in an Instagram or TikTok caption is not a link.** It renders as plain
text; nobody can tap it. Putting one there adds clutter and, on Instagram,
signals a link-out post.

**"Link in the first comment" is no longer the workaround it was.** Instagram
detects posts built to funnel to a comment link and applies a reach penalty
comparable to an in-caption link, and hides link-bearing comments. Building the
autopilot around first comments would be building around advice that expired.

So placement is a per-network decision with three outcomes, and the network
decides it, not the operator:

| placement | networks | why |
|---|---|---|
| `caption` | YouTube (description), X, Facebook, LinkedIn, Pinterest, Threads, Telegram, Reddit, Mastodon, Google Business | the link is clickable and carries no penalty |
| `first_comment` | only where the network takes a comment *and* a caption link is not clickable *and* comment links are not suppressed | narrow, and currently empty by default |
| `bio` | Instagram, TikTok | no clickable link exists in the post; the caption points at the profile link instead |

`bio` is not a failure mode. It is the correct answer for those networks, and
saying so on the page is more useful than silently posting a dead URL. The
tracking link still exists — it is what the profile link points at — so clicks
are still attributed.

## The second decision: disclosure

The FTC's endorsement guides require a disclosure that is **clear and
conspicuous**: near the endorsement, before or at the same time as the link,
prominent, and repeated per post — each post is its own advertisement.

That has one consequence the autopilot must enforce and cannot make optional:

> **The disclosure goes at the start of the caption, always — even when the link
> is in the first comment or in the bio.**

A disclosure in the comment discloses nothing to the reader who does not open
the comments, and one below the fold discloses nothing to the reader who does
not tap "more". The autopilot therefore composes the caption as
`disclosure · body · hashtags`, and refuses to build a post that has an offer
attached and no disclosure text.

## Cadence and the queue

The pattern every scheduler converged on, and there is no reason to invent
another: a **queue per campaign** whose items are drawn in order into the
workspace's existing time slots.

- An item that has posted goes to the back of the queue rather than being
  consumed, so a campaign keeps running without hand-feeding.
- A **minimum recycle interval** stops an item repeating too soon. This is not
  cosmetic: reposting identical media to the same account within a short window
  is what gets an account flagged. An item whose turn comes up too early is
  skipped, not posted early.
- A **per-account daily cap**, for the same reason.
- Only `approved` items are eligible, and only while the campaign is `active`.
  Autopilot never approves anything on its own.

## What "intelligent" is allowed to mean

The app already refuses to print a ratio whose denominator would make it
meaningless, and deleted a set of invented affiliate metrics rather than show
them. The autopilot holds the same line.

Ranking is by **measured earnings per click**, which the attribution summary
already computes per currency, per campaign and per creative format. Where the
sample is too small to mean anything, the autopilot says so and falls back to
round-robin — it does not dress a guess as a measurement.

Three things follow:

1. **A tracking link per destination, not per campaign.** Two accounts on two
   networks sharing one link cannot be told apart afterwards, so nothing can be
   ranked. This is the change that makes measurement possible at all.
2. **Ranking is stated, not silent.** Each scheduled post records why it went
   where it went: ranked by EPC over N conversions, or round-robin for want of
   data.
3. **Exploration is kept.** Always posting to the current best destination
   guarantees the others never gather the data that would overturn it, so a
   share of slots goes to the rest regardless of rank.

## What autopilot will not do

- **Not write captions.** Generating copy is a different feature with a
  different failure mode; the queue holds copy a human wrote or approved.
- **Not publish without approval.** The item is approved, or it is skipped.
- **Not touch DM automation.** It converts well and it is the fastest way to get
  an account restricted when done from a tool; out of scope by choice, not by
  oversight.
- **Not invent a posting schedule.** Slots stay a workspace preference, and a
  campaign with no slots posts nothing and says so.

## Sources

- [Links in captions vs first comment (2026)](https://www.socialync.io/blog/links-in-captions-vs-first-comment-2026)
- [Instagram tests clickable caption links, Meta Verified only](https://ppc.land/instagram-tests-clickable-links-in-post-captions-for-meta-verified-users/)
- [Put the link in the first comment — bundle.social](https://bundle.social/blog/links-in-first-comment)
- [SocialBee: evergreen (re-queue) vs share once](https://help.socialbee.com/article/73-evergreen-vs-share-once)
- [SmarterQueue: recycling evergreen content, minimum time to recycle](https://get-help.smarterqueue.com/article/689-recycle-evergreen-content-to-maximize-reach)
- [FTC endorsement guides: what people are asking](https://www.ftc.gov/business-guidance/resources/ftcs-endorsement-guides-what-people-are-asking)
- [FTC affiliate disclosure requirements (2026)](https://www.auditsocials.com/blog/ftc-affiliate-disclosure-requirements-2026-guide)
