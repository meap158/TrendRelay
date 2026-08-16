"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { clipLength, fileName, handoffPath, isBlurred } from "../../lib/media-rules";
import { useEffect, useMemo, useState } from "react";

import { PlatformIcon, platformLabels, type PublishingPlatform } from "../publishing-icons";
import {
  AssetFilters,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { ActionIcon } from "../ui/action-icons";
import { useT } from "../i18n-provider";

export type LibraryAsset = {
  id: string;
  title: string;
  platform: string | null;
  creator: string | null;
  original_path: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  media_kind: string;
  versions: { id: string; kind: string }[];
};

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

/** Marks a drag as carrying one of our own library paths. */
export const MEDIA_DRAG_TYPE = "application/x-trendrelay-media";

/**
 * The picker only ever offers videos, so that is where clearing returns to
 * rather than to nothing at all.
 */
export const PICKER_BASE: AssetFilterValues = { mediaKind: "video" };
/** The same dialog, looking for a carousel's frames instead of a clip. */
export const IMAGE_PICKER_BASE: AssetFilterValues = { mediaKind: "image" };

// Re-exported so the screens importing them from here keep working, while the
// rules themselves live somewhere they can be tested. Imported as well as
// re-exported because this file uses them too.
export { clipLength, fileName, handoffPath, isBlurred };

export function AssetThumbnail({
  asset,
  workspaceId,
  apiFetch,
}: {
  asset: LibraryAsset;
  workspaceId: string;
  apiFetch: Fetcher;
}) {
  const [source, setSource] = useState("");
  const hasThumbnail = asset.versions.some((version) => version.kind === "thumbnail");

  useEffect(() => {
    if (!hasThumbnail) return;
    let active = true;
    let objectUrl = "";
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/content/thumbnail`)
      .then((response) => {
        if (!response.ok) throw new Error("unavailable");
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (active) setSource(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, asset.id, hasThumbnail, workspaceId]);

  return (
    <span className="picker-thumb">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {source ? <img alt="" src={source} /> : <b aria-hidden="true">▶</b>}
      {asset.duration_ms ? <i>{clipLength(asset.duration_ms)}</i> : null}
    </span>
  );
}

/**
 * Pick a clip from the library instead of typing a path.
 *
 * The path is what the API takes, but nobody remembers one; this lists what is
 * actually in the library and hands back the same value the field expects.
 * Loading belongs to the caller so opening the panel is what triggers a read.
 */
export function MediaPicker({
  open,
  assets,
  workspaceId,
  apiFetch,
  loading,
  failure,
  facets,
  onSearch,
  onPick,
  onClose,
  mediaKind = "video",
}: {
  open: boolean;
  assets: LibraryAsset[];
  workspaceId: string;
  apiFetch: Fetcher;
  loading: boolean;
  failure: string | null;
  facets: AssetFacets;
  onSearch: (filters: AssetFilterValues) => void;
  onPick: (asset: LibraryAsset) => void;
  onClose: () => void;
  /** What this dialog is being opened to find. */
  mediaKind?: "video" | "image";
}) {
  const t = useT();
  const images = mediaKind === "image";
  const base = images ? IMAGE_PICKER_BASE : PICKER_BASE;
  // Initialised from what this dialog is looking for. The caller keys it on
  // `mediaKind`, so opening it for carousel frames after opening it for a clip
  // starts a fresh dialog rather than one still filtered to videos.
  const [filters, setFilters] = useState<AssetFilterValues>(base);

  /** Applied on change, because narrowing the list is a new search either way. */
  function apply(next: AssetFilterValues) {
    setFilters(next);
    onSearch(next);
  }

  // Stays mounted and is driven by `open`: unmounting it on close would cut
  // short the sequence that returns focus to whatever opened it.
  return (
    <Dialog
      open={open}
      size="wide"
      title={images ? t("composer.chooseImages") : t("composer.chooseClip")}
      description={images
        ? "Images in this workspace's library. Pick them in the order they are swiped."
        : "Videos in this workspace's library."}
      onClose={onClose}
    >
      {/* The same control the Library uses, minus the media kind: this dialog
          only ever offers videos, so a kind selector here would be a lie. */}
      <AssetFilters
        values={filters}
        facets={facets}
        fields={["query", "effect", "channel", "platform", "length"]}
        cleared={base}
        onChange={apply}
      />
      {failure && <p className="engine-warning" role="status">{failure}</p>}
      {!failure && !loading && !assets.length && (
        <p className="picker-empty">
          {images
            ? "No images matched. Import them in the Library tab, then pick them here."
            : "No videos matched. Import clips in the Library tab, then pick one here."}
        </p>
      )}
      <ul className="picker-results">
        {assets.map((asset) => (
          <li key={asset.id}>
            <button
              type="button"
              className="picker-result"
              draggable
              onDragStart={(event) => {
                // The path is what the field takes, and a plain-text payload
                // means the same drag also works into any text input.
                event.dataTransfer.setData("text/plain", asset.original_path);
                event.dataTransfer.setData(MEDIA_DRAG_TYPE, asset.original_path);
                event.dataTransfer.effectAllowed = "copy";
              }}
              onClick={() => onPick(asset)}
            >
              <AssetThumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
              <span className="picker-meta">
                <strong>{asset.title}</strong>
                <small>
                  {[asset.platform, asset.creator].filter(Boolean).join(" · ") || "No source recorded"}
                </small>
              </span>
              {isBlurred(asset) && <Badge tone="accent">{t("composer.facesBlurred")}</Badge>}
            </button>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}

/**
 * How the post will read on the network it is going to.
 *
 * The chrome is deliberately generic - it is a rehearsal of the caption and
 * frame against each network's shape, not a claim to render what the network
 * will. A Story crops to full bleed and carries no caption, so showing one
 * would be a lie.
 */
export function PostPreview({
  platform,
  postTypeLabel,
  handle,
  caption,
  title,
  thumbnail,
  source,
  sourceIsImage,
  carousel,
}: {
  platform: PublishingPlatform;
  postTypeLabel: string;
  handle: string;
  caption: string;
  title: string;
  thumbnail: string;
  /** The media itself, so the frame is the post rather than a still of it. */
  source?: string;
  /** True when `source` is an image: a carousel frame rather than a clip. */
  sourceIsImage?: boolean;
  /** Every frame of a carousel, in swipe order, so the preview can be swiped. */
  carousel?: string[];
}) {
  const t = useT();
  const story = postTypeLabel.toLowerCase() === "story";
  // Which frame the preview is showing. A carousel is swiped, so the question
  // "does this read" is asked of each one, not only of the cover.
  const [frame, setFrame] = useState(0);
  /**
   * The media's own width divided by its height, once the browser knows it.
   *
   * Measured rather than guessed from the platform: a 4:5 box letterboxed a
   * 9:16 clip and cropped a landscape one, and neither is what gets posted.
   *
   * Deliberately not cleared when the frame changes. Holding the last shape is
   * what keeps the box still while the next image loads; clearing it snapped
   * the box to the CSS default and out again, so every step jumped twice.
   */
  const [measured, setMeasured] = useState<number | null>(null);
  const frames = carousel ?? [];
  const showing = frames.length ? frames[Math.min(frame, frames.length - 1)] : source;
  const ratio = measured;
  // The frames either side, rendered but not shown, so stepping reads from
  // cache rather than starting a fresh request and blanking the box.
  const neighbours = frames.length > 1
    ? [frames[frame - 1], frames[frame + 1]].filter(Boolean)
    : [];
  const measure = (width: number, height: number) => {
    // Frames of one carousel are usually the same shape, so this is often the
    // same number again; where they differ the box settles once, on load,
    // rather than bouncing through a default nobody chose.
    if (width && height) setMeasured(width / height);
  };
  const showsTitle = platform === "youtube" || platform === "reddit" || platform === "pinterest";

  return (
    <figure className={`post-preview${story ? " story" : ""}`}>
      <figcaption>
        <PlatformIcon platform={platform} size={18} />
        <span>
          <strong>{handle || "your account"}</strong>
          <small>{platformLabels[platform]} · {postTypeLabel}</small>
        </span>
      </figcaption>
      <div
        className="post-preview-frame"
        // Not on a Story. That surface is 9:16 full bleed whatever the clip is,
        // and the CSS says so - but an inline variable beats any rule, so
        // measuring one here would quietly replace the network's shape with the
        // file's, which is the opposite of what this preview is for.
        style={
          ratio && !story
            ? ({ "--preview-ratio": String(ratio) } as React.CSSProperties)
            : undefined
        }
      >
        {/* The media, where there is any: a network shows the clip, not a
            still of it, and a caption judged against a frozen frame is judged
            against something nobody will see. The thumbnail is the fallback
            for a clip picked from the Library before its file can be read. */}
        {showing && !sourceIsImage ? (
          // The same gated player the Library uses, rather than a second one.
          // This panel used to sit beside a separate "What will be sent" card
          // that played the identical file, so the page asked the same question
          // twice and answered it two different ways.
          <UploadPreview key={showing} source={showing} poster={thumbnail} onNaturalRatio={setMeasured} />
        ) : showing ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img alt="" src={showing} onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            measure(naturalWidth, naturalHeight);
          }} />
        ) : thumbnail ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img alt="" src={thumbnail} onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            measure(naturalWidth, naturalHeight);
          }} />
        ) : (
          <p>{t("composer.chooseClipForFrame")}</p>
        )}
        {neighbours.map((source) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={source} src={source} alt="" aria-hidden="true" className="preview-preload" />
        ))}
        {/* Stepped rather than counted. The count alone says a carousel exists;
            being able to move through it is what answers whether the third
            frame still makes sense without the first. */}
        {frames.length > 1 && (
          <>
            <em className="post-preview-count">
              {Math.min(frame, frames.length - 1) + 1} / {frames.length}
            </em>
            <button
              type="button"
              className="post-preview-step start"
              aria-label={t("publish.moveEarlier")}
              disabled={frame === 0}
              onClick={() => setFrame((current) => Math.max(0, current - 1))}
            >&#8249;</button>
            <button
              type="button"
              className="post-preview-step end"
              aria-label={t("publish.moveLater")}
              disabled={frame >= frames.length - 1}
              onClick={() => setFrame((current) => Math.min(frames.length - 1, current + 1))}
            >&#8250;</button>
          </>
        )}
      </div>
      {story ? (
        <p className="post-preview-note">
          A Story fills the screen and carries no caption. It disappears after 24 hours.
        </p>
      ) : (
        <div className="post-preview-copy">
          {showsTitle && title && <strong>{title}</strong>}
          <p>{caption || "Your caption will appear here."}</p>
        </div>
      )}
    </figure>
  );
}

export type CalendarEntry = {
  at: Date;
  label: string;
  state: string;
  /** Where it is going, so the rail can show it without opening the post. */
  platforms?: PublishingPlatform[];
  title?: string | null;
};
export type Slot = {
  id: string;
  weekday: number;
  weekday_label: string;
  hour: number;
  minute: number;
  time: string;
};
export type SlotPreset = { id: string; label: string; summary: string; times: string[] };

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WEEKDAY_NAMES = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
];
export const EVERY_DAY = -1;

/** The Monday of the week containing `from`. */
function weekStart(from: Date) {
  const start = new Date(from);
  start.setHours(0, 0, 0, 0);
  // getDay() is Sunday-based; shift so the week opens on Monday.
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  return start;
}

function sameDay(left: Date, right: Date) {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  );
}

/** Monday-first index, matching how a slot stores its weekday. */
function weekdayIndex(day: Date) {
  return (day.getDay() + 6) % 7;
}

export function slotLabel(slot: { hour: number; minute: number }) {
  const suffix = slot.hour < 12 ? "am" : "pm";
  const display = slot.hour % 12 === 0 ? 12 : slot.hour % 12;
  return slot.minute
    ? `${display}:${String(slot.minute).padStart(2, "0")}${suffix}`
    : `${display}${suffix}`;
}

/** `datetime-local` wants a naive local string. */
export function localValue(value: Date) {
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
}

/** The next occurrences of the configured slots, for a one-click row. */
export function upcomingSlots(slots: Slot[], now: Date, count = 5) {
  const found: { value: string; label: string; day: string }[] = [];
  for (let ahead = 0; ahead < 8 && found.length < count; ahead += 1) {
    const day = new Date(now);
    day.setDate(day.getDate() + ahead);
    for (const slot of slots) {
      if (found.length >= count) break;
      if (slot.weekday !== EVERY_DAY && slot.weekday !== weekdayIndex(day)) continue;
      const at = new Date(day);
      at.setHours(slot.hour, slot.minute, 0, 0);
      if (at.getTime() <= now.getTime()) continue;
      found.push({
        value: localValue(at),
        label: slotLabel(slot),
        day:
          ahead === 0
            ? "Today"
            : ahead === 1
              ? "Tomorrow"
              : at.toLocaleDateString([], { weekday: "short" }),
      });
    }
  }
  return found;
}

/**
 * What is going out over the next few days, one day at a time.
 *
 * A day strip rather than a list, because the question this answers is "is
 * anything going out on Thursday" and a flat list makes that a counting
 * exercise. Each day carries how many posts it holds, so the busy and empty
 * days are visible without selecting each one - the strip is the summary, not
 * just a set of tabs.
 *
 * It opens on the first day that actually has something. Defaulting to today
 * shows an empty state to anyone whose next post is on Friday, which reads as
 * "nothing scheduled" when the truth is the opposite.
 */
export function UpcomingPosts({
  entries,
  slots,
  now,
  days = 7,
  onPickDay,
  onOpenCalendar,
}: {
  entries: CalendarEntry[];
  /** This workspace's posting times, so a new post lands on one of them. */
  slots: Slot[];
  now: Date;
  days?: number;
  onPickDay: (at: Date) => void;
  onOpenCalendar?: () => void;
}) {
  const t = useT();
  const strip = useMemo(() => {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    return Array.from({ length: days }, (_, offset) => {
      const at = new Date(start);
      at.setDate(at.getDate() + offset);
      const posts = entries
        .filter((entry) => sameDay(entry.at, at))
        .sort((left, right) => left.at.getTime() - right.at.getTime());
      return { at, offset, posts };
    });
  }, [days, entries, now]);

  const firstBusy = strip.find((day) => day.posts.length)?.offset ?? 0;
  const [picked, setPicked] = useState<number | null>(null);
  const active = strip.find((day) => day.offset === (picked ?? firstBusy)) ?? strip[0];

  return (
    <article className="upcoming">
      <header className="upcoming-head">
        <h2>{t("composer.upcoming")}</h2>
        {onOpenCalendar && (
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            aria-label={t("composer.openCalendar")}
            title={t("composer.openCalendar")}
            onClick={onOpenCalendar}
          >
            <CalendarGlyph />
          </Button>
        )}
      </header>

      <div className="upcoming-strip" role="tablist" aria-label={t("composer.days")}>
        {strip.map((day) => {
          const selected = day.offset === active.offset;
          return (
            <button
              key={day.offset}
              type="button"
              role="tab"
              aria-selected={selected}
              className={`upcoming-day${selected ? " selected" : ""}`}
              onClick={() => setPicked(day.offset)}
            >
              <span>{day.at.toLocaleDateString([], { weekday: "short" })}</span>
              <b>{String(day.at.getDate()).padStart(2, "0")}</b>
              {/* The count is what makes the strip readable at a glance; without
                  it every day looks the same until it is opened. It sits under
                  the date rather than over the corner, where at this width it
                  landed on top of the weekday. */}
              <i aria-hidden="true" className={day.posts.length ? "" : "empty"}>
                {day.posts.length || ""}
              </i>
              <span className="sr-only">
                {day.posts.length
                  ? `${day.posts.length} post${day.posts.length === 1 ? "" : "s"}`
                  : "no posts"}
              </span>
            </button>
          );
        })}
      </div>

      {active.posts.length ? (
        <ul className="upcoming-list">
          {active.posts.map((entry, index) => (
            <li key={`${entry.at.toISOString()}-${index}`}>
              <time dateTime={entry.at.toISOString()}>
                {entry.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </time>
              <div className="upcoming-post">
                <strong>{entry.title || entry.label}</strong>
                <span className="upcoming-meta">
                  {(entry.platforms ?? []).map((platform) => (
                    <PlatformIcon key={platform} platform={platform} size={14} />
                  ))}
                  <Badge tone={entry.state === "succeeded" ? "good" : "neutral"}>
                    {entry.state}
                  </Badge>
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="upcoming-empty">
          {/* Named by weekday, not by date. A partial date format leaves the
              order to the locale, which turned "Saturday 8" into "8 Saturday";
              inside a seven-day strip the weekday alone is unambiguous. */}
          <p>
            Nothing scheduled for{" "}
            {active.offset === 0
              ? "today"
              : active.offset === 1
                ? "tomorrow"
                : active.at.toLocaleDateString([], { weekday: "long" })}.
          </p>
          {/* Fills the composer's date with this day rather than opening a blank
              form, since the day was just chosen and asking again wastes it. */}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onPickDay(firstFreeTime(active.at, slots, active.posts, now))}
          >{t("composer.scheduleOne")}</Button>
        </div>
      )}
    </article>
  );
}

/** Default time of 9am, used when this workspace has set no posting times. */
const FALLBACK_HOUR = 9;

/**
 * The time a new post on this day should default to.
 *
 * The workspace's own posting slots come first — they exist precisely so this
 * decision is already made — skipping any that are taken or already past.
 * Falling back to "an hour from now" put a Saturday post at five in the morning
 * because that happened to be an hour from the moment the button was pressed.
 */
function firstFreeTime(day: Date, slots: Slot[], taken: CalendarEntry[], now: Date): Date {
  const busy = new Set(taken.map((entry) => `${entry.at.getHours()}:${entry.at.getMinutes()}`));
  const candidates = slots
    .filter((slot) => slot.weekday === EVERY_DAY || slot.weekday === weekdayIndex(day))
    .sort((left, right) => left.hour - right.hour || left.minute - right.minute);

  for (const slot of candidates) {
    if (busy.has(`${slot.hour}:${slot.minute}`)) continue;
    const at = new Date(day);
    at.setHours(slot.hour, slot.minute, 0, 0);
    if (at.getTime() > now.getTime()) return at;
  }

  const fallback = new Date(day);
  fallback.setHours(FALLBACK_HOUR, 0, 0, 0);
  // Today's nine o'clock may already be behind us, in which case the next hour
  // is the only honest suggestion.
  if (fallback.getTime() <= now.getTime()) {
    fallback.setTime(now.getTime());
    fallback.setHours(fallback.getHours() + 1, 0, 0, 0);
  }
  return fallback;
}

/** A small month page. Drawn rather than imported to match the icon set's weight. */
function CalendarGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  );
}

/**
 * A week of this workspace's posting slots, with what is already scheduled on it.
 *
 * Slots in the past are drawn but not offered - seeing that this morning's slot
 * has gone is more useful than a week that silently starts at noon. A workspace
 * with no slots configured gets an empty week rather than invented times.
 */
export function WeekCalendar({
  slots,
  entries,
  selected,
  now,
  onPick,
}: {
  slots: Slot[];
  entries: CalendarEntry[];
  selected: string;
  now: Date;
  onPick: (at: Date) => void;
}) {
  const t = useT();
  const [offset, setOffset] = useState(0);
  const start = useMemo(() => {
    const base = weekStart(now);
    base.setDate(base.getDate() + offset * 7);
    return base;
  }, [now, offset]);

  const days = useMemo(
    () =>
      Array.from({ length: 7 }, (_, index) => {
        const day = new Date(start);
        day.setDate(day.getDate() + index);
        return day;
      }),
    [start],
  );

  const range = `${days[0].toLocaleDateString([], { month: "short", day: "numeric" })} – ${days[6].toLocaleDateString([], { month: "short", day: "numeric" })}`;

  return (
    <section className="week-calendar">
      <header>
        <strong>{range}</strong>
        <div className="ui-choice-row">
          <Button variant="quiet" size="sm" onClick={() => setOffset(offset - 1)}>
            <ChevronLeft size={14} aria-hidden="true" />Previous
          </Button>
          <Button
            variant="quiet"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(0)}
          >{t("composer.thisWeek")}</Button>
          <Button variant="quiet" size="sm" onClick={() => setOffset(offset + 1)}>
            Next<ChevronRight size={14} aria-hidden="true" />
          </Button>
        </div>
      </header>
      <div className="week-grid">
        {days.map((day, index) => {
          const today = sameDay(day, now);
          const dayEntries = entries.filter((entry) => sameDay(entry.at, day));
          const daySlots = slots.filter(
            (slot) => slot.weekday === EVERY_DAY || slot.weekday === index,
          );
          const loose = dayEntries.filter(
            (entry) => !daySlots.some(
              (slot) => slot.hour === entry.at.getHours() && slot.minute === entry.at.getMinutes(),
            ),
          );
          return (
            <div key={day.toISOString()} className={`week-day${today ? " today" : ""}`}>
              <span className="week-day-head">
                <b>{DAY_NAMES[index]}</b>
                <i>{day.getDate()}</i>
              </span>
              {daySlots.map((slot) => {
                const at = new Date(day);
                at.setHours(slot.hour, slot.minute, 0, 0);
                const past = at.getTime() <= now.getTime();
                const value = localValue(at);
                const taken = dayEntries.filter(
                  (entry) => entry.at.getHours() === slot.hour
                    && entry.at.getMinutes() === slot.minute,
                );
                return (
                  <button
                    key={slot.id}
                    type="button"
                    className={`week-slot${selected === value ? " selected" : ""}${taken.length ? " taken" : ""}`}
                    disabled={past}
                    aria-pressed={selected === value}
                    title={taken.length ? taken.map((entry) => entry.label).join(", ") : undefined}
                    onClick={() => onPick(at)}
                  >
                    {slotLabel(slot)}
                    {taken.length > 0 && <em>{taken.length}</em>}
                  </button>
                );
              })}
              {loose.map((entry, entryIndex) => (
                <span key={`${entry.label}-${entryIndex}`} className="week-extra" title={entry.label}>
                  {entry.at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                </span>
              ))}
            </div>
          );
        })}
      </div>
      <p className="week-legend">
        {slots.length
          ? "Click a slot to schedule at that time. A number marks posts already queued then."
          : "No posting times set yet. Add some below, or apply a preset, and they will appear here."}
      </p>
    </section>
  );
}

/**
 * Edit the workspace's posting times.
 *
 * Presets are offered rather than applied: each states the assumption it makes
 * about where the audience is, so it can be judged instead of trusted.
 */
export function SlotEditor({
  slots,
  presets,
  timezone,
  canEdit,
  busy,
  onSave,
}: {
  slots: Slot[];
  presets: SlotPreset[];
  timezone: string;
  canEdit: boolean;
  busy: boolean;
  onSave: (entries: { weekday: number; time: string }[]) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState("");
  const [weekday, setWeekday] = useState(EVERY_DAY);

  const entries = slots.map((slot) => ({ weekday: slot.weekday, time: slot.time }));

  function add() {
    if (!draft) return;
    if (entries.some((entry) => entry.time === draft && entry.weekday === weekday)) return;
    onSave([...entries, { weekday, time: draft }]);
    setDraft("");
  }

  return (
    <div className="slot-editor">
      <div className="slot-editor-head">
        <div>
          <h4>{t("composer.postingTimes")}</h4>
          <p>Times are {timezone}, the clock you are reading.</p>
        </div>
        {slots.length > 0 && canEdit && (
          <Button variant="quiet" size="sm" busy={busy} onClick={() => onSave([])}>
            Clear all
          </Button>
        )}
      </div>
      {slots.length > 0 && (
        <ul className="slot-list">
          {slots.map((slot, index) => (
            <li key={slot.id}>
              <b>{slotLabel(slot)}</b>
              {slot.weekday !== EVERY_DAY && <i>{slot.weekday_label}</i>}
              {canEdit && (
                <button
                  type="button"
                  className="slot-remove"
                  aria-label={`Remove ${slotLabel(slot)}`}
                  disabled={busy}
                  onClick={() => onSave(entries.filter((_entry, at) => at !== index))}
                ><ActionIcon name="dismiss" /></button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit && (
        <div className="slot-add">
          <input
            type="time"
            value={draft}
            aria-label={t("composer.timeOfDay")}
            onChange={(event) => setDraft(event.target.value)}
          />
          <select
            value={weekday}
            aria-label={t("composer.repeats")}
            onChange={(event) => setWeekday(Number(event.target.value))}
          >
            <option value={EVERY_DAY}>{t("composer.everyDay")}</option>
            {WEEKDAY_NAMES.map((name, index) => (
              <option key={name} value={index}>{name} only</option>
            ))}
          </select>
          <Button variant="quiet" size="sm" disabled={busy || !draft} onClick={add}>
            Add time
          </Button>
        </div>
      )}
      {canEdit && (
        <div className="slot-presets">
          <span>{t("composer.startFromPreset")}</span>
          <div>
            {presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className="slot-preset"
                disabled={busy}
                title={preset.summary}
                onClick={() => onSave(preset.times.map((time) => ({ weekday: EVERY_DAY, time })))}
              >
                <b>{preset.label}</b>
                <small>{preset.times.join(" · ")}</small>
              </button>
            ))}
          </div>
          <p>{t("composer.presetWarning")}</p>
        </div>
      )}
    </div>
  );
}


/**
 * The file this delivery will upload, shown only when asked for.
 *
 * `preload` alone still pulls the file down as soon as the panel renders, and
 * this is the whole upload rather than a thumbnail - which is why the Library
 * gates its player the same way instead of loading every clip scrolled past.
 *
 * Keyed on the source by its caller, so choosing different media puts the gate
 * back rather than autoplaying whatever was picked next.
 */
export function UploadPreview({
  source,
  poster,
  onNaturalRatio,
}: {
  source: string;
  poster?: string;
  /** The media's own width/height, once the browser knows it. */
  onNaturalRatio?: (ratio: number) => void;
}) {
  const t = useT();
  const [requested, setRequested] = useState(false);

  if (requested) {
    return (
      <video className="blur-preview" controls controlsList="nodownload" autoPlay
        preload="none" poster={poster || undefined} src={source}
        onLoadedMetadata={(event) => {
          const { videoWidth, videoHeight } = event.currentTarget;
          if (videoWidth && videoHeight) onNaturalRatio?.(videoWidth / videoHeight);
        }} />
    );
  }
  return (
    <button type="button" className="blur-preview-launch" onClick={() => setRequested(true)}>
      {poster
        // eslint-disable-next-line @next/next/no-img-element
        ? <img alt="" src={poster} onLoad={(event) => {
            // The poster is the first frame, so it has the clip's shape and
            // arrives long before anyone presses play.
            const { naturalWidth, naturalHeight } = event.currentTarget;
            if (naturalWidth && naturalHeight) onNaturalRatio?.(naturalWidth / naturalHeight);
          }} />
        : <span className="blur-preview-empty" />}
      <span className="blur-preview-overlay">
        <span aria-hidden="true">&#9654;</span>
        <strong>{t("library.playPreview")}</strong>
        <small>{t("library.privatePreview")}</small>
      </span>
    </button>
  );
}
