"use client";

import { useEffect, useMemo, useState } from "react";

import { PlatformIcon, platformLabels, type PublishingPlatform } from "../publishing-icons";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";

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

export function clipLength(durationMs: number | null) {
  if (!durationMs) return "";
  const total = Math.round(durationMs / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Blurred renders are registered as another version of the same asset. */
export function isBlurred(asset: LibraryAsset) {
  return asset.versions.some((version) => version.kind === "blurred");
}

function AssetThumbnail({
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
  onSearch,
  onPick,
  onClose,
}: {
  open: boolean;
  assets: LibraryAsset[];
  workspaceId: string;
  apiFetch: Fetcher;
  loading: boolean;
  failure: string | null;
  onSearch: (query: string) => void;
  onPick: (asset: LibraryAsset) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");

  // Stays mounted and is driven by `open`: unmounting it on close would cut
  // short the sequence that returns focus to whatever opened it.
  return (
    <Dialog
      open={open}
      title="Choose a clip"
      description="Videos in this workspace's library."
      onClose={onClose}
    >
      <form
        className="picker-search"
        onSubmit={(event) => { event.preventDefault(); onSearch(query); }}
      >
        <input
          value={query}
          placeholder="Search titles, creators and transcripts"
          onChange={(event) => setQuery(event.target.value)}
        />
        <Button type="submit" variant="quiet" busy={loading}>
          {loading ? "Searching" : "Search"}
        </Button>
      </form>
      {failure && <p className="engine-warning" role="status">{failure}</p>}
      {!failure && !loading && !assets.length && (
        <p className="picker-empty">
          No videos matched. Import clips in the Library tab, then pick one here.
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
              {isBlurred(asset) && <Badge tone="accent">Faces blurred</Badge>}
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
}: {
  platform: PublishingPlatform;
  postTypeLabel: string;
  handle: string;
  caption: string;
  title: string;
  thumbnail: string;
}) {
  const story = postTypeLabel.toLowerCase() === "story";
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
      <div className="post-preview-frame">
        {thumbnail ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img alt="" src={thumbnail} />
        ) : (
          <p>Choose a clip to see its frame here.</p>
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

export type CalendarEntry = { at: Date; label: string; state: string };
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
            ← Previous
          </Button>
          <Button
            variant="quiet"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(0)}
          >This week</Button>
          <Button variant="quiet" size="sm" onClick={() => setOffset(offset + 1)}>
            Next →
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
          <h4>Posting times</h4>
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
                >×</button>
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
            aria-label="Time of day"
            onChange={(event) => setDraft(event.target.value)}
          />
          <select
            value={weekday}
            aria-label="Repeats"
            onChange={(event) => setWeekday(Number(event.target.value))}
          >
            <option value={EVERY_DAY}>Every day</option>
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
          <span>Start from a preset</span>
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
          <p>A preset replaces the list above. Each assumes something about where your audience is.</p>
        </div>
      )}
    </div>
  );
}
