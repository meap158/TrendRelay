"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
import { useAuth } from "../auth-provider";
import { useJobs } from "../jobs-provider";
import { WorkspaceSectionNav } from "../workspace-section-nav";

type Workspace = { id: string; name: string; role: string };
type ReachChannel = {
  id: string;
  status: "ready" | "setup-required" | "unavailable";
  detail: string;
};
type ResearchProviders = {
  last30days: { installed: boolean; active: boolean; engine_present: boolean };
  agent_reach: {
    provider: { installed: boolean; active: boolean };
    summary: { total: number; ready: number; setup_required: number; unavailable: number };
    channels: ReachChannel[];
  };
  meta_ads: {
    installed: boolean;
    active: boolean;
    ready: boolean;
    social_cli_present: boolean;
  };
  meta_ads_collector: {
    installed: boolean;
    active: boolean;
    ready: boolean;
    runtime_present: boolean;
  };
};
type Observation = {
  source?: string;
  title?: string;
  summary?: string;
  metrics?: Record<string, number>;
  evidence?: { source_url?: string };
};
type ResearchJob = {
  id: string;
  topic: string;
  status: string;
  created_at: string;
  error?: string | null;
  observations?: Observation[];
  source_status?: Record<string, unknown>;
};
type Range = { lower_bound?: number | null; upper_bound?: number | null };
type PublicAd = {
  id: string;
  page?: { name?: string } | null;
  is_active?: boolean | null;
  creatives?: Array<{
    body?: string | null;
    title?: string | null;
    image_url?: string | null;
    thumbnail_url?: string | null;
  }>;
  snapshot_url?: string | null;
  impressions?: Range | null;
  spend?: (Range & { currency?: string | null }) | null;
  publisher_platforms?: string[];
};
type AdSearchResult = { query: string; country: string; collected: number; ads: PublicAd[] };
type AdSignal = {
  name: string;
  campaign: string;
  spend: number;
  ctr: number;
  cpc: number;
  frequency: number;
};
type MetaBriefing = {
  preset: string;
  summary: { active_campaigns: number; ads_analyzed: number };
  signals: { winners: AdSignal[]; bleeders: AdSignal[]; fatigue: AdSignal[] };
};
type InspirationKind = "trend" | "ad" | "account" | "starter";
type Inspiration = {
  id: string;
  kind: InspirationKind;
  label: string;
  title: string;
  summary: string;
  source: string;
  href?: string;
  image?: string;
  metrics?: string[];
  topic?: string;
  relevance?: number;
};

const AFFILIATE_STARTERS: Inspiration[] = [
  {
    id: "starter-1",
    kind: "starter",
    label: "TikTok Shop Trend",
    title: "#TikTokMadeMeBuyIt - Viral Home Gadgets",
    summary: "High conversion rates on aesthetic home organization and cleaning tools. Audiences engage heavily with ASMR-style restock videos.",
    source: "Affiliate Signals",
    metrics: ["Conversion potential: Very High", "Avg. Commission: 10-15%"],
    relevance: 95,
    topic: "Home organization gadgets",
  },
  {
    id: "starter-2",
    kind: "starter",
    label: "Amazon Associates",
    title: "Travel Essentials - Packing Hacks",
    summary: "Packing cubes and travel tech accessories are showing a sustained spike in affiliate demand heading into the season.",
    source: "Affiliate Signals",
    metrics: ["Conversion potential: High", "Avg. Commission: 4%"],
    relevance: 88,
    topic: "Amazon travel essentials",
  },
  {
    id: "starter-3",
    kind: "starter",
    label: "Creator Commission",
    title: "Skincare Dupes - Drugstore vs High-end",
    summary: "Side-by-side comparison formats of popular luxury skincare alternatives remain top performers for creator affiliate links.",
    source: "Affiliate Signals",
    metrics: ["Conversion potential: High", "Engagement rate: 8.5%"],
    relevance: 92,
    topic: "Affiliate skincare dupes",
  }
];

function formatRange(range?: Range | null, currency?: string | null): string {
  if (!range || (range.lower_bound == null && range.upper_bound == null)) {
    return "Not reported";
  }
  const lower = range.lower_bound?.toLocaleString() ?? "0";
  const upper = range.upper_bound?.toLocaleString() ?? "unbounded";
  return `${currency ? `${currency} ` : ""}${lower}–${upper}`;
}

function metricLabels(metrics?: Record<string, number>): string[] {
  if (!metrics) return [];
  return Object.entries(metrics)
    .filter(([, value]) => Number.isFinite(value))
    .slice(0, 2)
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${value.toLocaleString()}`);
}

function extractRelevance(metrics?: Record<string, number>): number {
  if (!metrics) return 0;
  const keys = Object.keys(metrics);
  if (keys.length === 0) return 0;
  const first = metrics[keys[0]!]!;
  return Math.min(Math.max(first, 0), 100);
}

function statusDot(ready: boolean): React.CSSProperties {
  return {
    display: "inline-block",
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    background: ready ? "#34a853" : "#ea4335",
    marginRight: "6px",
  };
}

function barTrack(): React.CSSProperties {
  return {
    height: "6px",
    borderRadius: "3px",
    background: "#f1f3f4",
    overflow: "hidden",
    position: "relative",
    flex: 1,
  };
}

function barFill(pct: number, color: string): React.CSSProperties {
  return {
    height: "100%",
    width: `${Math.min(pct, 100)}%`,
    background: color,
    borderRadius: "3px",
    transition: "width 0.4s ease",
  };
}

function jobDot(status: string): React.CSSProperties {
  return {
    display: "inline-block",
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    marginRight: "10px",
    background:
      status === "succeeded" ? "#34a853" : status === "failed" ? "#ea4335" : "#fbbc04",
  };
}

const S: Record<string, React.CSSProperties> = {
  page: {
    fontFamily: "'Google Sans', 'Segoe UI', system-ui, -apple-system, sans-serif",
    background: "#fff",
    color: "#202124",
    minHeight: "100vh",
  },
  hero: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "80px 24px 48px",
    textAlign: "center",
  },
  logo: {
    fontSize: "42px",
    fontWeight: 400,
    letterSpacing: "-0.5px",
    color: "#202124",
    margin: "0 0 8px",
  },
  tagline: {
    fontSize: "15px",
    color: "#5f6368",
    margin: "0 0 36px",
    fontWeight: 400,
  },
  searchForm: {
    display: "flex",
    alignItems: "center",
    width: "100%",
    maxWidth: "584px",
    background: "#fff",
    border: "1px solid #dfe1e5",
    borderRadius: "24px",
    padding: "6px 8px 6px 16px",
    boxShadow: "0 1px 6px rgba(32,33,36,0.08)",
    transition: "box-shadow 0.2s",
  },
  searchInput: {
    flex: 1,
    border: "none",
    outline: "none",
    fontSize: "16px",
    padding: "10px 8px",
    background: "transparent",
    color: "#202124",
    fontFamily: "inherit",
  },
  quickLinksRow: {
    display: "flex",
    gap: "8px",
    marginTop: "24px",
    flexWrap: "wrap",
    justifyContent: "center",
    maxWidth: "800px",
  },
  quickLinkBtn: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    background: "#f8f9fa",
    border: "1px solid #dadce0",
    borderRadius: "16px",
    padding: "6px 14px",
    fontSize: "12px",
    cursor: "pointer",
    color: "#3c4043",
    textDecoration: "none",
    fontWeight: 500,
    transition: "all 0.15s",
  },
  modeRow: {
    display: "flex",
    gap: "8px",
    marginTop: "16px",
  },
  modeBtn: {
    background: "transparent",
    border: "1px solid #dadce0",
    borderRadius: "16px",
    padding: "6px 16px",
    fontSize: "13px",
    cursor: "pointer",
    color: "#5f6368",
    fontFamily: "inherit",
    fontWeight: 500,
    transition: "all 0.15s",
  },
  modeBtnActive: {
    background: "#e6f6ee",
    border: "1px solid #006b4e",
    color: "#006b4e",
  },
  topBar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 24px",
    borderBottom: "1px solid #f1f3f4",
    fontSize: "13px",
    color: "#5f6368",
  },
  wsSelect: {
    border: "1px solid #dadce0",
    borderRadius: "8px",
    padding: "4px 8px",
    fontSize: "13px",
    background: "#fff",
    color: "#3c4043",
    fontFamily: "inherit",
  },
  error: {
    background: "#fce8e6",
    color: "#c5221f",
    padding: "12px 24px",
    fontSize: "14px",
    textAlign: "center",
    margin: 0,
  },
  section: {
    maxWidth: "960px",
    margin: "0 auto",
    padding: "32px 24px",
  },
  sectionTitle: {
    fontSize: "20px",
    fontWeight: 400,
    color: "#202124",
    margin: "0 0 4px",
  },
  sectionSub: {
    fontSize: "13px",
    color: "#5f6368",
    margin: "0 0 20px",
  },
  filterRow: {
    display: "flex",
    gap: "8px",
    marginBottom: "24px",
    flexWrap: "wrap",
  },
  filterBtn: {
    background: "transparent",
    border: "1px solid #dadce0",
    borderRadius: "16px",
    padding: "5px 14px",
    fontSize: "13px",
    cursor: "pointer",
    color: "#5f6368",
    fontFamily: "inherit",
    transition: "all 0.15s",
  },
  filterBtnActive: {
    background: "#f1f8f5",
    border: "1px solid #006b4e",
    color: "#006b4e",
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
    gap: "16px",
  },
  card: {
    border: "1px solid #e8eaed",
    borderRadius: "12px",
    padding: "20px",
    background: "#fff",
    transition: "box-shadow 0.2s",
    cursor: "default",
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  cardLabel: {
    fontSize: "11px",
    fontWeight: 500,
    textTransform: "uppercase" as const,
    letterSpacing: "0.5px",
    color: "#006b4e",
  },
  cardTitle: {
    fontSize: "15px",
    fontWeight: 500,
    color: "#202124",
    margin: 0,
    lineHeight: 1.4,
  },
  cardSummary: {
    fontSize: "13px",
    color: "#5f6368",
    margin: 0,
    lineHeight: 1.5,
  },
  cardImg: {
    width: "100%",
    height: "140px",
    objectFit: "cover" as const,
    borderRadius: "8px",
    background: "#f8f9fa",
  },
  metricRow: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "12px",
    color: "#5f6368",
  },
  cardFooter: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: "auto",
    paddingTop: "8px",
    borderTop: "1px solid #f1f3f4",
    fontSize: "12px",
    color: "#80868b",
  },
  link: {
    color: "#006b4e",
    textDecoration: "none",
    fontSize: "12px",
    fontWeight: 500,
  },
  jobsSection: {
    maxWidth: "960px",
    margin: "0 auto",
    padding: "0 24px 48px",
  },
  jobRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 0",
    borderBottom: "1px solid #f1f3f4",
    fontSize: "13px",
  },
  empty: {
    textAlign: "center",
    padding: "40px 24px",
    color: "#80868b",
    fontSize: "14px",
  },
  accountRow: {
    display: "flex",
    gap: "12px",
    alignItems: "flex-end",
    flexWrap: "wrap",
    padding: "16px 0",
  },
  inputSmall: {
    border: "1px solid #dadce0",
    borderRadius: "8px",
    padding: "6px 10px",
    fontSize: "13px",
    fontFamily: "inherit",
    color: "#3c4043",
  },
  divider: {
    border: "none",
    borderTop: "1px solid #f1f3f4",
    margin: "0",
  },
};

const COLORS_BY_KIND: Record<string, string> = {
  trend: "#1a73e8",
  ad: "#e37400",
  account: "#34a853",
  starter: "#9334e6",
};

export default function ResearchDashboard() {
  const { apiFetch } = useAuth();
  const { jobs: allJobs, refresh: refreshJobs, setActiveWorkspaceId } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [providers, setProviders] = useState<ResearchProviders | null>(null);
  const [query, setQuery] = useState("");
  const [queryMode, setQueryMode] = useState<"trends" | "ads">("trends");
  const [adResult, setAdResult] = useState<AdSearchResult | null>(null);
  const [briefing, setBriefing] = useState<MetaBriefing | null>(null);
  const [feedFilter, setFeedFilter] = useState<"all" | "trend" | "ad" | "account">("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const jobs = useMemo(
    () =>
      allJobs
        .filter((job) => job.category === "research")
        .map((job) => job.raw as ResearchJob),
    [allJobs],
  );

  useEffect(() => {
    let cancelled = false;
    apiFetch("/api/workspaces")
      .then((response) => response.json() as Promise<{ workspaces: Workspace[] }>)
      .then((body) => {
        if (cancelled) return;
        setWorkspaces(body.workspaces);
        const first = body.workspaces[0]?.id ?? "";
        setWorkspaceId(first);
        setActiveWorkspaceId(first || null);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load workspaces.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, setActiveWorkspaceId]);

  const refreshProviders = useCallback(async () => {
    const response = await fetch(`${apiBaseUrl()}/api/research/status`, {
      cache: "no-store",
    });
    const payload = (await response.json()) as {
      providers?: ResearchProviders;
      detail?: string;
    };
    if (!response.ok || !payload.providers) {
      throw new Error(payload.detail ?? "Research sources are unavailable.");
    }
    setProviders(payload.providers);
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      void refreshProviders().catch((reason: unknown) => {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : "Research sources are unavailable.",
          );
        }
      });
    });
    return () => {
      cancelled = true;
    };
  }, [refreshProviders]);

  const trendInspirations = useMemo<Inspiration[]>(
    () =>
      jobs
        .filter((job) => job.status === "succeeded")
        .flatMap((job) =>
          (job.observations ?? []).map((observation, index) => ({
            id: `${job.id}-${index}`,
            kind: "trend" as const,
            label: job.topic,
            title: observation.title || "Untitled trend signal",
            summary: observation.summary || "No summary was returned.",
            source: observation.source || "Research evidence",
            href: observation.evidence?.source_url,
            metrics: metricLabels(observation.metrics),
            topic: job.topic,
            relevance: extractRelevance(observation.metrics),
          })),
        )
        .slice(0, 18),
    [jobs],
  );

  const adInspirations = useMemo<Inspiration[]>(
    () =>
      (adResult?.ads ?? []).map((ad) => {
        const creative = ad.creatives?.[0];
        return {
          id: `ad-${ad.id}`,
          kind: "ad",
          label: ad.is_active === false ? "Inactive ad" : "Active ad",
          title: creative?.title || ad.page?.name || "Untitled competitor creative",
          summary: creative?.body || "No public creative copy was returned.",
          source: ad.page?.name || "Meta Ad Library",
          href: ad.snapshot_url || undefined,
          image: creative?.image_url || creative?.thumbnail_url || undefined,
          topic: adResult?.query,
          metrics: [
            `Impressions: ${formatRange(ad.impressions)}`,
            `Spend: ${formatRange(ad.spend, ad.spend?.currency)}`,
          ],
          relevance: ad.impressions?.upper_bound
            ? Math.min((ad.impressions.upper_bound / 100_000) * 100, 100)
            : 30,
        } satisfies Inspiration;
      }),
    [adResult],
  );

  const accountInspirations = useMemo<Inspiration[]>(
    () =>
      briefing
        ? (Object.entries(briefing.signals) as Array<
            [keyof MetaBriefing["signals"], AdSignal[]]
          >).flatMap(([group, signals]) =>
            signals.map((signal, index) => ({
              id: `account-${group}-${index}-${signal.name}`,
              kind: "account" as const,
              label:
                group === "winners"
                  ? "Winning creative"
                  : group === "bleeders"
                    ? "Needs attention"
                    : "Fatigue signal",
              title: signal.name,
              summary:
                signal.campaign ||
                "First-party performance signal from the connected ad account.",
              source: "Your Meta account",
              metrics: [
                `$${signal.spend.toFixed(2)} spend`,
                `${signal.ctr.toFixed(2)}% CTR`,
              ],
              relevance: Math.min(signal.ctr * 20, 100),
            })),
          )
        : [],
    [briefing],
  );

  const liveInspirations = useMemo(
    () => {
      const items = [...adInspirations, ...trendInspirations, ...accountInspirations];
      return items.length > 0 ? items : AFFILIATE_STARTERS;
    },
    [accountInspirations, adInspirations, trendInspirations],
  );
  const visibleInspirations = liveInspirations.filter(
    (item) => feedFilter === "all" || item.kind === feedFilter,
  );

  const last30Ready = Boolean(
    providers?.last30days.installed &&
      providers.last30days.active &&
      providers.last30days.engine_present,
  );
  const collectorReady = Boolean(providers?.meta_ads_collector.ready);
  const metaReady = Boolean(providers?.meta_ads.ready);

  const readinessCount = [last30Ready, collectorReady, metaReady].filter(Boolean).length;

  function selectWorkspace(id: string) {
    setWorkspaceId(id);
    setActiveWorkspaceId(id || null);
  }

  function exploreTopic(topic: string) {
    setQuery(topic);
    setQueryMode("trends");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function runQuery(event: FormEvent) {
    event.preventDefault();
    const normalized = query.trim();
    if (!normalized) return;
    if (
      !window.confirm(
        queryMode === "trends"
          ? `Research "${normalized}" using the selected evidence sources?`
          : `Search Meta's public Ad Library for "${normalized}"?`,
      )
    ) {
      return;
    }
    setBusy(queryMode);
    setError(null);
    try {
      if (queryMode === "trends") {
        const response = await fetch(`${apiBaseUrl()}/api/research/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workspace_id: workspaceId,
            topic: normalized,
            days: 30,
            sources: [],
            mode: "quick",
            confirm_external_action: true,
          }),
        });
        const payload = (await response.json()) as { detail?: string };
        if (!response.ok) throw new Error(payload.detail ?? "Research could not start.");
        await refreshJobs();
      } else {
        const response = await fetch(`${apiBaseUrl()}/api/research/meta-ads/library/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: normalized,
            country: "US",
            ad_type: "all",
            media_type: "all",
            max_results: 20,
            confirm_external_action: true,
          }),
        });
        const payload = (await response.json()) as {
          result?: AdSearchResult;
          detail?: string;
        };
        if (!response.ok || !payload.result) {
          throw new Error(payload.detail ?? "Competitive-ad search failed.");
        }
        setAdResult(payload.result);
        setFeedFilter("ad");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Research could not complete.");
    } finally {
      setBusy(null);
    }
  }

  async function runAccountValidation() {
    if (!window.confirm("Read connected Meta performance and add its signals to this radar?")) {
      return;
    }
    setBusy("account");
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl()}/api/research/meta-ads/briefing`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account: null,
          preset: "last_7d",
          confirm_external_action: true,
        }),
      });
      const payload = (await response.json()) as {
        briefing?: MetaBriefing;
        detail?: string;
      };
      if (!response.ok || !payload.briefing) {
        throw new Error(payload.detail ?? "Account validation failed.");
      }
      setBriefing(payload.briefing);
      setFeedFilter("account");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Account validation failed.");
    } finally {
      setBusy(null);
    }
  }

  const canSearch =
    workspaceId && busy === null && (queryMode === "trends" ? last30Ready : collectorReady);

  return (
    <main style={S.page}>
      <WorkspaceSectionNav area="discover" />

      <div style={S.topBar}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <select
            style={S.wsSelect}
            value={workspaceId}
            onChange={(event) => selectWorkspace(event.target.value)}
          >
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name}
              </option>
            ))}
          </select>
          <span>
            <span style={statusDot(last30Ready)} />
            {readinessCount}/3 sources ready
          </span>
        </div>
        {metaReady && (
          <button
            type="button"
            className="secondary-link"
            style={{ borderRadius: "20px", display: "inline-flex", alignItems: "center", gap: "6px" }}
            disabled={busy !== null}
            onClick={() => void runAccountValidation()}
          >
            {busy === "account" ? "Reading…" : (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLineLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                Import account signals
              </>
            )}
          </button>
        )}
      </div>

      {error && <p style={S.error} role="alert">{error}</p>}

      <div style={S.hero}>
        <h1 style={S.logo}>TrendRelay</h1>
        <p style={S.tagline}>Discover what is trending. Research what matters.</p>

        <form style={S.searchForm} onSubmit={runQuery}>
          <input
            style={S.searchInput}
            required
            minLength={1}
            maxLength={300}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={
              queryMode === "trends"
                ? "Search trends..."
                : "Search competitor ads..."
            }
          />
          <button
            type="submit"
            className="primary-button"
            style={{ borderRadius: "20px" }}
            disabled={!canSearch}
          >
            {busy === queryMode ? "Searching…" : "Search"}
          </button>
        </form>

        <div style={S.modeRow}>
          {(
            [
              ["trends", "Trends"],
              ["ads", "Ads"],
            ] as const
          ).map(([val, label]) => (
            <button
              key={val}
              type="button"
              style={
                queryMode === val
                  ? { ...S.modeBtn, ...S.modeBtnActive }
                  : S.modeBtn
              }
              onClick={() => setQueryMode(val)}
            >
              {label}
            </button>
          ))}
        </div>

        <div style={S.quickLinksRow}>
          <a href="https://ads.tiktok.com/business/creativecenter/inspiration/popular/music/pc/en" target="_blank" rel="noreferrer" style={S.quickLinkBtn}>
            🎵 Viral TikTok Sounds
          </a>
          <a href="https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en" target="_blank" rel="noreferrer" style={S.quickLinkBtn}>
            # Popular Hashtags
          </a>
          <a href="https://ads.tiktok.com/business/creativecenter/inspiration/popular/creator/pc/en" target="_blank" rel="noreferrer" style={S.quickLinkBtn}>
            👑 Trending Creators
          </a>
          <a href="https://ads.tiktok.com/business/creativecenter/inspiration/popular/pc/en" target="_blank" rel="noreferrer" style={S.quickLinkBtn}>
            🔥 Hot content
          </a>
        </div>
      </div>

      {visibleInspirations.length > 0 && (
        <div style={S.section}>
          <h2 style={S.sectionTitle}>Results</h2>
          <p style={S.sectionSub}>
            {liveInspirations.length} signals from your research
          </p>

          <div style={S.filterRow}>
            {(
              [
                ["all", "All"],
                ["trend", "Trends"],
                ["ad", "Ads"],
                ["account", "Account"],
              ] as const
            ).map(([val, label]) => (
              <button
                key={val}
                type="button"
                style={
                  feedFilter === val
                    ? { ...S.filterBtn, ...S.filterBtnActive }
                    : S.filterBtn
                }
                onClick={() => setFeedFilter(val)}
              >
                {label}
              </button>
            ))}
          </div>

          <div style={S.grid}>
            {visibleInspirations.map((item) => {
              const color = COLORS_BY_KIND[item.kind] ?? "#5f6368";
              const pct = item.relevance ?? 40;
              return (
                <div
                  key={item.id}
                  style={S.card}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLDivElement).style.boxShadow =
                      "0 1px 6px rgba(32,33,36,0.15)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLDivElement).style.boxShadow = "none";
                  }}
                >
                  <span style={{ ...S.cardLabel, color }}>{item.label}</span>
                  {item.image && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={item.image}
                      alt=""
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      style={S.cardImg}
                    />
                  )}
                  <h3 style={S.cardTitle}>{item.title}</h3>
                  <p style={S.cardSummary}>{item.summary}</p>

                  <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <div style={S.metricRow}>
                      <span style={{ minWidth: "56px" }}>Relevance</span>
                      <div style={barTrack()}>
                        <div style={barFill(pct, color)} />
                      </div>
                      <span>{Math.round(pct)}%</span>
                    </div>
                    {item.metrics && item.metrics.length > 0 && (
                      <div style={S.metricRow}>
                        {item.metrics.map((m) => (
                          <span
                            key={m}
                            style={{
                              background: "#f1f3f4",
                              borderRadius: "10px",
                              padding: "2px 8px",
                              fontSize: "11px",
                            }}
                          >
                            {m}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div style={S.cardFooter}>
                    <span>{item.source}</span>
                    <div style={{ display: "flex", gap: "12px" }}>
                      {item.topic && (
                        <button
                          type="button"
                          className="quiet-action"
                          onClick={() => exploreTopic(item.topic!)}
                        >
                          Explore
                        </button>
                      )}
                      {item.href && (
                        <a
                          href={item.href}
                          target="_blank"
                          rel="noreferrer"
                          style={S.link}
                        >
                          Source
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {visibleInspirations.length === 0 && liveInspirations.length > 0 && (
        <div style={{ ...S.empty, ...S.section }}>
          <p>No signals of this type yet.</p>
          <button type="button" className="quiet-action" onClick={() => setFeedFilter("all")}>
            Show all results
          </button>
        </div>
      )}

      {liveInspirations.length === 0 && !busy && (
        <div style={{ ...S.empty, ...S.section }}>
          <p style={{ color: "#80868b", fontSize: "15px" }}>
            Start by searching a topic or product above.
          </p>
        </div>
      )}

      {jobs.length > 0 && (
        <div style={S.jobsSection}>
          <hr style={S.divider} />
          <h2 style={{ ...S.sectionTitle, marginTop: "24px" }}>Recent research</h2>
          <p style={S.sectionSub}>{jobs.length} runs</p>
          {jobs.slice(0, 8).map((job) => (
            <div key={job.id} style={S.jobRow}>
              <div style={{ display: "flex", alignItems: "center" }}>
                <span style={jobDot(job.status)} />
                <div>
                  <span style={{ color: "#202124", fontWeight: 500 }}>{job.topic}</span>
                  <span style={{ color: "#80868b", marginLeft: "8px" }}>
                    {job.status} · {new Date(job.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
              <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                {job.error && (
                  <span style={{ color: "#ea4335", fontSize: "12px" }}>{job.error}</span>
                )}
                {job.status === "succeeded" && (job.observations?.length ?? 0) > 0 && (
                  <Link
                    href={`/opportunities?trend=${encodeURIComponent(job.topic)}&job=${encodeURIComponent(job.id)}`}
                    style={S.link}
                  >
                    Score opportunity
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
