"use client";

/**
 * The TrendRelay mark, drawing itself.
 *
 * The trend line traces in and its four points light along it in turn, on a
 * gentle loop - the same figure the nav wears, animated as a sign that a wait
 * is working rather than stalled. It is deliberately pure markup: the whole
 * thing is CSS (see `.loading-mark` in console.css), so it weighs nothing,
 * runs no script, and starts no timer that a backgrounded tab could freeze.
 *
 * `pathLength={1}` normalises the line to a unit length, so the stroke-dash
 * animation holds whatever the exact path coordinates happen to be.
 */
export function LoadingMark() {
  return (
    <span className="loading-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" focusable="false">
        <path className="loading-mark-line" pathLength={1} d="M7.5 21.5 13 16l5 3 6.5-8.5" />
        <circle cx="7.5" cy="21.5" r="2" />
        <circle cx="13" cy="16" r="2" />
        <circle cx="18" cy="19" r="2" />
        <circle cx="24.5" cy="10.5" r="2" />
      </svg>
    </span>
  );
}
