"use client";

/**
 * Who is winning right now, and with what.
 *
 * The lists above are each a source answering its own question, and the topic
 * engine behind `trend_consolidation` answers "what subject is worth making".
 * This answers a blunter one - which posts are actually doing well, and who
 * made them - because a hashtag says a subject exists while a post says
 * somebody made something in it and it worked.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { apiBaseUrl } from "../../lib/api";
import { seedFromPost, type DiscoverySeed } from "../../lib/discovery-ideas";
import {
  coverageNote,
  creatorSearchUrl,
  postMetrics,
  type PopularPost,
} from "../../lib/post-board";
import { Button } from "../ui/button";
import { usePersistedCache, usePersistedState } from "../ui/use-persisted-state";

type Board = {
  region: string;
  period_days: number;
  posts: PopularPost[];
  post_count: number;
  sources: string[];
  complete: boolean;
  notes: string[];
  platform: "all" | "tiktok" | "youtube";
  providers: Array<{
    id: "tiktok" | "youtube";
    label: string;
    available: boolean;
    reason: string | null;
  }>;
};

const PLATFORMS: ReadonlyArray<readonly ["all" | "tiktok" | "youtube", string]> = [
  ["all", "All available"],
  ["tiktok", "TikTok"],
  ["youtube", "YouTube"],
];

/** Countries Creative Center will answer for. */
const REGIONS: ReadonlyArray<readonly [string, string]> = [
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
  ["VN", "Vietnam"],
];

/** The windows the source offers. One is asked for, not all three. */
const PERIODS: ReadonlyArray<readonly [number, string]> = [
  [7, "Last 7 days"],
  [30, "Last 30 days"],
  [120, "Last 120 days"],
];

const S: Record<string, React.CSSProperties> = {
  section: { padding: "8px 0 4px" },
  head: {
    display: "flex",
    gap: "16px",
    alignItems: "flex-start",
    justifyContent: "space-between",
    flexWrap: "wrap",
  },
  heading: { fontSize: "18px", fontWeight: 600, margin: "0 0 4px", color: "var(--text)" },
  sub: { fontSize: "13px", color: "var(--muted)", margin: 0, maxWidth: "56ch" },
  controls: { display: "flex", gap: "20px", flexWrap: "wrap", margin: "16px 0 0" },
  control: { display: "flex", flexDirection: "column", gap: "6px" },
  controlLabel: {
    fontSize: "11px",
    fontWeight: 600,
    letterSpacing: "0.04em",
    textTransform: "uppercase",
    color: "var(--muted)",
  },
  select: {
    padding: "7px 10px",
    borderRadius: "8px",
    border: "1px solid var(--line-strong)",
    background: "var(--panel)",
    color: "var(--text)",
    fontSize: "13px",
    minWidth: "170px",
  },
  coverage: { fontSize: "12px", color: "var(--muted)", margin: "16px 0 0" },
  notes: { margin: "8px 0 0", padding: "0 0 0 18px", fontSize: "12px", color: "var(--muted)" },
  notesWarn: { color: "var(--warn, #9a6700)" },
  error: { fontSize: "13px", color: "var(--danger, #b42318)", margin: "12px 0 0" },
  list: { listStyle: "none", margin: "14px 0 0", padding: 0, display: "grid", gap: "8px" },
  row: {
    display: "flex",
    gap: "12px",
    alignItems: "center",
    padding: "11px 14px",
    border: "1px solid var(--line)",
    borderRadius: "12px",
    background: "var(--panel)",
  },
  place: {
    fontSize: "12px",
    fontWeight: 600,
    color: "var(--muted)",
    minWidth: "20px",
    fontVariantNumeric: "tabular-nums",
  },
  cover: {
    inlineSize: "44px",
    blockSize: "58px",
    flexShrink: 0,
    objectFit: "cover",
    borderRadius: "6px",
    background: "var(--panel-raised)",
  },
  coverMissing: { border: "1px dashed var(--line-strong)" },
  body: { flex: 1, minWidth: 0 },
  creator: {
    fontSize: "15px",
    fontWeight: 600,
    color: "var(--text)",
    wordBreak: "break-word",
  },
  title: { display: "block", fontSize: "14px", fontWeight: 600, color: "var(--text)" },
  byline: { display: "flex", gap: "7px", alignItems: "center", flexWrap: "wrap", marginTop: "3px" },
  source: {
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: "0.04em",
    textTransform: "uppercase",
    color: "var(--link)",
  },
  niche: {
    fontSize: "11px",
    fontWeight: 600,
    color: "var(--muted)",
    border: "1px solid var(--line-strong)",
    borderRadius: "999px",
    padding: "1px 7px",
    marginInlineStart: "8px",
    whiteSpace: "nowrap",
  },
  metrics: { fontSize: "12px", color: "var(--muted)", margin: "3px 0 0" },
  actions: { display: "flex", gap: "6px", flexShrink: 0, flexWrap: "wrap", justifyContent: "flex-end" },
  empty: { fontSize: "13px", color: "var(--muted)", margin: "16px 0 0" },
};

export function PopularPosts({
  onResearch,
  selectedIds,
  onToggle,
}: {
  onResearch: (term: string) => void;
  selectedIds?: ReadonlySet<string>;
  onToggle?: (seed: DiscoverySeed) => void;
}) {
  const [region, setRegion] = usePersistedState<string>(
    "trendrelay.discover.posts.region",
    "US",
    (value): value is string => REGIONS.some(([code]) => code === value),
  );
  const [period, setPeriod] = usePersistedState<number>(
    "trendrelay.discover.posts.period",
    7,
    (value): value is number => PERIODS.some(([days]) => days === value),
  );
  const [platform, setPlatform] = usePersistedState<"all" | "tiktok" | "youtube">(
    "trendrelay.discover.posts.platform",
    "all",
    (value): value is "all" | "tiktok" | "youtube" =>
      PLATFORMS.some(([id]) => id === value),
  );
  const [board, setBoard, , cacheReady] = usePersistedCache<Board>(
    "trendrelay.discover.posts.v2",
    30 * 60 * 1000,
    (value): value is Board =>
      typeof value === "object" && value !== null && Array.isArray((value as Board).posts),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    const query = new URLSearchParams({ region, period: String(period), platform });
    try {
      const response = await fetch(
        `${apiBaseUrl()}/api/research/posts/popular?${query.toString()}`,
      );
      const payload = await response.json();
      if (!response.ok) {
        // The API separates "nothing answered" from "a quiet week", and that
        // distinction is the only useful thing to repeat here.
        throw new Error(payload?.detail ?? "The board could not be read.");
      }
      setBoard(payload as Board);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      setBoard(null);
    } finally {
      setBusy(false);
    }
  }, [period, platform, region, setBoard]);

  // Built without being asked, once there is nothing cached worth showing.
  const autoBuilt = useRef(false);
  useEffect(() => {
    if (!cacheReady || board || busy || error || autoBuilt.current) return;
    autoBuilt.current = true;
    void load();
  }, [cacheReady, board, busy, error, load]);

  // A different country or window is a different question, so what is on
  // screen no longer answers it.
  const asked = useRef(`${region}:${period}:${platform}`);
  useEffect(() => {
    const next = `${region}:${period}:${platform}`;
    if (asked.current === next) return;
    asked.current = next;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [region, period, platform]);

  return (
    <section style={S.section} aria-labelledby="popular-posts-heading">
      <div style={S.head}>
        <div>
          <h2 id="popular-posts-heading" style={S.heading}>
            Popular right now
          </h2>
          <p style={S.sub}>
            Public videos actually doing well, and the people who made them. Compare platforms,
            choose evidence, then turn it into an editable Campaign brief.
          </p>
        </div>
        <Button variant="primary" onClick={load} busy={busy}>
          <RefreshCw size={15} aria-hidden />
          {board ? "Refresh" : "Build the board"}
        </Button>
      </div>

      <div style={S.controls}>
        <label style={S.control}>
          <span style={S.controlLabel}>Platform</span>
          <select
            value={platform}
            onChange={(event) => setPlatform(event.target.value as typeof platform)}
            style={S.select}
          >
            {PLATFORMS.map(([id, label]) => {
              const provider = board?.providers?.find((item) => item.id === id);
              const unavailable = id !== "all" && provider?.available === false;
              return (
                <option key={id} value={id} disabled={unavailable}>
                  {label}{unavailable ? " · setup needed" : ""}
                </option>
              );
            })}
          </select>
        </label>
        <label style={S.control}>
          <span style={S.controlLabel}>Country</span>
          <select value={region} onChange={(event) => setRegion(event.target.value)} style={S.select}>
            {REGIONS.map(([code, name]) => (
              <option key={code} value={code}>{name}</option>
            ))}
          </select>
        </label>
        {platform === "youtube" ? (
          <label style={S.control}>
            <span style={S.controlLabel}>Time basis</span>
            <span style={{ ...S.select, color: "var(--muted)" }}>Current regional chart</span>
          </label>
        ) : (
          <label style={S.control}>
            <span style={S.controlLabel}>TikTok window</span>
            <select
              value={period}
              onChange={(event) => setPeriod(Number(event.target.value))}
              style={S.select}
            >
              {PERIODS.map(([days, label]) => (
                <option key={days} value={days}>{label}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && <p style={S.error}>{error}</p>}

      {board && (
        <>
          <p style={S.coverage}>
            {coverageNote(board.region, board.period_days, board.post_count, board.sources)}
          </p>
          {board.providers?.some((provider) => !provider.available) && (
            <p style={S.coverage}>
              {board.providers.filter((provider) => !provider.available)
                .map((provider) => `${provider.label}: ${provider.reason}`).join(" ")}
            </p>
          )}
          {/* A board cut short by the source looks exactly like a small week,
              so what could not be read is said rather than left as an absence. */}
          {board.notes.length > 0 && (
            <ul style={board.complete ? S.notes : { ...S.notes, ...S.notesWarn }}>
              {board.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          )}
          {board.posts.length === 0 ? (
            <p style={S.empty}>The source answered; it just had nothing for {board.region}.</p>
          ) : (
            <ol style={S.list}>
              {board.posts.map((post) => {
                const link = creatorSearchUrl(post);
                const metrics = postMetrics(post);
                const seed = seedFromPost(post);
                const selected = selectedIds?.has(seed.id) ?? false;
                return (
                  <li
                    key={`${post.source}:${post.creator}:${post.rank}`}
                    className="discover-post-row"
                    style={S.row}
                  >
                    <span style={S.place} aria-label={`${post.source} rank ${post.rank}`}>
                      {post.rank}
                    </span>
                    {/* The cover is the post. Without it a row is a creator's
                        name and two numbers, which is not what was asked for.
                        Decorative: the creator beside it already names it. */}
                    {post.thumbnail ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img alt="" src={post.thumbnail} style={S.cover} loading="lazy" />
                    ) : (
                      <span style={{ ...S.cover, ...S.coverMissing }} aria-hidden />
                    )}
                    <div style={S.body}>
                      {post.title && <span style={S.title}>{post.title}</span>}
                      <span style={S.byline}>
                        <span style={S.source}>{post.source}</span>
                        <span style={S.creator}>{post.creator}</span>
                        {post.niche && <span style={S.niche}>{post.niche}</span>}
                      </span>
                      <p style={S.metrics}>{metrics.join(" · ") || "No counts given"}</p>
                    </div>
                    <div className="discover-post-actions" style={S.actions}>
                      {onToggle && (
                        <Button
                          variant={selected ? "secondary" : "quiet"}
                          size="sm"
                          selected={selected}
                          aria-pressed={selected}
                          onClick={() => onToggle(seed)}
                        >
                          {selected ? "Added" : "Add to idea"}
                        </Button>
                      )}
                      <Button
                        variant="quiet"
                        size="sm"
                        onClick={() => onResearch(post.title ?? post.creator)}
                      >
                        Research
                      </Button>
                      {link && (
                        <a
                          className="link-action"
                          href={link}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {post.url ? "Open post" : "Find on TikTok"}
                        </a>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </>
      )}
      {board && board.posts.some((post) => !post.url) && (
        <p style={S.coverage}>
          Creative Center names the creator and the counts but never the video, so these open a
          search for the creator rather than the post itself.
        </p>
      )}
      {!board && !busy && !error && (
        <p style={S.empty}>Nothing collected yet.</p>
      )}
    </section>
  );
}
