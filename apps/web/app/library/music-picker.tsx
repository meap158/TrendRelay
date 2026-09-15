"use client";

/**
 * Choosing the music for a video: a track already in the Library, or one
 * found and added while editing.
 *
 * Two lists behind one control. "In the Library" is the workspace's own audio,
 * read through the shared library loop every other picker uses (ADR 0025).
 * "Find more" searches Openverse for tracks that may be published - CC0 and
 * CC BY only, the server refuses the rest - and adds one to the Library on
 * request. Adding is a download and an import, so it is a button per track and
 * never something a search does on its own; the track then appears under "In
 * the Library" once the ingest has filed it, and is chosen from there.
 *
 * The licence and the credit are shown on every row because they are the
 * point: a CC BY track owes a credit line, and the server puts it on the
 * caption of every post that publishes the video. Somebody choosing music
 * should see that before they choose.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useLibraryAssets } from "../../lib/use-library-assets";
import { contextKey as keyOf, hasSomethingToSuggest } from "../../lib/music-context";
import type { MusicContext } from "../../lib/music-context";
import { reasonText } from "../../lib/music-reasons";
import type { MusicReason } from "../../lib/music-reasons";
import { useT } from "../i18n-provider";
import { Button } from "../ui/button";
import { Badge } from "../ui/primitives";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

/** What a render needs to know about the chosen track, and what the row shows. */
export type MusicChoice = {
  id: string;
  title: string;
  creator: string | null;
  license: string | null;
  /** The licence as a person writes it, worded by the API. */
  license_label: string | null;
  attribution: string | null;
};

/** A Library row, as the assets endpoint serialises it. Suggested rows carry
    a reason; the ones a search returned have nothing to explain. */
type AudioRow = MusicChoice & { media_kind: string; reason?: MusicReason };

/** The offer for a piece: the searches run and why, the Library's own
    matches, and the tracks those searches found, each with its reason. */
type Suggestions = {
  queries: { q: string; reason: MusicReason }[];
  library: AudioRow[];
  tracks: FoundTrack[];
  unavailable: boolean;
};

/** A track Openverse offers, as the music search returns it. */
type FoundTrack = {
  id: string;
  title: string;
  creator: string | null;
  license: string;
  license_label: string;
  license_url: string | null;
  preview_url: string;
  duration_ms: number | null;
  source: string | null;
  credit_required: boolean;
  credit: string | null;
  reason?: MusicReason;
};

/** `CC-BY-4.0` as a person writes it, for a Library row that stores the SPDX id. */
function seconds(ms: number | null): string {
  if (!ms) return "";
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** The chosen track by id, for a reopened draft. Null when it has left the Library. */
export async function loadMusicChoice(
  apiFetch: Fetcher, workspaceId: string, assetId: string | null | undefined,
): Promise<MusicChoice | null> {
  if (!assetId) return null;
  const response = await apiFetch(
    `/api/workspaces/${workspaceId}/media/library/assets?asset_ids=${encodeURIComponent(assetId)}`,
  );
  if (!response.ok) return null;
  const body = (await response.json().catch(() => ({}))) as { assets?: AudioRow[] };
  const row = (body.assets ?? []).find((asset) => asset.id === assetId && asset.media_kind === "audio");
  return row
    ? {
      id: row.id, title: row.title, creator: row.creator,
      license: row.license, license_label: row.license_label, attribution: row.attribution,
    }
    : null;
}

/** The licence and, when one is owed, the credit - on a row or on the choice. */
function Terms({ label, attribution }: { label: string | null; attribution: string | null }) {
  const t = useT();
  if (!label) return null;
  return (
    <small className="music-terms">
      <Badge tone={attribution ? "info" : "good"} title={attribution ?? t("music.noCredit")}>
        {label}
      </Badge>
      <span>{attribution ? t("music.credit") : t("music.noCredit")}</span>
    </small>
  );
}

/**
 * One track already in the Library, to be chosen.
 *
 * The same row wherever it is listed - searched for by hand, or suggested -
 * so the two lists cannot drift into describing a track two ways. `note`
 * marks it as already yours in a list that mixes both.
 */
function LibraryRow({
  row, selected, note, onChoose,
}: {
  row: AudioRow;
  selected: boolean;
  note?: string;
  onChoose: () => void;
}) {
  const t = useT();
  const why = reasonText(t, row.reason);
  const line = [row.creator, note].filter(Boolean).join(" · ");
  return (
    <li className={selected ? "selected" : ""}>
      <button type="button" className="music-picker-row" onClick={onChoose}>
        <span className="music-picker-name">
          <strong>{row.title}</strong>
          {line && <small>{line}</small>}
          {why && <small>{why}</small>}
        </span>
        <Terms label={row.license_label} attribution={row.attribution} />
      </button>
    </li>
  );
}

/** One track that may be added: its terms, a preview, and the button that
    brings it in. Shared by the search results and the suggestions. */
function FoundRow({
  track, state, onAdd,
}: {
  track: FoundTrack;
  state: "busy" | "added" | undefined;
  onAdd: () => void;
}) {
  const t = useT();
  const why = reasonText(t, track.reason);
  return (
    <li>
      <div className="music-picker-row music-picker-found">
        <span className="music-picker-name">
          <strong>{track.title}</strong>
          <small>
            {[track.creator, track.source, seconds(track.duration_ms)]
              .filter(Boolean).join(" · ")}
          </small>
          {why && <small>{why}</small>}
          <small className="music-terms">
            <Badge
              tone={track.credit_required ? "info" : "good"}
              title={track.credit ?? t("music.noCredit")}
            >{track.license_label}</Badge>
            <span>{track.credit ?? t("music.noCredit")}</span>
          </small>
        </span>
        {/* Loaded only when played: the file is the source's, and nothing is
            fetched until somebody presses play. */}
        <audio
          controls
          preload="none"
          src={track.preview_url}
          aria-label={t("music.preview", { title: track.title })}
        />
        <Button
          variant="secondary"
          size="sm"
          busy={state === "busy"}
          disabled={state === "added"}
          onClick={onAdd}
        >{state === "added" ? t("music.addedShort") : t("music.add")}</Button>
      </div>
    </li>
  );
}

export function MusicPicker({
  workspaceId,
  apiFetch,
  value,
  onChange,
  onQueued,
  onError,
  emptyLabel,
  hint,
  context,
}: {
  workspaceId: string;
  apiFetch: Fetcher;
  value: MusicChoice | null;
  onChange: (choice: MusicChoice | null) => void;
  onQueued: (message: string) => void;
  onError: (message: string) => void;
  /** What no choice reads as: the template's own track, or no music at all. */
  emptyLabel?: string;
  /** What choosing does here - cuts on the beat, or plays under the voice. */
  hint?: string;
  /** What the music is for. With this the picker opens on suggestions. */
  context?: MusicContext;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  // The piece as a string, so a context object rebuilt on every render of
  // the dialog does not re-ask; only a change in what it says does. The key
  // is the request itself, so the effect below parses it back rather than
  // closing over an object that is new every time.
  const contextKey = keyOf(context);
  const hasContext = hasSomethingToSuggest(context);
  const [tab, setTab] = useState<"suggested" | "library" | "find">(
    hasContext ? "suggested" : "library",
  );

  const [suggested, setSuggested] = useState<Suggestions | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const suggestionRun = useRef(0);
  // Asked when the panel is open and the piece has something to say; asked
  // again, after a pause, when the script or the clips change under it.
  useEffect(() => {
    if (!open || !hasContext) return;
    const mine = ++suggestionRun.current;
    // Slow on purpose. Each ask is up to three searches of a public API, and
    // the thing that changes most here is a script somebody is still writing -
    // at a search box's reflexes that is a few calls a sentence, for a list
    // whose answer barely moves between one sentence and the next.
    const wait = window.setTimeout(() => {
      setSuggesting(true);
      void apiFetch(`/api/workspaces/${workspaceId}/media/library/music/suggestions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // The key is the request, so it is sent as it stands.
        body: contextKey,
      })
        .then((response) => response.json().then((body) => ({ ok: response.ok, body })))
        .then(({ ok, body }) => {
          if (mine !== suggestionRun.current) return;
          setSuggested(ok ? (body as Suggestions) : null);
        })
        .catch(() => { if (mine === suggestionRun.current) setSuggested(null); })
        .finally(() => { if (mine === suggestionRun.current) setSuggesting(false); });
    }, 1200);
    return () => window.clearTimeout(wait);
  }, [open, hasContext, contextKey, apiFetch, workspaceId]);

  // The Library's audio, through the shared loop: it owns the debounce, the
  // paging and the stale-response guard. Pinned to audio and kept to audio,
  // so clearing the search can never widen the list to every video.
  //
  // Read whenever the panel is open rather than only on its own tab: the tab
  // shows how many tracks there are, and a picker that opened on Suggested
  // was offering to show a Library it had not counted.
  const library = useLibraryAssets<AudioRow>({
    workspaceId,
    apiFetch,
    baseline: { mediaKind: "audio" },
    keep: (asset) => asset.media_kind === "audio",
    enabled: open,
  });

  const [query, setQuery] = useState("");
  const [found, setFound] = useState<FoundTrack[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState<Record<string, "busy" | "added">>({});
  const refreshes = useRef<number[]>([]);
  useEffect(() => () => { refreshes.current.forEach((handle) => window.clearTimeout(handle)); }, []);

  const search = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/music/search?q=${encodeURIComponent(q)}`,
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail ?? t("music.failed"));
      setFound((body.tracks ?? []) as FoundTrack[]);
    } catch (reason) {
      setFound([]);
      onError(reason instanceof Error ? reason.message : t("music.failed"));
    } finally {
      setSearching(false);
    }
  }, [apiFetch, workspaceId, query, onError, t]);

  /** Add one found track to the Library. Its own button because it is its own
      act - a download from an external service - and the click is the
      confirmation the route asks for. The id and nothing else travels: title,
      creator and licence are read back from the source, so nothing here can
      relabel a track. */
  const add = useCallback(async (track: FoundTrack) => {
    if (adding[track.id]) return;
    setAdding((current) => ({ ...current, [track.id]: "busy" }));
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/music/imports`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ track_id: track.id, confirm_external_action: true }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail ?? t("music.failed"));
      setAdding((current) => ({ ...current, [track.id]: "added" }));
      const job = body.job ?? {};
      if (job.duplicate && job.asset_id) {
        // Already filed: it can be chosen right away.
        onChange({
          id: job.asset_id, title: track.title, creator: track.creator,
          license: track.license, license_label: track.license_label,
          attribution: track.credit,
        });
        setOpen(false);
        return;
      }
      onQueued(t("music.added", { title: track.title }));
      // The ingest is a queue. Re-read the Library a couple of times as it
      // lands, so the track is there to pick without closing and reopening.
      for (const delay of [4_000, 10_000]) {
        refreshes.current.push(window.setTimeout(() => library.reload(), delay));
      }
    } catch (reason) {
      setAdding((current) => {
        const next = { ...current };
        delete next[track.id];
        return next;
      });
      onError(reason instanceof Error ? reason.message : t("music.failed"));
    }
  }, [adding, apiFetch, workspaceId, onChange, onQueued, onError, library, t]);

  const choose = (row: AudioRow) => {
    onChange({
      id: row.id, title: row.title, creator: row.creator,
      license: row.license, license_label: row.license_label, attribution: row.attribution,
    });
    setOpen(false);
  };

  return (
    <div className="music-picker">
      <div className="music-picker-current">
        <span className="music-picker-choice">
          {value ? (
            <>
              <strong>{value.title}</strong>
              {value.creator && <span className="music-picker-by"> · {value.creator}</span>}
              <Terms label={value.license_label} attribution={value.attribution} />
            </>
          ) : (
            <span className="music-picker-empty">{emptyLabel || t("music.none")}</span>
          )}
        </span>
        <span className="music-picker-actions">
          {/* Which tab to land on is decided on opening, not on mounting: the
              Storytelling picker is mounted with an empty script and earns its
              suggestions while somebody writes one. */}
          <Button
            variant="quiet"
            size="sm"
            onClick={() => {
              const next = !open;
              setOpen(next);
              if (next) setTab(hasContext ? "suggested" : "library");
            }}
          >{open ? t("common.close") : value ? t("music.change") : t("music.choose")}</Button>
          {value && (
            <Button variant="quiet" size="sm" onClick={() => onChange(null)}>{t("music.clear")}</Button>
          )}
        </span>
      </div>
      {hint && !open && <small className="music-picker-hint">{hint}</small>}

      {open && (
        <div className="music-picker-panel">
          <div className="library-category-tabs" role="tablist" aria-label={t("music.title")}>
            {hasContext && (
              <button
                type="button"
                role="tab"
                className={tab === "suggested" ? "selected" : ""}
                aria-selected={tab === "suggested"}
                onClick={() => setTab("suggested")}
              >{t("music.suggested")}</button>
            )}
            <button
              type="button"
              role="tab"
              className={tab === "library" ? "selected" : ""}
              aria-selected={tab === "library"}
              onClick={() => setTab("library")}
            >{t("music.inLibrary")} {library.total > 0 && <span>{library.total.toLocaleString()}</span>}</button>
            <button
              type="button"
              role="tab"
              className={tab === "find" ? "selected" : ""}
              aria-selected={tab === "find"}
              onClick={() => setTab("find")}
            >{t("music.findMore")}</button>
          </div>

          {tab === "suggested" ? (
            <>
              <p className="music-picker-note">{t("music.suggestedHint")}</p>
              {suggesting && !suggested && (
                <p className="music-picker-note">{t("common.loading")}</p>
              )}
              {suggested && suggested.library.length > 0 && (
                <ul className="music-picker-list">
                  {suggested.library.map((row) => (
                    <LibraryRow
                      key={row.id}
                      row={row}
                      selected={value?.id === row.id}
                      note={t("music.inLibrary")}
                      onChoose={() => choose(row)}
                    />
                  ))}
                </ul>
              )}
              {suggested && suggested.tracks.length > 0 && (
                <ul className="music-picker-list">
                  {suggested.tracks.map((track) => (
                    <FoundRow
                      key={track.id}
                      track={track}
                      state={adding[track.id]}
                      onAdd={() => void add(track)}
                    />
                  ))}
                </ul>
              )}
              {suggested && !suggesting && suggested.library.length === 0 && suggested.tracks.length === 0 && (
                <p className="music-picker-note">
                  {suggested.unavailable ? t("music.failed") : t("music.nothingToSuggest")}
                </p>
              )}
              {/* The searches that were run are the operator's to widen: the
                  first one lands in the search box, ready to be changed. */}
              <Button
                variant="quiet"
                size="sm"
                onClick={() => {
                  if (!query.trim() && suggested?.queries[0]) setQuery(suggested.queries[0].q);
                  setTab("find");
                }}
              >{t("music.searchForMore")}</Button>
            </>
          ) : tab === "library" ? (
            <>
              <input
                className="music-picker-search"
                type="search"
                value={library.filters.query ?? ""}
                placeholder={t("music.searchLibrary")}
                aria-label={t("music.searchLibrary")}
                onChange={(event) => library.setFilters({ ...library.filters, query: event.target.value })}
              />
              {library.failure ? (
                <p className="music-picker-note">{library.failure}</p>
              ) : library.assets.length === 0 ? (
                <p className="music-picker-note">
                  {library.loading ? t("common.loading") : t("music.noLibraryAudio")}
                </p>
              ) : (
                <ul className="music-picker-list">
                  {library.assets.map((row) => (
                    <LibraryRow
                      key={row.id}
                      row={row}
                      selected={value?.id === row.id}
                      onChoose={() => choose(row)}
                    />
                  ))}
                </ul>
              )}
              {library.canLoadMore && (
                <Button
                  variant="quiet"
                  size="sm"
                  busy={library.loading === "more"}
                  onClick={() => void library.loadMore()}
                >{t("music.loadMore")}</Button>
              )}
            </>
          ) : (
            <>
              <form
                className="music-picker-find"
                onSubmit={(event) => { event.preventDefault(); void search(); }}
              >
                <input
                  className="music-picker-search"
                  type="search"
                  value={query}
                  placeholder={t("music.searchOpenverse")}
                  aria-label={t("music.searchOpenverse")}
                  onChange={(event) => setQuery(event.target.value)}
                />
                <Button type="submit" variant="secondary" size="sm" busy={searching} disabled={!query.trim()}>
                  {t("common.search")}
                </Button>
              </form>
              <p className="music-picker-note">{t("music.terms")}</p>
              {found && found.length === 0 && !searching && (
                <p className="music-picker-note">{t("music.noResults")}</p>
              )}
              {found && found.length > 0 && (
                <ul className="music-picker-list">
                  {found.map((track) => (
                    <FoundRow
                      key={track.id}
                      track={track}
                      state={adding[track.id]}
                      onAdd={() => void add(track)}
                    />
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
