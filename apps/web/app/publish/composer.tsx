"use client";

import { zonedInstant, zonedParts } from "../../lib/schedule-time";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { clipLength, fileName, handoffPath, isBlurred } from "../../lib/media-rules";
import { type CSSProperties, useEffect, useMemo, useState } from "react";

import { PlatformIcon, platformLabels, type PublishingPlatform } from "../publishing-icons";
import {
  AssetFilters,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import { Button } from "../ui/button";
import { useLibraryAssets } from "../../lib/use-library-assets";
import { Select } from "../ui/select";
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
 * What "clear" returns the picker to: everything it can post.
 *
 * Not pinned to video any more. A post here is a clip *or* a carousel of
 * pictures, and a picker that shows only clips cannot express the second - the
 * carousel support looked absent when it was one filter out of reach. Audio is
 * dropped on arrival instead, because no destination here publishes a sound
 * file and offering one only to refuse it later is the worse half of the
 * choice.
 */
export const PICKER_BASE: AssetFilterValues = {};
/** The same dialog opened straight onto a carousel's frames. */
export const IMAGE_PICKER_BASE: AssetFilterValues = { mediaKind: "image" };

/** Kinds this picker can hand to Publish, in the order the chooser shows them. */
export const POSTABLE_KINDS = ["video", "image"] as const;

// Re-exported so the screens importing them from here keep working, while the
// rules themselves live somewhere they can be tested. Imported as well as
// re-exported because this file uses them too.
export { clipLength, fileName, handoffPath, isBlurred };

/**
 * A Library asset's still, as an object URL.
 *
 * Read through `apiFetch` rather than pointed at with a `src`, because the
 * endpoint wants the workspace identity that a bare `<img>` does not send. Any
 * panel that shows a clip before it plays wants this: the alternative is the
 * grey rectangle a poster-less player draws, which says nothing about which
 * video is about to go out.
 *
 * Returns "" while it loads and if there is nothing to load, so a caller can
 * treat both the same - there is no still to show either way.
 */
export function useAssetPoster(
  assetId: string | null | undefined,
  workspaceId: string,
  apiFetch: Fetcher,
): string {
  // The id travels with the URL rather than the effect clearing it first.
  // Clearing meant a setState in the effect body - a cascading render for
  // every poster on the page - where all that is wanted is to not show the
  // previous clip's frame under the next clip's name.
  const [loaded, setLoaded] = useState<{ id: string; url: string } | null>(null);

  useEffect(() => {
    if (!assetId) return;
    let active = true;
    let objectUrl = "";
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${assetId}/content/thumbnail`)
      .then((response) => {
        if (!response.ok) throw new Error("unavailable");
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (active) setLoaded({ id: assetId, url: objectUrl });
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, assetId, workspaceId]);

  return loaded && loaded.id === assetId ? loaded.url : "";
}

export function AssetThumbnail({
  asset,
  workspaceId,
  apiFetch,
}: {
  asset: LibraryAsset;
  workspaceId: string;
  apiFetch: Fetcher;
}) {
  const hasThumbnail = asset.versions.some((version) => version.kind === "thumbnail");
  const source = useAssetPoster(hasThumbnail ? asset.id : null, workspaceId, apiFetch);

  /* The clip's real shape, handed to CSS so a caller with room can honour it.
   *
   * Short-form video is 9:16 and every fixed thumbnail box here is landscape, so
   * `object-fit: cover` was throwing away more than half of every frame - 44% of
   * a 720x1280 clip survived a 92x72 box. Callers that have the room set their
   * box from this instead of cropping to a shape the media never had.
   *
   * Measured from the still itself rather than taken from the asset. The asset
   * carries `width`/`height` only where somebody filled them in - the campaign
   * pipeline builds its rows from a post's `asset_id` and a title, and passes
   * both as null - whereas the image being rendered always knows its own size.
   * The declared size is still used first where it exists, so the box is right
   * before the bytes arrive rather than reflowing when they do. */
  const declared = asset.width && asset.height ? `${asset.width} / ${asset.height}` : "";
  const [measured, setMeasured] = useState("");
  const ratio = declared || measured;

  return (
    <span
      className="picker-thumb"
      style={ratio ? ({ "--thumb-ratio": ratio } as CSSProperties) : undefined}
    >
      {source ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          alt=""
          src={source}
          onLoad={(event) => {
            const image = event.currentTarget;
            if (image.naturalWidth && image.naturalHeight) {
              setMeasured(`${image.naturalWidth} / ${image.naturalHeight}`);
            }
          }}
        />
      ) : <b aria-hidden="true">▶</b>}
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
  workspaceId,
  apiFetch,
  onPick,
  onClose,
  mediaKind = "video",
  chosen = [],
  pathOf,
  capacity = 0,
}: {
  open: boolean;
  workspaceId: string;
  apiFetch: Fetcher;
  onPick: (asset: LibraryAsset) => void;
  onClose: () => void;
  /** What this dialog is being opened to find. */
  mediaKind?: "video" | "image";
  /**
   * What is already in the carousel, in the order it will be swiped.
   *
   * The dialog had no idea. Clicking a picture added it behind the dialog with
   * nothing to show for it - no mark on the row, no count, and a second click
   * on the same one did nothing at all, because the caller silently refuses a
   * duplicate. Three clicks and a wrong guess about which of them landed is
   * indistinguishable from a broken button.
   */
  chosen?: string[];
  /** How a row's asset maps to the value `chosen` holds. */
  pathOf?: (asset: LibraryAsset) => string;
  /** The tightest destination's limit, or 0 when nothing constrains it. */
  capacity?: number;
}) {
  const t = useT();
  const images = mediaKind === "image";
  /** Where each chosen path sits in the swipe order, by path. */
  const order = new Map(chosen.map((path, index) => [path, index + 1]));
  const full = capacity > 0 && chosen.length >= capacity;
  const base = images ? IMAGE_PICKER_BASE : PICKER_BASE;
  // The shared library loop - the same hook the campaign media browser reads
  // with, so the two pickers cannot drift. It owns the debounce, the paging,
  // the stale-response guard and the counts; the baseline is what this dialog
  // was opened for, and the caller keys the component on `mediaKind` so
  // opening it for carousel frames after opening it for a clip starts fresh.
  // Audio is dropped on arrival: Publish sends a clip or a gallery of
  // pictures, so a sound file has nothing to become here.
  const picker = useLibraryAssets<LibraryAsset>({
    workspaceId, apiFetch,
    baseline: base,
    enabled: open,
    keep: (asset) => asset.media_kind !== "audio",
  });
  const { assets, filters } = picker;
  const loading = picker.loading !== "";
  const failure = picker.failure;

  // A kind in the chooser that returns a list nobody can pick from is worse
  // than one that is absent, so the facet row drops audio too.
  const postable: AssetFacets = {
    ...picker.facets,
    media_kinds: picker.facets.media_kinds.filter(
      (facet) => (POSTABLE_KINDS as readonly string[]).includes(facet.value),
    ),
  };

  // Stays mounted and is driven by `open`: unmounting it on close would cut
  // short the sequence that returns focus to whatever opened it.
  return (
    <Dialog
      open={open}
      size="wide"
      title={images ? t("composer.chooseImages") : t("composer.chooseMedia")}
      description={images
        ? "Images in this workspace's library. Pick them in the order they are"
          + " swiped, and click a picked one to take it out again."
        : "Clips and pictures in this workspace's library. A clip fills the video "
          + "slot; a picture starts a carousel."}
      onClose={onClose}
    >
      {/* The same control the Library uses, media kind included. It used to be
          left out on the grounds that this dialog only offered videos, which
          was true and was the bug: pictures are postable here as a carousel
          and there was no way to reach them. */}
      <AssetFilters
        values={filters}
        facets={postable}
        fields={["query", "mediaKind", "effect", "channel", "platform", "length"]}
        cleared={base}
        onChange={(next) => picker.setFilters(next)}
      />
      {/* What the picking has added up to, where the picking happens. It was
          only ever shown on the page behind this dialog, so the answer to "how
          many have I got" meant closing the thing you were counting with. */}
      {images && (
        <p className="picker-tally" role="status" aria-live="polite">
          {chosen.length === 0
            ? "Nothing picked yet."
            : `${chosen.length} picked, in swipe order.`}
          {capacity > 0 && (
            <span className={full ? "picker-tally-full" : undefined}>
              {full
                ? ` The tightest destination takes ${capacity}.`
                : ` Room for ${capacity - chosen.length} more.`}
            </span>
          )}
        </p>
      )}
      {failure && <p className="engine-warning" role="status">{failure}</p>}
      {!failure && !loading && !assets.length && (
        <p className="picker-empty">
          {images
            ? "No images matched. Import them in the Library tab, then pick them here."
            : "Nothing matched. Import media in the Library tab, then pick it here."}
        </p>
      )}
      <ul className="picker-results">
        {assets.map((asset) => {
          // Undefined when it is not in the carousel; its 1-based swipe
          // position when it is.
          const place = images && pathOf ? order.get(pathOf(asset)) : undefined;
          return (
          <li key={asset.id}>
            <button
              type="button"
              className="picker-result"
              // Pressed rather than selected: this is a toggle, and a screen
              // reader should say so before the tick is described.
              aria-pressed={images ? place !== undefined : undefined}
              data-picked={place === undefined ? undefined : true}
              // At capacity, only the ones already in can be touched - and
              // those only to come back out.
              disabled={images && full && place === undefined}
              title={images && full && place === undefined
                ? `The tightest destination takes ${capacity} images.`
                : undefined}
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
              {/* The number, not just a tick: the order is the post, and the
                  description above promises it is the order they are picked
                  in. A tick would confirm the click and still leave somebody
                  guessing where in the swipe it landed. */}
              {place !== undefined && (
                <span className="picker-order" aria-hidden="true">{place}</span>
              )}
            </button>
          </li>
          );
        })}
      </ul>
      {/* The count and the way to the rest. This dialog used to stop at its
          first page and never say so - a five-hundred-clip library showed
          forty rows and looked like forty was everything. */}
      {picker.canLoadMore && (
        <p className="picker-more">
          Showing {assets.length} of {picker.total.toLocaleString()} matching.
          <Button
            variant="quiet"
            size="sm"
            busy={picker.loading === "more"}
            onClick={() => void picker.loadMore()}
          >Load more</Button>
        </p>
      )}
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
  wantsCarousel,
  showsTitle: showsTitleProp,
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
  /**
   * Whether this network displays a title, when the caller knows.
   *
   * The engines' own limits table is the authority - a network has a title if
   * it has a title limit - and a caller holding those limits should say so
   * rather than let this file keep a second list that can drift from it. The
   * hardcoded fallback is for callers that have no limits to hand.
   */
  showsTitle?: boolean;
  /**
   * Whether this post is a carousel, which is not the same as having frames.
   *
   * Before any picture is chosen there are no frames to count, and the panel
   * still has to ask for the right thing: a post going out as a carousel needs
   * pictures, and telling somebody to choose a clip sends them to the wrong
   * control.
   */
  wantsCarousel?: boolean;
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
  const showsTitle = showsTitleProp
    ?? (platform === "youtube" || platform === "reddit" || platform === "pinterest");

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
        ) : thumbnail && !wantsCarousel ? (
          // The clip's own still, and only for a post that is a clip. A
          // carousel that has no pictures yet would otherwise show a frame of
          // whichever video was chosen before the post type changed - media
          // that is not going out, presented as though it were.
          // eslint-disable-next-line @next/next/no-img-element
          <img alt="" src={thumbnail} onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            measure(naturalWidth, naturalHeight);
          }} />
        ) : (
          <p>{wantsCarousel
            ? t("composer.choosePicturesForFrames")
            : t("composer.chooseClipForFrame")}</p>
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
  /** Library identity when known; path fallback keeps older jobs previewable. */
  assetId?: string | null;
  mediaPath?: string | null;
  /** The campaign this post belongs to, when it is a campaign's rather than a
      standalone one - so the rail can say which without opening it. */
  campaign?: { id: string; name: string };
};

function UpcomingThumbnail({
  entry,
  workspaceId,
  apiFetch,
}: {
  entry: CalendarEntry;
  workspaceId: string;
  apiFetch: Fetcher;
}) {
  const key = entry.assetId || entry.mediaPath || "";
  const [loaded, setLoaded] = useState<{ key: string; url: string } | null>(null);

  useEffect(() => {
    if (!key) return;
    let live = true;
    let objectUrl = "";
    const endpoint = entry.assetId
      ? `/api/workspaces/${workspaceId}/media/library/assets/${entry.assetId}/content/thumbnail`
      : `/api/workspaces/${workspaceId}/publishing/media/preview?thumbnail=true&path=${encodeURIComponent(entry.mediaPath ?? "")}`;
    apiFetch(endpoint)
      .then((response) => response.ok ? response.blob() : Promise.reject(new Error("unavailable")))
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (live) setLoaded({ key, url: objectUrl });
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, entry.assetId, entry.mediaPath, key, workspaceId]);

  const source = loaded?.key === key ? loaded.url : "";
  return (
    <span className="upcoming-thumb" aria-hidden="true">
      {source
        // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
        ? <img src={source} alt="" />
        : <ActionIcon name="play" />}
    </span>
  );
}
export type Slot = {
  id: string;
  weekday: number;
  weekday_label: string;
  hour: number;
  minute: number;
  time: string;
};
export type SlotPreset = {
  id: string;
  label: string;
  summary: string;
  kind: "builtin" | "custom";
  times: string[];
  slots: { weekday: number; time: string }[];
};

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WEEKDAY_NAMES = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
];
export const EVERY_DAY = -1;
/**
 * The groups a time can be added to in one go, past the single day.
 *
 * Buffer and Publer both offer these, and for the same reason: a rhythm is
 * rarely "Tuesday". It is every day, or the working week, or the weekend - and
 * building the working week one day at a time is five trips through the same
 * form to say one thing.
 *
 * `EVERY_DAY` stays its own stored value rather than expanding to seven rows:
 * it means "whatever the week turns out to be", and seven rows would have to
 * be edited seven times to change it back.
 */
const WEEKDAY_GROUPS: { value: string; label: string; days: number[] }[] = [
  { value: "weekdays", label: "Weekdays", days: [0, 1, 2, 3, 4] },
  { value: "weekends", label: "Weekends", days: [5, 6] },
];

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

/**
 * How a scheduled post reads, from its job's status.
 *
 * "queued" and "succeeded" are the words the job uses for itself, not ones a
 * reader recognises. A post waiting for its time is Scheduled, a fired one is
 * Published, and a failed one is Failed - the same vocabulary the campaign
 * timeline uses, so the two surfaces agree.
 */
export function scheduleLabel(
  state: string, at: Date,
): { label: string; tone: "good" | "warn" | "neutral" | "info" } {
  switch (state) {
    case "failed": return { label: "Failed", tone: "warn" };
    case "cancelled": return { label: "Cancelled", tone: "neutral" };
    case "planned": return { label: "Planned", tone: "neutral" };
    // Handed to the engine is not published: it stays Scheduled until its time
    // comes, and is Published only once it has passed - read against the clock,
    // not called done the moment the job returned. Scheduled gets its own tone
    // so the two do not look alike at a glance.
    case "succeeded":
      return at.getTime() <= Date.now()
        ? { label: "Published", tone: "good" }
        : { label: "Scheduled", tone: "info" };
    default: return { label: "Scheduled", tone: "info" };
  }
}

/**
 * The next occurrences of the configured slots, for a one-click row.
 *
 * `timeZone` is the workspace's, because that is the clock a slot's hour is
 * written on. This used to call `at.setHours(slot.hour, ...)`, which reads the
 * stored hour as the *reader's* wall clock - so a UTC workspace read from
 * Bangkok offered every slot seven hours before the scheduler would fire it,
 * and booking from the row put the post at a time no slot described. The
 * resulting moment is still shown in the reader's own zone; it is which moment
 * that was wrong, not how it was displayed.
 */
export function upcomingSlots(slots: Slot[], now: Date, timeZone: string, count = 5) {
  const found: { value: string; label: string; day: string }[] = [];
  for (let ahead = 0; ahead < 8 && found.length < count; ahead += 1) {
    const day = new Date(now.getTime() + ahead * 86_400_000);
    // Which day it is, and which weekday, in the workspace's zone rather than
    // the reader's - near midnight those disagree.
    const there = zonedParts(day, timeZone);
    for (const slot of slots) {
      if (found.length >= count) break;
      if (slot.weekday !== EVERY_DAY && slot.weekday !== there.weekday) continue;
      const at = zonedInstant(
        there.year, there.month, there.day, slot.hour, slot.minute, timeZone,
      );
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
  loadingCampaigns = false,
  workspaceId,
  apiFetch,
}: {
  entries: CalendarEntry[];
  /** This workspace's posting times, so a new post lands on one of them. */
  slots: Slot[];
  now: Date;
  days?: number;
  onPickDay: (at: Date) => void;
  onOpenCalendar?: () => void;
  /** True while campaign posts are still being gathered, so the strip can say
      it is not yet the whole picture rather than looking complete early. */
  loadingCampaigns?: boolean;
  workspaceId: string;
  apiFetch: Fetcher;
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
        {/* Says the count is not final yet while the campaigns are still being
            gathered, so a half-loaded strip does not read as the whole picture. */}
        {loadingCampaigns && (
          <span className="upcoming-loading" role="status">Adding campaigns…</span>
        )}
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
              <UpcomingThumbnail entry={entry} workspaceId={workspaceId} apiFetch={apiFetch} />
              <div className="upcoming-post">
                <strong>{entry.title || entry.label}</strong>
                <span className="upcoming-meta">
                  {(entry.platforms ?? []).map((platform) => (
                    <PlatformIcon key={platform} platform={platform} size={14} />
                  ))}
                  <Badge tone={scheduleLabel(entry.state, entry.at).tone}>
                    {scheduleLabel(entry.state, entry.at).label}
                  </Badge>
                  {/* Which campaign this post belongs to, when it is one, so a
                      campaign post is not mistaken for a standalone one. */}
                  {entry.campaign && (
                    <span className="upcoming-campaign" title={`Campaign: ${entry.campaign.name}`}>
                      {entry.campaign.name}
                    </span>
                  )}
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
  onCreatePreset,
}: {
  slots: Slot[];
  presets: SlotPreset[];
  timezone: string;
  canEdit: boolean;
  busy: boolean;
  onSave: (entries: { weekday: number; time: string }[]) => void;
  onCreatePreset: (label: string, entries: { weekday: number; time: string }[]) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState("");
  /** A weekday number, or one of the group values above. */
  const [weekday, setWeekday] = useState<string>(String(EVERY_DAY));
  const [presetName, setPresetName] = useState("");

  const entries = slots.map((slot) => ({ weekday: slot.weekday, time: slot.time }));
  const everyDay = slots.filter((slot) => slot.weekday === EVERY_DAY);

  /** Everything except this one, in the shape the save takes. */
  function withoutSlot(slot: Slot) {
    return entries.filter(
      (entry) => !(entry.weekday === slot.weekday && entry.time === slot.time),
    );
  }

  function add() {
    if (!draft) return;
    const group = WEEKDAY_GROUPS.find((item) => item.value === weekday);
    const days = group ? group.days : [Number(weekday)];
    // Whatever of the selection is not already there. Adding "weekdays" over a
    // week that already posts on Monday should add the other four rather than
    // refuse the lot, which is what checking the group as a unit would do.
    const wanted = days
      .filter((day) => !entries.some((entry) => entry.time === draft && entry.weekday === day))
      .map((day) => ({ weekday: day, time: draft }));
    if (!wanted.length) return;
    onSave([...entries, ...wanted]);
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
      {/* Every day first, on its own line: it applies to all seven columns,
          and repeating it under each of them would say seven times what it
          says once - and imply seven rows somebody could edit apart. */}
      {everyDay.length > 0 && (
        <div className="slot-week-everyday">
          <span>Every day</span>
          <ul>
            {everyDay.map((slot) => (
              <li key={slot.id}>
                <b>{slotLabel(slot)}</b>
                {canEdit && (
                  <button
                    type="button"
                    className="slot-remove"
                    aria-label={`Remove ${slotLabel(slot)} from every day`}
                    disabled={busy}
                    onClick={() => onSave(withoutSlot(slot))}
                  ><ActionIcon name="dismiss" /></button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {/* The week as a week. It was a flat list of every slot with its day
          written beside it, which answers "what times are set" and not "what
          does a Tuesday look like" - and the second is the question somebody
          opens this to ask. Both references it is modelled on show seven
          columns for the same reason. */}
      <div className="slot-week" role="group" aria-label={t("composer.postingTimes")}>
        {WEEKDAY_NAMES.map((name, day) => {
          const times = slots
            .filter((slot) => slot.weekday === day)
            .sort((left, right) => slotLabel(left).localeCompare(slotLabel(right)));
          return (
            <div className="slot-week-day" key={name}>
              {/* Short on the header, full in the label a reader hears. */}
              <h5 title={name}>{name.slice(0, 3)}</h5>
              <ul>
                {times.map((slot) => (
                  <li key={slot.id}>
                    <b>{slotLabel(slot)}</b>
                    {canEdit && (
                      <button
                        type="button"
                        className="slot-remove"
                        aria-label={`Remove ${slotLabel(slot)} on ${name}`}
                        disabled={busy}
                        onClick={() => onSave(withoutSlot(slot))}
                      ><ActionIcon name="dismiss" /></button>
                    )}
                  </li>
                ))}
                {/* A day with no times is a day nothing posts on, which is
                    worth seeing rather than inferring from an absence. */}
                {times.length === 0 && everyDay.length === 0 && (
                  <li className="slot-week-none" aria-label={`Nothing posts on ${name}`}>—</li>
                )}
              </ul>
            </div>
          );
        })}
      </div>
      {canEdit && (
        <div className="slot-add">
          <input
            type="time"
            value={draft}
            aria-label={t("composer.timeOfDay")}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Select
            value={weekday}
            aria-label={t("composer.repeats")}
            onChange={(event) => setWeekday(event.target.value)}
          >
            <option value={EVERY_DAY}>{t("composer.everyDay")}</option>
            {WEEKDAY_GROUPS.map((group) => (
              <option key={group.value} value={group.value}>{group.label}</option>
            ))}
            {WEEKDAY_NAMES.map((name, index) => (
              <option key={name} value={index}>{name} only</option>
            ))}
          </Select>
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
                onClick={() => onSave(preset.slots)}
              >
                <b>{preset.label}</b>
                <small>{preset.slots.map((entry) => (
                  entry.weekday === EVERY_DAY
                    ? entry.time
                    : `${DAY_NAMES[entry.weekday]} ${entry.time}`
                )).join(" · ")}</small>
              </button>
            ))}
          </div>
          <p>{t("composer.presetWarning")}</p>
          {slots.length > 0 && (
            <div className="slot-preset-save">
              <input
                value={presetName}
                maxLength={120}
                placeholder="Preset name"
                aria-label="Preset name"
                onChange={(event) => setPresetName(event.target.value)}
              />
              <Button
                variant="secondary"
                size="sm"
                disabled={busy || !presetName.trim()}
                onClick={() => {
                  onCreatePreset(presetName.trim(), entries);
                  setPresetName("");
                }}
              >Save current times</Button>
            </div>
          )}
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
