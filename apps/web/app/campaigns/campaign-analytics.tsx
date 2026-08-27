"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { BarChart3, Eye, FileCheck2, Heart, ImageIcon } from "lucide-react";

import { ActionIcon } from "../ui/action-icons";
import { Button } from "../ui/button";
import { Card } from "../ui/primitives";
import { SegmentedControl } from "../ui/segmented";
import { Select } from "../ui/select";
import { WaitingBlock } from "../ui/waiting-block";
import { useOpaqueMedia } from "../../lib/media-preview";

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
  const values = data.map((day) => day[metric]);
  const maximum = Math.max(...values, 1);
  const points = values.map((value, index) => {
    const x = values.length === 1 ? 50 : (index / (values.length - 1)) * 100;
    const y = 42 - (value / maximum) * 36;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const area = `0,42 ${points} 100,42`;
  const first = data.at(0)?.date;
  const last = data.at(-1)?.date;

  return (
    <div className="campaign-analytics-trend">
      <svg viewBox="0 0 100 44" preserveAspectRatio="none" role="img"
        aria-label={`${CHART_LABELS[metric]} trend. ${compact(values.reduce((sum, value) => sum + value, 0))} total.`}>
        <line x1="0" x2="100" y1="42" y2="42" />
        <polygon points={area} />
        <polyline points={points} />
      </svg>
      <span><time dateTime={first}>{first ? new Date(`${first}T00:00:00`).toLocaleDateString(undefined, {
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
  workspaceId,
  title,
  apiFetch,
}: {
  assetId: string | null;
  workspaceId: string;
  title: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  const source = assetId
    ? `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/content/thumbnail`
    : "";
  const { objectUrl } = useOpaqueMedia(
    source,
    "thumbnail.jpg",
    "image/jpeg",
    Boolean(assetId),
    apiFetch,
  );
  return objectUrl ? (
    // The post title is printed immediately below, so the visual is decorative
    // rather than the same accessible name announced twice.
    // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
    <img src={objectUrl} alt="" title={title} />
  ) : (
    <span className="campaign-top-thumb-empty" aria-hidden="true"><ImageIcon /></span>
  );
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
  const [range, setRange] = useState<AnalyticsRange>("28d");
  const [ranking, setRanking] = useState<RankingMetric>("views");
  const [chartMetric, setChartMetric] = useState<ChartMetric>("views");
  const [data, setData] = useState<Analytics | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

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
                <ol>
                  {data.top_content.map((post, index) => (
                    <li key={post.id}>
                      <div className="campaign-top-thumb">
                        <TopPostThumbnail assetId={post.asset_id} workspaceId={workspaceId}
                          title={post.title} apiFetch={apiFetch} />
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
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="campaign-analytics-empty">No measured posts were published in this period.</p>
              )}
            </section>

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
