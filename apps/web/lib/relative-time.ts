/**
 * How long ago something happened, in the reader's own language.
 *
 * A notification's timestamp answers "when exactly", which is the question you
 * ask second. The first one is "is this still happening" - and `23/08/2026,
 * 22:04` makes you do the arithmetic to find out.
 *
 * Built on `Intl.RelativeTimeFormat` rather than on templates, because the
 * languages this ships in do not agree that a number and a noun make a phrase.
 * Russian declines the noun by the number (1 минуту, 2 минуты, 5 минут) and
 * Arabic has a dual form for exactly two (ساعتين, not "2 ساعة"). A `{n} minutes
 * ago` template gets both wrong in a way nobody reviewing English would see.
 *
 * `numeric: "auto"` is what turns "1 day ago" into "yesterday", which is how
 * people say it in every locale here.
 */

/** Past this, "how long ago" stops being the useful answer and a date starts. */
export const RELATIVE_LIMIT_DAYS = 7;

/**
 * Below this, nothing is worth counting.
 *
 * Not zero: a job that finished four seconds ago reading "4 seconds ago" is
 * noise where "just now" is the answer. Forty-five seconds is where the
 * rounding to "1 minute" would start to be a lie.
 */
const JUST_NOW_MS = 45_000;

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/** Formatters are not free to build, and a list re-renders on every tick. */
const formatters = new Map<string, Intl.RelativeTimeFormat>();

function formatter(locale: string): Intl.RelativeTimeFormat {
  const found = formatters.get(locale);
  if (found) return found;
  // A locale the runtime does not know falls back to the default rather than
  // throwing: a notification list is not the place to discover that.
  let made: Intl.RelativeTimeFormat;
  try {
    made = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  } catch {
    made = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  }
  formatters.set(locale, made);
  return made;
}

/**
 * "just now", "5 minutes ago", "yesterday" - or `null` when a date is better.
 *
 * `null` rather than a string for the far past, so the caller shows the
 * absolute date it already has instead of this inventing a second date format.
 */
export function relativeTime(
  at: string | number | Date | null | undefined,
  { locale = "en", now = Date.now() }: { locale?: string; now?: number } = {},
): string | null {
  if (at === null || at === undefined) return null;
  const moment = at instanceof Date ? at.getTime() : Date.parse(String(at));
  if (Number.isNaN(moment)) return null;

  const elapsed = now - moment;
  // A timestamp from the future is a clock disagreeing with itself, usually by
  // a second or two. "in 3 seconds" on a job that has already finished reads as
  // a bug, so anything not yet past is simply now.
  if (elapsed < JUST_NOW_MS) return formatter(locale).format(0, "second");
  if (elapsed < HOUR_MS) {
    return formatter(locale).format(-Math.round(elapsed / MINUTE_MS), "minute");
  }
  if (elapsed < DAY_MS) {
    return formatter(locale).format(-Math.round(elapsed / HOUR_MS), "hour");
  }
  const days = Math.round(elapsed / DAY_MS);
  if (days <= RELATIVE_LIMIT_DAYS) return formatter(locale).format(-days, "day");
  return null;
}
