"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Download } from "lucide-react";

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
type TikTokTrendItem = {
  rank: number | null;
  name: string;
  category: string | null;
  descriptors: string[];
  metrics: Record<string, number>;
  url?: string;
};
type TikTokResult = {
  category: string;
  category_label: string;
  region: string;
  period_days: number;
  final_url: string;
  extraction: string;
  item_count: number;
  items: TikTokTrendItem[];
  notes: string[];
  collected_at?: string | null;
  cached?: boolean;
};

type TikTokCategory = {
  id: string;
  label: string;
  description: string;
  available: boolean;
  unavailable_reason: string;
};

type InspirationKind = "trend" | "ad" | "account";
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

const TIKTOK_REGIONS: ReadonlyArray<readonly [string, string]> = [
  ["US", "United States"],
  ["GB", "United Kingdom"],
  ["DE", "Germany"],
  ["FR", "France"],
  ["ES", "Spain"],
  ["IT", "Italy"],
  ["BR", "Brazil"],
  ["MX", "Mexico"],
  ["CA", "Canada"],
  ["AU", "Australia"],
  ["JP", "Japan"],
  ["ID", "Indonesia"],
];
const TIKTOK_PERIODS: ReadonlyArray<readonly [number, string]> = [
  [7, "Last 7 days"],
  [30, "Last 30 days"],
  [120, "Last 120 days"],
];

const TIKTOK_CATEGORY_ICONS: Record<string, string> = {
  hashtag: "#",
  video: "🔥",
  song: "🎵",
  creator: "👑",
};

function compactNumber(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

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
  tiktokHead: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: "16px",
    flexWrap: "wrap" as const,
  },
  tiktokControls: {
    display: "flex",
    gap: "8px",
    alignItems: "center",
    flexWrap: "wrap" as const,
  },
  tiktokSelect: {
    border: "1px solid #dadce0",
    borderRadius: "16px",
    padding: "6px 12px",
    fontSize: "12px",
    background: "#fff",
    color: "#3c4043",
    fontFamily: "inherit",
    cursor: "pointer",
  },
  tiktokNote: {
    margin: "0 0 12px",
    padding: "8px 12px",
    borderLeft: "3px solid #f5c33b",
    background: "#fffdf5",
    color: "#5f6368",
    fontSize: "12px",
    lineHeight: 1.5,
  },
  tiktokList: {
    display: "grid",
    gap: "1px",
    background: "#e8eaed",
    border: "1px solid #e8eaed",
    borderRadius: "12px",
    overflow: "hidden",
  },
  tiktokRow: {
    display: "grid",
    gridTemplateColumns: "28px minmax(0, 1fr) auto auto",
    alignItems: "center",
    gap: "12px",
    padding: "12px 16px",
    background: "#fff",
  },
  tiktokRank: {
    color: "#80868b",
    fontSize: "12px",
    fontVariantNumeric: "tabular-nums" as const,
    textAlign: "center" as const,
  },
  tiktokBody: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    minWidth: 0,
    flexWrap: "wrap" as const,
  },
  tiktokName: {
    fontSize: "14px",
    color: "#202124",
    fontWeight: 500,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    maxWidth: "100%",
  },
  tiktokTag: {
    fontSize: "11px",
    color: "#5f6368",
    background: "#f1f3f4",
    borderRadius: "10px",
    padding: "2px 8px",
  },
  tiktokMetrics: {
    display: "flex",
    gap: "14px",
    color: "#5f6368",
    fontSize: "12px",
    whiteSpace: "nowrap" as const,
  },
  tiktokMetric: {
    fontVariantNumeric: "tabular-nums" as const,
  },
  tiktokExplore: {
    border: "1px solid #dadce0",
    borderRadius: "14px",
    background: "#fff",
    color: "#3c4043",
    fontSize: "11px",
    fontFamily: "inherit",
    padding: "4px 10px",
    cursor: "pointer",
  },
  tiktokSource: {
    margin: "12px 0 0",
    color: "#80868b",
    fontSize: "11px",
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
    flexWrap: "wrap" as const,
    gap: "6px 8px",
    fontSize: "12px",
    color: "#5f6368",
  },
  cardFooter: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    flexWrap: "wrap" as const,
    gap: "8px 12px",
    marginTop: "auto",
    paddingTop: "10px",
    borderTop: "1px solid #f1f3f4",
    fontSize: "12px",
    color: "#80868b",
  },
  cardSource: {
    flex: "1 1 auto",
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  },
  cardActions: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    flex: "0 0 auto",
  },
  cardAction: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    minHeight: "30px",
    border: "1px solid transparent",
    borderRadius: "15px",
    padding: "0 14px",
    background: "#e8f0fe",
    color: "#1a56c4",
    fontSize: "12px",
    fontWeight: 500,
    fontFamily: "inherit",
    cursor: "pointer",
    transition: "background 150ms, border-color 150ms",
  },
  link: {
    display: "inline-flex",
    alignItems: "center",
    minHeight: "30px",
    padding: "0 4px",
    color: "#006b4e",
    textDecoration: "none",
    fontSize: "12px",
    fontWeight: 500,
    whiteSpace: "nowrap" as const,
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
  const [tiktokResult, setTiktokResult] = useState<TikTokResult | null>(null);
  const [tiktokCategories, setTiktokCategories] = useState<TikTokCategory[]>([]);
  const [tiktokRegion, setTiktokRegion] = useState("US");
  const [tiktokPeriod, setTiktokPeriod] = useState(7);
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
      return items;
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

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl()}/api/research/tiktok/status`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload: { provider?: { categories?: TikTokCategory[] } } | null) => {
        if (!cancelled && payload?.provider?.categories) {
          setTiktokCategories(payload.provider.categories);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  async function fetchTiktokDiscovery(
    category: string,
    options: { region?: string; period?: number; refresh?: boolean } = {},
  ) {
    const region = options.region ?? tiktokRegion;
    const period = options.period ?? tiktokPeriod;
    setBusy("tiktok");
    setError(null);
    try {
      const query = new URLSearchParams({
        region,
        period: String(period),
        limit: "20",
        refresh: String(Boolean(options.refresh)),
      });
      const response = await fetch(
        `${apiBaseUrl()}/api/research/tiktok/discovery/${category}?${query}`,
      );
      const payload = (await response.json()) as { result?: TikTokResult; detail?: string };
      if (!response.ok || !payload.result) {
        throw new Error(payload.detail ?? "TikTok Creative Center could not be read.");
      }
      // Notes explain what TikTok served; they are context, not a failure.
      setTiktokResult(payload.result);
    } catch (reason) {
      setTiktokResult(null);
      setError(reason instanceof Error ? reason.message : "TikTok discovery failed.");
    } finally {
      setBusy(null);
    }
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
                <Download size={14} strokeWidth={2} />
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
          {(tiktokCategories.length
            ? tiktokCategories
            : [{ id: "hashtag", label: "Hashtags", description: "", available: true, unavailable_reason: "" }]
          ).map((category) => (
            <button
              key={category.id}
              type="button"
              disabled={!category.available || busy === "tiktok"}
              title={category.available ? category.description : category.unavailable_reason}
              onClick={() => void fetchTiktokDiscovery(category.id)}
              style={
                category.available
                  ? S.quickLinkBtn
                  : { ...S.quickLinkBtn, opacity: 0.45, cursor: "not-allowed" }
              }
            >
              {TIKTOK_CATEGORY_ICONS[category.id] ?? "•"} {category.label}
              {category.available ? "" : " (retired)"}
            </button>
          ))}
        </div>
      </div>

      {(tiktokResult || busy === "tiktok") && (
        <div style={S.section}>
          <div style={S.tiktokHead}>
            <div>
              <h2 style={S.sectionTitle}>
                TikTok {tiktokResult?.category_label ?? "trends"}
              </h2>
              <p style={S.sectionSub}>
                {busy === "tiktok"
                  ? "Rendering TikTok Creative Center…"
                  : [
                      `${tiktokResult?.item_count ?? 0} public ${
                        tiktokResult?.item_count === 1 ? "entry" : "entries"
                      }`,
                      tiktokResult?.region,
                      `last ${tiktokResult?.period_days} days`,
                      tiktokResult?.cached ? "cached" : "freshly read",
                    ].join(" · ")}
              </p>
            </div>
            <div style={S.tiktokControls}>
              <select
                aria-label="TikTok region"
                style={S.tiktokSelect}
                value={tiktokRegion}
                disabled={busy === "tiktok"}
                onChange={(event) => {
                  const region = event.target.value;
                  setTiktokRegion(region);
                  if (tiktokResult) {
                    void fetchTiktokDiscovery(tiktokResult.category, { region });
                  }
                }}
              >
                {TIKTOK_REGIONS.map(([code, label]) => (
                  <option key={code} value={code}>{label}</option>
                ))}
              </select>
              <select
                aria-label="TikTok period"
                style={S.tiktokSelect}
                value={tiktokPeriod}
                disabled={busy === "tiktok"}
                onChange={(event) => {
                  const period = Number(event.target.value);
                  setTiktokPeriod(period);
                  if (tiktokResult) {
                    void fetchTiktokDiscovery(tiktokResult.category, { period });
                  }
                }}
              >
                {TIKTOK_PERIODS.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
              <button
                type="button"
                style={S.quickLinkBtn}
                disabled={busy === "tiktok" || !tiktokResult}
                onClick={() => {
                  if (tiktokResult) {
                    void fetchTiktokDiscovery(tiktokResult.category, { refresh: true });
                  }
                }}
              >
                {busy === "tiktok" ? "Reading…" : "↻ Refresh"}
              </button>
            </div>
          </div>

          {tiktokResult?.notes.map((note) => (
            <p key={note} style={S.tiktokNote}>{note}</p>
          ))}

          {tiktokResult && tiktokResult.items.length > 0 && (
            <div style={S.tiktokList}>
              {tiktokResult.items.map((item, index) => (
                <div key={`${item.name}-${index}`} style={S.tiktokRow}>
                  <span style={S.tiktokRank}>{item.rank ?? index + 1}</span>
                  <div style={S.tiktokBody}>
                    <span style={S.tiktokName} title={item.name}>{item.name}</span>
                    {item.category && <span style={S.tiktokTag}>{item.category}</span>}
                  </div>
                  <div style={S.tiktokMetrics}>
                    {Object.entries(item.metrics).map(([key, value]) => (
                      <span key={key} style={S.tiktokMetric}>
                        <b>{compactNumber(value)}</b> {key}
                      </span>
                    ))}
                  </div>
                  <button
                    type="button"
                    style={S.tiktokExplore}
                    onClick={() => exploreTopic(item.name.replace(/^#/, ""))}
                  >
                    Research
                  </button>
                </div>
              ))}
            </div>
          )}

          {tiktokResult && (
            <p style={S.tiktokSource}>
              Read from{" "}
              <a href={tiktokResult.final_url} target="_blank" rel="noreferrer">
                TikTok Creative Center
              </a>
              {tiktokResult.collected_at
                ? ` at ${new Date(tiktokResult.collected_at).toLocaleTimeString()}`
                : ""}
              {" "}· public data only, nothing was posted.
            </p>
          )}
        </div>
      )}

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
                    <div style={{ ...S.metricRow, flexWrap: "nowrap" }}>
                      <span style={{ flex: "0 0 56px" }}>Relevance</span>
                      <div style={barTrack()}>
                        <div style={barFill(pct, color)} />
                      </div>
                      <span style={{
                        flex: "0 0 32px",
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                      }}>{Math.round(pct)}%</span>
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
                    <span style={S.cardSource} title={item.source}>{item.source}</span>
                    <div style={S.cardActions}>
                      {item.topic && (
                        <button
                          type="button"
                          style={S.cardAction}
                          onClick={() => exploreTopic(item.topic!)}
                        >
                          <svg
                            width="13"
                            height="13"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                          >
                            <circle cx="11" cy="11" r="7" />
                            <line x1="21" y1="21" x2="16.65" y2="16.65" />
                          </svg>
                          Research
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

      {liveInspirations.length === 0 && !tiktokResult && !busy && (
        <div style={{ ...S.empty, ...S.section }}>
          <p style={{ color: "#5f6368", fontSize: "15px", margin: "0 0 6px" }}>
            Nothing collected yet.
          </p>
          <p style={{ color: "#80868b", fontSize: "13px", margin: 0 }}>
            Search a topic to run 30-day research, switch to Ads to read the public Meta Ad
            Library, or open a TikTok Creative Center list above. Every card below is read from
            a live source — TrendRelay does not seed the feed with examples.
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
