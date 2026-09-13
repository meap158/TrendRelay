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
  attribution: string | null;
};

/** A Library row, as the assets endpoint serialises it. */
type AudioRow = MusicChoice & { media_kind: string };

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
};

/** `CC-BY-4.0` as a person writes it, for a Library row that stores the SPDX id. */
function licenceLabel(license: string | null): string {
  if (!license) return "";
  return license === "CC0-1.0" ? "CC0 1.0" : license.replace(/-/g, " ");
}

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
  return row ? { id: row.id, title: row.title, creator: row.creator, license: row.license, attribution: row.attribution } : null;
}

/** The licence and, when one is owed, the credit - on a row or on the choice. */
function Terms({ license, attribution }: { license: string | null; attribution: string | null }) {
  const t = useT();
  if (!license) return null;
  return (
    <small className="music-terms">
      <Badge tone={attribution ? "info" : "good"} title={attribution ?? t("music.noCredit")}>
        {licenceLabel(license)}
      </Badge>
      <span>{attribution ? t("music.credit") : t("music.noCredit")}</span>
    </small>
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
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"library" | "find">("library");

  // The Library's audio, through the shared loop: it owns the debounce, the
  // paging and the stale-response guard. Pinned to audio and kept to audio,
  // so clearing the search can never widen the list to every video.
  const library = useLibraryAssets<AudioRow>({
    workspaceId,
    apiFetch,
    baseline: { mediaKind: "audio" },
    keep: (asset) => asset.media_kind === "audio",
    enabled: open && tab === "library",
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
          license: track.license, attribution: track.credit,
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
      license: row.license, attribution: row.attribution,
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
              <Terms license={value.license} attribution={value.attribution} />
            </>
          ) : (
            <span className="music-picker-empty">{emptyLabel || t("music.none")}</span>
          )}
        </span>
        <span className="music-picker-actions">
          <Button variant="quiet" size="sm" onClick={() => setOpen((current) => !current)}>
            {open ? t("common.close") : value ? t("music.change") : t("music.choose")}
          </Button>
          {value && (
            <Button variant="quiet" size="sm" onClick={() => onChange(null)}>{t("music.clear")}</Button>
          )}
        </span>
      </div>
      {hint && !open && <small className="music-picker-hint">{hint}</small>}

      {open && (
        <div className="music-picker-panel">
          <div className="library-category-tabs" role="tablist" aria-label={t("music.title")}>
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

          {tab === "library" ? (
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
                    <li key={row.id} className={value?.id === row.id ? "selected" : ""}>
                      <button type="button" className="music-picker-row" onClick={() => choose(row)}>
                        <span className="music-picker-name">
                          <strong>{row.title}</strong>
                          {row.creator && <small>{row.creator}</small>}
                        </span>
                        <Terms license={row.license} attribution={row.attribution} />
                      </button>
                    </li>
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
                    <li key={track.id}>
                      <div className="music-picker-row music-picker-found">
                        <span className="music-picker-name">
                          <strong>{track.title}</strong>
                          <small>
                            {[track.creator, track.source, seconds(track.duration_ms)]
                              .filter(Boolean).join(" · ")}
                          </small>
                          <small className="music-terms">
                            <Badge tone={track.credit_required ? "info" : "good"} title={track.credit ?? t("music.noCredit")}>
                              {track.license_label}
                            </Badge>
                            <span>{track.credit ?? t("music.noCredit")}</span>
                          </small>
                        </span>
                        {/* Loaded only when played: the file is the source's,
                            and nothing is fetched until somebody presses play. */}
                        <audio
                          controls
                          preload="none"
                          src={track.preview_url}
                          aria-label={t("music.preview", { title: track.title })}
                        />
                        <Button
                          variant="secondary"
                          size="sm"
                          busy={adding[track.id] === "busy"}
                          disabled={adding[track.id] === "added"}
                          onClick={() => void add(track)}
                        >{adding[track.id] === "added" ? t("music.addedShort") : t("music.add")}</Button>
                      </div>
                    </li>
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
