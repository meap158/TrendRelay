"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Crown, Download, Flame, Hash, Music2, RefreshCw, type LucideIcon } from "lucide-react";

import { apiBaseUrl } from "../../lib/api";
import type { DiscoverySeed } from "../../lib/discovery-ideas";
import { searchTerm, type Topic } from "../../lib/trend-shapes";
import { useAuth } from "../auth-provider";
import { useLocale } from "../i18n-provider";
import { buttonClass } from "../ui/button";
import { ActionIcon } from "../ui/action-icons";
import { numberIn, oneOf, usePersistedCache, usePersistedState } from "../ui/use-persisted-state";
import { useJobs } from "../jobs-provider";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { OpportunityScoring } from "./opportunity-scoring";
import { DiscoveryFeed } from "./discovery-feed";
import { PopularPosts } from "./popular-posts";
import { TrendingTopics } from "./trending-topics";
import { CampaignIdeaComposer } from "./campaign-idea-composer";
import { StandoutBoard } from "./standout-board";
import { NewsBoard } from "./news-board";
import { rankEngagedPosts, type ResearchPostJob } from "../../lib/engaged-posts";

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

const TIKTOK_PREFERENCE_KEY = "trendrelay.discover.tiktok";

/** Where "Score it" sends you. */
const SCORING_ANCHOR = "score-the-case";

type TikTokPreference = { category: string; region: string; period: number };

function readTikTokPreference(): TikTokPreference | null {
  try {
    const stored = window.localStorage.getItem(TIKTOK_PREFERENCE_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as Partial<TikTokPreference>;
    if (typeof parsed.category !== "string") return null;
    return {
      category: parsed.category,
      region: typeof parsed.region === "string" ? parsed.region : "US",
      period: typeof parsed.period === "number" ? parsed.period : 7,
    };
  } catch {
    return null;
  }
}

const TIKTOK_CATEGORY_ICONS: Record<string, LucideIcon> = {
  hashtag: Hash,
  video: Flame,
  song: Music2,
  creator: Crown,
};

function TikTokCategoryIcon({ id }: { id: string }) {
  const Icon = TIKTOK_CATEGORY_ICONS[id];
  return Icon ? <Icon size={14} strokeWidth={2} aria-hidden="true" /> : null;
}

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
    background: ready ? "var(--green)" : "var(--red)",
    marginRight: "6px",
  };
}

function barTrack(): React.CSSProperties {
  return {
    height: "6px",
    borderRadius: "3px",
    background: "var(--panel-raised)",
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
      status === "succeeded" ? "var(--green)" : status === "failed" ? "var(--red)" : "var(--amber)",
  };
}


const COLORS_BY_KIND: Record<string, string> = {
  trend: "var(--series-trend)",
  ad: "var(--series-ad)",
  account: "var(--green)",
};

type DouyinTrend = {
  rank: number;
  term: string;
  hot_value: number;
  /** The topic the term belongs to. Not a video, despite looking like one. */
  topic_id: string | null;
  /** Names the topic's own page, which Douyin serves without an account. This
      is what makes a term downloadable for someone who never signs in. */
  sentence_id: string | null;
  search_url: string;
  /** The board's own thumbnail: a signed URL that expires, so never stored. */
  cover_url: string | null;
  view_count: number;
};
type DouyinBoard = { count: number; fetched_at: string; items: DouyinTrend[] };
const isBoardView = oneOf("gallery", "list");

/** How many clips a topic download takes. Small on purpose: a trending topic
    has thousands of posts and the disk is finite. */
const TOPIC_COUNTS = [3, 5, 10, 20] as const;

type TopicNote = {
  tone: "good" | "bad";
  text: string;
  /** Search is the one Douyin call needing a real account, and the fix is a
      single command. Worth naming rather than leaving as a bare failure. */
  loginRequired?: boolean;
};

/** FastAPI carries a structured detail for the sign-in case and a plain string
    everywhere else, so both shapes have to be read.
 *
 * The sign-in case should now be rare: every board term carries a topic id, and
 * that route needs no account. It survives for terms that arrive without one. */
function readTopicFailure(detail: unknown): TopicNote {
  if (detail && typeof detail === "object" && "message" in detail) {
    const shaped = detail as { message?: unknown; login_required?: unknown };
    return {
      tone: "bad",
      text: typeof shaped.message === "string" ? shaped.message : "The topic could not be fetched.",
      loginRequired: shaped.login_required === true,
    };
  }
  return {
    tone: "bad",
    text: typeof detail === "string" ? detail : "The topic could not be fetched.",
  };
}

/** Shape guards for restored data. A cache written by an older build must not
 *  be trusted just because it parsed: a missing `items` array would crash the
 *  render it was restored into. */
const isBoard = (value: unknown): value is DouyinBoard =>
  !!value && typeof value === "object" && Array.isArray((value as DouyinBoard).items);
const isTikTok = (value: unknown): value is TikTokResult =>
  !!value && typeof value === "object" && Array.isArray((value as TikTokResult).items);
const isAdSearch = (value: unknown): value is AdSearchResult =>
  !!value && typeof value === "object" && Array.isArray((value as AdSearchResult).ads);
const isBriefing = (value: unknown): value is MetaBriefing =>
  !!value && typeof value === "object" && !!(value as MetaBriefing).signals;

/** The hot board is the most perishable thing here: its ranking moves through
 *  the day and its cover images are signed URLs that expire. Half an hour keeps
 *  a reload useful without pretending stale trends are current. */
const BOARD_MAX_AGE = 30 * 60 * 1000;
/** Research results are a piece of work someone commissioned, not a live feed,
 *  so they are worth keeping for a working day. */
const RESEARCH_MAX_AGE = 8 * 60 * 60 * 1000;

export default function ResearchDashboard() {
  const { apiFetch } = useAuth();
  const { t, rich } = useLocale();
  const { jobs: allJobs, refresh: refreshJobs, setActiveWorkspaceId } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [providers, setProviders] = useState<ResearchProviders | null>(null);
  const [query, setQuery] = useState("");
  const [queryMode, setQueryMode] = useState<"trends" | "ads">("trends");
  const [adResult, setAdResult] = usePersistedCache<AdSearchResult>(
    "trendrelay.discover.ads", RESEARCH_MAX_AGE, isAdSearch);
  const [tiktokResult, setTiktokResult] = usePersistedCache<TikTokResult>(
    "trendrelay.discover.tiktok.result", RESEARCH_MAX_AGE, isTikTok);
  const [tiktokCategories, setTiktokCategories] = useState<TikTokCategory[]>([]);
  const [tiktokRegion, setTiktokRegion] = useState("US");
  const [tiktokPeriod, setTiktokPeriod] = useState(7);
  const [briefing, setBriefing] = usePersistedCache<MetaBriefing>(
    "trendrelay.discover.briefing", RESEARCH_MAX_AGE, isBriefing);
  const [feedFilter, setFeedFilter] = useState<"all" | "trend" | "ad" | "account">("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [douyinBoard, setDouyinBoard, , boardReady] = usePersistedCache<DouyinBoard>(
    "trendrelay.discover.board", BOARD_MAX_AGE, isBoard);
  const [douyinError, setDouyinError] = useState<string | null>(null);
  const [douyinView, setDouyinView] = usePersistedState<"gallery" | "list">(
    "trendrelay.discover.douyinView", "gallery", isBoardView,
  );
  const [sourceBoardsOpen, setSourceBoardsOpen] = useState(false);
  /** Read once per visit; the ref is what stops a re-render asking again. */
  const douyinAutoRead = useRef(false);
  /** Which term is being fetched, so only that card shows the wait. */
  const [topicBusy, setTopicBusy] = useState<string | null>(null);
  const [topicNote, setTopicNote] = useState<TopicNote | null>(null);
  /** How many clips a topic download takes. Kept because it is a preference
      about disk, not a per-click decision. */
  const [topicCount, setTopicCount] = usePersistedState<number>(
    "trendrelay.discover.topicCount", 5, numberIn(...TOPIC_COUNTS),
  );
  const [ideaSeeds, setIdeaSeeds] = useState<DiscoverySeed[]>([]);
  /**
   * Every post research has turned up, ranked for the shelves above.
   *
   * The same jobs "Winning posts" reads further down, so the two can never
   * disagree about what was found - they disagree only about what is worth
   * leading with.
   */
  const standoutPosts = useMemo(
    () => rankEngagedPosts(
      allJobs
        .filter((job) => job.category === "research")
        .map((job) => job.raw as ResearchPostJob),
    ),
    [allJobs],
  );
  const ideaSeedIds = useMemo(() => new Set(ideaSeeds.map((seed) => seed.id)), [ideaSeeds]);

  function toggleIdeaSeed(seed: DiscoverySeed) {
    setIdeaSeeds((current) => current.some((item) => item.id === seed.id)
      ? current.filter((item) => item.id !== seed.id)
      : [...current, seed].slice(-12));
  }

  /** Read once on arrival, then on demand. */
  async function loadDouyinBoard() {
    if (!workspaceId) return;
    setBusy("douyin");
    setDouyinError(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/douyin/trending?limit=20`,
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The Douyin board could not be read.");
      setDouyinBoard(body);
    } catch (reason) {
      setDouyinError(
        reason instanceof Error ? reason.message : "The Douyin board could not be read.",
      );
    } finally {
      setBusy(null);
    }
  }

  /** Search a trending term and queue its top clips as one download.
   *
   * The board ranks topics rather than clips, so until now a term could only
   * open Douyin in a browser and leave the operator copying links back. This
   * does the search server-side and hands the real video URLs to the same
   * download job everything else uses.
   */
  async function downloadTopic(term: string, sentenceId: string | null) {
    if (!workspaceId || topicBusy) return;
    setTopicBusy(term);
    setTopicNote(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/douyin/topic/downloads`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workspace_id: workspaceId,
            term,
            // Sent when the board has one: it routes the download through the
            // topic page, which needs no Douyin account. Without it the server
            // falls back to search, which does.
            sentence_id: sentenceId,
            limit: topicCount,
            confirm_external_action: true,
          }),
        },
      );
      const body = await response.json();
      if (!response.ok) {
        setTopicNote(readTopicFailure(body.detail));
        return;
      }
      const queued: unknown[] = body.queued ?? [];
      setTopicNote({
        tone: "good",
        text: `Queued ${queued.length} ${queued.length === 1 ? "video" : "videos"} for “${term}”. Watch it in Downloads.`,
      });
      // The job is live from here, so the queue should show it rather than
      // waiting for the next poll.
      void refreshJobs();
    } catch (reason) {
      setTopicNote({
        tone: "bad",
        text: reason instanceof Error ? reason.message : "The topic could not be fetched.",
      });
    } finally {
      setTopicBusy(null);
    }
  }

  useEffect(() => {
    // Wait for the restore before deciding there is nothing to show, or the
    // first render would refetch over a perfectly good cached board.
    if (!workspaceId || !boardReady || douyinBoard || douyinAutoRead.current) return;
    douyinAutoRead.current = true;
    // Deferred so the read does not run inside the render that scheduled it.
    queueMicrotask(() => void loadDouyinBoard());
    // Once per workspace; the ref is the guard, so the callback's identity
    // changing would only repeat the same read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, boardReady, douyinBoard]);

  const jobs = useMemo(
    () =>
      allJobs
        .filter((job) => job.category === "research")
        .map((job) => job.raw as ResearchJob),
    [allJobs],
  );

  /**
   * The trend and evidence a "score this" link is carrying.
   *
   * The retired Opportunities page read these from its own query string. The
   * parameters are unchanged so links written before the merge still arrive
   * with their evidence attached rather than an empty form.
   */
  const [scorePrefill, setScorePrefill] = useState({ trend: "", evidence: "", job: "" });

  useEffect(() => {
    queueMicrotask(() => {
      const params = new URLSearchParams(window.location.search);
      const source = params.get("source") ?? "";
      const title = params.get("title") ?? "";
      setScorePrefill({
        trend: params.get("trend") ?? "",
        evidence: source && title ? `${source} | ${title} | ${params.get("url") ?? ""}` : "",
        job: params.get("job") ?? "",
      });
    });
  }, []);

  /** Runs that still want something from you: an error to read, or a wait. */
  const unfinished = useMemo(
    () => jobs.filter((job) => job.status !== "succeeded").slice(0, 5),
    [jobs],
  );

  /**
   * The newest run worth scoring.
   *
   * One link, not one per run. Scoring is a step you take after reading the
   * cards, and the cards on this page are the newest run's - so a column of
   * links to older runs offered a choice nobody was making.
   */
  const scorable = useMemo(
    () =>
      jobs.find(
        (job) => job.status === "succeeded" && (job.observations?.length ?? 0) > 0,
      ) ?? null,
    [jobs],
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

  /** Carry a ranked topic and the evidence for its position into scoring.
   *
   * This component existed before the Discover/Opportunities merge but was no
   * longer mounted, so its Score action had nowhere to go. Keeping the
   * evidence in the established pipe-delimited shape restores the complete
   * topic -> scored opportunity -> campaign path without inventing a URL the
   * source did not provide.
   */
  function scoreTopic(topic: Topic) {
    const term = searchTerm(topic);
    const source = topic.sources.join(" + ") || "trend discovery";
    const title = [
      topic.label,
      topic.shape,
      `rank ${topic.best_rank ?? "unknown"}`,
      `score ${topic.score}`,
      `${topic.region} market`,
    ].join(" · ");
    setScorePrefill({ trend: term, evidence: `${source} | ${title} |`, job: "" });
    requestAnimationFrame(() => {
      document.querySelector(".opportunity-scoring")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }


  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl()}/api/research/tiktok/status`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload: { provider?: { categories?: TikTokCategory[] } } | null) => {
        if (!cancelled && payload?.provider?.categories) {
          // Retired tabs stay in the registry for honesty, not for the operator.
          setTiktokCategories(payload.provider.categories.filter((item) => item.available));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Open on the trend the operator last looked at, or the first live tab, and
  // let the adapter's cache decide whether that costs a fresh render.
  const autoLoadedRef = useRef(false);
  useEffect(() => {
    if (autoLoadedRef.current || tiktokCategories.length === 0) return;
    autoLoadedRef.current = true;
    const saved = readTikTokPreference();
    const remembered = saved && tiktokCategories.some((item) => item.id === saved.category)
      ? saved
      : null;
    const category = remembered ? remembered.category : tiktokCategories[0]!.id;
    void (async () => {
      if (remembered) {
        setTiktokRegion(remembered.region);
        setTiktokPeriod(remembered.period);
      }
      await fetchTiktokDiscovery(
        category,
        remembered ? { region: remembered.region, period: remembered.period } : {},
      );
    })();
    // fetchTiktokDiscovery is re-created each render; the ref guards the one run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tiktokCategories]);

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
      try {
        window.localStorage.setItem(
          TIKTOK_PREFERENCE_KEY,
          JSON.stringify({ category, region, period }),
        );
      } catch {
        // A blocked storage quota must never break discovery.
      }
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
    <main className="dsc-page">
      <WorkspaceSectionNav area="discover" />

      <div className="dsc-top-bar">
        <div className="dsc-row">
          <select
            className="dsc-ws-select"
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
            {readinessCount}/3 research tools ready
          </span>
        </div>
        {metaReady && (
          <button
            type="button"
            className="secondary-link dsc-pill-row"
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

      {error && <p className="dsc-error" role="alert">{error}</p>}

      <div className="dsc-hero">
        <h1 className="dsc-logo">Discovery command center</h1>
        <p className="dsc-tagline">{t("app.tagline")}</p>

        <form className="dsc-search-form" onSubmit={runQuery}>
          <input
            className="dsc-search-input"
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
            className={buttonClass({ variant: "primary" })}
            disabled={!canSearch}
          >
            <ActionIcon name="search" />{busy === queryMode ? "Searching…" : "Search"}
          </button>
        </form>

        <div className="dsc-mode-row">
          {(
            [
              ["trends", "Trends"],
              ["ads", "Ads"],
            ] as const
          ).map(([val, label]) => (
            <button
              key={val}
              type="button"
              className={`dsc-mode-btn${queryMode === val ? " dsc-mode-btn-active" : ""}`}
              onClick={() => setQueryMode(val)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="dsc-quick-links-row">
          {(tiktokCategories.length
            ? tiktokCategories
            : [{ id: "hashtag", label: "Hashtags", description: "", available: true, unavailable_reason: "" }]
          ).map((category) => (
            <button
              key={category.id}
              type="button"
              disabled={!category.available || busy === "tiktok"}
              title={category.available ? category.description : category.unavailable_reason}
              onClick={() => {
                setSourceBoardsOpen(true);
                void fetchTiktokDiscovery(category.id);
              }}
              className={`dsc-quick-link-btn${category.available ? "" : " is-disabled"}`}
            >
              <TikTokCategoryIcon id={category.id} />{category.label}
              {category.available ? "" : " (retired)"}
            </button>
          ))}
        </div>
      </div>

      {/* One list first, because the question anyone opens this page with is
          "what is hot", not "what did TikTok say". Seven sources on seven
          boards is fourteen answers to read before knowing anything. */}
      <DiscoveryFeed selectedIds={ideaSeedIds} onToggle={toggleIdeaSeed} />

      {/* Folded, not deleted. A hashtag and a video are different evidence,
          and these two boards answer what the merged list cannot: what shape a
          topic has over time, which posts earn their engagement, and what the
          research jobs found. A summary that replaces its own detail is one
          nobody can check. */}
      <details className="discovery-source-boards">
        <summary>
          <span>
            <strong>Dig into the evidence</strong>
            <small>Trend shapes over time, engagement ranking, and research jobs</small>
          </span>
          <ChevronDown size={18} aria-hidden="true" />
        </summary>

        <div className="dsc-section">
          <TrendingTopics
            onResearch={exploreTopic}
            onScore={scoreTopic}
            selectedIds={ideaSeedIds}
            onToggle={toggleIdeaSeed}
          />
        </div>

        <div className="dsc-section">
          <PopularPosts
            onResearch={exploreTopic}
            selectedIds={ideaSeedIds}
            onToggle={toggleIdeaSeed}
          />
        </div>
      </details>

      <CampaignIdeaComposer
        seeds={ideaSeeds}
        workspaceId={workspaceId}
        canCreate={["owner", "editor"].includes(
          workspaces.find((workspace) => workspace.id === workspaceId)?.role ?? "",
        )}
        apiFetch={apiFetch}
        onRemove={(id) => setIdeaSeeds((current) => current.filter((seed) => seed.id !== id))}
        onClear={() => setIdeaSeeds([])}
      />

      <details
        className="discovery-source-boards"
        open={sourceBoardsOpen}
        onToggle={(event) => setSourceBoardsOpen(event.currentTarget.open)}
      >
        <summary>
          <span>
            <strong>Explore raw source boards</strong>
            <small>Douyin hot search and TikTok Creative Center, before consolidation</small>
          </span>
          <ChevronDown className={sourceBoardsOpen ? "open" : ""} size={18} aria-hidden="true" />
        </summary>

      {/* Findings before machinery. Everything below this is how research is
          run; this is what it found, and the only part somebody arriving with
          "what should I make today" can act on. */}
      {/* Above the post board on purpose. The post board needs research to
          have been run before it can say anything, and on a first visit it is
          empty; the news board always has something, because it reads public
          feeds rather than this workspace's history. Leading with the shelf
          that is reliably full is what stops Discover opening as a form. */}
      <NewsBoard seeds={ideaSeeds} onSeed={toggleIdeaSeed} />

      <StandoutBoard posts={standoutPosts} seeds={ideaSeeds} onSeed={toggleIdeaSeed} />

      <div className="dsc-section">
        <div className="dsc-tiktok-head">
          <div>
            <h2 className="dsc-section-title">{t("discover.title")}</h2>
            <p className="dsc-section-sub">
              {douyinBoard
                ? `${douyinBoard.count} terms · read ${new Date(douyinBoard.fetched_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
                : "What is trending on Douyin right now, from your connected session."}
            </p>
          </div>
          <div className="dsc-tiktok-controls">
            <label className="dsc-topic-count-label">
              Download
              <select
                className="dsc-topic-count-select"
                value={topicCount}
                onChange={(event) => setTopicCount(Number(event.target.value))}
                title={t("research.perTopicHelp")}
              >
                {TOPIC_COUNTS.map((count) => (
                  <option key={count} value={count}>{count}</option>
                ))}
              </select>
              per topic
            </label>
            <div className="dsc-board-view-toggle" role="group" aria-label={t("discover.boardLayout")}>
              {(["gallery", "list"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={douyinView === mode}
                  className={`dsc-board-view-button${douyinView === mode ? " dsc-board-view-button-on" : ""}`}
                  onClick={() => setDouyinView(mode)}
                >{mode === "gallery" ? "Gallery" : "List"}</button>
              ))}
            </div>
            <button
              type="button"
              className="dsc-quick-link-btn"
              disabled={busy === "douyin" || !workspaceId}
              onClick={() => void loadDouyinBoard()}
            >
              {douyinBoard && <RefreshCw size={13} aria-hidden="true" />}
              {busy === "douyin" ? "Reading…" : douyinBoard ? "Refresh" : "Read the board"}
            </button>
          </div>
        </div>

        {douyinError && <p className="dsc-tiktok-note">{douyinError}</p>}

        {topicNote && (
          <p
            className={`dsc-tiktok-note${topicNote.tone === "good" ? " dsc-topic-note-good" : ""}`}
            role={topicNote.tone === "bad" ? "alert" : "status"}
          >
            {topicNote.text}
            {topicNote.loginRequired && (
              <>
                {" "}
                Terms from the board above do not need one.{" "}
                <Link href="/tools" className="dsc-topic-note-link">
                  Connect an account in Tools
                </Link>
              </>
            )}
          </p>
        )}

        {douyinBoard && douyinBoard.items.length > 0 && douyinView === "gallery" && (
          <div className="dsc-board-grid">
            {douyinBoard.items.map((item) => (
              <article key={`${item.rank}-${item.term}`} className="dsc-board-card">
                <div className="dsc-board-thumb">
                  {item.cover_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={item.cover_url}
                      alt=""
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      className="dsc-board-image"
                    />
                  ) : (
                    <span className="dsc-board-no-image">{t("research.noImage")}</span>
                  )}
                  <span className="dsc-board-rank">{item.rank}</span>
                </div>
                <div className="dsc-board-body">
                  <a
                    href={item.search_url}
                    target="_blank"
                    rel="noreferrer"
                    className="board-term-link dsc-board-term"
                    title={`Find "${item.term}" on Douyin`}
                  >{item.term}</a>
                  <span className="dsc-board-meta">
                    {item.hot_value > 0 && <>{t("discover.heat", { value: compactNumber(item.hot_value) })}</>}
                    {item.hot_value > 0 && item.view_count > 0 && " · "}
                    {item.view_count > 0 && <>{compactNumber(item.view_count)} views</>}
                  </span>
                  <div className="dsc-board-actions">
                    <button
                      type="button"
                      className="dsc-board-download"
                      disabled={topicBusy !== null || !workspaceId}
                      onClick={() => void downloadTopic(item.term, item.sentence_id)}
                      title={`Search this term and download its top ${topicCount} videos`}
                    >
                      {topicBusy === item.term ? "Queueing…" : (
                        <>
                          <Download size={13} strokeWidth={2} />
                          Download {topicCount}
                        </>
                      )}
                    </button>
                    <a
                      href={item.search_url}
                      target="_blank"
                      rel="noreferrer"
                      className="dsc-board-action"
                      title={t("research.openTermOnDouyin")}
                    >{t("discover.browse")}</a>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}

        {douyinBoard && douyinBoard.items.length > 0 && douyinView === "list" && (
          <div className="dsc-tiktok-list">
            {douyinBoard.items.map((item) => (
              <div key={`${item.rank}-${item.term}`} className="dsc-tiktok-row">
                <span className="dsc-tiktok-rank">{item.rank}</span>
                <div className="dsc-tiktok-body">
                  <a
                    href={item.search_url}
                    target="_blank"
                    rel="noreferrer"
                    className="board-term-link dsc-board-term-link"
                    title={`Find "${item.term}" on Douyin`}
                  >{item.term}</a>
                </div>
                <div className="dsc-tiktok-metrics">
                  {item.hot_value > 0 && (
                    <span className="dsc-tiktok-metric">
                      {rich("discover.heat", {
                        value: <b>{compactNumber(item.hot_value)}</b>,
                      })}
                    </span>
                  )}
                </div>
                {/* A term names a topic, not a clip. The download searches it
                    first and queues the real videos it finds; the link is for
                    looking at the topic rather than taking it. */}
                <div className="dsc-board-actions">
                  <button
                    type="button"
                    className="dsc-board-download"
                    disabled={topicBusy !== null || !workspaceId}
                    onClick={() => void downloadTopic(item.term, item.sentence_id)}
                    title={`Search this term and download its top ${topicCount} videos`}
                  >
                    {topicBusy === item.term ? "Queueing…" : (
                      <>
                        <Download size={13} strokeWidth={2} />
                        Download {topicCount}
                      </>
                    )}
                  </button>
                  <a
                    href={item.search_url}
                    target="_blank"
                    rel="noreferrer"
                    className="dsc-tiktok-explore"
                    title={t("research.openTermOnDouyin")}
                  >
                    Browse
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}

        {douyinBoard && douyinBoard.items.length === 0 && !douyinError && (
          <p className="dsc-tiktok-note">{t("discover.emptyBoard")}</p>
        )}
      </div>

      {(tiktokResult || busy === "tiktok") && (
        <div className="dsc-section">
          <div className="dsc-tiktok-head">
            <div>
              <h2 className="dsc-section-title">
                TikTok {tiktokResult?.category_label ?? "trends"}
              </h2>
              <p className="dsc-section-sub">
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
            <div className="dsc-tiktok-controls">
              <select
                aria-label={t("research.tiktokRegion")}
                className="dsc-tiktok-select"
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
                aria-label={t("research.tiktokPeriod")}
                className="dsc-tiktok-select"
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
                className="dsc-quick-link-btn"
                disabled={busy === "tiktok" || !tiktokResult}
                onClick={() => {
                  if (tiktokResult) {
                    void fetchTiktokDiscovery(tiktokResult.category, { refresh: true });
                  }
                }}
              >
                <RefreshCw size={13} aria-hidden="true" />
                {busy === "tiktok" ? "Reading…" : "Refresh"}
              </button>
            </div>
          </div>

          {tiktokResult?.notes.map((note) => (
            <p key={note} className="dsc-tiktok-note">{note}</p>
          ))}

          {tiktokResult && tiktokResult.items.length > 0 && (
            <div className="dsc-tiktok-list">
              {tiktokResult.items.map((item, index) => (
                <div key={`${item.name}-${index}`} className="dsc-tiktok-row">
                  <span className="dsc-tiktok-rank">{item.rank ?? index + 1}</span>
                  <div className="dsc-tiktok-body">
                    <span className="dsc-tiktok-name" title={item.name}>{item.name}</span>
                    {item.category && <span className="dsc-tiktok-tag">{item.category}</span>}
                  </div>
                  <div className="dsc-tiktok-metrics">
                    {Object.entries(item.metrics).map(([key, value]) => (
                      <span key={key} className="dsc-tiktok-metric">
                        <b>{compactNumber(value)}</b> {key}
                      </span>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="dsc-tiktok-explore"
                    onClick={() => exploreTopic(item.name.replace(/^#/, ""))}
                  >
                    Research
                  </button>
                </div>
              ))}
            </div>
          )}

          {tiktokResult && (
            <p className="dsc-tiktok-source">
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
      </details>

      {visibleInspirations.length > 0 && (
        <div className="dsc-section">
          <h2 className="dsc-section-title">{t("research.results")}</h2>
          <p className="dsc-section-sub">
            {liveInspirations.length} signals from your research
          </p>

          <div className="dsc-filter-row">
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
                className={`dsc-filter-btn${feedFilter === val ? " dsc-filter-btn-active" : ""}`}
                onClick={() => setFeedFilter(val)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="dsc-grid">
            {visibleInspirations.map((item) => {
              const color = COLORS_BY_KIND[item.kind] ?? "var(--muted)";
              const pct = item.relevance ?? 40;
              return (
                <div
                  key={item.id}
                  className="dsc-card"
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLDivElement).style.boxShadow =
                      "0 1px 6px rgba(32,33,36,0.15)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLDivElement).style.boxShadow = "none";
                  }}
                >
                  <span className="dsc-card-label" style={{ color }}>{item.label}</span>
                  {item.image && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={item.image}
                      alt=""
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      className="dsc-card-img"
                    />
                  )}
                  <h3 className="dsc-card-title">{item.title}</h3>
                  <p className="dsc-card-summary">{item.summary}</p>

                  <div className="dsc-stack">
                    <div className="dsc-metric-row dsc-metric-row-tight">
                      <span className="dsc-metric-name">{t("research.relevance")}</span>
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
                      <div className="dsc-metric-row">
                        {item.metrics.map((m) => (
                          <span
                            key={m}
                            style={{
                              background: "var(--panel-raised)",
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

                  <div className="dsc-card-footer">
                    <span className="dsc-card-source" title={item.source}>{item.source}</span>
                    <div className="dsc-card-actions">
                      {item.topic && (
                        <button
                          type="button"
                          className="dsc-card-action"
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
                          className="dsc-link"
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
        <div className="dsc-empty dsc-section">
          <p>{t("research.noSignals")}</p>
          <button type="button" className={buttonClass({ variant: "quiet" })} onClick={() => setFeedFilter("all")}>
            Show all results
          </button>
        </div>
      )}

      {liveInspirations.length === 0 && !tiktokResult && !busy && (
        <div className="dsc-empty dsc-section">
          <p className="dsc-note-lead">
            Nothing collected yet.
          </p>
          <p className="dsc-note">
            Search a topic to run 30-day research, switch to Ads to read the public Meta Ad
            Library, or open a TikTok Creative Center list above. Every card below is read from
            a live source — TrendRelay does not seed the feed with examples.
          </p>
        </div>
      )}

      {/* After the evidence, not on another page. Scoring a trend was a
          separate destination reached by a link carrying the trend, the
          evidence and the job id in a query string - a hand-off that existed
          only because they were two pages. */}
      {workspaceId && (
        <div className="dsc-jobs-section" id={SCORING_ANCHOR}>
          <hr className="dsc-divider" />
          <OpportunityScoring
            key={`${workspaceId}:${scorePrefill.job}:${scorePrefill.trend}`}
            workspaceId={workspaceId}
            role={workspaces.find((ws) => ws.id === workspaceId)?.role ?? ""}
            apiFetch={apiFetch}
            prefill={scorePrefill}
          />
        </div>
      )}

      {/* Not a run log. A finished, successful run has already put its results
          on the page above; repeating it as a timestamped row said nothing the
          cards did not. What the log was carrying that nothing else did is kept:
          a run that failed, a run still going, and the way through to scoring. */}
      {(unfinished.length > 0 || scorable) && (
        <div className="dsc-jobs-section">
          <hr className="dsc-divider" />
          {unfinished.map((job) => (
            <div key={job.id} className="dsc-job-row">
              <div className="dsc-row-tight">
                <span style={jobDot(job.status)} />
                <div>
                  <span className="dsc-strong">{job.topic}</span>
                  <span className="dsc-muted dsc-muted-inline">
                    {job.status === "failed"
                      ? (job.error ?? t("research.runFailed"))
                      : t("research.stillResearching")}
                  </span>
                </div>
              </div>
            </div>
          ))}
          {scorable && (
            <div className="dsc-job-row">
              <span className="dsc-muted">
                {t("research.readyToScore", { topic: scorable.topic })}
              </span>
              <button
                type="button"
                className="dsc-link dsc-link-button"
                onClick={() => {
                  setScorePrefill({ trend: scorable.topic, evidence: "", job: scorable.id });
                  document.querySelector(".opportunity-scoring")
                    ?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                {t("research.scoreOpportunity")}
              </button>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
