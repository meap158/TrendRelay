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
};

const TREND_SOURCES = ["reddit", "youtube", "x", "web", "github", "instagram", "tiktok"];
const STARTER_INSPIRATIONS: Inspiration[] = [
  {
    id: "starter-proof",
    kind: "starter",
    label: "Reliable format",
    title: "Show the proof before explaining the product",
    summary:
      "Lead with a visible result, then rewind to the small decision or product feature that created it.",
    source: "Creative pattern",
    topic: "before and after product proof",
    metrics: ["Hook: outcome first", "Best for: visual products"],
  },
  {
    id: "starter-comparison",
    kind: "starter",
    label: "High-intent angle",
    title: "Turn the buying decision into a three-way comparison",
    summary:
      "Compare the default choice, the premium choice, and the unexpectedly practical option your audience may overlook.",
    source: "Creative pattern",
    topic: "three way product comparison",
    metrics: ["Hook: this vs that", "Best for: considered purchases"],
  },
  {
    id: "starter-routine",
    kind: "starter",
    label: "Repeatable story",
    title: "Build the product into a specific daily ritual",
    summary:
      "Anchor the idea to a recognizable moment—first coffee, commute, desk reset, or evening wind-down.",
    source: "Creative pattern",
    topic: "daily routine product ideas",
    metrics: ["Hook: a familiar moment", "Best for: lifestyle offers"],
  },
  {
    id: "starter-objection",
    kind: "starter",
    label: "Conversion angle",
    title: "Answer the objection people are embarrassed to ask",
    summary:
      "Use candid creator language to resolve friction around price, complexity, quality, or whether it actually works.",
    source: "Creative pattern",
    topic: "customer objections product reviews",
    metrics: ["Hook: honest concern", "Best for: UGC"],
  },
  {
    id: "starter-niche",
    kind: "starter",
    label: "Community signal",
    title: "Borrow the exact phrase enthusiasts use",
    summary:
      "Research niche communities for the shorthand, frustrations, and tiny details that signal genuine category fluency.",
    source: "Research pattern",
    topic: "niche community product language",
    metrics: ["Hook: insider language", "Best for: enthusiast niches"],
  },
  {
    id: "starter-mistake",
    kind: "starter",
    label: "Educational hook",
    title: "Reveal the mistake that makes the product seem ineffective",
    summary:
      "Teach one overlooked setup or usage detail, then position the offer as the easier path to the desired result.",
    source: "Creative pattern",
    topic: "common product usage mistakes",
    metrics: ["Hook: you may be doing this wrong", "Best for: demos"],
  },
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

export default function ResearchDashboard() {
  const { apiFetch } = useAuth();
  const { jobs: allJobs, refresh: refreshJobs, setActiveWorkspaceId } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [providers, setProviders] = useState<ResearchProviders | null>(null);
  const [query, setQuery] = useState("");
  const [queryMode, setQueryMode] = useState<"trends" | "ads">("trends");
  const [depth, setDepth] = useState("quick");
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [country, setCountry] = useState("US");
  const [adType, setAdType] = useState("all");
  const [mediaType, setMediaType] = useState("all");
  const [maxResults, setMaxResults] = useState(20);
  const [adResult, setAdResult] = useState<AdSearchResult | null>(null);
  const [account, setAccount] = useState("");
  const [preset, setPreset] = useState("last_7d");
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

  const reachById = useMemo(
    () => new Map((providers?.agent_reach.channels ?? []).map((channel) => [channel.id, channel])),
    [providers],
  );

  const readiness = useMemo(() => {
    const values = [
      {
        name: "Trend discovery",
        detail: "Recent demand, community language, and cited web evidence",
        ready: Boolean(
          providers?.last30days.installed &&
            providers.last30days.active &&
            providers.last30days.engine_present,
        ),
      },
      {
        name: "Channel coverage",
        detail: `${providers?.agent_reach.summary.ready ?? 0} of ${providers?.agent_reach.summary.total ?? 0} research channels ready`,
        ready: Boolean(providers?.agent_reach.provider.active),
      },
      {
        name: "Competitive creative",
        detail: "Public Meta Ad Library creative and delivery evidence",
        ready: Boolean(providers?.meta_ads_collector.ready),
      },
      {
        name: "Account validation",
        detail: "Read-only performance, winners, bleeders, and fatigue",
        ready: Boolean(providers?.meta_ads.ready),
      },
    ];
    return { values, ready: values.filter((item) => item.ready).length };
  }, [providers]);

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
            })),
          )
        : [],
    [briefing],
  );

  const liveInspirations = useMemo(
    () => [...adInspirations, ...trendInspirations, ...accountInspirations],
    [accountInspirations, adInspirations, trendInspirations],
  );
  const baseInspirations = liveInspirations.length > 0 ? liveInspirations : STARTER_INSPIRATIONS;
  const visibleInspirations = baseInspirations.filter(
    (item) => feedFilter === "all" || item.kind === feedFilter,
  );

  const last30Ready = Boolean(
    providers?.last30days.installed &&
      providers.last30days.active &&
      providers.last30days.engine_present,
  );
  const collectorReady = Boolean(providers?.meta_ads_collector.ready);
  const metaReady = Boolean(providers?.meta_ads.ready);

  function selectWorkspace(id: string) {
    setWorkspaceId(id);
    setActiveWorkspaceId(id || null);
  }

  function toggleSource(source: string) {
    setSelectedSources((current) =>
      current.includes(source)
        ? current.filter((item) => item !== source)
        : [...current, source],
    );
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
          ? `Research “${normalized}” using the selected evidence sources?`
          : `Search Meta's public Ad Library for “${normalized}”?`,
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
            sources: selectedSources,
            mode: depth,
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
            country,
            ad_type: adType,
            media_type: mediaType,
            max_results: maxResults,
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
          account: account || null,
          preset,
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

  return (
    <main className="research-page research-radar">
      <WorkspaceSectionNav area="research" />
      <header className="research-radar-header">
        <div>
          <p className="eyebrow">INSPIRATION RADAR</p>
          <h1>What is worth making next?</h1>
          <p className="lede">
            Recent demand, competitor creative, and your own performance signals—ranked in
            one place.
          </p>
        </div>
        <div className="research-header-controls">
          <label className="workspace-picker">
            Workspace
            <select value={workspaceId} onChange={(event) => selectWorkspace(event.target.value)}>
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name} · {workspace.role}
                </option>
              ))}
            </select>
          </label>
          <details className="research-sources-menu">
            <summary>
              <span className="source-pulse" />
              {readiness.ready}/{readiness.values.length} intelligence inputs ready
            </summary>
            <div className="research-sources-popover">
              <div className="sources-popover-heading">
                <div>
                  <strong>Intelligence inputs</strong>
                  <small>Different signals, one inspiration ranking.</small>
                </div>
                <button type="button" onClick={() => void refreshProviders()}>
                  Refresh
                </button>
              </div>
              {readiness.values.map((item) => (
                <div className="source-health-row" key={item.name}>
                  <span className={item.ready ? "health-ready" : "health-setup"} />
                  <div>
                    <strong>{item.name}</strong>
                    <small>{item.detail}</small>
                  </div>
                  <span>{item.ready ? "Ready" : "Setup"}</span>
                </div>
              ))}
              <Link href="/tools">Manage research tools</Link>
            </div>
          </details>
        </div>
      </header>

      <section className="research-query-shell" aria-label="Research controls">
        <div className="query-mode-switch" aria-label="Research mode">
          <button
            type="button"
            className={queryMode === "trends" ? "selected" : ""}
            onClick={() => setQueryMode("trends")}
          >
            Find demand
          </button>
          <button
            type="button"
            className={queryMode === "ads" ? "selected" : ""}
            onClick={() => setQueryMode("ads")}
          >
            Inspect ads
          </button>
        </div>
        <form className="research-query-form" onSubmit={runQuery}>
          <label>
            <span>{queryMode === "trends" ? "Topic or product" : "Brand, product, or angle"}</span>
            <input
              required
              minLength={1}
              maxLength={queryMode === "trends" ? 300 : 200}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={
                queryMode === "trends"
                  ? "e.g. portable espresso makers"
                  : "e.g. mushroom coffee"
              }
            />
          </label>
          {queryMode === "trends" ? (
            <label className="query-short-field">
              <span>Depth</span>
              <select value={depth} onChange={(event) => setDepth(event.target.value)}>
                <option value="quick">Quick scan</option>
                <option value="standard">Standard</option>
                <option value="deep">Deep dive</option>
              </select>
            </label>
          ) : (
            <label className="query-short-field">
              <span>Country</span>
              <input
                minLength={2}
                maxLength={2}
                value={country}
                onChange={(event) => setCountry(event.target.value.toUpperCase())}
              />
            </label>
          )}
          <button
            className="research-run-button"
            disabled={
              busy !== null ||
              !workspaceId ||
              (queryMode === "trends" ? !last30Ready : !collectorReady)
            }
          >
            {busy === queryMode
              ? "Gathering signals…"
              : queryMode === "trends"
                ? "Scan trends"
                : "Find creative"}
          </button>
        </form>

        {queryMode === "trends" ? (
          <div className="research-source-picker" aria-label="Optional channels">
            <span>Focus channels</span>
            {TREND_SOURCES.map((source) => {
              const reach = reachById.get(source === "x" ? "twitter" : source);
              return (
                <button
                  key={source}
                  type="button"
                  className={selectedSources.includes(source) ? "selected" : ""}
                  onClick={() => toggleSource(source)}
                  title={reach?.detail ?? "Provider-selected when available"}
                >
                  {source}
                  <small>{reach?.status ?? "auto"}</small>
                </button>
              );
            })}
          </div>
        ) : (
          <details className="research-advanced">
            <summary>Ad search filters</summary>
            <div>
              <label>
                Ad type
                <select value={adType} onChange={(event) => setAdType(event.target.value)}>
                  <option value="all">All ads</option>
                  <option value="political">Political / issue</option>
                  <option value="housing">Housing</option>
                  <option value="employment">Employment</option>
                  <option value="credit">Credit</option>
                </select>
              </label>
              <label>
                Creative
                <select value={mediaType} onChange={(event) => setMediaType(event.target.value)}>
                  <option value="all">Any media</option>
                  <option value="video">Video</option>
                  <option value="image">Image</option>
                  <option value="none">No media</option>
                </select>
              </label>
              <label>
                Results
                <select
                  value={maxResults}
                  onChange={(event) => setMaxResults(Number(event.target.value))}
                >
                  <option value={10}>10</option>
                  <option value={20}>20</option>
                  <option value={30}>30</option>
                  <option value={50}>50</option>
                </select>
              </label>
            </div>
          </details>
        )}
      </section>

      {error && <p className="registry-error research-error" role="alert">{error}</p>}

      <section className="inspiration-section" aria-labelledby="inspiration-heading">
        <div className="inspiration-heading">
          <div>
            <p className="eyebrow">
              {liveInspirations.length > 0 ? "LATEST EVIDENCE" : "STARTER RADAR"}
            </p>
            <h2 id="inspiration-heading">Inspiration worth exploring</h2>
            <p>
              {liveInspirations.length > 0
                ? `${liveInspirations.length} fresh signals from your research activity.`
                : "Proven creative directions to start from while live evidence builds."}
            </p>
          </div>
          <div className="feed-filter" aria-label="Filter inspiration">
            {(
              [
                ["all", "For you"],
                ["trend", "Demand"],
                ["ad", "Ads"],
                ["account", "Your winners"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={feedFilter === value ? "selected" : ""}
                onClick={() => setFeedFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {visibleInspirations.length > 0 ? (
          <div className="inspiration-grid">
            {visibleInspirations.map((item, index) => (
              <article className={`inspiration-card inspiration-${item.kind}`} key={item.id}>
                {item.image && (
                  // eslint-disable-next-line @next/next/no-img-element -- public evidence URL
                  <img src={item.image} alt="" loading="lazy" referrerPolicy="no-referrer" />
                )}
                <div className="inspiration-card-body">
                  <div className="inspiration-card-meta">
                    <span>{item.label}</span>
                    <small>#{String(index + 1).padStart(2, "0")}</small>
                  </div>
                  <h3>{item.title}</h3>
                  <p>{item.summary}</p>
                  {item.metrics && item.metrics.length > 0 && (
                    <div className="inspiration-metrics">
                      {item.metrics.map((metric) => (
                        <span key={metric}>{metric}</span>
                      ))}
                    </div>
                  )}
                  <footer>
                    <span>{item.source}</span>
                    <div>
                      {item.topic && (
                        <button type="button" onClick={() => exploreTopic(item.topic!)}>
                          Explore angle
                        </button>
                      )}
                      {item.href && (
                        <a href={item.href} target="_blank" rel="noreferrer">
                          View evidence
                        </a>
                      )}
                    </div>
                  </footer>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="feed-empty-filter">
            <p>No signals of this type yet.</p>
            <button type="button" onClick={() => setFeedFilter("all")}>Show all inspiration</button>
          </div>
        )}
      </section>

      <section className="research-secondary-grid">
        <details className="account-validation">
          <summary>
            <span>
              <strong>Validate ideas against your ad account</strong>
              <small>Add winners, bleeders, and fatigue to the same inspiration radar.</small>
            </span>
            <span className={metaReady ? "source-ready" : "source-setup"}>
              {metaReady ? "Ready" : "Setup needed"}
            </span>
          </summary>
          <div className="account-validation-form">
            <label>
              Account (optional)
              <input
                value={account}
                onChange={(event) => setAccount(event.target.value)}
                placeholder="act_123456789"
              />
            </label>
            <label>
              Window
              <select value={preset} onChange={(event) => setPreset(event.target.value)}>
                <option value="last_7d">Last 7 days</option>
                <option value="last_30d">Last 30 days</option>
                <option value="last_90d">Last 90 days</option>
                <option value="today">Today</option>
                <option value="yesterday">Yesterday</option>
              </select>
            </label>
            <button
              type="button"
              disabled={!metaReady || busy !== null}
              onClick={() => void runAccountValidation()}
            >
              {busy === "account" ? "Reading performance…" : "Add account signals"}
            </button>
          </div>
        </details>

        <details className="research-activity">
          <summary>
            <span>
              <strong>Research activity</strong>
              <small>{jobs.length} recent runs · open for provenance and errors</small>
            </span>
          </summary>
          <div className="research-activity-list">
            {jobs.length === 0 && <p>No workspace research runs yet.</p>}
            {jobs.slice(0, 10).map((job) => (
              <article key={job.id}>
                <span className={`activity-status activity-${job.status}`} />
                <div>
                  <strong>{job.topic}</strong>
                  <small>
                    {job.status} · {new Date(job.created_at).toLocaleString()}
                  </small>
                  {job.error && <p>{job.error}</p>}
                </div>
                {job.status === "succeeded" && (job.observations?.length ?? 0) > 0 && (
                  <Link
                    href={`/opportunities?trend=${encodeURIComponent(job.topic)}&job=${encodeURIComponent(job.id)}`}
                  >
                    Score opportunity
                  </Link>
                )}
              </article>
            ))}
          </div>
        </details>
      </section>
    </main>
  );
}
