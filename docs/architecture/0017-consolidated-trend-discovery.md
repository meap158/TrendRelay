# ADR 0017: Consolidate trending topics, and separate durable interest from a spike

Status: Accepted and built - consolidation core, provider readers,
`GET /api/research/trends/consolidated`, and the Discover section.

## Interface and handoff update (2026-08-22)

Discover is one workspace with five focused views rather than one continuous
stack of overlapping boards: Overview, Trends, Posts, Ads & signals, and
Opportunities. The search, country, workspace, and provider readiness controls
remain shared. Provider-heavy boards mount only when their view is open, which
keeps an ordinary visit from refreshing every source at once.

Every usable finding now has the same primary decision: **Add to campaign**.
The persistent evidence tray accepts merged feed rows, news, research posts,
ranked topics, Douyin terms, TikTok Creative Center entries, Meta public ads,
and first-party account signals. It creates an editable draft Campaign with the
source URL and evidence attached. Douyin keeps a separate **Save to Library**
action because it can acquire real media; adding evidence never implies media
rights or downloads a third party's asset.

News remains labelled as a story in the evidence tray, but is persisted as a
`topic` Campaign signal. A news event is something to make content about, not a
social post owned by the workspace, and `story` is not a durable signal kind.

## Context

Discover shows each source's own list - TikTok Creative Center's hashtags and
videos, Douyin's hot board, the research engine's entities. The same topic
appears in several of them under different spellings, and nothing says which is
worth making a video about. The goal is an evergreen topic generator and an
opportunity ranking to pick from, configurable by country and by time.

Two capability facts shape what is possible, and one of them corrects an
assumption worth recording.

**Agent Reach cannot contribute.** It is side-effect-free local diagnostics -
it reports which of fifteen channels are present on this machine and explicitly
performs no network probes or upstream execution. It has no trend data to give.

**TikTok Creative Center already carries country and time.** Its adapter takes a
two-letter `region` and a `period` of 7, 30 or 120 days. That is the country
filter and the time window the goal asks for, and the comparison between those
periods is the only durability signal available here.

**Popular posts do not share one clock.** TikTok's public Creative Center video
list uses those 7/30/120-day windows but does not publish a direct video URL or
caption to signed-out readers. The official YouTube Data API publishes direct
video resources and supports a two-letter regional chart, but it is a current
snapshot rather than a selectable time window. Since July 2025, that chart is
limited to Trending Music, Movies and Gaming. Discover keeps the native rank
and time basis on every post rather than manufacturing a cross-platform rank.

YouTube is optional and uses `YOUTUBE_DATA_API_KEY`. Provider availability is
part of the popular-post response so the platform filter can distinguish a
source that is not configured from a source that returned no posts. Country is
the finest common geographic filter these providers expose; Discover does not
offer a city selector that upstream data cannot honor.

## Decision

- A topic's identity ignores spacing entirely. A hashtag has none, so
  `#MorningRoutine` and `Morning Routine` meet only if the key drops it, and
  those two arriving from TikTok and from research is the ordinary case. Case
  and punctuation go with it. Plurals are left apart: "shoe" and "shoes" are
  sometimes two topics, and a wrong merge cannot be undone by whoever reads the
  list.
- Region is part of a topic's identity, not a filter applied afterwards. The
  same hashtag trending in Vietnam and in the United States is two
  opportunities with two audiences, and averaging them describes neither.
- Rank travels between sources; the underlying numbers do not. Views, posts and
  search interest are different quantities, and adding them invents one.
- A topic's shape comes from which windows it appears in. Holding across 7, 30
  and 120 days is `durable` - the structural shift that rewards early work.
  Present only in the last 7 is `emerging`. Gone from the short window is
  `fading`. One window alone is `single`, because a single point has no
  direction and calling it emerging would be a guess dressed as a reading.
- Ranking is weighted toward durability rather than loudness, because the goal
  is evergreen ideas: a topic that has held four months beats one that is loud
  this week, even though the loud one looks more urgent. Corroboration comes
  next - a topic one source saw might be that source's quirk.
- The score reports its contributions rather than only a total, following the
  opportunity score already in this codebase. A ranking somebody can override
  is one they can also trust.
- Topics and posts remain different evidence types, but both can be selected
  into one auditable idea basket. Synthesis is deterministic and editable until
  a general-purpose model is configured; creating the draft uses the existing
  Campaign API and retains the exact selected evidence in the handoff UI.

## Consequences

Seasonality is not offered. A yearly cycle needs years of history and the
longest window any source here has is 120 days, so a topic that returns every
November is indistinguishable from one that is simply back. Naming a bucket we
cannot measure would make the other two less trustworthy; the same reasoning
keeps `single` from being folded into `emerging`.

The core is pure and tested away from any adapter, so a fourth source is a new
kind of `Sighting` rather than a change to how topics are merged or ranked.

One request costs three Creative Center renders, because a shape cannot be read
from a single window. Creative Center's own cache absorbs the repeat cost, and
the endpoint deliberately does not let a caller ask for fewer windows: doing so
would return a list where every topic is `single` and quietly remove the only
reading the feature exists for. The time control therefore filters the ranked
result rather than narrowing the fetch.
