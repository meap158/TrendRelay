"use client";

/**
 * The TrendRelay mark, drawing itself.
 *
 * One repeating gesture: the green trend line traces itself from first point
 * to last, a glowing pen rides just ahead of the ink so the figure is visibly
 * being drawn rather than fading in, each point lights as the pen passes it,
 * the peak pings once while the finished chart holds, and the scene dissolves
 * before tracing again - the same figure the nav wears, animated as a sign
 * that a wait is working rather than stalled. It is deliberately pure markup:
 * the whole thing is CSS (see `.loading-mark` in console.css), so it weighs
 * nothing, runs no script, and starts no timer that a backgrounded tab could
 * freeze.
 *
 * `pathLength={1}` normalises the line to a unit length, so the stroke-dash
 * animation holds whatever the exact path coordinates happen to be. The pen
 * follows the same geometry through `offset-path`; where a browser lacks
 * motion path the pen simply never appears and the line still draws itself.
 */
export function LoadingMark() {
  return (
    <span className="loading-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" focusable="false">
        <path className="loading-mark-line" pathLength={1} d="M7.5 21.5 13 16l5 3 6.5-8.5" />
        {/* The shine that runs the finished line while the chart holds. The
            same geometry again, drawn over the ink with a short lit dash: a
            highlight travelling first point to last, the way the pen went. */}
        <path className="loading-mark-glint" pathLength={1} d="M7.5 21.5 13 16l5 3 6.5-8.5" />
        <g className="loading-mark-pen">
          <circle className="loading-mark-pen-glow" r="4.5" />
          <circle r="1.9" />
        </g>
        <circle className="loading-mark-point" cx="7.5" cy="21.5" r="2" />
        <circle className="loading-mark-point" cx="13" cy="16" r="2" />
        <circle className="loading-mark-point" cx="18" cy="19" r="2" />
        <circle className="loading-mark-point" cx="24.5" cy="10.5" r="2" />
        <circle className="loading-mark-ring" cx="24.5" cy="10.5" r="2" />
      </svg>
    </span>
  );
}
