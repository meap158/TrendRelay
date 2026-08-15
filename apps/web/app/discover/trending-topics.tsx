"use client";

/**
 * One list of topics, merged across sources and sorted by what is worth making.
 *
 * The lists above this one are each a single source answering a single
 * question. This asks every source, merges the answers, and sorts them for
 * ideation - so the question it answers is "what should I film" rather than
 * "what is on TikTok right now".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { apiBaseUrl } from "../../lib/api";
import { seedFromTopic, type DiscoverySeed } from "../../lib/discovery-ideas";
import { SHAPE_COPY, reasons, searchTerm, windowSummary, type Shape, type Topic } from "../../lib/trend-shapes";
import { Button } from "../ui/button";
import { usePersistedCache, usePersistedState } from "../ui/use-persisted-state";

type Consolidated = {
  region: string;
  windows: number[];
  sources: string[];
  complete: boolean;
  notes: string[];
  topics: Topic[];
  topic_count: number;
};

/**
 * Countries this list can be asked about.
 *
 * China is here where the single-source lists do not offer it, because it is
 * the one country where two sources can corroborate each other: the Douyin
 * board only covers CN.
 */
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
  ["CN", "China"],
];

/**
 * The time control, phrased as what somebody is looking for.
 *
 * It filters the ranked result rather than narrowing the fetch: every window is
 * always read, because a topic's shape is only visible by comparing them.
 */
const LENSES: ReadonlyArray<{ id: string; label: string; shapes: Shape[]; hint: string }> = [
  { id: "all", label: "Everything", shapes: [], hint: "Every topic, best first." },
  {
    id: "evergreen",
    label: "Evergreen",
    shapes: ["durable"],
    hint: "Held across all three windows - still findable next month.",
  },
  {
    id: "emerging",
    label: "Emerging",
    shapes: ["emerging"],
    hint: "Only in the last 7 days. Quick turnaround or not at all.",
  },
];

const TONE_COLOUR: Record<string, string> = {
  good: "var(--good, #1a7f37)",
  info: "var(--info, #0969da)",
  warn: "var(--warn, #9a6700)",
  muted: "var(--muted)",
};

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
  sub: { fontSize: "13px", color: "var(--muted)", margin: 0, maxWidth: "52ch" },
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
    minWidth: "180px",
  },
  lensRow: { display: "flex", gap: "6px", flexWrap: "wrap" },
  hint: { fontSize: "12px", color: "var(--muted)", margin: "10px 0 0" },
  error: { fontSize: "13px", color: "var(--danger, #b42318)", margin: "12px 0 0" },
  coverage: { fontSize: "12px", color: "var(--muted)", margin: "16px 0 0" },
  notes: { margin: "8px 0 0", padding: "0 0 0 18px", fontSize: "12px", color: "var(--muted)" },
  notesWarn: { color: "var(--warn, #9a6700)" },
  note: { margin: "2px 0" },
  empty: { fontSize: "13px", color: "var(--muted)", margin: "16px 0 0" },
  list: { listStyle: "none", margin: "16px 0 0", padding: 0, display: "grid", gap: "10px" },
  row: {
    display: "flex",
    gap: "12px",
    alignItems: "flex-start",
    padding: "12px 14px",
    border: "1px solid var(--line)",
    borderRadius: "12px",
    background: "var(--panel)",
  },
  place: {
    fontSize: "12px",
    fontWeight: 600,
    color: "var(--muted)",
    minWidth: "20px",
    paddingTop: "2px",
  },
  body: { flex: 1, minWidth: 0 },
  titleRow: { display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" },
  label: { fontSize: "15px", fontWeight: 600, color: "var(--text)", wordBreak: "break-word" },
  shape: {
    fontSize: "11px",
    fontWeight: 600,
    padding: "1px 7px",
    borderRadius: "999px",
    border: "1px solid",
  },
  advice: { fontSize: "13px", color: "var(--text)", margin: "4px 0 0" },
  why: { margin: "6px 0 0", padding: "0 0 0 16px", fontSize: "12px", color: "var(--muted)" },
  reason: { margin: "1px 0" },
  actions: { display: "flex", gap: "6px", flexShrink: 0, flexWrap: "wrap" },
};

export function TrendingTopics({
  onResearch,
  onScore,
  selectedIds,
  onToggle,
}: {
  onResearch: (term: string) => void;
  onScore: (topic: Topic) => void;
  selectedIds?: ReadonlySet<string>;
  onToggle?: (seed: DiscoverySeed) => void;
}) {
  const [region, setRegion] = usePersistedState<string>(
    "trendrelay.discover.consolidated.region",
    "US",
    (value): value is string => REGIONS.some(([code]) => code === value),
  );
  const [lens, setLens] = usePersistedState<string>(
    "trendrelay.discover.consolidated.lens",
    "all",
    (value): value is string => LENSES.some((item) => item.id === value),
  );
  /**
   * Kept between visits, because building it is three Creative Center renders.
   *
   * Half an hour: long enough that walking between pages does not re-render
   * TikTok three times, short enough that a list called "worth making" is not
   * describing yesterday.
   */
  const [result, setResult, , cacheReady] = usePersistedCache<Consolidated>(
    "trendrelay.discover.consolidated",
    30 * 60 * 1000,
    (value): value is Consolidated =>
      typeof value === "object" && value !== null && Array.isArray((value as Consolidated).topics),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    const shapes = LENSES.find((item) => item.id === lens)?.shapes ?? [];
    const query = new URLSearchParams({ region });
    for (const shape of shapes) query.append("shape", shape);
    try {
      const response = await fetch(
        `${apiBaseUrl()}/api/research/trends/consolidated?${query.toString()}`,
      );
      const payload = await response.json();
      if (!response.ok) {
        // The API distinguishes "no source answered" from "nothing is trending",
        // and that distinction is the only useful thing to say here.
        throw new Error(payload?.detail ?? "The trend sources could not be read.");
      }
      setResult(payload as Consolidated);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }, [lens, region, setResult]);

  /**
   * Build it without being asked, once there is nothing cached to show.
   *
   * A section headed "Topics worth making" that is empty until a button is
   * found is a section most people never see. It waits for the cache to be
   * read first, so a fresh visit with a good answer already stored does not
   * spend three renders re-fetching it.
   */
  const autoBuilt = useRef(false);
  useEffect(() => {
    if (!cacheReady || result || busy || error || autoBuilt.current) return;
    autoBuilt.current = true;
    void load();
  }, [cacheReady, result, busy, error, load]);

  // A different country is a different question, so the answer on screen no
  // longer belongs to it. The lens only filters, and is left to the button.
  const firstRegion = useRef(region);
  useEffect(() => {
    if (firstRegion.current === region) return;
    firstRegion.current = region;
    void load();
    // `load` is rebuilt whenever the region changes, which would run this again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [region]);

  const activeLens = LENSES.find((item) => item.id === lens) ?? LENSES[0];

  return (
    <section style={S.section} aria-labelledby="consolidated-heading">
      <div style={S.head}>
        <div>
          <h2 id="consolidated-heading" style={S.heading}>
            Topics worth making
          </h2>
          <p style={S.sub}>
            Every source, every window, merged into one list. A topic that held for four months
            beats one that is loud this week.
          </p>
        </div>
        <Button variant="primary" onClick={load} busy={busy}>
          <RefreshCw size={15} aria-hidden />
          {result ? "Refresh" : "Build the list"}
        </Button>
      </div>

      <div style={S.controls}>
        <label style={S.control}>
          <span style={S.controlLabel}>Country</span>
          <select
            value={region}
            onChange={(event) => setRegion(event.target.value)}
            style={S.select}
          >
            {REGIONS.map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <div style={S.control}>
          <span style={S.controlLabel}>Looking for</span>
          <div style={S.lensRow} role="group" aria-label="What kind of topic">
            {LENSES.map((item) => (
              <Button
                key={item.id}
                variant="quiet"
                size="sm"
                selected={item.id === lens}
                aria-pressed={item.id === lens}
                onClick={() => setLens(item.id)}
              >
                {item.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
      <p style={S.hint}>{activeLens.hint}</p>

      {error && <p style={S.error}>{error}</p>}

      {result && <Result
        result={result}
        onResearch={onResearch}
        onScore={onScore}
        selectedIds={selectedIds}
        onToggle={onToggle}
      />}
    </section>
  );
}

function Result({
  result,
  onResearch,
  onScore,
  selectedIds,
  onToggle,
}: {
  result: Consolidated;
  onResearch: (term: string) => void;
  onScore: (topic: Topic) => void;
  selectedIds?: ReadonlySet<string>;
  onToggle?: (seed: DiscoverySeed) => void;
}) {
  return (
    <>
      <p style={S.coverage}>
        {windowSummary(result.windows)}{" "}
        {result.sources.length > 0
          ? `Sources: ${result.sources.join(", ")}.`
          : "No source contributed."}
      </p>

      {/* A list quietly missing a window looks exactly like a list where nothing
          held, so what could not be read is said rather than left as an absence. */}
      {result.notes.length > 0 && (
        <ul style={result.complete ? S.notes : { ...S.notes, ...S.notesWarn }}>
          {result.notes.map((note) => (
            <li key={note} style={S.note}>
              {note}
            </li>
          ))}
        </ul>
      )}

      {result.topics.length === 0 ? (
        <p style={S.empty}>
          Nothing matched. The sources answered; they just had no topic of this kind for{" "}
          {result.region}.
        </p>
      ) : (
        <ol style={S.list}>
          {result.topics.map((topic, index) => (
            <TopicRow
              key={`${topic.key}:${topic.region}`}
              topic={topic}
              place={index + 1}
              onResearch={onResearch}
              onScore={onScore}
              selectedIds={selectedIds}
              onToggle={onToggle}
            />
          ))}
        </ol>
      )}
    </>
  );
}

function TopicRow({
  topic,
  place,
  onResearch,
  onScore,
  selectedIds,
  onToggle,
}: {
  topic: Topic;
  place: number;
  onResearch: (term: string) => void;
  onScore: (topic: Topic) => void;
  selectedIds?: ReadonlySet<string>;
  onToggle?: (seed: DiscoverySeed) => void;
}) {
  const copy = SHAPE_COPY[topic.shape];
  const why = reasons(topic);
  const seed = seedFromTopic(topic);
  const selected = selectedIds?.has(seed.id) ?? false;
  return (
    <li style={S.row}>
      <span style={S.place} aria-hidden>
        {place}
      </span>
      <div style={S.body}>
        <div style={S.titleRow}>
          <span style={S.label}>{topic.label}</span>
          <span style={{ ...S.shape, color: TONE_COLOUR[copy.tone], borderColor: "currentColor" }}>
            {copy.label}
          </span>
        </div>
        <p style={S.advice}>{copy.advice}</p>
        <ul style={S.why}>
          {why.map((reason) => (
            <li key={reason.key} style={S.reason}>
              {reason.text}
            </li>
          ))}
        </ul>
      </div>
      <div style={S.actions}>
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
        <Button variant="quiet" size="sm" onClick={() => onResearch(searchTerm(topic))}>
          Research
        </Button>
        <Button variant="quiet" size="sm" onClick={() => onScore(topic)}>
          Score it
        </Button>
      </div>
    </li>
  );
}
