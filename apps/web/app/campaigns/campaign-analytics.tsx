"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { BarChart3, ExternalLink, Eye, FileCheck2, Heart, ImageIcon } from "lucide-react";

import { ActionIcon } from "../ui/action-icons";
import { Button } from "../ui/button";
import { Card } from "../ui/primitives";
import { SegmentedControl } from "../ui/segmented";
import { Select } from "../ui/select";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";
import { WaitingBlock } from "../ui/waiting-block";
import { useOpaqueMedia } from "../../lib/media-preview";
import { platformLabels, type PublishingPlatform } from "../publishing-icons";

type AnalyticsRange = "today" | "7d" | "28d" | "90d";
type RankingMetric = "views" | "engagement" | "likes" | "comments" | "shares" | "saves";
type ChartMetric = "views" | "engagement" | "published";

type Totals = {
  views: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  watch_seconds: number;
  engagement: number;
  published: number;
  measured: number;
  engagement_rate: number | null;
};

type Analytics = {
  range: AnalyticsRange;
  sort: RankingMetric;
  timezone: string;
  starts_at: string;
  ends_at: string;
  current: Totals;
  previous: Totals;
  daily: { date: string; views: number; engagement: number; published: number }[];
  top_content: {
    id: string;
    asset_id: string | null;
    media_path: string;
    image_paths: string[];
    title: string;
    platform: string | null;
    destination: string | null;
    published_at: string;
    post_url: string | null;
    views: number;
    engagement: number;
    likes: number;
    comments: number;
    shares: number;
    saves: number;
  }[];
  coverage: {
    published: number;
    measured: number;
    last_measured_at: string | null;
    unreportable: {
      engine: string; label: string; reason: string; destinations: string[];
    }[];
  };
};

const RANGE_OPTIONS = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "28d", label: "28 days" },
  { value: "90d", label: "90 days" },
] as const;

const RANKING_LABELS: Record<RankingMetric, string> = {
  views: "Views",
  engagement: "Engagement",
  likes: "Likes",
  comments: "Comments",
  shares: "Shares",
  saves: "Saves",
};

const CHART_LABELS: Record<ChartMetric, string> = {
  views: "Views",
  engagement: "Engagement",
  published: "Content published",
};

function compact(value: number) {
  return new Intl.NumberFormat(undefined, {
    notation: Math.abs(value) >= 1_000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(value) >= 1_000 ? 1 : 0,
  }).format(value);
}

/** A human scale: 184 tops out at 200, not at the data's arbitrary peak. */
function trendScale(values: number[]) {
  const highest = Math.max(...values, 0);
  if (highest <= 0) return { maximum: 1, ticks: [0, 1] };
  const roughStep = highest / 4;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const fraction = roughStep / magnitude;
  const niceFraction = fraction < 1.5 ? 1 : fraction < 3 ? 2 : fraction < 7 ? 5 : 10;
  // These are counts. Decimal ticks imply precision the providers do not
  // report and are especially noisy for daily post counts.
  const step = Math.max(1, niceFraction * magnitude);
  const maximum = Math.ceil(highest / step) * step;
  const ticks = Array.from(
    { length: Math.round(maximum / step) + 1 },
    (_, index) => index * step,
  );
  return { maximum, ticks };
}

function comparison(current: number | null, previous: number | null) {
  if (current === null) return { label: "Not available", tone: "flat" };
  const baseline = previous ?? 0;
  if (baseline === 0) {
    return current > 0
      ? { label: "New in this period", tone: "up" }
      : { label: "No change", tone: "flat" };
  }
  const percent = ((current - baseline) / baseline) * 100;
  return {
    label: `${percent >= 0 ? "+" : ""}${percent.toFixed(Math.abs(percent) >= 10 ? 0 : 1)}%`,
    tone: percent > 0 ? "up" : percent < 0 ? "down" : "flat",
  };
}

function periodLabel(data: Analytics) {
  const format = new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", timeZone: data.timezone,
  });
  return `${format.format(new Date(data.starts_at))}–${format.format(new Date(data.ends_at))}`;
}

function TrendLine({
  data,
  metric,
}: {
  data: Analytics["daily"];
  metric: ChartMetric;
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const values = data.map((day) => day[metric]);
  const { maximum, ticks } = trendScale(values);
  const plotTop = 4;
  const plotBottom = 42;
  const plotHeight = plotBottom - plotTop;
  const points = data.map((day, index) => {
    const value = day[metric];
    const x = values.length === 1 ? 50 : (index / (values.length - 1)) * 100;
    const y = plotBottom - (value / maximum) * plotHeight;
    return { day, x, y };
  });
  const path = points.map(({ x, y }) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `0,${plotBottom} ${path} 100,${plotBottom}`;
  const first = data.at(0)?.date;
  const last = data.at(-1)?.date;
  const active = activeIndex === null ? null : points[activeIndex] ?? null;
  /**
   * The one point worth labelling without being asked.
   *
   * A tooltip enhances; it must not be the only way to read a number, and the
   * peak is the number everybody wants - the chart's whole shape is one spike
   * and "how high" was unanswerable without finding the hover.
   *
   * The extreme only, never a number on every point: direct labels work
   * because they are sparing. Nothing is labelled when the series is flat or
   * empty, because then there is no extreme to point at.
   */
  const peakIndex = values.reduce(
    (best, value, index) => (value > (values[best] ?? -1) ? index : best),
    0,
  );
  const peak = values.length > 1
    && (values[peakIndex] ?? 0) > 0
    && new Set(values).size > 1
    ? points[peakIndex] ?? null
    : null;
  const spokenPoint = active ?? points.at(-1);
  const dateLabel = (date: string) => new Date(`${date}T00:00:00`).toLocaleDateString(
    undefined,
    { month: "short", day: "numeric" },
  );
  const pointLabel = (day: Analytics["daily"][number]) => [
    dateLabel(day.date),
    `${compact(day.views)} views`,
    `${compact(day.engagement)} engagements`,
    `${compact(day.published)} ${day.published === 1 ? "post" : "posts"} published`,
  ].join(". ");
  const nearestIndex = (event: ReactPointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const portion = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    return Math.round(portion * Math.max(0, points.length - 1));
  };
  const moveByKeyboard = (event: ReactKeyboardEvent<SVGRectElement>) => {
    const current = activeIndex ?? points.length - 1;
    let next = current;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") next = Math.max(0, current - 1);
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      next = Math.min(points.length - 1, current + 1);
    } else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = points.length - 1;
    else return;
    event.preventDefault();
    setActiveIndex(next);
  };

  return (
    <div className="campaign-analytics-trend">
      <div className="campaign-trend-plot">
        <div className="campaign-trend-y-labels" aria-hidden="true">
          {[...ticks].reverse().map((tick) => {
            const y = plotBottom - (tick / maximum) * plotHeight;
            return (
              <span key={tick} style={{
                "--trend-tick-y": `${(y / 44) * 100}%`,
              } as CSSProperties}>{compact(tick)}</span>
            );
          })}
        </div>
        <div className="campaign-trend-canvas">
          <svg viewBox="0 0 100 44" preserveAspectRatio="none" role="group"
            aria-label={`${CHART_LABELS[metric]} trend. Y-axis from 0 to ${compact(maximum)}. ${compact(values.reduce((sum, value) => sum + value, 0))} total.`}>
            {ticks.map((tick) => {
              const y = plotBottom - (tick / maximum) * plotHeight;
              return <line key={`grid-${tick}`} className="campaign-trend-gridline"
                x1="0" x2="100" y1={y} y2={y} />;
            })}
            <line className="campaign-trend-y-axis" x1="0" x2="0"
              y1={plotTop} y2={plotBottom} />
            {ticks.map((tick) => {
              const y = plotBottom - (tick / maximum) * plotHeight;
              return <line key={`tick-${tick}`} className="campaign-trend-y-tick"
                x1="0" x2="0.8" y1={y} y2={y} />;
            })}
            <polygon points={area} />
            <polyline points={path} />
            {active ? (
              <line className="campaign-trend-guide" x1={active.x} x2={active.x}
                y1={plotTop} y2={plotBottom} />
            ) : null}
            <rect className="campaign-trend-hit-area" x="0" y="0" width="100" height="44"
              tabIndex={0} role="slider" aria-label="Inspect the campaign trend by date"
              aria-valuemin={0} aria-valuemax={Math.max(0, points.length - 1)}
              aria-valuenow={activeIndex ?? Math.max(0, points.length - 1)}
              aria-valuetext={spokenPoint ? pointLabel(spokenPoint.day) : "No chart data"}
              onPointerMove={(event) => setActiveIndex(nearestIndex(event))}
              onPointerDown={(event) => setActiveIndex(nearestIndex(event))}
              onPointerLeave={() => setActiveIndex(null)}
              onFocus={() => setActiveIndex((current) => current ?? points.length - 1)}
              onBlur={() => setActiveIndex(null)}
              onKeyDown={moveByKeyboard} />
          </svg>
          {/* Hidden while that point is the hovered one - the tooltip is
              already saying it, and two readouts of one number at one place
              read as two numbers. */}
          {peak && peakIndex !== activeIndex ? (
            <span className="campaign-trend-peak" aria-hidden="true"
              /* Below its point when there is no room above it. A label that
                 does not fit is moved, never clipped, and a peak that fills
                 the plot leaves nothing overhead. */
              data-below={peak.y / 44 < 0.2 ? "" : undefined}
              style={{
                // Clamped so a label at either end is not clipped by the plot
                // it belongs to; it is centred on its point everywhere else.
                "--trend-point-x": `${Math.min(92, Math.max(8, peak.x))}%`,
                "--trend-point-y": `${(peak.y / 44) * 100}%`,
              } as CSSProperties}>{compact(values[peakIndex] ?? 0)}</span>
          ) : null}
          {active ? (
            <span className="campaign-trend-active-dot" aria-hidden="true"
              style={{
                "--trend-point-x": `${active.x}%`,
                "--trend-point-y": `${(active.y / 44) * 100}%`,
              } as CSSProperties} />
          ) : null}
          {active ? (
            <div className="campaign-trend-tooltip" role="status"
              style={{ "--trend-x": `${Math.min(82, Math.max(18, active.x))}%` } as CSSProperties}>
              <strong>{dateLabel(active.day.date)}</strong>
              <dl>
                <div className={metric === "views" ? "selected" : ""}>
                  <dt>Views</dt><dd>{compact(active.day.views)}</dd>
                </div>
                <div className={metric === "engagement" ? "selected" : ""}>
                  <dt>Engagement</dt><dd>{compact(active.day.engagement)}</dd>
                </div>
                <div className={metric === "published" ? "selected" : ""}>
                  <dt>Published</dt><dd>{compact(active.day.published)}</dd>
                </div>
              </dl>
            </div>
          ) : null}
        </div>
      </div>
      <span className="campaign-trend-dates"><time dateTime={first}>{first ? new Date(`${first}T00:00:00`).toLocaleDateString(undefined, {
        month: "short", day: "numeric",
      }) : ""}</time><time dateTime={last}>{last ? new Date(`${last}T00:00:00`).toLocaleDateString(undefined, {
        month: "short", day: "numeric",
      }) : ""}</time></span>
    </div>
  );
}

function Scorecard({
  label,
  value,
  previous,
  icon,
  selected = false,
  onClick,
  suffix = "",
  hint,
}: {
  label: string;
  value: number | null;
  previous: number | null;
  icon: ReactNode;
  selected?: boolean;
  onClick?: () => void;
  suffix?: string;
  hint?: string;
}) {
  const change = comparison(value, previous);
  const body = (
    <>
      <span className="campaign-analytics-card-label">{icon}<strong>{label}</strong></span>
      <b>{value === null ? "—" : `${compact(value)}${suffix}`}</b>
      <small className={change.tone}>{change.label} <span>vs previous period</span></small>
    </>
  );
  return onClick ? (
    <button type="button" className={selected ? "selected" : ""} onClick={onClick}
      aria-pressed={selected} title={hint ?? `Show ${label.toLowerCase()} trend`}>
      {body}
    </button>
  ) : <article title={hint}>{body}</article>;
}

function TopPostThumbnail({
  assetId,
  mediaPath,
  imagePaths,
  workspaceId,
  title,
  apiFetch,
}: {
  assetId: string | null;
  mediaPath: string;
  imagePaths: string[];
  workspaceId: string;
  title: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  const frozenPath = imagePaths[0] ?? mediaPath;
  // A published execution retains the exact frozen media path even when an
  // old Library row has no generated thumbnail. The publishing preview route
  // serves stills directly and resolves a video's Library thumbnail, making
  // it the durable source for both kinds. Asset identity remains the fallback
  // for records created before frozen paths were returned by analytics.
  const source = frozenPath
    ? `/api/workspaces/${workspaceId}/publishing/media/preview?thumbnail=true&path=${encodeURIComponent(frozenPath)}`
    : assetId
      ? `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/content/thumbnail`
      : "";
  const { objectUrl } = useOpaqueMedia(
    source,
    "thumbnail.jpg",
    "image/jpeg",
    Boolean(source),
    apiFetch,
  );
  return objectUrl ? (
    // The post title is printed immediately below, so the visual is decorative
    // rather than the same accessible name announced twice.
    // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
    <img src={objectUrl} alt="" title={title} draggable={false} />
  ) : (
    <span className="campaign-top-thumb-empty" aria-hidden="true"><ImageIcon /></span>
  );
}

function socialPlatformLabel(platform: string | null) {
  if (!platform || !(platform in platformLabels)) return "social platform";
  return platformLabels[platform as PublishingPlatform];
}

export function CampaignAnalytics({
  base,
  workspaceId,
  timezone,
  apiFetch,
}: {
  base: string;
  workspaceId: string;
  timezone: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  /**
   * How somebody wants to look at a campaign, remembered per workspace.
   *
   * These were scoped per campaign, so switching from one to the next reset
   * the window and the sort. That is the wrong boundary for what they are: a
   * period and a sort are a way of looking, not a fact about a campaign, and
   * switching campaigns is usually *comparing* them - which four different
   * windows quietly makes impossible. Setting "7 days" on one and reading
   * "28 days" on the next, with nothing on screen saying they differ, is worse
   * than not remembering at all.
   *
   * Per workspace rather than global because a workspace is a brand with its
   * own reporting rhythm. The other campaign preferences beside this one - the
   * work tab, the timeline view, the queue filter - are already carried across
   * campaigns, so this was also the only one that did not.
   */
  const preferenceScope = `trendrelay.campaigns.analytics.${workspaceId}`;
  const [range, setRange] = usePersistedState<AnalyticsRange>(
    `${preferenceScope}.range`,
    "28d",
    oneOf<AnalyticsRange>("today", "7d", "28d", "90d"),
  );
  const [ranking, setRanking] = usePersistedState<RankingMetric>(
    `${preferenceScope}.ranking`,
    "views",
    oneOf<RankingMetric>("views", "engagement", "likes", "comments", "shares", "saves"),
  );
  const [chartMetric, setChartMetric] = usePersistedState<ChartMetric>(
    `${preferenceScope}.chartMetric`,
    "views",
    oneOf<ChartMetric>("views", "engagement", "published"),
  );
  const [data, setData] = useState<Analytics | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const topPostsDrag = useRef({
    pointerId: null as number | null,
    startX: 0,
    startScrollLeft: 0,
    moved: false,
  });
  const [draggingTopPosts, setDraggingTopPosts] = useState(false);

  const startTopPostsDrag = (event: ReactPointerEvent<HTMLOListElement>) => {
    if (!event.isPrimary || event.button !== 0) return;
    topPostsDrag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: event.currentTarget.scrollLeft,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveTopPostsDrag = (event: ReactPointerEvent<HTMLOListElement>) => {
    const drag = topPostsDrag.current;
    if (drag.pointerId !== event.pointerId) return;
    const distance = event.clientX - drag.startX;
    if (!drag.moved && Math.abs(distance) < 5) return;
    if (!drag.moved) {
      drag.moved = true;
      setDraggingTopPosts(true);
    }
    event.preventDefault();
    event.currentTarget.scrollLeft = drag.startScrollLeft - distance;
  };
  const finishTopPostsDrag = (event: ReactPointerEvent<HTMLOListElement>) => {
    const drag = topPostsDrag.current;
    if (drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    drag.pointerId = null;
    setDraggingTopPosts(false);
    window.setTimeout(() => { drag.moved = false; }, 0);
  };
  const moveTopPostsByKeyboard = (event: ReactKeyboardEvent<HTMLOListElement>) => {
    if (event.target !== event.currentTarget || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    event.currentTarget.scrollBy({
      left: event.key === "ArrowLeft" ? -event.currentTarget.clientWidth * 0.72 : event.currentTarget.clientWidth * 0.72,
      behavior: "smooth",
    });
  };

  const load = useCallback(async (signal?: AbortSignal) => {
    setBusy(true);
    setFailure(null);
    try {
      const query = new URLSearchParams({ range, sort: ranking, timezone });
      const response = await apiFetch(`${base}/analytics?${query}`, { signal });
      const body = await response.json() as Analytics & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Campaign analytics are unavailable.");
      setData(body);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setFailure(reason instanceof Error ? reason.message : "Campaign analytics are unavailable.");
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }, [apiFetch, base, range, ranking, timezone]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [load, refreshKey]);

  const scorecards = useMemo(() => data ? [
    {
      key: "views" as const, label: "Views", value: data.current.views,
      previous: data.previous.views, icon: <Eye size={17} aria-hidden="true" />,
      hint: "Provider-reported views on posts published in this period.",
    },
    {
      key: "engagement" as const, label: "Engagement", value: data.current.engagement,
      previous: data.previous.engagement, icon: <Heart size={17} aria-hidden="true" />,
      hint: "Likes, comments, shares and saves on posts published in this period.",
    },
    {
      key: "published" as const, label: "Content published", value: data.current.published,
      previous: data.previous.published, icon: <FileCheck2 size={17} aria-hidden="true" />,
      hint: "Posts confirmed published by a connected publishing service.",
    },
  ] : [], [data]);

  return (
    <Card className="campaign-analytics" eyebrow="Overview" title="Performance"
      aside={
        <div className="campaign-analytics-controls">
          <SegmentedControl label="Analytics date range" value={range}
            options={RANGE_OPTIONS} onChange={setRange} />
          <Button variant="secondary" size="sm" busy={busy} spinsIcon
            aria-label="Refresh campaign analytics" title="Refresh campaign analytics"
            onClick={() => setRefreshKey((value) => value + 1)}>
            <ActionIcon name="refresh" /> Refresh
          </Button>
        </div>
      }>
      {!data && busy ? <WaitingBlock message="Loading campaign performance…" /> : null}
      {failure && !data ? <p className="autopilot-refusal" role="alert">{failure}</p> : null}
      {data ? (
        <div className="campaign-analytics-body" aria-busy={busy || undefined}>
          <header className="campaign-analytics-period">
            <span>{periodLabel(data)}</span>
            <small>Compared with the preceding {data.range === "today" ? "day" : RANGE_OPTIONS.find((item) => item.value === data.range)?.label.toLowerCase()}.</small>
          </header>
          {failure ? <p className="autopilot-refusal" role="alert">{failure}</p> : null}
          <div className="campaign-analytics-scorecards" aria-label="Performance scorecards">
            {scorecards.map(({ key, ...card }) => (
              <Scorecard key={key} {...card}
                selected={chartMetric === key}
                onClick={() => {
                  setChartMetric(key);
                  if (key !== "published") setRanking(key);
                }} />
            ))}
            <Scorecard label="Engagement rate"
              value={data.current.engagement_rate}
              previous={data.previous.engagement_rate}
              suffix="%" icon={<BarChart3 size={17} aria-hidden="true" />}
              hint="Engagement divided by provider-reported views. Missing view data is not treated as zero." />
          </div>
          <section className="campaign-analytics-chart" aria-labelledby="campaign-trend-title">
            <div>
              <strong id="campaign-trend-title">{CHART_LABELS[chartMetric]}</strong>
              <small>Grouped by the date each post was published</small>
            </div>
            <TrendLine data={data.daily} metric={chartMetric} />
          </section>

          <div className="campaign-analytics-lower">
            <section className="campaign-top-content" aria-labelledby="campaign-top-content-title">
              <header>
                <div>
                  <strong id="campaign-top-content-title">
                    Top posts by {RANKING_LABELS[ranking].toLowerCase()}
                  </strong>
                  <small>Published in the selected date range</small>
                </div>
                <label>Sort by
                  <Select value={ranking} onChange={(event) => setRanking(event.target.value as RankingMetric)}>
                    {Object.entries(RANKING_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </Select>
                </label>
              </header>
              {data.top_content.length ? (
                <ol tabIndex={0}
                  className={draggingTopPosts ? "is-dragging" : undefined}
                  aria-label="Top posts. Drag horizontally or use the left and right arrow keys to browse."
                  onPointerDown={startTopPostsDrag}
                  onPointerMove={moveTopPostsDrag}
                  onPointerUp={finishTopPostsDrag}
                  onPointerCancel={finishTopPostsDrag}
                  onKeyDown={moveTopPostsByKeyboard}
                  onClickCapture={(event) => {
                    if (!topPostsDrag.current.moved) return;
                    event.preventDefault();
                    event.stopPropagation();
                    topPostsDrag.current.moved = false;
                  }}>
                  {data.top_content.map((post, index) => (
                    <li key={post.id}>
                      <div className="campaign-top-thumb">
                        {post.post_url ? (
                          <a className="campaign-top-thumb-link" href={post.post_url}
                            target="_blank" rel="noreferrer"
                            aria-label={`Open ${post.title} on ${socialPlatformLabel(post.platform)}`}>
                            <TopPostThumbnail assetId={post.asset_id} mediaPath={post.media_path}
                              imagePaths={post.image_paths} workspaceId={workspaceId}
                              title={post.title} apiFetch={apiFetch} />
                          </a>
                        ) : (
                          <TopPostThumbnail assetId={post.asset_id} mediaPath={post.media_path}
                            imagePaths={post.image_paths} workspaceId={workspaceId}
                            title={post.title} apiFetch={apiFetch} />
                        )}
                        <span className="campaign-top-rank">{index + 1}</span>
                      </div>
                      <div className="campaign-top-copy">
                        <strong>{post.post_url ? (
                          <a href={post.post_url} target="_blank" rel="noreferrer">{post.title}</a>
                        ) : post.title}</strong>
                        <small>{[post.destination, post.platform, new Date(post.published_at).toLocaleDateString()]
                          .filter(Boolean).join(" · ")}</small>
                        <span className="campaign-top-metric">
                          <strong>{compact(post[ranking])}</strong>
                          <small>{RANKING_LABELS[ranking]}</small>
                        </span>
                        {post.post_url ? (
                          <a className="campaign-top-open" href={post.post_url}
                            target="_blank" rel="noreferrer">
                            Open on {socialPlatformLabel(post.platform)}
                            <ExternalLink size={11} aria-hidden="true" />
                          </a>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="campaign-analytics-empty">No measured posts were published in this period.</p>
              )}
            </section>

            {/* A strip under the posts rather than a column beside them.
                
                Three short lines of provenance held a 220px-minimum column at
                .7fr - about a quarter of the row - and `align-items: start`
                left the rest of that column empty all the way down. The posts
                are the thing somebody came to look at, and they were sharing
                a scroller with the space this was not using.
                
                It also belongs here: the coverage qualifies every number
                above it, the chart as much as the cards, so it reads better
                as a footnote to the section than as a neighbour of one part
                of it. */}
            <aside className="campaign-analytics-coverage">
              <strong>Data coverage</strong>
              <p><b>{data.coverage.measured}</b> of <b>{data.coverage.published}</b> published posts have provider metrics.</p>
              {data.coverage.last_measured_at ? (
                <small>Last provider read {new Date(data.coverage.last_measured_at).toLocaleString()}.</small>
              ) : <small>No provider measurement has landed in this period yet.</small>}
              {data.coverage.unreportable.map((gap) => (
                <p className="campaign-analytics-gap" key={gap.engine} title={gap.reason}>
                  <strong>{gap.label}</strong> cannot report engagement for {gap.destinations.join(", ")}.
                </p>
              ))}
            </aside>
          </div>
        </div>
      ) : null}
    </Card>
  );
}
