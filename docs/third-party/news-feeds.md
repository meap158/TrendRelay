# Newsroom Feeds

Discover could tell you what a *post* was doing - reactions, views, pace - but
nothing about what had actually happened that morning, which is where most
timely content starts. This reads the news, and it is a first-party capability
built on the Python standard library rather than a third-party checkout.

The catalogue lists it so the Tools tab can explain it beside everything else,
and lists `newspaper4k` next to it because that is the library most people
would reach for here and the reasoning for not using it is worth recording.

## Why feeds rather than an article scraper

`newspaper4k` is a good library - MIT, maintained, and it will fetch an article
and hand back the body text, the byline and keywords. It is the wrong tool for
*this* job, for two reasons:

- **It fetches every article page.** A board of forty headlines is forty
  requests against forty newsrooms, to rank items that will mostly not be
  chosen. A feed is one request per newsroom for all of them.
- **What it returns is the article's prose**, which belongs to whoever wrote
  it, and which this app would then be one paste away from putting into a
  caption.

A feed is the same publisher offering the same information *for syndication* -
that is what the format is for - and it carries everything a board needs: the
headline, the outlet, the link and the time. So the reader is the standard
library and adds no dependency at all.

`newspaper4k` stays in the catalogue, marked `evaluated-not-adapted`, for the
day someone wants the body of one *chosen* article. That is a different job
from ranking forty, and a good use for it.

## What makes news hot, when there are no numbers

A feed publishes no reactions, so the post board's ranking cannot be reused.
What news has instead is **corroboration**: when several newsrooms run the same
story at once, that is a signal no single outlet can manufacture.

Two shelves, counted rather than measured:

- **Widely covered** - how many distinct outlets are carrying it.
- **Just in** - recent, and so far only one newsroom has it.

Deliberately the same shape as the post board's hot and emerging shelves, and
deliberately not the same units. A story on six outlets is not comparable to a
post with six thousand likes, and nothing puts them in one ranking.

## Deciding when two headlines are one story

This took three passes against nine live feeds, and each rule exists because
of a false merge the previous one allowed. Newsrooms write their own headlines,
so an exact match finds almost nothing.

**Shared subject words, at least two.** Stopwords and short words are dropped
first, and a trailing plural is folded - "tariff refund" and "tariff refunds"
were the same story reported twice, and without folding they shared one word
too few.

**And a real fraction of the shorter headline.** Counting alone merged "Brazil
bus crash kills at least 23 ... five" into a Kenya helicopter crash on `crash`
and `five`, then promoted the Brazil headline to lead a story about Kenya. Two
words out of eight is a coincidence; two out of five is a story.

**And not on words that turn up all morning.** The fraction rule still let
"Trump administration can target Ethiopians for deportation" join an FDA
nomination on `trump` and `administration` - two out of five, which clears it.

The obvious fix for that is to trust only uncommon words, and it is a trap
worth writing down: **a distinctive term's frequency *is* its story's
coverage.** `rajab` appeared in three headlines because three newsrooms ran it,
so any rarity cap low enough to reject `crash` (four) would also reject the
widely-covered stories the board exists to find. Discounting only the *common*
end has no such conflict - `trump` was in fifteen headlines of two hundred and
forty-seven - and it separates the two cases that no count can.

Six per cent looked right and did not work, because fifteen of two hundred and
forty-seven is 6.07% and a term exactly on the threshold still counts. The
false merge survived a threshold that looked correct. It is four per cent.

## Operating notes

- **No key and no session.** These are public syndication endpoints, which is
  why this source is always available where most of the others are not.
- **https only**, and a response is capped before parsing. `xml.etree` is
  documented as vulnerable to entity-expansion blowup, so a hostile or broken
  feed costs a bounded read rather than the process.
- **Feeds are read concurrently.** Nine in sequence took three seconds and one
  slow newsroom would have added its whole timeout on top; the board now costs
  about as long as its slowest feed. Results are mapped back onto the requested
  order, because a board that reshuffles between two identical requests is one
  nobody can trust.
- **One newsroom being down does not take the board down.** It becomes a note,
  and the response says how many outlets answered - "3 newsrooms" means
  something different when four of the nine could not be reached.
- **Adding an outlet is one row** in `DEFAULT_FEEDS`: an id, a label, an https
  URL and a desk. The reader takes any RSS or Atom feed and needs nothing else
  to know about it. Spread new outlets across desks and publishers - a board of
  five technology sites would agree with itself constantly and call every
  agreement a big story.
