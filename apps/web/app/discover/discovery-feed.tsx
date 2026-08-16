"use client";

/**
 * One list of what is hot, across every source that will answer.
 *
 * Discover used to ask two questions on two boards - "what subject is worth
 * making" and "which posts are doing well" - and answer each per source. That
 * was readable at three sources. At seven it is fourteen answers to scan before
 * knowing anything, which is the opposite of what the page is for.
 *
 * So this is the page's first screen: one ranked stream, one region, one
 * window, and a chip per source to narrow it. The two detailed boards still
 * exist below for the questions this cannot answer - trend shapes, engagement
 * sorting, the research jobs - because a summary that replaces its own detail
 * is a summary nobody can check.
 *
 * `lib/discovery-feed` holds the merging, and holds it apart from the markup on
 * purpose: which row outranks which is the part worth testing, and it is not
 * testable through a component.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";

import { apiBaseUrl } from "../../lib/api";
import {
  mergeFeed,
  rowFromPost,
  rowFromTopic,
  sourcesIn,
  type FeedRow,
} from "../../lib/discovery-feed";
import { seedFromPost, seedFromTopic, type DiscoverySeed } from "../../lib/discovery-ideas";
import type { PopularPost } from "../../lib/post-board";
import type { Topic } from "../../lib/trend-shapes";
import { Button } from "../ui/button";
import { usePersistedCache, usePersistedState } from "../ui/use-persisted-state";

/** Countries every source that has a region will answer for. */
const REGIONS: ReadonlyArray<readonly [string, string]> = [
  ["VN", "Vietnam"],
  ["US", "United States"],
  ["GB", "United Kingdom"],
  ["ID", "Indonesia"],
  ["JP", "Japan"],
  ["DE", "Germany"],
  ["FR", "France"],
  ["BR", "Brazil"],
];

const PERIODS: ReadonlyArray<readonly [number, string]> = [
  [7, "Last 7 days"],
  [30, "Last 30 days"],
  [120, "Last 120 days"],
];

/** What each source is called on its chip, since the ids are not names. */
const SOURCE_LABELS: Record<string, string> = {
  topics: "Cross-source",
  "google-trends": "Google Trends",
  tiktok: "TikTok",
  douyin: "Douyin",
  youtube: "YouTube",
  bluesky: "Bluesky",
  hackernews: "Hacker News",
};

function label(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

type Loaded = {
  rows: FeedRow[];
  /** Kept beside the rows: a partial answer that looks whole is the failure. */
  notes: string[];
  seeds: Record<string, DiscoverySeed>;
};

export function DiscoveryFeed({
  selectedIds,
  onToggle,
}: {
  selectedIds?: ReadonlySet<string>;
  onToggle?: (seed: DiscoverySeed) => void;
}) {
  const [region, setRegion] = usePersistedState<string>(
    "trendrelay.discover.feed.region",
    "VN",
    (value): value is string => REGIONS.some(([code]) => code === value),
  );
  const [period, setPeriod] = usePersistedState<number>(
    "trendrelay.discover.feed.period",
    7,
    (value): value is number => PERIODS.some(([days]) => days === value),
  );
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [loaded, setLoaded, , cacheReady] = usePersistedCache<Loaded>(
    "trendrelay.discover.feed.v1",
    30 * 60 * 1000,
    (value): value is Loaded =>
      typeof value === "object" && value !== null && Array.isArray((value as Loaded).rows),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    const rows: FeedRow[] = [];
    const seeds: Record<string, DiscoverySeed> = {};
    const notes: string[] = [];

    // Asked for together and reported separately. One source being down is a
    // thinner list, not an empty page, so a rejected half does not discard the
    // half that answered.
    const [topics, posts] = await Promise.allSettled([
      fetch(`${apiBaseUrl()}/api/research/trends/consolidated?${new URLSearchParams({ region })}`),
      fetch(`${apiBaseUrl()}/api/research/posts/popular?${new URLSearchParams({
        region, period: String(period), platform: "all",
      })}`),
    ]);

    if (topics.status === "fulfilled" && topics.value.ok) {
      const payload = await topics.value.json();
      for (const topic of (payload.topics ?? []) as Topic[]) {
        const row = rowFromTopic(topic);
        rows.push(row);
        seeds[row.key] = seedFromTopic(topic);
      }
      notes.push(...((payload.notes ?? []) as string[]));
    } else {
      notes.push("No topic source answered.");
    }

    if (posts.status === "fulfilled" && posts.value.ok) {
      const payload = await posts.value.json();
      ((payload.posts ?? []) as PopularPost[]).forEach((post, index) => {
        const row = rowFromPost(post, index);
        rows.push(row);
        seeds[row.key] = seedFromPost(post);
      });
      notes.push(...((payload.notes ?? []) as string[]));
    } else {
      notes.push("No post source answered.");
    }

    if (!rows.length) {
      setError(notes[0] ?? "Nothing answered.");
    }
    setLoaded({ rows, notes: [...new Set(notes)], seeds });
    setBusy(false);
  }, [period, region, setLoaded]);

  useEffect(() => {
    if (!cacheReady) return;
    // Deferred out of the effect body: the load settles state, and doing that
    // synchronously here is the cascading-render pattern React warns about.
    queueMicrotask(() => { void load(); });
  }, [cacheReady, load]);

  const rows = useMemo(() => loaded?.rows ?? [], [loaded]);
  const sources = useMemo(() => sourcesIn(rows), [rows]);
  const shown = useMemo(
    () => mergeFeed(rows.filter((row) => !hidden.has(row.source))),
    [rows, hidden],
  );

  function toggleSource(source: string) {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(source)) next.delete(source);
      else next.add(source);
      return next;
    });
  }

  return (
    <section className="discovery-feed">
      <header className="discovery-feed-head">
        <div>
          <h2>What is hot right now</h2>
          <p>
            Every source that answered, ranked together. Each row keeps the figure
            its own source published — they are different quantities and are not
            added up.
          </p>
        </div>
        <div className="discovery-feed-controls">
          <label>
            Region
            <select value={region} onChange={(event) => setRegion(event.target.value)}>
              {REGIONS.map(([code, name]) => (
                <option key={code} value={code}>{name}</option>
              ))}
            </select>
          </label>
          <label>
            Window
            <select
              value={period}
              onChange={(event) => setPeriod(Number(event.target.value))}
            >
              {PERIODS.map(([days, name]) => (
                <option key={days} value={days}>{name}</option>
              ))}
            </select>
          </label>
          <Button variant="secondary" size="sm" onClick={() => void load()} busy={busy}>
            <RefreshCw size={14} /> Refresh
          </Button>
        </div>
      </header>

      {/* One chip per source that actually returned something, rather than a
          fixed list: a chip for a source that answered nothing is a filter
          that does nothing, and looks like a bug. */}
      {sources.length > 1 && (
        <div className="discovery-feed-chips">
          {sources.map((source) => (
            <button
              key={source}
              type="button"
              data-on={!hidden.has(source) || undefined}
              onClick={() => toggleSource(source)}
            >
              {label(source)}
              <em>{rows.filter((row) => row.source === source).length}</em>
            </button>
          ))}
        </div>
      )}

      {error && <p className="discovery-feed-error">{error}</p>}

      <ol className="discovery-feed-rows">
        {shown.map((row) => {
          const seed = loaded?.seeds[row.key];
          const picked = Boolean(seed && selectedIds?.has(seed.id));
          return (
            <li key={row.key} data-kind={row.kind}>
              <span className="discovery-feed-rank">{row.rank}</span>
              <span className="discovery-feed-body">
                <strong>{row.title}</strong>
                <small>
                  <b>{label(row.source)}</b>
                  {row.detail && <> · {row.detail}</>}
                </small>
              </span>
              <span className="discovery-feed-metric">{row.metric}</span>
              <span className="discovery-feed-actions">
                {onToggle && seed && (
                  <Button
                    variant={picked ? "primary" : "quiet"}
                    size="sm"
                    onClick={() => onToggle(seed)}
                  >{picked ? "Added" : "Use"}</Button>
                )}
                {row.url && (
                  <a href={row.url} target="_blank" rel="noreferrer" aria-label="Open">
                    <ExternalLink size={14} />
                  </a>
                )}
              </span>
            </li>
          );
        })}
      </ol>

      {!shown.length && !busy && !error && (
        <p className="discovery-feed-empty">
          {rows.length
            ? "Every source is switched off. Turn one back on above."
            : "Nothing has come back yet."}
        </p>
      )}

      {/* Kept under the list rather than above it. These are qualifications on
          what was just read, and a page that opens with its caveats buries the
          thing they qualify. */}
      {Boolean(loaded?.notes.length) && (
        <details className="discovery-feed-notes">
          <summary>What these sources do and do not cover</summary>
          <ul>
            {loaded?.notes.map((note) => <li key={note}>{note}</li>)}
          </ul>
        </details>
      )}
    </section>
  );
}
