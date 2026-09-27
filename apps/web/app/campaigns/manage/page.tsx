"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";

import { useAuth } from "../../auth-provider";
import { useLocale } from "../../i18n-provider";
import { CampaignViewNav } from "../campaign-view-nav";
import { useWorkspace } from "../../workspace-provider";
import { ActionIcon } from "../../ui/action-icons";
import { Badge } from "../../ui/primitives";
import { ActionMenu } from "../../ui/action-menu";
import { Button, buttonClass } from "../../ui/button";
import { Select } from "../../ui/select";
import { Tooltip } from "../../ui/tooltip";
import { CampaignWarningsDialog } from "./warnings-dialog";
import { StatusToasts, useStatus } from "../../ui/status";
import { WaitingBlock } from "../../ui/waiting-block";
import { WaitingScreen } from "../../ui/waiting-screen";
import { oneOf, usePersistedState } from "../../ui/use-persisted-state";
import { readTabSnapshot, refreshTabSnapshot } from "../../../lib/tab-snapshots";

import styles from "./manage.module.css";

type Range = "today" | "7d" | "14d" | "28d" | "90d";
type CampaignFilter = "current" | "active" | "draft" | "archived" | "all";
type CampaignSort = "attention" | "views" | "engagement" | "published" | "name";
type ComparisonMetric = "views" | "engagement" | "published";

type Performance = {
  views: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  engagement: number;
  engagement_rate: number | null;
  published: number;
  measured: number;
};

type DailyPoint = {
  date: string;
  views: number;
  engagement: number;
  published: number;
};

type CampaignRow = {
  id: string;
  name: string;
  objective: string;
  audience: string;
  languages: string[];
  status: "draft" | "active" | "archived";
  autopilot_enabled: boolean;
  authority: string | null;
  destinations: number;
  tagged_products: number;
  queue_total: number;
  queue_ready: number;
  scheduled: number;
  next_scheduled_at: string | null;
  pending_approvals: number;
  delivery_warnings: number;
  performance: Performance;
  daily?: DailyPoint[];
};

type Approval = {
  id: string;
  campaign_id: string;
  campaign_name: string;
  title: string | null;
  caption: string;
  platform: string | null;
  destination_label: string | null;
  scheduled_at: string | null;
  created_at: string;
  held_reason: string | null;
  asset_id: string | null;
  has_media: boolean;
};

type ManagementSnapshot = {
  range: Range;
  timezone: string;
  starts_at: string;
  ends_at: string;
  generated_at: string;
  totals: Performance & {
    campaigns: number;
    active: number;
    pending_approvals: number;
    delivery_warnings: number;
  };
  campaigns: CampaignRow[];
  approvals: { total: number; items: Approval[] };
};

const RANGES: readonly [Range, string][] = [
  ["today", "Today"], ["7d", "7 days"], ["14d", "14 days"],
  ["28d", "28 days"], ["90d", "90 days"],
];

const compactNumber = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Campaign overview unavailable.");
  return body;
}

function labelForApproval(item: Approval): string {
  return item.title?.trim() || item.caption.trim().split(/\r?\n/)[0] || "Untitled post";
}

function timeLabel(value: string | null): string {
  if (!value) return "Not scheduled";
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }).format(new Date(value));
}

const SERIES_COLORS = ["#315ca8", "#087d67", "#a35412", "#7a4ab0", "#c13d5a", "#27788d"];

function chartScale(values: number[]) {
  const highest = Math.max(...values, 0);
  if (highest <= 0) return { maximum: 1, ticks: [0, 1] };
  const rough = highest / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / magnitude;
  const step = Math.max(1, (fraction < 1.5 ? 1 : fraction < 3 ? 2 : fraction < 7 ? 5 : 10) * magnitude);
  const maximum = Math.ceil(highest / step) * step;
  return {
    maximum,
    ticks: Array.from({ length: Math.round(maximum / step) + 1 }, (_, index) => index * step),
  };
}

function CampaignComparisonChart({
  rows,
  metric,
}: {
  rows: CampaignRow[];
  metric: ComparisonMetric;
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const series = useMemo(() => [...rows]
    .sort((left, right) => right.performance[metric] - left.performance[metric])
    .slice(0, SERIES_COLORS.length), [metric, rows]);
  const days = series.find((campaign) => campaign.daily?.length)?.daily ?? [];
  const { maximum, ticks } = chartScale(series.flatMap((campaign) => (
    (campaign.daily ?? []).map((day) => day[metric])
  )));
  const plotTop = 5;
  const plotBottom = 55;
  const plotHeight = plotBottom - plotTop;
  const coordinates = (campaign: CampaignRow) => (campaign.daily ?? []).map((day, index) => ({
    day,
    x: campaign.daily?.length === 1 ? 50 : (index / Math.max(1, (campaign.daily?.length ?? 1) - 1)) * 100,
    y: plotBottom - (day[metric] / maximum) * plotHeight,
  }));
  const nearestIndex = (event: ReactPointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const portion = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    return Math.round(portion * Math.max(0, days.length - 1));
  };
  const moveByKeyboard = (event: ReactKeyboardEvent<SVGRectElement>) => {
    const current = activeIndex ?? Math.max(0, days.length - 1);
    let next = current;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") next = Math.max(0, current - 1);
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") next = Math.min(days.length - 1, current + 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = Math.max(0, days.length - 1);
    else return;
    event.preventDefault();
    setActiveIndex(next);
  };
  const dateLabel = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString(
    undefined, { month: "short", day: "numeric" },
  );

  if (!series.length || !days.length) {
    return <div className={styles.empty}><strong>No campaigns to compare</strong></div>;
  }

  return (
    <div className={styles.comparisonChart}>
      <div className={styles.plot}>
        <div className={styles.yLabels} aria-hidden="true">
          {[...ticks].reverse().map((tick) => (
            <span key={tick}>{compactNumber.format(tick)}</span>
          ))}
        </div>
        <div className={styles.canvas}>
          <svg viewBox="0 0 100 60" preserveAspectRatio="none" role="group"
            aria-label={`Daily ${comparisonLabel(metric)} by campaign. Scale 0 to ${compactNumber.format(maximum)}.`}>
            {ticks.map((tick) => {
              const y = plotBottom - (tick / maximum) * plotHeight;
              return <line key={tick} className={styles.gridLine} x1="0" x2="100" y1={y} y2={y} />;
            })}
            {series.map((campaign, index) => {
              const points = coordinates(campaign);
              return (
                <polyline key={campaign.id} className={styles.seriesLine}
                  style={{ "--series-color": SERIES_COLORS[index] } as CSSProperties}
                  points={points.map(({ x, y }) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ")} />
              );
            })}
            {activeIndex !== null ? (
              <line className={styles.guide} x1={(activeIndex / Math.max(1, days.length - 1)) * 100}
                x2={(activeIndex / Math.max(1, days.length - 1)) * 100} y1={plotTop} y2={plotBottom} />
            ) : null}
            <rect className={styles.hitArea} x="0" y="0" width="100" height="60"
              tabIndex={0} role="slider" aria-label="Inspect campaign comparison by date"
              aria-valuemin={0} aria-valuemax={Math.max(0, days.length - 1)}
              aria-valuenow={activeIndex ?? Math.max(0, days.length - 1)}
              aria-valuetext={(() => {
                const index = activeIndex ?? days.length - 1;
                return `${dateLabel(days[index]?.date ?? "")}. ${series.map((campaign) => {
                  const day = campaign.daily?.[index];
                  return `${campaign.name}: ${compactNumber.format(day?.views ?? 0)} views, ${compactNumber.format(day?.engagement ?? 0)} engagements, ${day?.published ?? 0} posts`;
                }).join(". ")}`;
              })()}
              onPointerMove={(event) => setActiveIndex(nearestIndex(event))}
              onPointerDown={(event) => setActiveIndex(nearestIndex(event))}
              onPointerLeave={() => setActiveIndex(null)}
              onFocus={() => setActiveIndex((current) => current ?? days.length - 1)}
              onBlur={() => setActiveIndex(null)} onKeyDown={moveByKeyboard} />
          </svg>
          {activeIndex !== null ? series.map((campaign, index) => {
            const point = coordinates(campaign)[activeIndex];
            return point ? <span key={campaign.id} className={styles.activeDot} aria-hidden="true"
              style={{
                "--series-color": SERIES_COLORS[index],
                "--point-x": `${point.x}%`,
                "--point-y": `${(point.y / 60) * 100}%`,
              } as CSSProperties} /> : null;
          }) : null}
          {activeIndex !== null && days[activeIndex] ? (
            <div className={styles.chartTooltip} role="status"
              style={{ "--tooltip-x": `${Math.min(80, Math.max(20, (activeIndex / Math.max(1, days.length - 1)) * 100))}%` } as CSSProperties}>
              <strong>{dateLabel(days[activeIndex].date)}</strong>
              {series.map((campaign, index) => (
                <span key={campaign.id} style={{ "--series-color": SERIES_COLORS[index] } as CSSProperties}>
                  <i /><em>{campaign.name}</em><b>
                    {compactNumber.format(campaign.daily?.[activeIndex]?.views ?? 0)} views · {compactNumber.format(campaign.daily?.[activeIndex]?.engagement ?? 0)} eng. · {campaign.daily?.[activeIndex]?.published ?? 0} posts
                  </b>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      <div className={styles.xLabels} aria-hidden="true">
        <span>{dateLabel(days[0]?.date ?? "")}</span>
        <span>{dateLabel(days.at(-1)?.date ?? "")}</span>
      </div>
      <ol className={styles.legend} aria-label="Campaign comparison legend">
        {series.map((campaign, index) => (
          <li key={campaign.id} style={{ "--series-color": SERIES_COLORS[index] } as CSSProperties}>
            <i /><span>{campaign.name}</span><strong>{compactNumber.format(campaign.performance[metric])}</strong>
            <small>{campaign.performance.published} published · {campaign.scheduled} scheduled · {campaign.queue_ready} planned</small>
          </li>
        ))}
      </ol>
      {rows.length > series.length ? <small className={styles.chartNote}>Leading {series.length} of {rows.length} campaigns by {comparisonLabel(metric).toLowerCase()}.</small> : null}
    </div>
  );
}

function PipelineComparison({ rows, snapshot }: { rows: CampaignRow[]; snapshot: ManagementSnapshot | null }) {
  const { t, locale } = useLocale();
  const numbers = new Intl.NumberFormat(locale);
  const dates = new Intl.DateTimeFormat(locale, {
    month: "short", day: "numeric", year: "numeric", timeZone: snapshot?.timezone,
  });
  const period = snapshot
    ? `${dates.format(new Date(snapshot.starts_at))} – ${dates.format(new Date(snapshot.ends_at))}`
    : "";
  const hint = (campaign: CampaignRow, kind: "published" | "scheduled" | "planned", value: number) => {
    const lines = [campaign.name, `${numbers.format(value)} · ${t(`campaignPipeline.${kind}`)}`];
    if (kind === "published") {
      lines.push(t("campaignPipeline.publishedHint", { period }));
    } else if (kind === "scheduled") {
      lines.push(t("campaignPipeline.scheduledHint"));
      if (campaign.next_scheduled_at) {
        const at = new Intl.DateTimeFormat(locale, {
          month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
          timeZone: snapshot?.timezone, timeZoneName: "short",
        }).format(new Date(campaign.next_scheduled_at));
        lines.push(t("campaignPipeline.next", { at }));
      }
    } else {
      lines.push(t("campaignPipeline.plannedHint"));
      lines.push(t("campaignPipeline.queueTotal", { total: numbers.format(campaign.queue_total) }));
    }
    return lines.join("\n");
  };
  const series = [...rows]
    .sort((left, right) => (
      right.performance.published + right.scheduled + right.queue_ready
      - left.performance.published - left.scheduled - left.queue_ready
    ))
    .slice(0, SERIES_COLORS.length);
  const maxima = {
    published: Math.max(1, ...series.map((campaign) => campaign.performance.published)),
    scheduled: Math.max(1, ...series.map((campaign) => campaign.scheduled)),
    planned: Math.max(1, ...series.map((campaign) => campaign.queue_ready)),
  };
  return (
    <section className={styles.pipelineChart} aria-labelledby="pipeline-comparison-heading">
      <header>
        <div><p className="section-kicker">Pipeline now</p><h3 id="pipeline-comparison-heading">Output and workload</h3></div>
        <p>Each column uses its own scale so smaller workloads remain readable.</p>
      </header>
      <div className={styles.pipelineColumns} aria-hidden="true">
        <span>Campaign</span>
        <span><i data-kind="published" />{t("campaignPipeline.published")}</span>
        <span><i data-kind="scheduled" />{t("campaignPipeline.scheduled")}</span>
        <span><i data-kind="planned" />{t("campaignPipeline.planned")}</span>
      </div>
      {series.length ? <ol>
        {series.map((campaign) => (
          <li key={campaign.id}>
            <strong>{campaign.name}</strong>
            {([
              ["published", campaign.performance.published],
              ["scheduled", campaign.scheduled],
              ["planned", campaign.queue_ready],
            ] as const).map(([kind, value]) => (
              <Tooltip key={kind} content={hint(campaign, kind, value)}>
                <button type="button" className={styles.pipelineValue} data-kind={kind}
                  aria-label={`${campaign.name}: ${numbers.format(value)} ${t(`campaignPipeline.${kind}`)}`}>
                  <span className={styles.pipelineTrack} aria-hidden="true">
                    <i style={{ inlineSize: `${(value / maxima[kind]) * 100}%` }} />
                  </span>
                  <b>{numbers.format(value)}</b>
                </button>
              </Tooltip>
            ))}
          </li>
        ))}
      </ol> : <div className={styles.empty}><strong>No pipeline activity</strong></div>}
    </section>
  );
}

function comparisonLabel(metric: ComparisonMetric) {
  return metric === "published" ? "Posts published" : metric === "engagement" ? "Engagements" : "Views";
}

export default function CampaignManagementPage() {
  const { t } = useLocale();
  const { apiFetch, loading, user } = useAuth();
  const { workspaceId } = useWorkspace();
  const { messages, fail, dismiss } = useStatus();
  const [range, setRange] = usePersistedState<Range>(
    "trendrelay:campaign-management:range", "28d",
    oneOf("today", "7d", "14d", "28d", "90d"),
  );
  const [filter, setFilter] = usePersistedState<CampaignFilter>(
    "trendrelay:campaign-management:filter", "current",
    oneOf("current", "active", "draft", "archived", "all"),
  );
  const [sort, setSort] = usePersistedState<CampaignSort>(
    "trendrelay:campaign-management:sort", "attention",
    oneOf("attention", "views", "engagement", "published", "name"),
  );
  const [comparison, setComparison] = usePersistedState<ComparisonMetric>(
    "trendrelay:campaign-management:comparison", "views",
    oneOf("views", "engagement", "published"),
  );
  const [snapshot, setSnapshot] = useState<ManagementSnapshot | null>(null);
  const [warningCampaign, setWarningCampaign] = useState<CampaignRow | null>(null);
  const warningTrigger = useRef<HTMLButtonElement | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const snapshotKey = `campaign-management:${workspaceId}:${range}:${timezone}`;

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setRefreshing(true);
    try {
      const body = await refreshTabSnapshot(snapshotKey, async () => json<ManagementSnapshot>(
        await apiFetch(`/api/workspaces/${workspaceId}/campaigns/management?range=${range}`
          + `&timezone=${encodeURIComponent(timezone)}&approval_limit=30`),
      ));
      setSnapshot(body);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Campaign overview unavailable.");
    } finally {
      setRefreshing(false);
    }
  }, [apiFetch, fail, range, snapshotKey, timezone, workspaceId]);

  /** The post a decision is in flight for, so the row can say so and the rest
      hold still rather than letting a second press race the first. */
  const [deciding, setDeciding] = useState<string>("");

  /**
   * Decide one held post without leaving the control room.
   *
   * The panel listed what needed a person and then sent them somewhere else
   * to do it - six posts meant six trips into a campaign and back. These are
   * the same four endpoints the campaign's own inbox calls, so a decision
   * made here is the decision made there: the same finalisation checks, the
   * same delivery block, the same audit trail.
   *
   * The row goes when the snapshot comes back rather than the moment it is
   * pressed. Approval can be refused - a post that is not finished says what
   * to fix - and a row that vanished optimistically would take the reason
   * with it.
   */
  const decide = useCallback(async (
    item: ManagementSnapshot["approvals"]["items"][number],
    action: "approve" | "publish-now" | "skip" | "decline",
  ) => {
    if (!workspaceId || deciding) return;
    const base = `/api/workspaces/${workspaceId}/campaigns/`
      + `${encodeURIComponent(item.campaign_id)}/autopilot/executions/`
      + `${encodeURIComponent(item.id)}`;
    const approving = action === "approve" || action === "publish-now";
    setDeciding(item.id);
    try {
      const response = await apiFetch(approving ? `${base}/approve` : `${base}/dismiss`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(approving
          ? { confirm_external_action: true, publish_now: action === "publish-now" }
          : { stop_proposing: action === "decline" }),
      });
      if (!response.ok) {
        // The reason a post was refused is the useful part - "this post is
        // not finished" names what to fix - so it is shown rather than a
        // generic failure.
        const detail = await response.json().catch(() => null);
        throw new Error(
          (detail && typeof detail.detail === "string" && detail.detail)
          || "That decision could not be made.",
        );
      }
      await load();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "That decision could not be made.");
    } finally {
      setDeciding("");
    }
  }, [apiFetch, deciding, fail, load, workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    const cached = readTabSnapshot<ManagementSnapshot>(snapshotKey);
    if (cached) queueMicrotask(() => setSnapshot(cached));
    queueMicrotask(() => void load());
  }, [load, snapshotKey, workspaceId]);

  const visibleCampaigns = useMemo(() => {
    const rows = snapshot?.campaigns.filter((campaign) => {
      if (filter === "all") return true;
      if (filter === "current") return campaign.status !== "archived";
      return campaign.status === filter;
    }) ?? [];
    return [...rows].sort((left, right) => {
      if (sort === "name") return left.name.localeCompare(right.name);
      if (sort === "views") return right.performance.views - left.performance.views;
      if (sort === "engagement") return right.performance.engagement - left.performance.engagement;
      if (sort === "published") return right.performance.published - left.performance.published;
      const rightAttention = right.pending_approvals * 1000 + right.delivery_warnings;
      const leftAttention = left.pending_approvals * 1000 + left.delivery_warnings;
      return rightAttention - leftAttention || right.performance.views - left.performance.views;
    });
  }, [filter, snapshot, sort]);
  if (loading) return <WaitingScreen className="campaign-page" message="Loading campaigns…" />;
  if (!user) {
    return (
      <main className="campaign-page">
        <Link className={buttonClass({ variant: "primary" })}
          href="/sign-in?next=%2Fcampaigns%2Fmanage">Sign in to manage campaigns</Link>
      </main>
    );
  }

  const totals = snapshot?.totals;
  return (
    <main className={`campaign-page ${styles.page}`}>
      <header className="campaign-heading">
        <div>
          <p className="section-kicker">Campaign operations</p>
          <h1>Campaign control room</h1>
          <p className="lede">Compare every campaign and clear the work that needs you.</p>
        </div>
        <CampaignViewNav />
      </header>

      <StatusToasts messages={messages} onDismiss={dismiss} />

      <section className={styles.toolbar} aria-label="Campaign management controls">
        <div>
          <strong>All-campaign performance</strong>
          <small>Stored provider measurements · no live network calls</small>
        </div>
        <label>
          <span className="sr-only">Performance range</span>
          <Select value={range} onChange={(event) => setRange(event.target.value as Range)}>
            {RANGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
        </label>
        <Button size="sm" spinsIcon busy={refreshing} onClick={() => void load()}>
          <ActionIcon name="refresh" />Refresh
        </Button>
      </section>

      {!snapshot ? (
        <WaitingBlock message="Loading campaign performance…" />
      ) : (
        <>
          <section className={styles.metrics} aria-label="Workspace campaign totals">
            <article><span>Active campaigns</span><strong>{totals?.active ?? 0}</strong><small>{totals?.campaigns ?? 0} total</small></article>
            <article><span>Posts published</span><strong>{compactNumber.format(totals?.published ?? 0)}</strong><small>{totals?.measured ?? 0} measured</small></article>
            <article><span>Views</span><strong>{compactNumber.format(totals?.views ?? 0)}</strong><small>Selected date range</small></article>
            <article><span>Engagements</span><strong>{compactNumber.format(totals?.engagement ?? 0)}</strong><small>{totals?.engagement_rate == null ? "Rate unavailable" : `${totals.engagement_rate}% rate`}</small></article>
            <article data-attention={(totals?.pending_approvals ?? 0) > 0 || undefined}>
              <span>Waiting approval</span><strong>{totals?.pending_approvals ?? 0}</strong>
              <small>{totals?.delivery_warnings ?? 0} delivery warnings</small>
            </article>
          </section>

          <section className={styles.comparison} aria-labelledby="campaign-comparison-heading">
            <header className={styles.sectionHead}>
              <div><p className="section-kicker">Compare</p><h2 id="campaign-comparison-heading">Campaign performance</h2></div>
              <label><span className="sr-only">Comparison metric</span><Select value={comparison} onChange={(event) => setComparison(event.target.value as ComparisonMetric)}>
                <option value="views">Views</option><option value="engagement">Engagement</option><option value="published">Published</option>
              </Select></label>
            </header>
            <div className={styles.comparisonBody}>
              <CampaignComparisonChart rows={visibleCampaigns} metric={comparison} />
              <PipelineComparison rows={visibleCampaigns} snapshot={snapshot} />
            </div>
          </section>

          <div className={styles.layout}>
            <section className={styles.campaigns} aria-labelledby="all-campaigns-heading">
              <header className={styles.sectionHead}>
                <div><h2 id="all-campaigns-heading">All campaigns</h2><small>{visibleCampaigns.length} shown</small></div>
                <div className={styles.filters}>
                  <label><span className="sr-only">Campaign status</span><Select value={filter} onChange={(event) => setFilter(event.target.value as CampaignFilter)}>
                    <option value="current">Current</option><option value="active">Active</option>
                    <option value="draft">Draft</option><option value="archived">Archived</option><option value="all">All</option>
                  </Select></label>
                  <label><span className="sr-only">Sort campaigns</span><Select value={sort} onChange={(event) => setSort(event.target.value as CampaignSort)}>
                    <option value="attention">Needs attention</option><option value="views">Most views</option>
                    <option value="engagement">Most engagement</option><option value="published">Most published</option><option value="name">Name</option>
                  </Select></label>
                </div>
              </header>

              <div className={styles.columnHead} aria-hidden="true">
                <span>Campaign</span><span>Output</span><span>Audience response</span><span>Queue</span><span>Attention</span>
              </div>
              <ol className={styles.campaignList}>
                {visibleCampaigns.map((campaign) => (
                  <li key={campaign.id}>
                    <div className={styles.campaignRow}>
                      <span className={styles.identity}>
                        <span><Link href={`/campaigns?campaign=${encodeURIComponent(campaign.id)}`}><strong>{campaign.name}</strong></Link><Badge tone={campaign.status === "active" ? "good" : "neutral"}>{campaign.status}</Badge></span>
                        <small>{campaign.objective}</small>
                        <em>{campaign.destinations} account{campaign.destinations === 1 ? "" : "s"} · {campaign.tagged_products} products</em>
                      </span>
                      <span className={styles.figure} data-label="Output"><strong>{campaign.performance.published}</strong><small>published · {campaign.scheduled} upcoming</small></span>
                      <span className={styles.figure} data-label="Audience response"><strong>{compactNumber.format(campaign.performance.views)} views</strong><small>{compactNumber.format(campaign.performance.engagement)} engagements{campaign.performance.engagement_rate == null ? "" : ` · ${campaign.performance.engagement_rate}%`}</small></span>
                      <span className={styles.figure} data-label="Queue"><strong>{campaign.queue_ready} ready</strong><small>{campaign.queue_total} total</small></span>
                      <span className={styles.attention} data-label="Attention">
                        {campaign.pending_approvals > 0 && <Link href={`/campaigns?campaign=${encodeURIComponent(campaign.id)}#campaign-approvals`}><Badge tone="warn">{campaign.pending_approvals} approval{campaign.pending_approvals === 1 ? "" : "s"}</Badge></Link>}
                        {campaign.delivery_warnings > 0 && <Button variant="link" size="sm"
                          aria-label={t("campaignWarnings.open", { campaign: campaign.name, count: campaign.delivery_warnings })}
                          onClick={(event) => { warningTrigger.current = event.currentTarget; setWarningCampaign(campaign); }}><Badge tone="bad">{campaign.delivery_warnings} warning{campaign.delivery_warnings === 1 ? "" : "s"}</Badge></Button>}
                        {campaign.pending_approvals === 0 && campaign.delivery_warnings === 0 && <small>Clear</small>}
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
              {warningCampaign && snapshot && <CampaignWarningsDialog
                key={`${workspaceId}:${warningCampaign.id}:${snapshot.starts_at}:${snapshot.ends_at}`}
                campaign={warningCampaign} workspaceId={workspaceId} startsAt={snapshot.starts_at}
                endsAt={snapshot.ends_at} timezone={snapshot.timezone} apiFetch={apiFetch}
                onClose={() => { setWarningCampaign(null); requestAnimationFrame(() => warningTrigger.current?.focus({ preventScroll: true })); }} />}
              {visibleCampaigns.length === 0 && <div className={styles.empty}><strong>No campaigns in this view</strong><small>Change the status filter or create a campaign in Workspace.</small></div>}
            </section>

            <div className={styles.rightRail}>
              <aside className={styles.approvals} aria-labelledby="approval-inbox-heading">
                <header className={styles.sectionHead}>
                  <div><p className="section-kicker">Approval inbox</p><h2 id="approval-inbox-heading">Pending posts</h2></div>
                  {snapshot.approvals.total > 0 && <Badge tone="warn">{snapshot.approvals.total}</Badge>}
                </header>
                {snapshot.approvals.items.length > 0 ? (
                  <ol className={styles.approvalList}>
                    {snapshot.approvals.items.map((item) => (
                      <li key={item.id}>
                        {/* The row reads as one thing and acts as another: the
                            post opens where it can be read in full, and the
                            decisions sit on the line the time already owns.
                            A second row of controls per post would have cost
                            six of them the height of the panel. */}
                        <Link href={`/campaigns?campaign=${encodeURIComponent(item.campaign_id)}#campaign-approvals`}>
                          <span className={styles.approvalTop}><strong>{item.campaign_name}</strong><Badge tone="warn">Waiting</Badge></span>
                          <b>{labelForApproval(item)}</b>
                          <span>{[item.platform, item.destination_label].filter(Boolean).join(" · ") || "Destination pending"}</span>
                        </Link>
                        <div className={styles.approvalActions}>
                          <small>{timeLabel(item.scheduled_at ?? item.created_at)}</small>
                          <Tooltip content={`Approve this post. It goes out on ${item.campaign_name}'s schedule; one past its own time takes the next free slot.`}>
                            <Button variant="primary" size="sm" iconOnly
                              busy={deciding === item.id}
                              disabled={Boolean(deciding) && deciding !== item.id}
                              aria-label={`Approve ${labelForApproval(item)}`}
                              onClick={() => void decide(item, "approve")}>
                              <ActionIcon name="confirm" />
                            </Button>
                          </Tooltip>
                          {/* Publishing now and refusing a post are both
                              things somebody should mean, so they are one
                              press further away than approving. */}
                          <ActionMenu
                            label="More"
                            ariaLabel={`More for ${labelForApproval(item)}`}
                            disabled={Boolean(deciding)}
                            items={[
                              { id: "now", label: "Publish now",
                                description: "Send it immediately instead of waiting for its slot." },
                              { id: "skip", label: "Skip this time",
                                description: "Frees the slot and the clip; the post returns next cycle." },
                              { id: "decline", label: "Decline",
                                description: "Also pauses the post, so it stops being proposed." },
                            ]}
                            onSelect={(id) => {
                              if (id === "now") {
                                if (!window.confirm(
                                  `Publishes to ${item.destination_label ?? item.platform ?? "its account"} immediately instead of waiting for its slot. Continue?`,
                                )) return;
                                void decide(item, "publish-now");
                              } else {
                                void decide(item, id === "skip" ? "skip" : "decline");
                              }
                            }} />
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className={styles.empty}><strong>Nothing waiting</strong><small>Autonomous work continues without an approval detour.</small></div>
                )}
                {snapshot.approvals.total > snapshot.approvals.items.length && (
                  <small className={styles.more}>Showing the oldest {snapshot.approvals.items.length} of {snapshot.approvals.total}. Open a campaign to finish its inbox.</small>
                )}
              </aside>
            </div>
          </div>
        </>
      )}
    </main>
  );
}
