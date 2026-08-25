"use client";

import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { FolderInput, Upload } from "lucide-react";

import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { Badge } from "../ui/primitives";
import { FilterChipStrip } from "../ui/filter-strip";
import { ObjectViewer } from "./object-viewer";
import { ParamControl } from "./effect-params";
import type { EffectDefinition, EffectParam, ParamOption } from "./effect-params";
import { optionGroup, optionLabel } from "../../lib/i18n/effects";
import { useT } from "../i18n-provider";
import { useLibraryAssets } from "../../lib/use-library-assets";
import { AssetFilters } from "../ui/asset-filters";
import { AssetThumbnail, IMAGE_PICKER_BASE } from "../publish/composer";
import type { LibraryAsset } from "../publish/composer";

/**
 * Choosing between picture-shaped options, and seeing the choice on a real
 * frame before paying for a render.
 *
 * A panel rather than a dialog of its own. Opened as a second modal on top of
 * the editor it was both nested inside another Radix layer and confined to the
 * standard dialog width, where the grid of objects came out as a scrollbar with
 * two thumbnails behind it. It takes over the editor's panel instead: one
 * modal, the full width, and a way back.
 *
 * A dropdown was the obvious thing and the wrong one. These options are
 * pictures — overlay objects, portraits to swap in — and nobody picks one by
 * reading its name; a dozen of them in a select is a list of words describing
 * images the reader cannot see. So the choice is a gallery, and the thumbnails
 * are the real thing at a smaller size.
 *
 * Nothing here knows what it is showing. The option says where its thumbnail
 * comes from and the effect says how to render a frame, so the same dialog
 * serves a sticker catalogue and a folder of faces, and will serve whatever is
 * declared next without being edited.
 *
 * The gallery still cannot answer the question that actually matters, which is
 * whether *this* choice sits right on *this* face. Only a frame answers that,
 * so one sits beside the grid and re-renders as the choice and the settings
 * change. It costs a decode; finding the same thing out from a render costs
 * minutes.
 */

export type PickerValues = Record<string, unknown>;

/** One object the API thinks suits the clip on screen, and the words it
 *  matched on - shown, because a suggestion whose reason is invisible is a
 *  suggestion nobody trusts a second time. */
type Suggestion = {
  value: string;
  label: string;
  score: number;
  matched: string[];
  /** Whether it turns with the head. A chip is the same choice as a tile and
   *  has to say the same things about it. */
  dimensional?: boolean;
};

/**
 * A thumbnail that this repository ships is the same bytes every time, and the
 * grid asks for a dozen at once each time it opens. Those are kept for the life
 * of the page rather than re-fetched per mount, which is what stopped the
 * gallery flashing.
 *
 * Anything the operator supplied is deliberately *not* kept. It is a file on
 * their disk and they may well be editing it — caching one for the session
 * means saving a change and being shown the old version until a reload, with
 * nothing on screen to explain why.
 */
const thumbnailCache = new Map<string, string>();

function useThumbnail(
  base: string,
  option: ParamOption,
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>,
): string {
  const path = option.preview ? `${base}/${option.preview}` : "";
  const durable = !option.custom;
  // For a cached thumbnail the map is the source of truth and is read during
  // render, so it comes back on the first render rather than after a state
  // round trip. `fresh` carries the ones that are not cached.
  const [, redraw] = useReducer((count: number) => count + 1, 0);
  const [fresh, setFresh] = useState("");

  useEffect(() => {
    if (!path) return;
    if (durable && thumbnailCache.has(path)) return;
    let active = true;
    let created = "";
    apiFetch(path)
      .then((response) => (response.ok ? response.blob() : Promise.reject(new Error("none"))))
      .then((blob) => {
        created = URL.createObjectURL(blob);
        if (!active) {
          URL.revokeObjectURL(created);
          return;
        }
        if (durable) {
          thumbnailCache.set(path, created);
          redraw();
        } else {
          setFresh(created);
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
      // Only the uncached ones are cleaned up; a cached URL outlives the
      // component on purpose.
      if (!durable && created) URL.revokeObjectURL(created);
    };
  }, [apiFetch, durable, path]);

  if (!path) return "";
  return (durable ? thumbnailCache.get(path) : fresh) ?? "";
}

function Thumbnail({
  base,
  option,
  apiFetch,
}: {
  base: string;
  option: ParamOption;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  const source = useThumbnail(base, option, apiFetch);
  if (!source) return <span className="overlay-sprite-empty" aria-hidden="true" />;
  // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
  return <img className="overlay-sprite" src={source} alt="" />;
}

export function GalleryPanel({
  open,
  workspaceId,
  assetPath,
  effect,
  param,
  values,
  onChange,
  onCatalogueChanged,
  apiFetch,
  canEdit,
}: {
  open: boolean;
  workspaceId: string;
  assetPath: string;
  effect: EffectDefinition;
  param: EffectParam;
  values: PickerValues;
  onChange: (next: PickerValues) => void;
  /** The folder gained an option, so the effect declaration needs re-reading. */
  onCatalogueChanged?: (chosen: string) => void;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  canEdit: boolean;
}) {
  const t = useT();
  const base = `/api/workspaces/${workspaceId}/media/library`;
  const [frame, setFrame] = useState("");
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState("");
  const [note, setNote] = useState("");
  /** Null until a frame has been fetched, so the first load can auto-pick. */
  const [position, setPosition] = useState<number | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  /** Which tile owns the group's single tab stop. */
  const [focused, setFocused] = useState("");
  const objectUrl = useRef("");
  /** Counts frame requests, so a slow one cannot overwrite a newer one. */
  const requests = useRef(0);

  const chosen = String(values[param.id] ?? "");
  const current = param.options.find((option) => option.value === chosen);
  /** Everything except the choice itself, judged while looking at the frame. */
  const settings = effect.params.filter((item) => item.id !== param.id);

  const grouped = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const matching = param.options.filter((option) => {
      if (category && (option.group ?? "") !== category) return false;
      if (!needle) return true;
      return [
        optionLabel(t, effect.id, option.value, option.label),
        optionGroup(t, option.group_id, option.group ?? ""),
        option.note ?? "",
      ].some((value) => value.toLowerCase().includes(needle));
    });
    // Nullable, not merely optional: the API sends an explicit null for a
    // group an operator named, which is different from a group not saying.
    const sections: { group: string; groupId?: string | null; items: ParamOption[] }[] = [];
    for (const option of matching) {
      const group = option.group ?? "";
      const last = sections[sections.length - 1];
      // The API already orders these, so a run of the same group is a section
      // and no re-sorting is needed here.
      if (last && last.group === group) last.items.push(option);
      else sections.push({ group, groupId: option.group_id, items: [option] });
    }
    return sections;
  }, [category, effect.id, param.options, search, t]);

  const categories = useMemo(() => {
    const found = new Map<string, { label: string; count: number }>();
    for (const option of param.options) {
      const key = option.group ?? "";
      if (!key) continue;
      const currentGroup = found.get(key);
      found.set(key, {
        label: optionGroup(t, option.group_id, option.group ?? ""),
        count: (currentGroup?.count ?? 0) + 1,
      });
    }
    return [...found.entries()];
  }, [param.options, t]);

  const load = useCallback(
    async (next: PickerValues, at: number | null) => {
      if (!assetPath || !next[param.id]) return;
      /**
       * Frames take a decode and clicking through a gallery is faster than
       * that, so requests overlap and they do not come back in order. Without
       * a ticket the slow one lands last and the preview shows something other
       * than what is selected — which is the one thing this dialog exists to
       * get right.
       */
      const ticket = requests.current + 1;
      requests.current = ticket;
      setLoading(true);
      setFailure("");
      try {
        const response = await apiFetch(`${base}/effects/frame`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            source_path: assetPath,
            effect: effect.id,
            values: next,
            // Omitted on the first look so the clip is searched for a frame
            // that has something on it; sent once the operator starts choosing.
            at,
          }),
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail ?? "The frame could not be rendered.");
        }
        const blob = await response.blob();
        if (ticket !== requests.current) return;
        setNote(decodeURIComponent(response.headers.get("X-Preview-Note") ?? ""));
        const landed = Number(response.headers.get("X-Frame-Position") ?? "");
        if (Number.isFinite(landed)) setPosition(landed);
        const clip = Number(response.headers.get("X-Clip-Duration") ?? "");
        setDuration(Number.isFinite(clip) && clip > 0 ? clip : null);
        if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
        objectUrl.current = URL.createObjectURL(blob);
        setFrame(objectUrl.current);
      } catch (reason) {
        // A superseded request that failed says nothing about the one that
        // replaced it, so it does not get to put an error on the screen.
        if (ticket !== requests.current) return;
        setFailure(reason instanceof Error ? reason.message : "The frame could not be rendered.");
      } finally {
        // Still loading, as far as the user is concerned, until the newest
        // request finishes — so only that one may clear the spinner.
        if (ticket === requests.current) setLoading(false);
      }
    },
    [apiFetch, assetPath, base, effect.id, param.id],
  );

  // Declared alongside the options rather than fetched: a choice fed by a
  // folder says where that folder is, and a choice that is not simply has none.
  const folder = param.folder;
  const [browsing, setBrowsing] = useState(false);
  const [suggested, setSuggested] = useState<Suggestion[]>([]);
  const [adding, setAdding] = useState(false);
  const [addFailure, setAddFailure] = useState("");

  /**
   * The library's pictures, through the loop every other picker reads with.
   *
   * This used to be a hand-rolled fetch of `assets?media_kind=image&limit=100`
   * with no paging, no filter and no thumbnails - a copy of the shared loop
   * born already behind it, which is the failure ADR 0025 is about. The hook
   * owns the debounce, the paging, the stale-response guard and the counts.
   */
  const libraryPictures = useLibraryAssets<LibraryAsset>({
    workspaceId,
    apiFetch,
    baseline: IMAGE_PICKER_BASE,
    enabled: open && Boolean(folder?.import_from_library),
    keep: (asset) => asset.media_kind === "image",
  });



  /**
   * Copy a library picture into the folder this choice is fed from.
   *
   * The new option is not merged in locally: the effect declaration is the one
   * source of the list, so the editor reloads it and the gallery redraws from
   * what the API now says. A locally patched list would be a second version of
   * the truth, and the one that validation does not read.
   */
  async function addFromLibrary(assetId: string) {
    if (!folder?.import_from_library || !assetId) return;
    setAdding(true);
    setAddFailure("");
    try {
      const response = await apiFetch(`${base}/${folder.import_from_library}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ asset_id: assetId }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail ?? "That picture could not be added.");
      setBrowsing(false);
      onCatalogueChanged?.(String(body.face?.value ?? body.object?.value ?? ""));
    } catch (reason) {
      setAddFailure(reason instanceof Error ? reason.message : "That picture could not be added.");
    } finally {
      setAdding(false);
    }
  }

  /**
   * What the API thinks suits this clip, and why.
   *
   * A shortlist above forty tiles rather than a reordering of them: the
   * gallery's own order is a privacy decision - the things that cover a face
   * lead - and quietly resorting it on a guess would move that.
   *
   * Only for a choice fed by a folder, which is how this component tells an
   * overlay catalogue from a set of face-swap portraits without being told
   * which one it is showing.
   */
  useEffect(() => {
    if (!open || !folder || !assetPath) return undefined;
    const controller = new AbortController();
    apiFetch(
      `${base}/face-overlay/objects/suggest`
      + `?source_path=${encodeURIComponent(assetPath)}&limit=5`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error("no suggestions");
        const body = (await response.json()) as { suggestions?: Suggestion[] };
        setSuggested(body.suggestions ?? []);
      })
      .catch(() => {
        // A choice with no suggest endpoint behind it simply has no shortlist.
        // Nothing is reported: the absence is the message.
      });
    return () => controller.abort();
  }, [apiFetch, base, folder, assetPath, open]);

  /**
   * Add a picture from the operator's own machine.
   *
   * The other half of the same answer. A sticker somebody has just exported is
   * on their desktop rather than in the media library, and naming a folder was
   * telling them to leave this dialog to do the thing this dialog is for.
   *
   * Sent as multipart rather than read here and posted as base64: the file is
   * handed straight to the request, so nothing has to hold a megabyte of it in
   * a string first.
   */
  async function addFromDisk(file: File | undefined) {
    if (!folder?.upload || !file) return;
    setAdding(true);
    setAddFailure("");
    try {
      const carrying = new FormData();
      carrying.append("file", file);
      const response = await apiFetch(`${base}/${folder.upload}`, {
        method: "POST",
        // Deliberately no content-type: the browser sets it, and setting it by
        // hand loses the multipart boundary the server needs to read the parts.
        body: carrying,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail ?? "That picture could not be added.");
      onCatalogueChanged?.(String(body.object?.value ?? ""));
    } catch (reason) {
      setAddFailure(reason instanceof Error ? reason.message : "That picture could not be added.");
    } finally {
      setAdding(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => {
      setPosition(null);
      void load(values, null);
    });
    // Opening renders one frame. Everything after that is driven by the change
    // handlers, which re-render on release rather than on every pixel of a drag.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, assetPath, effect.id]);

  useEffect(
    () => () => {
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    },
    [],
  );

  /** Change a setting, tell the editor, and show what it did. */
  function adjust(patch: PickerValues, redraw = true) {
    const next = { ...values, ...patch };
    onChange(next);
    if (redraw) void load(next, position);
  }

  /**
   * Arrow keys move through the gallery and select as they go.
   *
   * That is how a radio group behaves, and it is the right behaviour here for
   * a second reason: selecting on arrival re-renders the preview, so arrowing
   * along the row is how somebody tries options against their own footage.
   */
  function onGalleryKey(event: ReactKeyboardEvent<HTMLDivElement>) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    const flat = grouped.flatMap((section) => section.items);
    if (!step || flat.length < 2) return;
    event.preventDefault();
    const from = flat.findIndex((option) => option.value === (focused || chosen));
    // Wraps, so the end of the last section leads back to the first option
    // rather than into nothing.
    const next = flat[(Math.max(0, from) + step + flat.length) % flat.length];
    setFocused(next.value);
    adjust({ [param.id]: next.value });
    event.currentTarget
      .querySelector<HTMLButtonElement>(`[data-choice="${CSS.escape(next.value)}"]`)
      ?.focus();
  }

  /**
   * A choice that claims to hide a face, turned down until it does not.
   *
   * Driven by the option's own claim and by a setting named `opacity`, so it is
   * about what was declared rather than about which effect this happens to be.
   */
  const opacity = values.opacity;
  const faded =
    Boolean(current?.occludes) && typeof opacity === "number" && opacity < 0.95;

  return (
    <div className="overlay-picker">
        <div className="overlay-preview">
          <div className="overlay-frame">
            {frame ? (
              // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
              <img alt="Preview frame with the choice applied" src={frame} />
            ) : (
              <p>{loading ? "Rendering a frame…" : failure || "No frame yet."}</p>
            )}
            {loading && frame && (
              <span className="overlay-frame-busy">{t("blurSettings.rendering")}</span>
            )}
          </div>

          <label className="overlay-scrub">
            <span>
              {t("overlayPicker.frame")}
              <b>
                {position === null
                  ? "finding a frame to show"
                  : duration
                    ? `${(position * duration).toFixed(1)}s of ${duration.toFixed(1)}s`
                    : `${Math.round(position * 100)}% through the clip`}
              </b>
            </span>
            <input
              type="range"
              min={0}
              max={1000}
              step={5}
              value={Math.round((position ?? 0) * 1000)}
              disabled={loading && position === null}
              onChange={(event) => setPosition(Number(event.target.value) / 1000)}
              onPointerUp={() => void load(values, position)}
              onKeyUp={() => void load(values, position)}
            />
          </label>

          {/* Written by the effect, because what makes a frame look wrong is
              specific to it and so is the setting that would fix it. */}
          <p className="overlay-frame-note">{failure || note}</p>

          {/* The settings sit with the picture they change, not under the
              gallery. Sharing a column with the grid is what squeezed the grid
              into a scrollbar with two thumbnails behind it. */}
          <div className="overlay-adjust">
            {/* Only for an object that has a back as well as a front. The
                question it answers - which way should this face - is one a
                sticker cannot be asked, and a viewport beside a flat object
                would be a control with nothing on the other end of it. */}
            {current?.dimensional && (
              <ObjectViewer
                base={base}
                overlayId={String(chosen)}
                turn={Number(values.turn ?? 0)}
                tilt={Number(values.tilt ?? 0)}
                disabled={!canEdit}
                apiFetch={apiFetch}
                // Mid-drag: the angle moves, the frame is left alone. A render
                // per degree would queue a dozen decodes to show one answer.
                onTurn={({ turn, tilt }) => adjust({ turn, tilt }, false)}
                onCommit={({ turn, tilt }) => adjust({ turn, tilt })}
              />
            )}
            {current?.note && <p className="overlay-note">{current.note}</p>}
            {faded && (
              <p className="overlay-warning" role="alert">
                {current?.label} is faded, so the face shows through it. Put the
                solidity back to full to actually cover the face.
              </p>
            )}

            {/* The effect's own remaining settings, generated from what it
                declares — so this dialog gains a control when an effect gains
                one, and holds nothing about any effect by name. */}
            {settings.map((item) => (
              <ParamControl
                key={item.id}
                param={item}
                effectId={effect.id}
                value={values[item.id] ?? item.default}
                disabled={!canEdit}
                onChange={(next) => adjust({ [item.id]: next }, false)}
                onCommit={(next) => void load({ ...values, [item.id]: next }, position)}
              />
            ))}
          </div>
        </div>

        <div className="overlay-choices">
          <div className="overlay-discovery">
            <label className="overlay-search">
              <span className="sr-only">{t("overlayPicker.search")}</span>
              <input
                type="search"
                value={search}
                placeholder={t("overlayPicker.search")}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            {/* The shared strip, not a row of its own: it already drags with
                the mouse, pans natively under a finger, and fades whichever
                end still holds chips. The copy that used to live here scrolled
                and showed no scrollbar, so on a desktop with no horizontal
                wheel the categories past the fold could not be reached at all. */}
            {categories.length > 1 && (
              <FilterChipStrip
                chips={[
                  { key: "", label: t("common.all"), count: param.options.length },
                  ...categories.map(([key, item]) => ({
                    key, label: item.label, count: item.count,
                  })),
                ]}
                selected={category}
                onSelect={setCategory}
                ariaLabel={t("overlayPicker.categories")}
                className="overlay-categories"
                dense
              />
            )}
          </div>

          {/* Above the gallery rather than reordering it. The gallery's own
              order is a decision - the objects that cover a face lead - and
              quietly resorting forty tiles on a guess would move that without
              anybody asking for it. A shortlist is additive: worth a glance,
              costless to ignore. */}
          {suggested.length > 0 && (
            <div className="overlay-suggested">
              <h4>{t("overlayPicker.suggested")}</h4>
              <div className="overlay-suggested-row">
                {suggested.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    disabled={!canEdit}
                    className={item.value === chosen ? "is-chosen" : ""}
                    /* The words the clip and the object turned out to share.
                       On the tooltip rather than the chip: the chip has to
                       stay glanceable, and the reason is what somebody wants
                       when a suggestion looks wrong. */
                    title={item.matched.join(" · ")}
                    onClick={() => adjust({ [param.id]: item.value })}
                  >
                    <Thumbnail
                      base={base}
                      option={{ value: item.value, label: item.label,
                                preview: `face-overlay/objects/${item.value}/sprite` }}
                      apiFetch={apiFetch}
                    />
                    <span>{optionLabel(t, effect.id, item.value, item.label)}</span>
                    {/* The same badge the gallery tile wears. A suggestion is
                        the shortest look somebody gives this dialog, so it is
                        the last place that should leave out the one thing
                        distinguishing two objects with the same name. */}
                    {item.dimensional && (
                      <em
                        className="overlay-solid"
                        title={t("overlayPicker.turnsWithHead")}
                      >
                        3D
                      </em>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* One radio group across every section, because it is one choice.
              The sections are labelled subgroups of it rather than groups of
              their own, which would tell a screen reader there are several
              separate decisions to make. */}
          <div
            className="overlay-gallery"
            role="radiogroup"
            aria-label={t("overlayPicker.heading")}
            onKeyDown={onGalleryKey}
          >
            {grouped.map((section) => (
              <section
                key={section.group}
                role="group"
                aria-label={optionGroup(t, section.groupId, section.group)}
              >
                {section.group && <h4>{optionGroup(t, section.groupId, section.group)}</h4>}
                <div className="overlay-grid">
                  {section.items.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      role="radio"
                      aria-checked={option.value === chosen}
                      /* Roving tabindex: the group is one tab stop and the
                         arrows move within it, which is the contract `radio`
                         signs up to. A dozen stops would be the alternative. */
                      tabIndex={option.value === focused ? 0 : -1}
                      data-choice={option.value}
                      className={`overlay-choice${option.value === chosen ? " is-chosen" : ""}`}
                      disabled={!canEdit}
                      title={option.note || optionLabel(t, effect.id, option.value, option.label)}
                      onFocus={() => setFocused(option.value)}
                      onClick={() => adjust({ [param.id]: option.value })}
                    >
                      <Thumbnail base={base} option={option} apiFetch={apiFetch} />
                      <span>{optionLabel(t, effect.id, option.value, option.label)}</span>
                      {/* Said on the tile, because whether a thing covers a face
                          is the reason most people are choosing one at all. */}
                      {option.occludes && <em>{t("overlayPicker.hidesTheFace")}</em>}
                      {/* And whether it has a back as well as a front, which a
                          thumbnail of it cannot show and is the whole reason
                          to pick one of these over the sticker beside it.
                          "3D" literally, because that is the word somebody
                          scanning a grid is looking for and it needs no
                          translating; what it means for the object is on the
                          badge's own tooltip and in the group's name. */}
                      {option.dimensional && (
                        <em
                          className="overlay-solid"
                          title={t("overlayPicker.turnsWithHead")}
                        >
                          3D
                        </em>
                      )}
                    </button>
                  ))}
                </div>
              </section>
            ))}
            {!grouped.length && <p className="overlay-empty">{t("overlayPicker.noMatch")}</p>}
          </div>

          {/* The folder is the extension point, so it is named where somebody
              is looking at the options rather than only in the documentation —
              and a file that did not load is reported here, because its absence
              from the grid above is the only other sign of it. */}
          {folder && (
            <div className="overlay-folder">
              {/* One row for the three ways in, because this is supporting
                  chrome under the grid people actually came for: the library
                  is where an operator's pictures already are, so it gets the
                  words; a file from disk and the bulk-drop folder are the
                  rarer cases and each folds into an icon whose tooltip says
                  the sentence the row no longer spends a line on. */}
              <div className="overlay-import">
                {/* A dropdown of filenames was the wrong control here for the
                    same reason it was wrong for the objects themselves - see
                    the note at the top of this file. These are pictures, and
                    nobody picks one by reading its name.

                    Folded behind a toggle rather than always open: this is
                    supporting chrome under the grid people came for, and a
                    permanent second grid underneath it competes with the one
                    the dialog is about. */}
                {folder.import_from_library && (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!canEdit || adding}
                    aria-expanded={browsing}
                    onClick={() => setBrowsing((current) => !current)}
                  >{t("overlayPicker.addFromLibrary")}</Button>
                )}
                {/* A file on their own machine, which is where a sticker they
                    have just exported actually is. */}
                {folder.upload && (
                  <label className="overlay-upload" title={t("overlayPicker.chooseFile")}>
                    <input
                      type="file"
                      accept={(folder.accepts ?? [".png"]).join(",")}
                      disabled={!canEdit || adding}
                      onChange={(event) => {
                        const [chosen] = event.target.files ?? [];
                        // Cleared so choosing the same file twice fires again,
                        // which it will after a rejection somebody has fixed.
                        event.target.value = "";
                        void addFromDisk(chosen);
                      }}
                    />
                    <Upload size={14} aria-hidden />
                    <span className="sr-only">{t("overlayPicker.chooseFile")}</span>
                  </label>
                )}
                <span
                  className="overlay-drop-hint"
                  title={t("overlayPicker.dropHint").replace("{directory}", folder.directory)}
                >
                  <FolderInput size={14} aria-hidden />
                  <span className="sr-only">
                    {t("overlayPicker.dropHint").replace("{directory}", folder.directory)}
                  </span>
                </span>
              </div>
              {/* The pictures themselves, at a size somebody can recognise one
                  at. One click imports: there is a single choice to make here,
                  so a separate Add press would be a second click for nothing. */}
              {browsing && folder.import_from_library && (
                <div className="overlay-library">
                  {/* The same control the Library page and the Publish picker
                      use, on the same terms. Library's own set exactly, plus
                      the search it keeps in a form of its own - minus length,
                      which is a duration and means nothing about a picture.

                      Everything else is facet-driven, so an image-only list
                      offers only the channels, platforms, effects and workflow
                      states that images here actually have. */}
                  <AssetFilters
                    values={libraryPictures.filters}
                    facets={libraryPictures.facets}
                    fields={["query", "channel", "platform", "effect", "processing", "downloaded"]}
                    cleared={IMAGE_PICKER_BASE}
                    onChange={(next) => libraryPictures.setFilters(next)}
                  />
                  {/* How much of the library is being looked at. Without it a
                      filter that matches nothing is indistinguishable from a
                      library that holds nothing. */}
                  <p className="overlay-library-tally" role="status" aria-live="polite">
                    {t("overlayPicker.picturesShown", {
                      shown: libraryPictures.assets.length,
                      total: libraryPictures.total,
                    })}
                  </p>
                  {libraryPictures.failure && (
                    <p className="console-error" role="alert">{libraryPictures.failure}</p>
                  )}
                  {libraryPictures.loading === "list" && !libraryPictures.assets.length ? (
                    <p className="overlay-note">{t("common.loading")}</p>
                  ) : libraryPictures.assets.length ? (
                    <>
                      <div className="overlay-library-grid">
                        {libraryPictures.assets.map((asset) => (
                          <button
                            key={asset.id}
                            type="button"
                            disabled={!canEdit || adding}
                            title={asset.title}
                            onClick={() => void addFromLibrary(asset.id)}
                          >
                            <AssetThumbnail
                              asset={asset}
                              workspaceId={workspaceId}
                              apiFetch={apiFetch}
                            />
                            <span>{asset.title}</span>
                          </button>
                        ))}
                      </div>
                      {/* The loop pages; without this the grid stops at the
                          first page and looks like the whole library. */}
                      {libraryPictures.canLoadMore && (
                        <Button
                          variant="secondary"
                          size="sm"
                          busy={Boolean(libraryPictures.loading)}
                          onClick={() => libraryPictures.loadMore()}
                        >{t("common.loadMore")}</Button>
                      )}
                    </>
                  ) : (
                    <p className="overlay-note">{t("overlayPicker.noPictures")}</p>
                  )}
                </div>
              )}

              {/* Failures keep their own line: a rejection folded into a
                  tooltip is a rejection nobody sees. */}
              {(folder.skipped.length > 0 || addFailure) && (
                <p>
                  {folder.skipped.map((item) => (
                    <span key={item.file} className="overlay-skipped">
                      <strong>{item.file}</strong> {item.reason}
                    </span>
                  ))}
                  {addFailure && <span className="overlay-skipped">{addFailure}</span>}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
  );
}

/** The compact control the editor shows in place of a dropdown. */
export function GalleryChoiceButton({
  workspaceId,
  option,
  apiFetch,
  disabled,
  onOpen,
}: {
  workspaceId: string;
  option: ParamOption | undefined;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  disabled: boolean;
  onOpen: () => void;
}) {
  const t = useT();
  const base = `/api/workspaces/${workspaceId}/media/library`;
  return (
    <button type="button" className="overlay-chosen" disabled={disabled} onClick={onOpen}>
      {option ? (
        <Thumbnail base={base} option={option} apiFetch={apiFetch} />
      ) : (
        <span className="overlay-sprite-empty" aria-hidden="true" />
      )}
      <span className="overlay-chosen-name">
        <strong>{option?.label ?? t("overlayPicker.none")}</strong>
        {option?.occludes && <Badge tone="accent">{t("overlayPicker.hidesTheFace")}</Badge>}
        {/* The same mark the gallery tile and the suggestion chip carry. This
            is the only place the chosen object is named once the dialog is
            shut, so leaving it off here is where "Cap" and a solid cap stop
            being distinguishable at all. */}
        {option?.dimensional && (
          <Badge tone="info" className="overlay-solid-badge">3D</Badge>
        )}
      </span>
      <span className="overlay-chosen-action">{t("overlayPicker.change")}</span>
    </button>
  );
}
