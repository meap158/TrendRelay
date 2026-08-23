"use client";

import { useSyncExternalStore } from "react";

import { relativeTime } from "../../lib/relative-time";
import { useLocale } from "../i18n-provider";

/**
 * A timestamp that says how long ago, and keeps saying it.
 *
 * Two things make this more than a call to `relativeTime`.
 *
 * **It ages.** A row that said "just now" when the panel opened is still
 * saying it twenty minutes later unless something re-renders it. One timer
 * serves every row on the page: fifty rows do not need fifty intervals to
 * agree about what time it is, and the timer stops entirely when the last one
 * unmounts - which is most of the time, since this lives in a panel.
 *
 * **It survives hydration.** Relative time is the worst possible thing to
 * render on a server: the HTML is built at one instant and hydrated at
 * another, and React compares them. `useSyncExternalStore` is the way out -
 * the server snapshot and the first client render agree by construction, so
 * the markup matches and the relative label appears on the render after it.
 *
 * Both are shown, not one instead of the other. "How long ago" is the question
 * you ask first and "when exactly" is the one you ask next, and a row that
 * answers only the first makes you hover to find out whether two notifications
 * arrived together. The exact time is shortened rather than dropped: something
 * from today needs a clock time, not a date you already know.
 */

/** Every mounted label, so one interval can wake all of them. */
const listeners = new Set<() => void>();
let timer = 0;
/** A counter rather than a clock, so the snapshot is stable between ticks. */
let tick = 0;

/**
 * How often to look again.
 *
 * The finest thing this says is minutes, so half a minute is enough to keep
 * every label within one of the truth without waking the page more than it has
 * to.
 */
const TICK_MS = 30_000;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  timer ||= window.setInterval(() => {
    tick += 1;
    for (const waiting of listeners) waiting();
  }, TICK_MS);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      window.clearInterval(timer);
      timer = 0;
    }
  };
}

const getSnapshot = () => tick;
/**
 * What the server sees, and what the first client render sees with it.
 *
 * Its own value rather than `0`, so "not hydrated yet" is a state this can
 * recognise rather than a tick that happens to be the first one.
 */
const NOT_YET = -1;
const getServerSnapshot = () => NOT_YET;

export function RelativeTime({
  at,
  className,
}: {
  /** When it happened, as the API sends it. */
  at: string | null | undefined;
  className?: string;
}) {
  const { locale } = useLocale();
  const hydrated = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  if (!at) return null;
  const moment = new Date(at);
  const exact = moment.toLocaleString();
  // Before hydration, and past the point where "how long ago" stops helping,
  // the date is the whole answer - so there is exactly one place that formats
  // it and no row ever shows two dates.
  const shown = hydrated === NOT_YET ? null : relativeTime(at, { locale });

  return (
    <time dateTime={at} className={className} title={exact}>
      {shown ? `${shown} · ${clockOrDate(moment)}` : exact}
    </time>
  );
}

/**
 * The exact time, as short as it can be and still be unambiguous.
 *
 * Beside "5 minutes ago", the date is already known and only the clock is
 * news; beside "3 days ago" it is the other way round. Printing the full
 * `23/08/2026, 22:04` in both cases spends a line's worth of width saying what
 * the relative label just said.
 */
function clockOrDate(moment: Date): string {
  const today = new Date();
  const sameDay = moment.getFullYear() === today.getFullYear()
    && moment.getMonth() === today.getMonth()
    && moment.getDate() === today.getDate();
  return sameDay
    ? moment.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : moment.toLocaleString([], {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    });
}
