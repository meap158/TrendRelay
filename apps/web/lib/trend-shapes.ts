/**
 * Reading a consolidated trend list: what each shape means, and why a topic ranked.
 *
 * The API returns a shape and a score breakdown; this turns both into something
 * a person can act on. Kept out of the component so the wording that decides
 * what somebody films can be tested without rendering anything.
 */

export type Shape = "durable" | "emerging" | "fading" | "single";

export type Topic = {
  key: string;
  label: string;
  region: string;
  shape: Shape;
  sources: string[];
  windows: number[];
  momentum: number | null;
  /** Best place it reached in any window. Null when nothing ranked it. */
  best_rank: number | null;
  contributions: Record<string, number>;
  score: number;
};

type ShapeCopy = {
  /** What to call it. */
  label: string;
  /** What the shape means in general. For one topic use `shapeMeaning`. */
  meaning: string;
  /** What to do about it, which is the only reason to show the shape at all. */
  advice: string;
  tone: "good" | "warn" | "muted" | "info";
};

export const SHAPE_COPY: Record<Shape, ShapeCopy> = {
  durable: {
    label: "Evergreen",
    meaning: "Held its place across 7, 30 and 120 days.",
    advice: "Worth a considered video; it will still be findable next month.",
    tone: "good",
  },
  emerging: {
    label: "Emerging",
    meaning: "Only in the last 7 days, and climbing.",
    advice: "Quick turnaround or not at all - most of these are gone in a fortnight.",
    tone: "info",
  },
  fading: {
    label: "Fading",
    meaning: "Big over the longer window and gone from this week.",
    advice: "Skip unless you already have the footage.",
    tone: "warn",
  },
  single: {
    label: "Unread",
    meaning: "Seen in one window only, so its direction is unknown.",
    advice: "Not evidence of anything yet; check again once another window answers.",
    tone: "muted",
  },
};

/** How the score is explained, in the order a person would ask. */
const REASON_COPY: Record<string, (topic: Topic) => string> = {
  durability: shapeMeaning,
  sources: (topic) =>
    topic.sources.length > 1
      ? `Seen by ${topic.sources.length} sources (${topic.sources.join(", ")}).`
      : `Only ${topic.sources[0] ?? "one source"} saw it.`,
  momentum: (topic) =>
    topic.momentum && topic.momentum > 0
      ? `Climbed ${topic.momentum} places between the longest and shortest window.`
      : "No climb between windows.",
  position: (topic) =>
    topic.best_rank ? `Ranked ${ordinal(topic.best_rank)} where it appeared.` : "Ranked highly.",
};

/**
 * What the windows actually showed for this one topic.
 *
 * The general wording overclaims on a real row: `durable` needs the 7 and 120
 * day windows and does not need the 30, so a fixed "held across 7, 30 and 120
 * days" says a topic was somewhere it never appeared. On a list whose whole
 * job is deciding what to film, that is the sentence somebody acts on.
 */
export function shapeMeaning(topic: Topic): string {
  const windows = [...(topic.windows ?? [])].sort((left, right) => left - right);
  if (!windows.length) return SHAPE_COPY[topic.shape].meaning;
  const shortest = windows[0];
  const longest = windows[windows.length - 1];
  switch (topic.shape) {
    case "durable":
      return `Held its place across ${andList(windows)} days.`;
    case "emerging":
      return windows.length > 1
        ? `Better placed over ${shortest} days than over ${longest}, and absent before that.`
        : `Only in the last ${shortest} days.`;
    case "fading":
      return windows.includes(7)
        ? `Bigger over ${longest} days than over ${shortest}.`
        : `Gone from the last week; last seen over ${longest} days.`;
    default:
      return `Seen in the ${shortest}-day window only, so its direction is unknown.`;
  }
}

/** `7`, `7 and 120`, `7, 30 and 120` - so a reason reads as a sentence. */
function andList(values: number[]): string {
  if (values.length === 1) return String(values[0]);
  return `${values.slice(0, -1).join(", ")} and ${values[values.length - 1]}`;
}

/**
 * Why this topic sits where it does, strongest reason first.
 *
 * A ranking somebody can argue with is one they can also trust, so the reasons
 * are ordered by what actually moved the score rather than by a fixed list.
 * Reasons worth nothing are dropped: "contributed 0" is noise on every row.
 */
export function reasons(topic: Topic): Array<{ key: string; points: number; text: string }> {
  return Object.entries(topic.contributions ?? {})
    .filter(([, points]) => points > 0)
    .sort(([leftKey, left], [rightKey, right]) => right - left || leftKey.localeCompare(rightKey))
    .map(([key, points]) => ({
      key,
      points,
      text: REASON_COPY[key]?.(topic) ?? key,
    }));
}

function ordinal(value: number): string {
  const tens = value % 100;
  if (tens >= 11 && tens <= 13) return `${value}th`;
  const suffix = { 1: "st", 2: "nd", 3: "rd" }[value % 10] ?? "th";
  return `${value}${suffix}`;
}

/**
 * What the fetch actually covered.
 *
 * Shown next to the list because "no evergreen topics" and "we never got the
 * 120-day window" look identical on screen, and only one of them means anything.
 */
export function windowSummary(windows: number[]): string {
  if (!windows.length) return "No window answered.";
  if (windows.length === 1) return `Only the ${windows[0]}-day window answered.`;
  return `${windows.map((days) => `${days}d`).join(" · ")} compared.`;
}

/**
 * A topic's name as it should be searched for, without the hash.
 *
 * The list merges `#ColdBrew` with `cold brew`, so the label can carry a hash
 * that would find nothing if it were pasted into a search box.
 */
export function searchTerm(topic: Topic): string {
  return topic.label.replace(/^#+/, "").trim() || topic.label;
}
