# ADR 0017: Consolidate trending topics, and separate durable interest from a spike

Status: Accepted for the consolidation core. The adapter wiring and the Discover
surface are not built yet.

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

## Consequences

Seasonality is not offered. A yearly cycle needs years of history and the
longest window any source here has is 120 days, so a topic that returns every
November is indistinguishable from one that is simply back. Naming a bucket we
cannot measure would make the other two less trustworthy; the same reasoning
keeps `single` from being folded into `emerging`.

The core is pure and tested away from any adapter, so a fourth source is a new
kind of `Sighting` rather than a change to how topics are merged or ranked.
