---
id: design.data-visualization
action: design.data-visualization
title: Build readable, responsive data visualizations
summary: Reuse TrendRelay's chart interaction, scaling, typography, tooltip, accessibility, and responsive standards.
version: 1
tags: [design, charts, analytics, accessibility, responsive]
aliases: [chart-design, data-visualization, analytics-design]
---
# Data visualization standards

Use these rules for every new or revised chart in TrendRelay. The individual
Campaign Overview is the reference implementation; the all-campaign control
room extends the same rules to comparison charts.

## 1. Start with the question

- Use a time series for change over time, a bar or aligned matrix for direct
  category comparison, and a table when exact values matter more than shape.
- Do not place unrelated scales in one plot. For operational columns such as
  published, scheduled, and planned, give each column its own stated scale.
- Keep summaries, trends, and operational workload visually distinct.

## 2. Scale honestly

- Count axes must start at zero and use rounded human ticks rather than the
  data's arbitrary maximum.
- Aligned campaign series must share one scale for the selected metric.
- Metrics with radically different magnitudes must use small multiples or a
  metric selector, never several deceptive axes over one plot.
- Empty dates must remain in a time series as zero-value points so campaigns
  stay aligned.

## 3. Keep the plot readable

- Chart text must normally be at least 11px on desktop and 10px on compact
  mobile layouts. Never solve density by shrinking labels to illegible text.
- Reserve stable space for axes and legends; truncate long campaign names only
  where the full name remains available to assistive technology or a tooltip.
- Clip lines and filled areas to the plot bounds. A series must never paint
  over the date axis, legend, adjacent chart, or following content.
- Focus markers must be fixed-size HTML overlays (8px by default), not SVG
  circles inside a stretched `preserveAspectRatio="none"` coordinate system.
- Use semantic chart-series colors consistently between line, marker, legend,
  tooltip, and related comparison rows. Status colors still mean status.

## 4. Make inspection forgiving

- The whole plot width must be the hover/tap target. Resolve the nearest date
  from the pointer's horizontal portion; never require hitting a one-pixel
  point exactly.
- Hover and tap tooltips must show the date and the useful related values, not
  only the selected series. Cross-campaign tooltips must identify every visible
  campaign with its matching color.
- Keep the tooltip inside the chart bounds and large enough to read without
  covering the full plot. It must not intercept pointer events.
- Secondary bars must expose an exact, descriptive tooltip and accessible name.

## 5. Support keyboard and touch

- An inspectable time series must expose one focusable plot control with
  Left/Right (and Down/Up), Home, and End navigation.
- The control must report its date and values through `aria-valuetext`.
- Touch and pointer-down must use the same nearest-date behavior as hover.
- Do not create dozens of individually tabbable data points.

## 6. Responsive layout

- Validate fully loaded charts at 320px, 390px, 768px, and desktop widths.
- Stack related charts before shrinking typography below the minimum sizes.
- Legends may move from three columns to two and then one; they must not overlap
  the plot or require horizontal page scrolling.
- Dense comparison matrices may retain four compact columns on mobile only when
  the complete row fits; otherwise stack each campaign's metrics.

## 7. Required QA

- Verify populated, zero-data, loading, stale-cache, and error states.
- Exercise hover, tap, focus, arrow keys, Home, and End.
- Confirm tooltip values against the API response and confirm the selected
  metric and date range persist across reloads where the page supports them.
- Check that active markers stay 8px, lines are clipped, labels remain readable,
  and `document.documentElement.scrollWidth === clientWidth` on mobile.
- Run TypeScript, lint, relevant API tests, and the production build before
  committing an analytics surface.
