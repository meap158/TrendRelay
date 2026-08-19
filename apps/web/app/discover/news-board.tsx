"use client";

/**
 * What is happening, at the top of Discover.
 *
 * The post board answers "what did well". This answers "what is going on",
 * which is where most timely content actually starts, and it is the half
 * Discover never had - a page about finding something to make that could not
 * tell you a single thing that had happened that morning.
 *
 * Two shelves, and they are different questions. **Widely covered** is the
 * story several newsrooms are running at once, which is corroboration no
 * single outlet can manufacture. **Just in** is the recent one only one
 * newsroom has, which is earlier and less certain - the news equivalent of
 * the post board's emerging shelf, and carrying the same warning.
 *
 * Every row ends where the post board's rows end: added to an idea. That is
 * the whole point of putting news here - not to read it, but to make something
 * from it while it is still today's.
 *
 * **Density is a real control, not decoration.** This board sits above the
 * rest of Discover, so whatever it spends is spent by every section below it.
 * Three levels, the way a browser's new-tab feed offers them: cards when you
 * are browsing, rows normally, and headlines when you know what you are
 * looking for and want ten of them on screen instead of four.
 */

import { useEffect, useMemo, useState } from "react";
import { Building2, Cpu, Globe, Newspaper, Plus, RefreshCw, Zap } from "lucide-react";

import { apiBaseUrl } from "../../lib/api";
import {
  coverageLabel,
  sinceLabel,
  type Desk,
  type NewsBoard as Board,
  type NewsStory,
} from "../../lib/news-stories";
import { seedFromNewsStory, type DiscoverySeed, type SeedLabels } from "../../lib/discovery-ideas";
import { useLocale } from "../i18n-provider";
import { Button } from "../ui/button";
import { SegmentedControl } from "../ui/segmented";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";

type Density = "cards" | "rows" | "headlines";

function Row({
  story,
  density,
  added,
  onAdd,
}: {
  story: NewsStory;
  density: Density;
  added: boolean;
  onAdd: () => void;
}) {
  const { t } = useLocale();
  const since = sinceLabel(story.published_at, Date.now(), {
    justNow: t("discover.news.sinceJustNow"),
    minutes: t("discover.news.sinceMinutes"),
    hours: t("discover.news.sinceHours"),
    days: t("discover.news.sinceDays"),
  });
  const outlets = coverageLabel(story, {
    pair: t("discover.news.coveragePair"),
    many: t("discover.news.coverageMany"),
  });
  return (
    <li className="news-row">
      {/* The text is one cell rather than three siblings the button has to
          span. A span has to know how many rows there are, and that changes
          with density - at "cards" a summary appears and the button sank to
          the bottom of a three-line row. */}
      <div className="news-row-text">
        <a className="news-headline" href={story.url} target="_blank" rel="noreferrer">
          {story.title}
        </a>
        {density === "cards" && story.summary ? (
          <p className="news-summary">{story.summary}</p>
        ) : null}
        {density === "headlines" ? null : (
          <p className="news-meta">
            {story.coverage > 1 ? (
              // The count is the finding, so it is the part that is
              // emphasised. Without it this row is a link to a news site.
              <span className="news-count">
                {t("discover.news.newsrooms", { count: story.coverage })}
              </span>
            ) : null}
            <span>{outlets}</span>
            {since ? <span>{since}</span> : null}
          </p>
        )}
      </div>
      <Button
        variant={added ? "quiet" : "secondary"}
        size="sm"
        disabled={added}
        onClick={onAdd}
      >
        {added ? t("discover.news.added") : <><Plus size={13} aria-hidden="true" /> {t("discover.news.add")}</>}
      </Button>
    </li>
  );
}

function Shelf({
  title,
  blurb,
  icon: Icon,
  tone,
  stories,
  density,
  chosen,
  onAdd,
}: {
  title: string;
  blurb: string;
  icon: typeof Zap;
  tone: "covered" | "breaking";
  stories: NewsStory[];
  density: Density;
  chosen: Set<string>;
  onAdd: (story: NewsStory) => void;
}) {
  if (!stories.length) return null;
  return (
    <section className={`news-shelf ${tone}`} aria-label={title}>
      <header>
        <Icon size={14} aria-hidden="true" />
        <h3>{title}</h3>
        <p>{blurb}</p>
      </header>
      <ul data-density={density}>
        {stories.map((story) => (
          <Row
            key={story.id}
            story={story}
            density={density}
            // Asked of the seed this row would create rather than of the
            // story, because the seed's id has its own shape and guessing it
            // here would silently never match. The id does not depend on the
            // localized labels, so the default English seed is enough to test.
            added={chosen.has(seedFromNewsStory(story).id)}
            onAdd={() => onAdd(story)}
          />
        ))}
      </ul>
    </section>
  );
}

export function NewsBoard({
  seeds,
  onSeed,
}: {
  seeds: DiscoverySeed[];
  onSeed: (seed: DiscoverySeed) => void;
}) {
  const { t } = useLocale();
  const [desk, setDesk] = usePersistedState<Desk>(
    "discover.news.desk",
    "all",
    oneOf("all", "general", "business", "technology"),
  );
  const [density, setDensity] = usePersistedState<Density>(
    "discover.news.density",
    "rows",
    oneOf("cards", "rows", "headlines"),
  );
  // One piece of state carrying which desk it describes, rather than a board
  // and an error and a loading flag that can disagree with each other. Switching
  // desks twice quickly used to let the first answer land after the second and
  // overwrite it; a result that does not match the desk on screen is simply not
  // the current result, so the race has nowhere to land.
  const [result, setResult] = useState<{
    desk: Desk;
    board: Board | null;
    error: string | null;
  } | null>(null);
  const [reload, setReload] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  // Built here rather than at module scope so the labels follow the current
  // locale; the values are the API's desk names and never translate.
  const deskOptions = useMemo(
    () => [
      { value: "all" as const, label: t("discover.news.deskAll"), icon: <Newspaper size={13} />, title: t("discover.news.deskAllTitle") },
      { value: "general" as const, label: t("discover.news.deskWorld"), icon: <Globe size={13} />, title: t("discover.news.deskWorldTitle") },
      { value: "business" as const, label: t("discover.news.deskBusiness"), icon: <Building2 size={13} />, title: t("discover.news.deskBusinessTitle") },
      { value: "technology" as const, label: t("discover.news.deskTech"), icon: <Cpu size={13} />, title: t("discover.news.deskTechTitle") },
    ],
    [t],
  );
  const densityOptions = useMemo(
    () => [
      { value: "cards" as const, label: t("discover.news.densityCards"), title: t("discover.news.densityCardsTitle") },
      { value: "rows" as const, label: t("discover.news.densityRows"), title: t("discover.news.densityRowsTitle") },
      { value: "headlines" as const, label: t("discover.news.densityHeadlines"), title: t("discover.news.densityHeadlinesTitle") },
    ],
    [t],
  );

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${apiBaseUrl()}/api/research/news?desk=${desk}&limit=6`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = (await response.json()) as Board & { detail?: string };
        if (!response.ok) {
          throw new Error(payload.detail ?? t("discover.news.readError"));
        }
        setResult({ desk, board: payload, error: null });
      })
      .catch((problem: unknown) => {
        // An abort is this effect being replaced, not a newsroom failing.
        if (problem instanceof DOMException && problem.name === "AbortError") return;
        setResult({
          desk,
          board: null,
          error: problem instanceof Error ? problem.message : t("discover.news.readError"),
        });
      })
      .finally(() => setRefreshing(false));

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desk, reload]);

  const current = result?.desk === desk ? result : null;
  const board = current?.board ?? null;
  const error = current?.error ?? null;
  // Derived rather than stored: a board for another desk is by definition not
  // loaded yet, so the desk buttons need no loading bookkeeping of their own.
  const loading = !current || refreshing;

  const chosen = useMemo(() => new Set(seeds.map((seed) => seed.id)), [seeds]);

  const partial = board && !board.complete && board.outlets_read.length > 0;

  // The seed an "add" stores carries an evidence line; localize it here so what
  // lands in the idea tray reads in the same language as the board it came from.
  const seedLabels: SeedLabels = {
    carried: t("discover.news.seedCarried"),
    only: t("discover.news.seedOnly"),
  };

  return (
    <div className="news-board">
      <div className="news-board-head">
        <h2>
          <Newspaper size={15} aria-hidden="true" />
          {t("discover.news.heading")}
        </h2>
        <div className="news-board-controls">
          <SegmentedControl
            value={desk}
            options={deskOptions}
            onChange={setDesk}
            label={t("discover.news.deskLabel")}
          />
          <SegmentedControl
            value={density}
            options={densityOptions}
            onChange={setDensity}
            label={t("discover.news.densityLabel")}
          />
          <Button
            variant="quiet"
            size="sm"
            disabled={loading}
            onClick={() => {
              setRefreshing(true);
              setReload((count) => count + 1);
            }}
            title={t("discover.news.refreshTitle")}
          >
            <RefreshCw size={13} aria-hidden="true" />
            {loading ? t("discover.news.refreshing") : t("discover.news.refresh")}
          </Button>
        </div>
      </div>

      {error ? (
        <p className="news-note" role="alert">{error}</p>
      ) : null}

      {partial ? (
        // Said out loud because it changes what the numbers mean: "3
        // newsrooms" is a different finding when four of the nine were
        // unreachable.
        <p className="news-note">
          {t("discover.news.partial", {
            read: board.outlets_read.length,
            requested: board.outlets_requested.length,
          })}
        </p>
      ) : null}

      {loading && !board ? (
        <p className="news-empty">{t("discover.news.loading")}</p>
      ) : null}

      {board && !board.covered.length && !board.breaking.length && !loading ? (
        <p className="news-empty">{t("discover.news.empty")}</p>
      ) : null}

      {board ? (
        <>
          <Shelf
            title={t("discover.news.covered")}
            blurb={t("discover.news.coveredBlurb")}
            icon={Newspaper}
            tone="covered"
            stories={board.covered}
            density={density}
            chosen={chosen}
            onAdd={(story) => onSeed(seedFromNewsStory(story, seedLabels))}
          />
          <Shelf
            title={t("discover.news.breaking")}
            blurb={t("discover.news.breakingBlurb")}
            icon={Zap}
            tone="breaking"
            stories={board.breaking}
            density={density}
            chosen={chosen}
            onAdd={(story) => onSeed(seedFromNewsStory(story, seedLabels))}
          />
        </>
      ) : null}
    </div>
  );
}
