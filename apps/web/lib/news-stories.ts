/**
 * The news board's shape, and how long ago a story broke.
 *
 * The API groups headlines into stories and counts how many newsrooms carry
 * each one; this is the reading end of that. Nothing here re-ranks - the order
 * arrives decided, because the corroboration count is only meaningful against
 * the whole batch of headlines and the browser never sees that.
 */

export type NewsStory = {
  id: string;
  title: string;
  url: string;
  /** The newsroom that ran it first, of those carrying it. */
  outlet: string;
  outlets: string[];
  coverage: number;
  published_at: string | null;
  summary: string;
  shelf: "covered" | "breaking";
  /** Why it is on this shelf, in the operator's terms. */
  reason: string;
};

export type NewsBoard = {
  desk: string;
  covered: NewsStory[];
  breaking: NewsStory[];
  headline_count: number;
  outlets_read: string[];
  outlets_requested: string[];
  notes: string[];
  complete: boolean;
};

export const DESKS = ["all", "general", "business", "technology"] as const;
export type Desk = (typeof DESKS)[number];

/**
 * How long ago, short enough to sit in a row of metadata.
 *
 * Minutes and hours only, then days. A news board that said "2 weeks ago"
 * would be admitting it had nothing, and the reader that fills it drops
 * anything older than three days anyway.
 */
/**
 * The four phrasings this label can take, as `{n}` templates. English by
 * default so the callers that do not translate - and the unit tests - keep the
 * same output; the news board passes its locale's versions instead.
 */
export type SinceLabels = { justNow: string; minutes: string; hours: string; days: string };

const EN_SINCE: SinceLabels = {
  justNow: "just now",
  minutes: "{n}m ago",
  hours: "{n}h ago",
  days: "{n}d ago",
};

export function sinceLabel(
  published: string | null,
  now = Date.now(),
  labels: SinceLabels = EN_SINCE,
): string {
  if (!published) return "";
  const at = Date.parse(published);
  if (Number.isNaN(at)) return "";

  const minutes = Math.round((now - at) / 60_000);
  // A feed's clock can be a little ahead of ours, and "in 2 minutes" on a news
  // board reads as a bug rather than as a rounding difference.
  if (minutes < 1) return labels.justNow;
  if (minutes < 60) return labels.minutes.replace("{n}", String(minutes));

  const hours = Math.round(minutes / 60);
  if (hours < 24) return labels.hours.replace("{n}", String(hours));
  return labels.days.replace("{n}", String(Math.round(hours / 24)));
}

/**
 * Who is carrying it, without listing nine mastheads in a card.
 *
 * Two names and a count: enough to show the story is corroborated and by
 * whom, without the row wrapping onto a third line.
 */
/**
 * The two phrasings, as `{first}`/`{second}`/`{rest}` templates - English by
 * default for the same reason {@link SinceLabels} is.
 */
export type CoverageLabels = { pair: string; many: string };

const EN_COVERAGE: CoverageLabels = {
  pair: "{first} and {second}",
  many: "{first}, {second} +{rest}",
};

export function coverageLabel(story: NewsStory, labels: CoverageLabels = EN_COVERAGE): string {
  if (story.coverage <= 1) return story.outlet;
  const [first = "", second = "", ...rest] = story.outlets;
  if (!rest.length) return labels.pair.replace("{first}", first).replace("{second}", second);
  return labels.many
    .replace("{first}", first)
    .replace("{second}", second)
    .replace("{rest}", String(rest.length));
}
