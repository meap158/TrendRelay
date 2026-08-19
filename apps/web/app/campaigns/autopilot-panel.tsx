"use client";

/**
 * A campaign that feeds accounts by itself.
 *
 * The panel is built around one question asked in order: will this post, where
 * will it post, what will it post, and what exactly happens next. The switch is
 * last to become available, not first, because the failure this design is
 * avoiding is an autopilot that is switched on and silently does nothing - the
 * same failure the engine cards were fixed for.
 *
 * So readiness is a checklist with a link out of each unmet row, the switch is
 * disabled with a reason until every row is met, and the preview shows the
 * actual captions and times before anything is created. Handing an account to a
 * scheduler should feel like delegating, not gambling.
 */

import { clipLength, handoffPath } from "../../lib/media-rules";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiBaseUrl } from "../../lib/api";

import { Button } from "../ui/button";
import { SegmentedControl } from "../ui/segmented";
import { ActionIcon } from "../ui/action-icons";
import { Dialog } from "../ui/dialog";
import { Badge, Card, Switch } from "../ui/primitives";
import { SearchSelect } from "../ui/search-select";
import { useT } from "../i18n-provider";
import { LOCALES } from "../../lib/i18n/locales";
import { EffectEditor } from "../library/effect-editor";
import { TimelinePlayer } from "./timeline-player";
import { accountIdentity, type EngineAccount } from "../publishing-account";
import { commissionLabel, type CommissionBearing } from "../commission";

/**
 * One affiliate product attached to a post, as the post carries it.
 *
 * The rate travels with the name: it is why this offer was attached rather than
 * another, and a list of product names alone cannot answer "is this post worth
 * it" without going to another screen.
 */
type AttachedProduct = CommissionBearing & { offer_id: string; name: string };
import {
  AssetFilters,
  EMPTY_FACETS,
  assetFilterParams,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import {
  AssetThumbnail,
  PostPreview,
  useAssetPoster,
  type Slot,
} from "../publish/composer";
import {
  FeatureReach,
  PlatformIcon,
  platformLabels,
  type PublishingPlatform,
} from "../publishing-icons";
import { followUpKind, followUpLabel, isThreadPlatform, takesFollowUp } from "../../lib/follow-up";

type Account = {
  id: string;
  platform: PublishingPlatform;
  label: string;
  provider: string;
  provider_label: string;
  /** Whose login this account is reached through, as the engine reports it. */
  connection_account?: EngineAccount;
  available?: boolean;
  unavailable_reason?: string | null;
};

type Destination = {
  id: string;
  provider: string;
  /** The login in words - "Buffer", or "Buffer · Client B" - not its stored id. */
  provider_label?: string;
  connection_account?: EngineAccount;
  /** Whether this login can post a gallery of pictures to this network. */
  accepts_carousel?: boolean;
  /** Whether a first comment or thread reply can be delivered here. */
  follow_up_deliverable?: boolean;
  /** Whether this network has a title field at all. */
  takes_title?: boolean;
  integration_id: string;
  platform: PublishingPlatform;
  label: string;
  enabled: boolean;
  /** What this account posts as - a Reel, a Story - where it has been set. */
  post_type?: string | null;
  /** The stored configuration; 'auto' lets the network decide. */
  link_placement_setting: "auto" | "caption" | "first_comment" | "bio";
  /** What the configuration resolves to today. */
  link_placement: "caption" | "first_comment" | "bio" | "none";
  link_reason: string;
};

type QueueItem = {
  id: string;
  asset_id?: string | null;
  video_path: string;
  image_paths: string[];
  /** The body is still the placeholder nobody wrote; the card says so. */
  needs_copy: boolean;
  title: string | null;
  body: string;
  hashtags: string[];
  first_comment: string | null;
  thread: string[];
  state: "draft" | "approved" | "paused" | "retired";
  position: number;
  times_posted: number;
  last_posted_at: string | null;
  offer_ids: string[];
  offer_match: {
    matches?: OfferMatch[];
    strategy?: MatchStrategy;
    selected_offer_ids?: string[];
  };
};

type Autopilot = {
  enabled: boolean;
  delivery: "draft" | "schedule" | "now";
  /** How much the campaign may do alone; run by exception is the default. */
  authority: "assist" | "auto_draft" | "run_by_exception" | "autonomous";
  /** What ranking optimises for. */
  priority: "reach" | "discussion" | "revenue" | "balanced";
  offer_id: string | null;
  offer_mode: "smart" | "manual" | "none";
  candidate_offer_ids: string[];
  max_products_per_post: number;
  disclosure: string;
  bio_hint: string;
  min_recycle_days: number;
  daily_cap_per_account: number;
  weekly_post_cap: number | null;
  /** The language the composed scaffolding speaks. */
  post_language: string;
  posts_scheduled: number;
  last_run_at: string | null;
  last_note: string | null;
  destinations: number;
  queue_total: number;
  queue_approved: number;
  /**
   * Approved *and* written, which is what can actually go out.
   *
   * Items arrive approved - approval is the authority dial's business rather
   * than a form's - so `queue_approved` says only that nobody has parked it.
   */
  queue_ready: number;
};

type HeldExecution = {
  id: string;
  /** The queue package this was frozen from, to reach its products from here. */
  queue_item_id: string | null;
  scheduled_at: string | null;
  destination_label: string | null;
  platform: string | null;
  caption: string;
  title: string | null;
  held_reason: string | null;
  reason: string;
  /** The frozen post itself, so approval judges the post, not a summary. */
  first_comment: string | null;
  thread: string[];
  media_path: string | null;
  image_paths: string[];
  post_type: string | null;
  placement: string | null;
  /** The exact Library version this was frozen against, for its still. */
  asset_id?: string | null;
};

type PreviewPost = {
  destination_id: string;
  queue_item_id: string;
  at: string;
  title: string | null;
  asset_id: string | null;
  caption: string;
  first_comment: string | null;
  thread: string[];
  offer_ids: string[];
  products: string[];
  product_details: AttachedProduct[];
  destination: {
    label: string;
    platform: PublishingPlatform;
    provider: string;
    post_type: string | null;
  } | null;
  placement: string;
  reason: string;
  /** What the delivering engine would refuse this post for, if anything. */
  problem: string | null;
};

type Offer = {
  id: string;
  network: string;
  affiliate_url: string;
  commission_bps?: number | null;
  commission_flat_cents?: number | null;
  currency?: string | null;
  product: {
    name: string;
    brand?: string | null;
    marketplace?: string | null;
  };
};

/**
 * One row of the campaign timeline, whichever half it came from.
 *
 * Deliberately flat and fully populated: every field is present on every row,
 * null where that half has nothing to say. A union would be more precise and
 * would push the choosing into the markup, which is exactly where the two
 * lists grew apart the first time.
 */
type TimelineEntry = {
  key: string;
  kind: "delivered" | "planned";
  /**
   * The queued post this one came from, where the plan knows it.
   *
   * A scheduled post is one outing of a post that repeats, and the schedule
   * named neither the post nor how to find it - so the two tabs read as two
   * unrelated lists rather than two ends of one pipeline.
   */
  queue_item_id?: string | null;
  at: string;
  title: string | null;
  caption: string;
  first_comment: string | null;
  thread: string[];
  destination: PreviewPost["destination"];
  destination_id: string | null;
  /** Delivered rows only: what the engine did with it. */
  status: DeployedPost["status"] | null;
  delivery: DeployedPost["delivery"] | null;
  post_url: string | null;
  page_url: string | null;
  video_path: string | null;
  image_paths: string[];
  last_error: string | null;
  asset_id: string | null;
  /** Planned rows only: what it will carry and why it was chosen. */
  problem: string | null;
  offer_ids: string[];
  product_details: AttachedProduct[];
  placement: string | null;
  reason: string | null;
  route: { label: string; detail: string } | null;
};

type DeployedPost = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  at: string;
  title: string | null;
  caption: string;
  first_comment: string | null;
  thread: string[];
  delivery: "draft" | "schedule" | "now";
  queue_item_id: string | null;
  /** Resolved through the queue item, for the row's thumbnail. */
  asset_id: string | null;
  destination_id: string | null;
  destination: PreviewPost["destination"];
  last_error: string | null;
  created_at: string;
  updated_at: string;
  /** The live post, when the engine reported a permalink. */
  post_url: string | null;
  /** The account's own page, when its label is handle-shaped. */
  page_url: string | null;
  video_path: string | null;
  image_paths: string[];
};

type OfferMatch = {
  offer_id: string;
  product_id: string;
  product_name: string;
  score: number;
  confidence: "high" | "medium" | "low";
  matched_terms: string[];
  reasons: string[];
  evidence_sources: string[];
  network: string;
  availability: string;
  commission_bps?: number | null;
  commission_flat_cents?: number | null;
  currency: string;
};

type MatchStrategy = {
  offer_mode: string;
  candidate_scope: string;
  evidence_sources: string[];
  media?: { media_kind?: string | null; duration_ms?: number | null; creative_format?: string | null };
  platforms: string[];
  post_types: string[];
  posting_slots: number;
  posts_scheduled: number;
  recommended_products_per_post: number;
  rotation: string;
  selection?: string;
};

type Recommendations = {
  item_id: string | null;
  matches: OfferMatch[];
  /**
   * Which of the matches would actually attach, as the scheduler resolves it.
   *
   * Not the same as the top of the ranking: the resolver drops low confidence,
   * caps the count, and honours pins and campaign mode over the scores. Sent by
   * the API rather than worked out here, so the panel cannot come to disagree
   * with what posts.
   */
  chosen_offer_ids?: string[];
  strategy: MatchStrategy;
};

function offerDescription(offer: Offer): string {
  // Through the shared formatter, which knows what a minor unit is worth. This
  // divided flat commission by a hundred whatever the currency was - the same
  // slip already found and fixed in Attribution's own formatter, where a 95,000
  // dong commission showed as ₫950. A hundred times too small and plausible
  // enough on screen to go unquestioned.
  const paid = commissionLabel(offer);
  return [
    offer.product.marketplace,
    offer.network,
    paid ? `${paid} commission` : null,
  ].filter(Boolean).join(" · ");
}

//: How many packages are sent at once. One SQLite file takes writes in series
//: whatever the client does, so a hundred parallel posts only queue up inside
//: the API; a small batch keeps the browser responsive and the database calm.
const QUEUE_BATCH = 8;

/** What one queued package is written with, before it is sent. */
type PostCopy = { body: string; hashtags: string };

/**
 * One row in the composer: the media it posts, and the copy written for it.
 *
 * A carousel is one package holding several pictures, so the assets are a list
 * rather than one - and `id` keys the copy, which is why it is the lead asset's
 * id and not the index. Reordering rows must not move somebody's caption onto
 * another clip.
 */
type DraftPost = {
  id: string;
  kind: "video" | "carousel" | "image";
  assets: LibraryAsset[];
};

type LibraryAsset = {
  id: string;
  title: string;
  original_path: string;
  media_kind: string;
  /**
   * What the clip was posted with where it came from.
   *
   * Shown beside the copy as reference and never seeded into it: these are
   * Douyin captions in Chinese and the campaign posts in Vietnamese, so
   * pre-filling would put the wrong language into the post - and machine
   * translation of an idiom is a draft nobody asked for. It is here to remind
   * the writer what the clip is about.
   */
  caption?: string | null;
  hashtags?: string[] | null;
  duration_ms: number | null;
  platform: string | null;
  creator: string | null;
  width: number | null;
  height: number | null;
  versions: { id: string; kind: string; path?: string }[];
};

/** Seconds, rounded, for a clip length nobody needs to the millisecond. */
async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Autopilot request failed.");
  return body;
}

/** A word for where the link lands, and the tone that matches its meaning. */
function placementTone(placement: string): "good" | "neutral" | "warn" {
  if (placement === "caption") return "good";
  if (placement === "none") return "neutral";
  return "warn";
}

/**
 * The timeline as a month, the way Buffer and Zernio show the same posts.
 *
 * The list answers "what exactly went out"; the calendar answers "how does
 * the month look" - cadence, gaps, and pile-ups - which no list can show.
 * Same entries, same statuses, different question.
 */
function TimelineCalendar({
  entries,
  timezone,
  month,
  onMonthChange,
}: {
  entries: TimelineEntry[];
  timezone: string;
  /** The month on display, as YYYY-MM in the schedule's own timezone. */
  month: string;
  onMonthChange: (next: string) => void;
}) {
  const byDay = entries.reduce<Record<string, TimelineEntry[]>>((days, entry) => {
    const key = new Date(entry.at).toLocaleDateString("en-CA", { timeZone: timezone });
    (days[key] ??= []).push(entry);
    return days;
  }, {});
  const [year, monthNumber] = month.split("-").map(Number);
  const first = new Date(Date.UTC(year, monthNumber - 1, 1));
  const daysInMonth = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
  const cells: (string | null)[] = [
    ...Array.from({ length: first.getUTCDay() }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) =>
      `${year}-${String(monthNumber).padStart(2, "0")}-${String(index + 1).padStart(2, "0")}`),
  ];
  while (cells.length % 7) cells.push(null);
  const today = new Date().toLocaleDateString("en-CA", { timeZone: timezone });
  const monthLabel = first.toLocaleDateString(undefined, {
    month: "long", year: "numeric", timeZone: "UTC",
  });
  const step = (delta: number) => {
    const moved = new Date(Date.UTC(year, monthNumber - 1 + delta, 1));
    onMonthChange(
      `${moved.getUTCFullYear()}-${String(moved.getUTCMonth() + 1).padStart(2, "0")}`,
    );
  };
  const chipTone = (entry: TimelineEntry) =>
    entry.kind === "delivered"
      ? `delivered ${entry.status ?? ""}`.trim()
      : entry.problem ? "refused" : "planned";

  return (
    <div className="campaign-calendar" role="grid" aria-label={`Posts in ${monthLabel}`}>
      <header className="campaign-calendar-head">
        <strong>{monthLabel}</strong>
        <span>
          <Button variant="quiet" size="sm" onClick={() => step(-1)}
            aria-label="Earlier month">&#8249;</Button>
          <Button variant="quiet" size="sm"
            onClick={() => onMonthChange(today.slice(0, 7))}>Today</Button>
          <Button variant="quiet" size="sm" onClick={() => step(1)}
            aria-label="Later month">&#8250;</Button>
        </span>
      </header>
      <div className="campaign-calendar-weekdays" aria-hidden="true">
        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((name) => (
          <span key={name}>{name}</span>
        ))}
      </div>
      <div className="campaign-calendar-grid">
        {cells.map((key, index) => {
          const dayEntries = key ? byDay[key] ?? [] : [];
          return (
            <div
              key={key ?? `blank-${index}`}
              className={[
                "campaign-calendar-cell",
                key ? "" : "blank",
                key === today ? "today" : "",
              ].filter(Boolean).join(" ")}
            >
              {key && <em>{Number(key.slice(8))}</em>}
              {dayEntries.slice(0, 3).map((entry) => (
                <span key={entry.key} className={`campaign-calendar-chip ${chipTone(entry)}`}
                  title={displayTitle(entry.title) ?? entry.caption.slice(0, 80)}>
                  <b>{new Date(entry.at).toLocaleTimeString(undefined, {
                    hour: "2-digit", minute: "2-digit", hour12: false,
                    timeZone: timezone,
                  })}</b>
                  {entry.destination?.platform && (
                    <PlatformIcon platform={entry.destination.platform} size={12} />
                  )}
                  <span>{displayTitle(entry.title) || entry.caption.slice(0, 40) || "Post"}</span>
                </span>
              ))}
              {dayEntries.length > 3 && (
                <small className="campaign-calendar-more">+{dayEntries.length - 3} more</small>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** A media title without its file extension: ".mp4" tells a reader nothing
    the thumbnail beside it does not, and reads like a path rather than a
    post. Only the display is trimmed; the stored title keeps its name. */
function displayTitle(value: string | null): string | null {
  return value ? value.replace(/\.(mp4|mov|webm|mkv|avi|jpg|jpeg|png|webp)$/i, "") : value;
}

/* Which queue package a held post was frozen from, so approval traces back to
   the row in "What it posts" instead of floating free of it. The frozen title
   matches that row's own name; its clip's filename is the fallback when the
   post carries no title (a Threads post, say). */
function heldSourceName(item: HeldExecution): string | null {
  const titled = displayTitle(item.title);
  if (titled && titled.trim()) return titled.trim();
  if (item.media_path) {
    const base = item.media_path.split(/[\\/]/).pop() ?? "";
    const trimmed = displayTitle(base);
    if (trimmed && trimmed.trim()) return trimmed.trim();
  }
  return null;
}

/* The browser reports a dead connection as the subjectless "Failed to
   fetch"; the person reading the toast needs to know it was the local API
   that did not answer, not which browser API gave up. */
function explainFailure(reason: unknown, fallback: string): string {
  if (!(reason instanceof Error)) return fallback;
  return reason.message === "Failed to fetch"
    ? "The local API did not answer. If it is restarting, retry in a moment."
    : reason.message;
}

function placementSummary(post: PreviewPost): { label: string; detail: string } {
  if (!post.offer_ids.length) {
    return { label: "Organic post", detail: "No affiliate product or tracked link is attached." };
  }
  if (post.placement === "bio") {
    return {
      label: "Profile bio",
      detail: "The caption points people to the profile bio; the clickable product link lives there.",
    };
  }
  if (post.placement === "first_comment") {
    // The network's own word for it. On Threads and the other thread networks
    // there is no comment box separate from the thread - the reply *is* the
    // next post - so a heading reading "First comment" over a link that will
    // arrive as a reply describes something the reader never sees. The chips
    // beside it already said the right thing, which made this the one line on
    // the entry that disagreed with the rest.
    const kind = followUpKind(post.destination?.platform);
    return {
      label: followUpLabel(post.destination?.platform, 0),
      detail: `The post publishes first, then the tracked product link follows it as ${
        kind === "reply in the thread" ? "a reply in the thread" : "its first comment"}.`,
    };
  }
  if (post.thread.length) {
    return {
      label: "Post + replies",
      detail: `The primary product is in the post and ${post.thread.length} additional product ${post.thread.length === 1 ? "link is" : "links are"} published as replies.`,
    };
  }
  return {
    label: "Post content",
    detail: "The tracked product link is included directly in the caption or description.",
  };
}

function dayHeading(value: string, timeZone: string): string {
  const date = new Date(value);
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const dateOptions = { timeZone };
  const key = date.toLocaleDateString("en-CA", dateOptions);
  const prefix = key === today.toLocaleDateString("en-CA", dateOptions)
    ? "Today"
    : key === tomorrow.toLocaleDateString("en-CA", dateOptions)
      ? "Tomorrow"
      : date.toLocaleDateString(undefined, { weekday: "long", timeZone });
  return `${prefix} · ${date.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone })}`;
}

/**
 * What this post goes out as on this account.
 *
 * The account's configured post type when it has one - a Reel and a Story are
 * different posts made from the same clip - and otherwise the shape of the
 * media itself, which is the honest answer when the network has only one kind
 * of post.
 */
function formatName(post: PreviewPost, item: QueueItem): string {
  return namedFormat(post.destination?.post_type, item);
}

/** The same answer for an account the plan has not reached. */
function destinationFormat(destination: Destination, item: QueueItem): string {
  return namedFormat(destination.post_type ?? null, item);
}

function namedFormat(configured: string | null | undefined, item: QueueItem): string {
  if (configured) {
    return configured.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
  }
  if (item.video_path) return "Video";
  if (item.image_paths.length > 1) return `Carousel · ${item.image_paths.length}`;
  if (item.image_paths.length === 1) return "Image";
  return "Post";
}


/**
 * A held post, shown as the post it is rather than as a grey rectangle.
 *
 * The player is gated - the clip is read only when somebody presses play, so
 * a page of held posts does not pull a dozen videos off disk - and a gated
 * player with no poster draws nothing at all. Which is the one thing an
 * approval screen cannot afford: the question being asked is "is this the
 * right video", and the answer was a blank.
 *
 * Its own component because the still is fetched by a hook, and a hook cannot
 * be called from inside the list's map.
 */
function HeldPreview({
  item,
  workspaceId,
  apiFetch,
}: {
  item: HeldExecution;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  const poster = useAssetPoster(item.asset_id, workspaceId, apiFetch);
  const media = (path: string) =>
    `${apiBaseUrl()}/api/workspaces/${workspaceId}/publishing/media/preview`
    + `?path=${encodeURIComponent(path)}`;
  return (
    <PostPreview
      platform={(item.platform ?? "tiktok") as PublishingPlatform}
      postTypeLabel={item.post_type ?? "Post"}
      handle={item.destination_label ?? ""}
      caption={item.caption}
      title={item.title ?? ""}
      thumbnail={poster}
      source={item.media_path ? media(item.media_path) : undefined}
      carousel={item.image_paths.length ? item.image_paths.map(media) : undefined}
      sourceIsImage={!item.media_path && item.image_paths.length > 0}
    />
  );
}

/**
 * Every outing a queued post has ahead of it, rehearsed.
 *
 * The row used to say "Next Thu 09:00 on halcyonbooks" and stop, which answers
 * one of the four questions somebody has before approving: when, where, what it
 * reads like there, and what follows it. A post going to Threads and to
 * Instagram is two different posts - different caption length, and text after
 * it that is a reply on one and a comment on the other - and the row showed
 * neither. Read from the same preview the Schedule tab renders, so the two
 * cannot disagree.
 */
function QueueRehearsal({
  item,
  outings,
  workspaceId,
  apiFetch,
  noPlanReason,
  destinations,
  slots,
}: {
  item: QueueItem;
  outings: PreviewPost[];
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  /** Why this post has no outings, when it has none. */
  noPlanReason: string;
  /** The campaign's accounts, for a row the plan has not reached. */
  destinations: Destination[];
  /** The campaign's posting times, for the same reason. */
  slots: Slot[];
}) {
  // The clip's own frame, for the gated player below. Read here rather than in
  // the branch that uses it, because a hook cannot run only sometimes.
  const poster = useAssetPoster(item.asset_id, workspaceId, apiFetch);
  // Where and when, even with nothing scheduled. A row with no plan used to
  // say only why, and the two questions somebody actually has - which accounts
  // is this for, and when does this campaign post - are answered by the
  // campaign itself rather than by the plan. No times are invented here: these
  // are the campaign's own posting times and its own accounts, said plainly as
  // what this post is waiting on rather than as a schedule it has been given.
  if (!outings.length) {
    return (
      <details className="campaign-row-rehearsal">
        {/* The reason on the line, the detail behind it. A boxed paragraph
            with a list of accounts under it turned every unscheduled row into
            a panel, and there are usually several of them. */}
        <summary>
          <span>Where it would go</span>
          <small>{noPlanReason}</small>
        </summary>
        <div className="campaign-rehearsal-list">
          {destinations.map((destination) => (
            <p key={destination.id} className="campaign-would-go">
              <PlatformIcon platform={destination.platform} size={16} />
              <b>{destination.label}</b>
              <span>{platformLabels[destination.platform]}
                {" · "}{destinationFormat(destination, item)}
                {" · "}link {destination.link_placement === "bio"
                  ? "in the profile"
                  : destination.link_placement === "first_comment"
                    ? `in the ${followUpKind(destination.platform)}`
                    : destination.link_placement === "none"
                      ? "not carried"
                      : "in the caption"}</span>
            </p>
          ))}
          {slots.length > 0 && (
            <p className="campaign-would-when">
              This campaign posts at {slots.map(
                (slot) => `${slot.weekday_label} ${slot.time}`).join(", ")}.
            </p>
          )}
          {!destinations.length && (
            <p className="campaign-would-when">No accounts on this campaign yet.</p>
          )}
        </div>
      </details>
    );
  }
  const media = (path: string) =>
    `${apiBaseUrl()}/api/workspaces/${workspaceId}/publishing/media/preview?path=${encodeURIComponent(path)}`;
  const when = (value: string) => new Date(value).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
  return (
    <details className="campaign-row-rehearsal">
      <summary>
        <span>Goes out {outings.length}×</span>
        {/* The first two spelled out, the rest counted. Enough to recognise the
            plan without the summary line wrapping to three rows. */}
        <small>{outings.slice(0, 2).map((post) =>
          `${when(post.at)} · ${post.destination
            ? platformLabels[post.destination.platform] : "Account"}`
            + ` · ${formatName(post, item)}`).join("  ·  ")}
          {outings.length > 2 && `  ·  +${outings.length - 2} more`}</small>
      </summary>
      <div className="campaign-rehearsal-list">
        {outings.map((post) => {
          const platform = post.destination?.platform;
          const followUps = [
            ...(post.first_comment ? [post.first_comment] : []),
            ...post.thread,
          ];
          return (
            <article key={`${post.destination_id}-${post.at}`}>
              {/* The time and where the link lands - the two things the post
                  preview below cannot show. It already draws the account, the
                  network and the format under its own handle, so repeating
                  them here said the same thing twice in two type sizes. */}
              <header>
                {!platform && <strong>{post.destination?.label ?? "Account"}</strong>}
                <small>{when(post.at)}</small>
                <Badge tone={placementTone(post.placement)}>
                  {placementSummary(post).label}
                </Badge>
              </header>
              {/* Said before the caption, not after it: this one is not going
                  anywhere, and reading the copy first wastes the reader's time. */}
              {post.problem && (
                <p className="autopilot-refusal" role="status">
                  <strong>Would be refused.</strong> {post.problem}
                </p>
              )}
              {platform && (
                <PostPreview
                  platform={platform}
                  // The destination's own answer, from the engines' limits.
                  showsTitle={destinations.find(
                    (account) => account.id === post.destination_id)?.takes_title}
                  postTypeLabel={formatName(post, item)}
                  handle={post.destination?.label ?? ""}
                  caption={post.caption}
                  title={post.title ?? ""}
                  thumbnail={poster}
                  source={item.video_path
                    ? media(item.video_path)
                    : item.image_paths[0] ? media(item.image_paths[0]) : undefined}
                  sourceIsImage={!item.video_path && item.image_paths.length > 0}
                  carousel={item.image_paths.length > 1
                    ? item.image_paths.map(media) : undefined}
                  wantsCarousel={item.image_paths.length > 1}
                />
              )}
              {/* Named for the network it lands on. On Threads there is no
                  comment box separate from the thread, so calling this a first
                  comment describes something the reader will never see. */}
              {followUps.map((text, index) => (
                <div key={`follow-${index}`} className="campaign-rehearsal-follow">
                  <strong>{followUpLabel(platform, index)}</strong>
                  <pre>{text}</pre>
                </div>
              ))}
              {!followUps.length && takesFollowUp(platform) && (
                <p className="campaign-rehearsal-follow empty">
                  Nothing follows the post here, though this account takes a
                  {" "}{followUpKind(platform)}.
                </p>
              )}
              {post.product_details.length > 0 && (
                <ul className="campaign-pipeline-products" aria-label="Attached products">
                  {post.product_details.map((product, index) => (
                    <li key={`${product.offer_id}-${index}`}>
                      <span aria-hidden="true">{index + 1}</span>
                      <strong>{product.name}</strong>
                      <small>{placementSummary(post).label}
                        {commissionLabel(product) ? ` · ${commissionLabel(product)}` : ""}</small>
                    </li>
                  ))}
                </ul>
              )}
            </article>
          );
        })}
      </div>
    </details>
  );
}

export function AutopilotPanel({
  workspaceId,
  campaignId,
  campaignStatus,
  canEdit,
  apiFetch,
  succeed,
  fail,
  onCampaignChanged,
}: {
  workspaceId: string;
  campaignId: string;
  campaignStatus: string;
  canEdit: boolean;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  succeed: (message: string) => void;
  fail: (message: string) => void;
  onCampaignChanged: () => Promise<void>;
  /** Hand-planned posts, rendered inside the posting timeline so what will
      post and what has posted is one story in one place. */
}) {
  const t = useT();
  const [autopilot, setAutopilot] = useState<Autopilot | null>(null);
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(new Set());
  const [offers, setOffers] = useState<Offer[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendations | null>(null);
  const [productItem, setProductItem] = useState<QueueItem | null>(null);
  const [pinnedOffers, setPinnedOffers] = useState<Set<string>>(new Set());
  const [slots, setSlots] = useState<Slot[]>([]);
  const [scheduleTimezone, setScheduleTimezone] = useState("UTC");
  /**
   * Every moment on this page is read on the workspace's clock.
   *
   * One clock, chosen in the toolbar, so a slot's hour and the time a post
   * appears at are the same kind of thing. This briefly read on the browser's
   * clock instead, which was the right answer while the workspace zone was a
   * column nobody could set: it defaulted to UTC, and an operator seven hours
   * away could only be shown their own. Now that it is a visible setting, the
   * two agree by being one.
   */
  const readerZone = scheduleTimezone;
  const [preview, setPreview] = useState<
    { note: string; posts: PreviewPost[]; deployed: DeployedPost[]; problems: number } | null
  >(null);
  /** Posts the authority rules deferred to a person, reason attached. */
  const [exceptions, setExceptions] = useState<HeldExecution[]>([]);
  const [busy, setBusy] = useState("");
  const [adding, setAdding] = useState(false);
  const [library, setLibrary] = useState<LibraryAsset[]>([]);
  const [libraryFacets, setLibraryFacets] = useState<AssetFacets>(EMPTY_FACETS);
  // No media-kind filter to begin with: a campaign can post a clip or a
  // carousel, so the picker opens on everything and the filter row above it is
  // there for narrowing down. Starting on "video" was what made pictures
  // invisible even after the queue learned to hold them.
  const [libraryFilters, setLibraryFilters] = useState<AssetFilterValues>({});
  const [libraryTotal, setLibraryTotal] = useState(0);
  /** The clips sharing this campaign copy. Empty when the composer is closed. */
  const [drafting, setDrafting] = useState<LibraryAsset[]>([]);
  // The product decision is part of the package, made when it is added:
  // smart matching by default, or offers pinned by hand - so what gets
  // approved later is a post whose products were already decided.
  const [draftProductMode, setDraftProductMode] = useState<"smart" | "manual">("smart");
  const [draftPinned, setDraftPinned] = useState<Set<string>>(new Set());
  const [draftMatches, setDraftMatches] = useState<OfferMatch[] | null>(null);
  /**
   * The products that fit each clip, per row, matched before anything queues.
   *
   * Fetched for the whole selection in one call as the composer opens, so the
   * writer sees what would attach while choosing rather than after approving.
   * Keyed by asset because that is what was scored; a carousel is answered for
   * by its cover, which is the picture the row is named after.
   */
  const [rowMatches, setRowMatches] = useState<Record<string, OfferMatch[]>>({});
  /**
   * What the current selection will become, in the words used to describe it.
   *
   * The rule is one sentence long on purpose: a video is a post, and pictures
   * chosen together are one carousel. Anything cleverer - per-picture posts,
   * mixed packages - is a rule somebody has to be told rather than one they
   * can see, and this is the screen where a wrong guess becomes real posts.
   */
  const draftingSplit = {
    // Both named rather than one being "whatever is left". Audio never reaches
    // the picker, but reading videos as "not an image" is the kind of rule that
    // turns a sound file into a video post the first time one slips through.
    videos: drafting.filter((asset) => asset.media_kind === "video"),
    images: drafting.filter((asset) => asset.media_kind === "image"),
  };
  const draftingPosts = draftingSplit.videos.length + (draftingSplit.images.length ? 1 : 0);
  /**
   * Copy is per package, not per batch.
   *
   * One caption used to be typed once and spread across every selected clip,
   * so picking five videos made five posts word for word identical - which is
   * the one thing a hundred clips must not be. Keyed by package so a row keeps
   * what was written for it while its neighbours are edited.
   */
  const [draftCopy, setDraftCopy] = useState<Record<string, PostCopy>>({});
  /**
   * Whether the selected pictures ride together.
   *
   * One carousel is what multi-selecting pictures usually means, so it is the
   * default; splitting turns them into a post each. Videos are never grouped -
   * two clips are two posts on every network here.
   */
  const [splitPictures, setSplitPictures] = useState(false);
  /**
   * The rows, derived rather than stored.
   *
   * Held as a derivation of the selection so adding or dropping media cannot
   * leave a row pointing at an asset that is no longer chosen. The copy is
   * stored separately and keyed by package id, which survives that.
   */
  const draftPosts: DraftPost[] = [
    ...draftingSplit.videos.map((asset) => ({
      id: asset.id, kind: "video" as const, assets: [asset],
    })),
    ...(splitPictures
      ? draftingSplit.images.map((asset) => ({
          id: asset.id, kind: "image" as const, assets: [asset],
        }))
      : draftingSplit.images.length
        ? [{
            id: draftingSplit.images[0].id,
            kind: "carousel" as const,
            assets: draftingSplit.images,
          }]
        : []),
  ];
  const copyFor = (id: string): PostCopy =>
    draftCopy[id] ?? { body: "", hashtags: "" };
  const setCopyFor = (id: string, patch: Partial<PostCopy>) =>
    setDraftCopy((current) => ({
      ...current, [id]: { ...copyFor(id), ...patch },
    }));
  const [selectedAssets, setSelectedAssets] = useState<Record<string, LibraryAsset>>({});
  const [effectOpen, setEffectOpen] = useState(false);
  const [editing, setEditing] = useState<QueueItem | null>(null);
  const [editingReplies, setEditingReplies] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  // Two panes: Posts is what goes out, Queue & setup is everything behind it.
  //
  // It was three, split from one endless scroll so each section could say what
  // it was for. That was right, and one line too far: getting a campaign live
  // needs accounts and content, and the readiness checklist threw the operator
  // between two tabs to satisfy one job. They are now two columns of one pane -
  // the queue wide, the settings beside it - so the checklist scrolls instead
  // of switching, and neither is a scroll away from the other.
  //
  // Approvals stay above the panes: the one thing that must never hide. The old
  // `accounts`/`settings` names survive in the readiness rows, which is why
  // `jumpTo` translates them.
  const [view, setView] = useState<"posts" | "content">(
    campaignStatus === "active" ? "posts" : "content",
  );
  const searchTimer = useRef<number | null>(null);
  const automaticPreview = useRef(false);
  // The references this mirrors (Buffer, Zernio) offer the same posts as a
  // list and as a calendar; the list answers "what went out", the calendar
  // answers "how does the month look". Null month means the current one.
  const [timelineView, setTimelineView] = useState<"list" | "calendar">("list");
  const [calendarMonth, setCalendarMonth] = useState<string | null>(null);
  // The held post being rewritten before its decision, if any.
  const [editingHeld, setEditingHeld] = useState<HeldExecution | null>(null);

  const base = `/api/workspaces/${workspaceId}/campaigns/${campaignId}`;
  /** The same media the Publish composer plays, streamed from the same roots. */
  const previewMediaUrl = (path: string) =>
    `${apiBaseUrl()}/api/workspaces/${workspaceId}/publishing/media/preview`
    + `?path=${encodeURIComponent(path)}`;

  const refresh = useCallback(async () => {
    const body = await json<{
      autopilot: Autopilot; destinations: Destination[]; queue: QueueItem[];
    }>(await apiFetch(`${base}/autopilot`));
    setAutopilot(body.autopilot);
    setDestinations(body.destinations);
    setQueue(body.queue);
  }, [apiFetch, base]);

  const loadExceptions = useCallback(async () => {
    try {
      const body = await json<{ exceptions: HeldExecution[] }>(
        await apiFetch(`${base}/autopilot/exceptions`),
      );
      setExceptions(body.exceptions);
    } catch {
      // The inbox is supplementary; a failed read leaves the last answer.
    }
  }, [apiFetch, base]);


  useEffect(() => {
    queueMicrotask(() => {
      void refresh().catch((reason) =>
        fail(explainFailure(reason, "Autopilot unavailable.")));
      // Everything the readiness check needs, loaded once. Each of these is a
      // different subsystem, and the point of the checklist is that it names
      // which one is missing rather than reporting a single blank "not ready".
      void apiFetch(`/api/workspaces/${workspaceId}/publishing/slots`)
        .then((response) => json<{ slots: Slot[]; timezone: string }>(response))
        .then((body) => {
          setSlots(body.slots);
          setScheduleTimezone(body.timezone || "UTC");
        })
        .catch(() => { setSlots([]); });
      void apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`)
        .then((response) => json<{ offers: Offer[] }>(response))
        .then((body) => setOffers(body.offers))
        .catch(() => setOffers([]));
    });
  }, [refresh, apiFetch, workspaceId, fail]);

  useEffect(() => () => {
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
  }, []);

  // The inbox is the panel's front door now - approving held posts is the
  // operator's recurring job - so it loads with the page rather than behind
  // a pane. Deferred out of the effect body, like the initial refresh.
  useEffect(() => {
    queueMicrotask(() => {
      void loadExceptions();
    });
  }, [loadExceptions]);

  async function decideException(
    executionId: string,
    action: "approve" | "dismiss",
    { publishNow = false } = {},
  ) {
    setBusy(`${action}-${executionId}`);
    try {
      const response = await apiFetch(
        `${base}/autopilot/executions/${executionId}/${action}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(
            action === "approve"
              ? { confirm_external_action: true, publish_now: publishNow }
              : {},
          ),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "The decision was refused.");
      succeed(action === "dismiss"
        ? "Skipped this cycle. Its slot and clip are free, and the post returns next cycle."
        : publishNow
          ? "Approved and publishing now."
          : "Approved. The post is queued exactly as you approved it.");
      await loadExceptions();
    } catch (reason) {
      fail(explainFailure(reason, "The decision was refused."));
    } finally {
      setBusy("");
    }
  }

  /** Save the operator's rewrite of a held post; approval still covers it. */
  async function saveHeldEdit(
    executionId: string,
    edit: {
      title: string | null;
      caption: string;
      first_comment: string | null;
      thread: string[];
    },
  ) {
    setBusy(`edit-held-${executionId}`);
    try {
      const response = await apiFetch(
        `${base}/autopilot/executions/${executionId}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(edit),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "The edit was refused.");
      succeed("Saved. What you approve is what you wrote.");
      setEditingHeld(null);
      await loadExceptions();
    } catch (reason) {
      fail(explainFailure(reason, "The edit was refused."));
    } finally {
      setBusy("");
    }
  }

  async function loadAccounts() {
    setBusy("accounts");
    try {
      const body = await json<{ accounts: Account[] }>(await apiFetch(
        `/api/workspaces/${workspaceId}/publishing/integrations/all`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      ));
      setAccounts(body.accounts);
      setSelectedAccounts(new Set());
      setAdding(true);
    } catch (reason) {
      fail(explainFailure(reason, "Could not load accounts."));
    } finally {
      setBusy("");
    }
  }

  async function loadLibrary(filters: AssetFilterValues = libraryFilters) {
    setBusy("library");
    try {
      // Videos and pictures both: a campaign can post a carousel now, and a
      // picker that only offers clips cannot express one. Still only what the
      // library considers ready - the queue posts unattended, so an asset mid
      // processing has no business in it.
      const params = assetFilterParams(filters);
      params.set("limit", "100");
      const body = await json<{
        assets: LibraryAsset[]; facets?: AssetFacets; total?: number;
      }>(await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets?${params.toString()}`,
      ));
      // A campaign posts a clip or a carousel, so a sound file has nothing to
      // become here. Dropped on arrival rather than offered and then refused.
      setLibrary((body.assets ?? []).filter((asset) => asset.media_kind !== "audio"));
      if (body.facets) setLibraryFacets(body.facets);
      setLibraryTotal(body.total ?? body.assets?.length ?? 0);
      setDrafting([]);
      setPicking(true);
    } catch (reason) {
      fail(explainFailure(reason, "The library could not be read."));
    } finally {
      setBusy("");
    }
  }

  function filterLibrary(next: AssetFilterValues) {
    // Passed through as given. This used to pin `mediaKind` to "video" on the
    // way past, so the picker opened on everything and then hid every picture
    // the moment anybody typed a search or chose a channel - which made the
    // carousel support look absent when it was only one line out of reach.
    setLibraryFilters(next);
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => void loadLibrary(next), 220);
  }

  const run = useCallback(async (label: string, work: () => Promise<string>) => {
    setBusy(label);
    try {
      if (label !== "preview") setPreview(null);
      succeed(await work());
      await refresh();
    } catch (reason) {
      fail(explainFailure(reason, "That did not work."));
    } finally {
      setBusy("");
    }
  }, [refresh, succeed, fail]);

  const loadPreview = useCallback(async (announce = true) => {
    setBusy("preview");
    try {
      const body = await json<{
        note: string; posts: PreviewPost[]; deployed: DeployedPost[]; problems: number;
      }>(await apiFetch(`${base}/autopilot/preview`, { method: "POST" }));
      setPreview(body);
      if (announce) {
        succeed(body.problems
          ? t("autopilot.previewProblems", { count: body.problems })
          : body.note);
      }
    } catch (reason) {
      fail(explainFailure(reason, "The next posts could not be previewed."));
    } finally {
      setBusy("");
    }
  }, [apiFetch, base, fail, succeed, t]);

  async function save(changes: Partial<Autopilot>, { confirm = false } = {}) {
    if (!autopilot) return;
    const next = { ...autopilot, ...changes };
    const turningOn = Boolean(next.enabled && !autopilot.enabled);
    await run("settings", async () => {
      await json(await apiFetch(`${base}/autopilot`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          enabled: next.enabled,
          offer_id: next.offer_id,
          offer_mode: next.offer_mode,
          candidate_offer_ids: next.candidate_offer_ids,
          max_products_per_post: next.max_products_per_post,
          disclosure: next.disclosure,
          bio_hint: next.bio_hint,
          min_recycle_days: next.min_recycle_days,
          daily_cap_per_account: next.daily_cap_per_account,
          weekly_post_cap: next.weekly_post_cap,
          delivery: next.delivery,
          authority: next.authority,
          priority: next.priority,
          post_language: next.post_language,
          confirm_external_action: confirm,
        }),
      }));
      return next.enabled && !autopilot.enabled
        ? t("autopilot.switchedOn")
        : t("autopilot.saved");
    });
    // Switching on activates the campaign server-side; the parent's status
    // chip and list need to hear about it.
    if (turningOn) await onCampaignChanged();
  }

  async function loadDraftMatches() {
    setDraftProductMode("manual");
    if (draftMatches) return;
    setBusy("draft-products");
    try {
      const body = await json<Recommendations>(await apiFetch(
        `${base}/offer-recommendations?limit=12`,
      ));
      setDraftMatches(body.matches);
    } catch (reason) {
      fail(explainFailure(reason, "Products could not be analyzed."));
      setDraftProductMode("smart");
    } finally {
      setBusy("");
    }
  }

  /**
   * Match the whole selection at once, when the composer opens.
   *
   * Failure is quiet: a row without a suggestion still composes, and an error
   * banner over a panel somebody opened to write copy is noise about a feature
   * they were not using yet.
   */
  const loadRowMatches = useCallback(async (assets: LibraryAsset[]) => {
    const ids = assets.map((asset) => asset.id);
    if (!ids.length) return;
    try {
      const body = await json<{ assets: Record<string, { matches: OfferMatch[] }> }>(
        await apiFetch(`${base}/offer-recommendations/draft`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ asset_ids: ids.slice(0, 100), limit: 2 }),
        }),
      );
      setRowMatches(Object.fromEntries(
        Object.entries(body.assets).map(([id, found]) => [id, found.matches]),
      ));
    } catch {
      setRowMatches({});
    }
  }, [apiFetch, base]);

  function resetDraftProducts() {
    setDraftProductMode("smart");
    setDraftPinned(new Set());
    setDraftMatches(null);
  }

  /**
   * Bring a panel that has just opened into view.
   *
   * Both editors render after the whole queue, so pressing Edit content or
   * Review products on a row near the top opened something below the fold and
   * read as a button that did nothing. The next frame, because the panel does
   * not exist until this render commits.
   */
  function revealPanel(id: string) {
    window.requestAnimationFrame(() => {
      document.getElementById(id)?.scrollIntoView({
        behavior: "smooth", block: "center",
      });
    });
  }

  async function loadRecommendations(item: QueueItem | null = null) {
    setBusy(item ? `recommend-${item.id}` : "recommendations");
    try {
      const query = item ? `?item_id=${encodeURIComponent(item.id)}&limit=12` : "?limit=12";
      const body = await json<Recommendations>(await apiFetch(
        `${base}/offer-recommendations${query}`,
      ));
      setRecommendations(body);
      setProductItem(item);
      setPinnedOffers(new Set(item?.offer_ids ?? []));
      if (item) revealPanel("campaign-products");
    } catch (reason) {
      fail(explainFailure(reason, "Products could not be analyzed."));
    } finally {
      setBusy("");
    }
  }

  const ready = useMemo(() => {
    // Each row is a separate thing that can be missing, and each names where to
    // go and fix it. A single "not ready" would be true and useless.
    const rows = [
      {
        id: "active",
        met: campaignStatus === "active",
        label: t("autopilot.needActive"),
        section: null as string | null,
      },
      {
        id: "destinations",
        met: destinations.length > 0,
        label: t("autopilot.needDestinations"),
        section: "accounts" as const,
      },
      {
        id: "queue",
        // Written, not merely unparked: the checklist's promise is that the
        // campaign can post, and a queue of placeholders cannot.
        met: (autopilot?.queue_ready ?? 0) > 0,
        label: t("autopilot.needApproved"),
        section: "media" as const,
      },
      {
        id: "slots",
        met: slots.length > 0,
        label: t("autopilot.needSlots"),
        section: "schedule" as const,
      },
    ];
    return {
      rows,
      all: rows.every((row) => row.met),
      configured: rows.filter((row) => row.id !== "active").every((row) => row.met),
    };
  }, [campaignStatus, destinations.length, autopilot?.queue_ready, slots.length, t]);

  useEffect(() => {
    if (
      automaticPreview.current
      || campaignStatus !== "active"
      || !ready.configured
      || preview
    ) return;
    automaticPreview.current = true;
    void loadPreview(false);
  }, [campaignStatus, loadPreview, preview, ready.configured]);

  if (!autopilot) return null;

  const unmet = ready.rows.filter((row) => !row.met);

  /**
   * Open a work area, fetching whatever that area needs to be worth looking at.
   *
   * One door, because every caller used to do this itself and they had drifted:
   * the readiness "Fix it" button loaded accounts, the switch loaded accounts
   * and the preview, and the Monetization tab loaded recommendations - so which
   * data you got depended on how you arrived. It also absorbs the merge:
   * readiness rows still say `accounts` and `settings` because that is what
   * they are about, and both now open the one area that answers them.
   */
  function jumpTo(target: string) {
    const settings = ["accounts", "settings", "revenue"].includes(target);
    const pane = target === "media" || settings ? "content"
      : target === "schedule" ? "posts"
      : null;
    if (!pane) return;
    if (settings) {
      if (!accounts.length) void loadAccounts();
      if (!recommendations || recommendations.item_id) void loadRecommendations();
    }
    if (pane === "posts" && ready.configured && !preview) void loadPreview(false);
    setView(pane);
    // Content and settings share a pane now, so a checklist row scrolls to the
    // block that answers it rather than moving the page under the reader.
    if (settings) {
      window.requestAnimationFrame(() => {
        document.getElementById("campaign-setup")
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }

  /**
   * When each queued post next goes out, and to which account.
   *
   * Derived from the preview rather than recomputed: the scheduler already
   * decided this, and a second guess at it here would be a number that drifts
   * from the one on the Schedule tab. Empty until a preview has been loaded,
   * which is honest - nothing is due until the plan says so.
   */
  /** The queued posts by id, so the schedule can name the one it came from. */
  const queueById = new Map(queue.map((item) => [item.id, item]));
  /**
   * Every planned outing, by the post it came from.
   *
   * Was only the next one, which made a post going to four accounts look like
   * a post going to one.
   */
  const outings = new Map<string, PreviewPost[]>();
  for (const post of preview?.posts ?? []) {
    const forItem = outings.get(post.queue_item_id) ?? [];
    forItem.push(post);
    outings.set(post.queue_item_id, forItem);
  }

  /**
   * What to call the text after the post, given where this campaign posts.
   *
   * A campaign posting only to Threads has no comment box; one posting to both
   * kinds has to name both, because one field feeds them all.
   */
  const followUpPlatforms = destinations
    .filter((item) => item.follow_up_deliverable)
    .map((item) => item.platform);
  const followUpFieldName = !followUpPlatforms.length
    ? "First comment"
    : followUpPlatforms.every((platform) => isThreadPlatform(platform))
      ? "First reply in the thread"
      : followUpPlatforms.some((platform) => isThreadPlatform(platform))
        ? "First comment, or first reply in the thread"
        : "First comment";

  /**
   * The fields worth asking for, given where this campaign posts.
   *
   * Every field was always shown, so a campaign posting only to Threads was
   * asked for a title no network it uses has, and one posting only to TikTok
   * was asked for a first comment no engine there will deliver. Asking for
   * something nobody will ever see is worse than not asking: it reads as a
   * field somebody forgot to fill in.
   */
  const titleAccounts = destinations.filter((item) => item.takes_title);
  const followUpAccounts = destinations.filter((item) => item.follow_up_deliverable);

  const selectedLibrary = Object.values(selectedAssets);
  /**
   * The kind row, and the counts beside it.
   *
   * Read from the facets rather than the page of results, because the API
   * computes them with the media kind left out of its own filter - so each
   * count is what choosing that kind would actually show, under whatever else
   * is already narrowed. `libraryTotal` is not used for this: it counts audio
   * too, and audio is not offered here.
   */
  const kindCount = (kind: string) =>
    libraryFacets.media_kinds.find((facet) => facet.value === kind)?.count ?? 0;
  const postableMatching = kindCount("video") + kindCount("image");
  const selectedKind = libraryFilters.mediaKind ?? "";
  /** What the head reports: the chosen kind's count, or both kinds together. */
  const matchingCount = selectedKind ? kindCount(selectedKind) : postableMatching;
  /**
   * Which of this campaign's destinations could carry a carousel.
   *
   * Read off the destinations rather than worked out here: whether a login
   * posts galleries is the engine's property, and the API is where the engine
   * definitions live. Only enabled destinations count - a switched-off one is
   * not somewhere the package was going anyway.
   */
  const carouselReach = {
    total: destinations.filter((item) => item.enabled).length,
    accepting: destinations.filter((item) => item.enabled && item.accepts_carousel),
    refusing: destinations.filter((item) => item.enabled && item.accepts_carousel === false),
  };
  const postableKinds: Array<{ value: "" | "video" | "image"; label: string }> = [
    { value: "", label: t("common.all") },
    { value: "video", label: t("library.videos") },
    { value: "image", label: t("library.images") },
  ];
  /**
   * Delivered jobs and forecast posts as rows of one kind.
   *
   * They arrive as two shapes because they are two things - one is a durable
   * job with an outcome, the other a calculation - but on screen they answer
   * the same questions: when, where, through which engine, and has it gone
   * out. Normalising here rather than in the markup keeps one renderer, which
   * is what stops the two halves drifting apart again.
   */
  const timeline: TimelineEntry[] = preview ? [
    ...preview.deployed.map((item): TimelineEntry => ({
      key: `delivered-${item.id}`,
      kind: "delivered",
      at: item.at,
      title: item.title,
      caption: item.caption,
      first_comment: item.first_comment,
      thread: item.thread,
      destination: item.destination,
      destination_id: item.destination_id,
      status: item.status,
      delivery: item.delivery,
      post_url: item.post_url,
      page_url: item.page_url,
      video_path: item.video_path,
      image_paths: item.image_paths,
      last_error: item.last_error,
      asset_id: item.asset_id,
      problem: null,
      offer_ids: [],
      product_details: [],
      placement: null,
      reason: null,
      route: null,
    })),
    ...preview.posts.map((post): TimelineEntry => ({
      key: `planned-${post.destination_id}-${post.queue_item_id}-${post.at}`,
      kind: "planned",
      queue_item_id: post.queue_item_id,
      at: post.at,
      title: post.title,
      caption: post.caption,
      first_comment: post.first_comment,
      thread: post.thread,
      destination: post.destination,
      destination_id: post.destination_id,
      status: null,
      delivery: null,
      post_url: null,
      page_url: null,
      video_path: null,
      image_paths: [],
      last_error: null,
      asset_id: post.asset_id,
      problem: post.problem,
      offer_ids: post.offer_ids,
      product_details: post.product_details,
      placement: post.placement,
      reason: post.reason,
      route: placementSummary(post),
    })),
  ].sort((left, right) => left.at.localeCompare(right.at)) : [];
  const timelineDays = Object.entries(
    timeline.reduce<Record<string, TimelineEntry[]>>((days, entry) => {
      const key = new Date(entry.at).toLocaleDateString("en-CA", { timeZone: readerZone });
      (days[key] ??= []).push(entry);
      return days;
    }, {}),
  );
  const deliveredCount = timeline.filter((entry) => entry.kind === "delivered").length;
  const plannedCount = timeline.length - deliveredCount;
  const timelineAccounts = new Set(timeline.map((entry) => entry.destination_id)).size;
  // A failed delivery is a delivery warning as much as a preflight refusal
  // is: a zero above a red row would call the list a liar.
  const deliveryWarnings = (preview?.problems ?? 0) + timeline.filter(
    (entry) => entry.kind === "delivered" && entry.status === "failed",
  ).length;

  return (
    <div className="autopilot">
      {/* One name. "Runs by itself" stacked over "Autopilot" said it twice. */}
      <Card
        title={t("autopilot.heading")}
        aside={
          <div className="autopilot-run-row">
            {/* Beside the switch, not behind a tab named for revenue. The
                switch says whether the campaign runs; this says what running
                does, and reading one without the other explains nothing. */}
            <label className="autopilot-delivery">
              <span>{t("autopilot.delivery")}</span>
              <select
                value={autopilot.delivery}
                disabled={!canEdit}
                onChange={(event) =>
                  void save({ delivery: event.target.value as Autopilot["delivery"] })}
              >
                <option value="draft">{t("autopilot.deliveryDraft")}</option>
                <option value="schedule">{t("autopilot.deliverySchedule")}</option>
                {/* The column and the engines have always allowed this; only
                    the dropdown did not, so "Planned · publish now" was a badge
                    for a state nothing could reach. Chosen before the engine is
                    handed anything, which is what keeps it simple: there is no
                    scheduled job to modify, so there is no second post to
                    make. */}
                <option value="now">{t("autopilot.deliveryNow")}</option>
              </select>
            </label>
          <Switch
            checked={autopilot.enabled}
            disabled={!canEdit || campaignStatus === "archived"}
            label={t("autopilot.switch")}
            description={campaignStatus === "archived"
              ? "Restore this campaign to use automation."
              : !ready.configured
                ? "Click to finish the missing setup."
                : campaignStatus !== "active"
                  ? "Switching on activates the campaign and starts posting."
                  : undefined}
            onChange={(next) => {
              if (next && !ready.configured) {
                const nextStep = unmet.find((row) => row.section);
                if (nextStep?.section) jumpTo(nextStep.section);
                fail(`Finish setup first: ${nextStep?.label ?? "complete the checklist"}.`);
                return;
              }
              // One confirmed action. Switching on activates the campaign and
              // runs it now; there is no separate deploy step to find.
              if (next && !window.confirm(t("autopilot.confirmOn"))) return;
              void save({ enabled: next }, { confirm: true });
            }}
          />
          </div>
        }
      >
        {/* No static lede: the dynamic summary below says the same thing
            with this campaign's own numbers, and one of them is enough. */}
        {/* What the settings add up to, in one sentence.
         *
         * Everything needed to work this out was already on the screen -
         * packages here, accounts there, posting times in a third place - and
         * nobody should have to multiply three tiles together to find out how
         * often their accounts are about to post. Said before the switch,
         * because after it the answer arrives as posts.
         *
         * "Up to", not "will": the rest interval, the daily cap and how many
         * packages are approved all pull the real number down, and a promise
         * that overshoots is worse than a bound that holds. */}
        {destinations.length > 0 && slots.length > 0 && (
          <p className="autopilot-expansion" role="status">
            {(() => {
              const perAccount = Math.min(slots.length, autopilot.daily_cap_per_account);
              const perDay = perAccount * destinations.length;
              const posts = autopilot.queue_ready;
              return (
                <>
                  <strong>{posts} {posts === 1 ? "post" : "posts"}</strong>
                  {posts === 1 ? " goes to " : " go to "}
                  <strong>{destinations.length} {destinations.length === 1 ? "account" : "accounts"}</strong>
                  {", up to "}
                  <strong>{perDay} {perDay === 1 ? "post" : "posts"} a day</strong>
                  {perAccount < slots.length
                    ? ` (${perAccount} per account, your daily cap).`
                    : ` (one per posting time, per account).`}
                  {autopilot.offer_mode === "none"
                    ? " No affiliate link is attached."
                    : " Each post carries its affiliate link where that link can be clicked."}
                </>
              );
            })()}
          </p>
        )}

        {/* The operator's rule for tabs: each does exactly one thing, none
            overlap, and left to right they tell the pipeline's own story -
            packages enter the Queue, leave as Posts, governed by Setup.
            "Queue" and not "Content", because next to a tab called Posts,
            Content · post packages read as the same thing. */}
        <nav className="campaign-work-tabs" aria-label="Campaign workspace">
          {/* Counts the queue, because that is the number somebody comes to
              this tab for. The destinations are named on the tab's own second
              line rather than competing for the figure. */}
          <button type="button" className={view === "content" ? "active" : ""}
            onClick={() => jumpTo("media")}>
            <span>Queue &amp; setup</span><strong>{autopilot.queue_total}</strong>
            <small>{destinations.length === 1
              ? "posts · 1 account"
              : `posts · ${destinations.length} accounts`}</small>
          </button>
          <button type="button" className={view === "posts" ? "active" : ""}
            onClick={() => jumpTo("schedule")}>
            {/* Committed jobs still waiting to go out are upcoming posts too;
                only what has already delivered or failed leaves the count. */}
            <span>Schedule</span><strong>{preview
              ? preview.posts.length + preview.deployed.filter((item) =>
                  item.status === "queued" || item.status === "running").length
              : slots.length}</strong>
            <small>{preview ? "upcoming" : "posting times"}</small>
          </button>

        </nav>

        {/* Before the switch, not after it. An autopilot switched on with
            nothing to post is the failure this whole panel is arranged to
            prevent, so what is missing is stated where the switch is. */}
        {!ready.all && (
          <ul className="autopilot-checklist">
            {ready.rows.map((row) => (
              <li key={row.id} className={row.met ? "met" : "unmet"}>
                {/* The outstanding rows carry the weight, not the finished ones: what
                    is left to do is the reason this list is on screen. */}
                <span aria-hidden="true">{row.met ? "✓" : "•"}</span>
                <span>{row.label}</span>
                {!row.met && row.section && (
                  <button type="button" className="autopilot-fix" onClick={() => {
                    if (row.section) jumpTo(row.section);
                  }}>{t("autopilot.fixIt")}</button>
                )}
                {!row.met && row.id === "active" && (
                  <small>Activates when you switch posting on.</small>
                )}
              </li>
            ))}
          </ul>
        )}

        {autopilot.last_note && (
          <p className="autopilot-note" role="status">
            <strong>{t("autopilot.lastRun")}</strong> {autopilot.last_note}
          </p>
        )}

      </Card>

      {/* The operator's recurring job, front and centre: every post below
          earned autonomy waits here, shown as it will look - the same
          preview Publish rehearses with - and Approve sends exactly this
          frozen record. */}
      {exceptions.length > 0 && (
        <Card
          eyebrow="Approval"
          title="Needs your approval"
          aside={<Badge tone="warn">{exceptions.length} held</Badge>}
        >
          <p className="autopilot-lede">Nothing is published until you approve it
            here, and what you see is exactly what will go out. A post that is
            not finished can’t be approved — it shows what to fix first.</p>
          {/* One line that reconciles the scattered counts into the operator's
              own three buckets: what needs them now, what is ready to go, and
              what is still unfinished. All from authoritative figures, so it
              never disagrees with the rows below. */}
          {(() => {
            const waiting = exceptions.length;
            const ready = autopilot.queue_ready;
            const needsCopy = queue.filter((item) => item.needs_copy).length;
            return (
              <p className="autopilot-approval-summary" role="status">
                <strong>{waiting}</strong> waiting for you
                {ready > 0 && <> · <strong>{ready}</strong> ready to post</>}
                {needsCopy > 0 && (
                  <> · <strong>{needsCopy}</strong>{" "}
                    {needsCopy === 1 ? "still needs copy" : "still need copy"}</>
                )}
              </p>
            );
          })()}
          <ul className="campaign-approval-list">
            {exceptions.map((item) => (
              <li key={item.id}>
                <HeldPreview item={item} workspaceId={workspaceId} apiFetch={apiFetch} />
                <div className="campaign-approval-facts">
                  <small>
                    {item.destination_label ?? item.platform ?? "destination"}
                    {item.scheduled_at
                      ? ` · ${new Date(item.scheduled_at).toLocaleString()}`
                      : ""}
                  </small>
                  {/* Where this held post came from, so it lines up with its
                      row in "What it posts" below rather than reading as a
                      separate, unexplained item. */}
                  {heldSourceName(item) && (
                    <small className="campaign-approval-source">
                      From <strong>{heldSourceName(item)}</strong> in your queue
                    </small>
                  )}
                  {editingHeld?.id === item.id ? (
                    /* The rewrite: everything the post says is the
                       operator's to change; the media stays frozen. The
                       approve gate still refuses an edit that removes the
                       link or blanks the copy. */
                    <form
                      className="campaign-approval-edit"
                      onSubmit={(event) => {
                        event.preventDefault();
                        const form = new FormData(event.currentTarget);
                        void saveHeldEdit(item.id, {
                          title: String(form.get("title") ?? "").trim() || null,
                          caption: String(form.get("caption") ?? ""),
                          first_comment:
                            String(form.get("first_comment") ?? "").trim() || null,
                          thread: item.thread.map((_, index) =>
                            String(form.get(`reply-${index}`) ?? "").trim(),
                          ).filter(Boolean),
                        });
                      }}
                    >
                      {item.title !== null && (
                        <label>Title
                          <input name="title" defaultValue={item.title ?? ""}
                            maxLength={200} />
                        </label>
                      )}
                      <label>Caption
                        <textarea name="caption" rows={5} maxLength={4000}
                          defaultValue={item.caption} required />
                      </label>
                      <label>First comment
                        <textarea name="first_comment" rows={3} maxLength={2000}
                          defaultValue={item.first_comment ?? ""} />
                      </label>
                      {item.thread.map((reply, index) => (
                        <label key={`${item.id}-edit-reply-${index}`}>
                          Reply {index + 1}
                          <textarea name={`reply-${index}`} rows={2}
                            maxLength={4000} defaultValue={reply} />
                        </label>
                      ))}
                      <span className="campaign-exception-actions">
                        <Button type="submit" variant="primary" size="sm"
                          busy={busy === `edit-held-${item.id}`}>Save</Button>
                        <Button variant="quiet" size="sm"
                          onClick={() => setEditingHeld(null)}>Cancel</Button>
                      </span>
                    </form>
                  ) : (
                    <>
                      {item.first_comment && <>
                        <strong>First comment</strong>
                        <pre>{item.first_comment}</pre>
                      </>}
                      {item.thread.map((reply, index) => (
                        <div key={`${item.id}-reply-${index}`}>
                          <strong>Reply {index + 1}</strong>
                          <pre>{reply}</pre>
                        </div>
                      ))}
                      <p className="autopilot-note" role="status">{item.held_reason}</p>
                      {canEdit && (
                        <>
                          <span className="campaign-exception-actions">
                            <Button variant="primary" size="sm"
                              busy={busy === `approve-${item.id}`}
                              onClick={() => void decideException(item.id, "approve")}
                            >Approve</Button>
                            <Button variant="secondary" size="sm"
                              busy={busy === `approve-${item.id}`}
                              onClick={() => {
                                if (!window.confirm(
                                  `Publishes to ${item.destination_label ?? item.platform} immediately instead of waiting for the slot. Continue?`,
                                )) return;
                                void decideException(item.id, "approve", { publishNow: true });
                              }}
                            >Publish now</Button>
                            <Button variant="quiet" size="sm"
                              onClick={() => setEditingHeld(item)}>Edit</Button>
                            {/* The hold reason often points at a weak product
                                match; this opens that post's products so the
                                advice has a control beside it rather than
                                sending the operator to hunt for the row. */}
                            {item.queue_item_id
                              && queue.some((row) => row.id === item.queue_item_id) && (
                              <Button variant="quiet" size="sm"
                                onClick={() => {
                                  const row = queue.find((entry) => entry.id === item.queue_item_id);
                                  if (!row) return;
                                  jumpTo("media");
                                  void loadRecommendations(row);
                                }}
                              >Review products</Button>
                            )}
                            <Button variant="quiet" size="sm"
                              busy={busy === `dismiss-${item.id}`}
                              onClick={() => void decideException(item.id, "dismiss")}
                            >Skip this cycle</Button>
                          </span>
                          {/* What each button does, once, where the choice is
                              made — "Skip" especially, which frees the slot
                              rather than deleting the post. */}
                          <small className="campaign-approval-actions-hint">
                            <strong>Approve</strong> sends it on the campaign’s
                            schedule · <strong>Publish now</strong> sends it
                            immediately · <strong>Skip</strong> frees the slot and
                            clip; the post returns next cycle.
                          </small>
                        </>
                      )}
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}


      {view === "content" && (
        /* Two columns: the queue is the daily job and takes the width,
           the settings sit beside it. They were separate tabs, and
           getting a campaign live needs both - the readiness checklist
           threw the operator between them to satisfy one job. */
        <div className="campaign-work-split">
          <div className="campaign-work-main">
      {<Card
        eyebrow={t("autopilot.queueEyebrow")}
        title={t("autopilot.queue", {
          ready: autopilot.queue_ready, total: autopilot.queue_total,
        })}
        aside={canEdit ? (
          <Button variant="secondary" size="sm" busy={busy === "library"}
            onClick={() => void loadLibrary()}><ActionIcon name="clip" />{t("autopilot.addFromLibrary")}</Button>
        ) : undefined}
      >
        <p className="autopilot-lede">{t("autopilot.queueHelp")}</p>

        {/* Choosing media is a temporary action, not another section in the
            campaign workspace. Keep the full multi-select and effects tools,
            but place them in the same modal surface as every other Library
            picker. Copy stays in the page after the modal hands the clips
            back, because that is campaign content rather than browsing. */}
        <Dialog
          open={picking && drafting.length === 0}
          size="wide"
          title="Add media from Library"
          description="Choose one or more videos, or pictures to post as one carousel, optionally apply effects, then write their campaign copy."
          onClose={() => {
            setPicking(false);
            setSelectedAssets({});
          }}
        >
          <div className="campaign-media-browser">
            <div className="campaign-media-browser-head">
              <div>
                <strong>{selectedLibrary.length
                ? `${selectedLibrary.length} selected`
                : t("autopilot.chooseMedia")}</strong>
                <small>{matchingCount.toLocaleString()} matching · showing {library.length}</small>
              </div>
            </div>
            {/* The same row the Library page carries, so narrowing to pictures
                works the way it does there. Audio is left off rather than shown
                and refused: a campaign posts a clip or a carousel, and there is
                no third thing for a sound file to become. */}
            <div className="campaign-media-kinds" role="group" aria-label="Media kind">
              {postableKinds.map(({ value, label }) => {
                const active = selectedKind === value;
                const count = value ? kindCount(value) : postableMatching;
                return (
                  <button
                    key={value || "all"}
                    type="button"
                    className={active ? "selected" : ""}
                    aria-pressed={active}
                    onClick={() => filterLibrary({ ...libraryFilters, mediaKind: value })}
                  >{label} <span>{(count ?? 0).toLocaleString()}</span></button>
                );
              })}
            </div>
            <AssetFilters
              values={libraryFilters}
              facets={libraryFacets}
              fields={["query", "effect", "channel", "platform", "length"]}
              cleared={{}}
              onChange={filterLibrary}
            />
            <div className="campaign-media-actions">
              <span>{selectedLibrary.length
                ? `${selectedLibrary.length} ready for campaign actions`
                : "Select clips to edit or add to the campaign"}</span>
              <Button variant="secondary" size="sm" disabled={!selectedLibrary.length}
                onClick={() => setEffectOpen(true)}>Apply effects</Button>
              <Button variant="primary" size="sm" disabled={!selectedLibrary.length}
                onClick={() => {
                  setDrafting(selectedLibrary);
                  // Matched as the composer opens, so the suggestions are
                  // already there when the first row is read rather than
                  // arriving under the cursor a moment later.
                  void loadRowMatches(selectedLibrary);
                }}>Write campaign copy</Button>
              {selectedLibrary.length > 0 && (
                <Button variant="quiet" size="sm" onClick={() => setSelectedAssets({})}>
                  Clear selection
                </Button>
              )}
            </div>
            <ul className="campaign-media-grid">
              {library.map((asset) => (
                <li key={asset.id}>
                  <label className={selectedAssets[asset.id] ? "selected" : ""}>
                    <input className="sr-only" type="checkbox"
                      checked={Boolean(selectedAssets[asset.id])}
                      onChange={() => setSelectedAssets((current) => {
                        const next = { ...current };
                        if (next[asset.id]) delete next[asset.id]; else next[asset.id] = asset;
                        return next;
                      })} />
                    <AssetThumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
                    <span className="campaign-media-meta">
                      <strong>{asset.title}</strong>
                      <small>{[asset.creator, asset.platform, clipLength(asset.duration_ms)]
                        .filter(Boolean).join(" · ") || "No source recorded"}</small>
                      <em>{asset.versions.some((version) => ["blurred", "edited"].includes(version.kind))
                        ? "Effects applied" : "Original"}</em>
                    </span>
                    <b className="campaign-media-check" aria-hidden="true">✓</b>
                  </label>
                </li>
              ))}
              {!library.length && <li className="campaign-media-empty">{t("autopilot.noClips")}</li>}
            </ul>
          </div>
        </Dialog>

        {drafting.length > 0 && (
          <form
            className="autopilot-compose"
            onSubmit={(event) => {
              event.preventDefault();
              void run("queue", async () => {
                const offerIds = draftProductMode === "manual"
                  ? Array.from(draftPinned)
                  : [];
                const post = async (payload: Record<string, unknown>) =>
                  json(await apiFetch(`${base}/queue`, {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ ...payload, offer_ids: offerIds }),
                  }));
                // Each row carries the copy written for it. Copy is still
                // optional: a package can be picked today and written later,
                // and the API stands a placeholder in and marks it, which is
                // what the badge on the queue reads from.
                const requests = draftPosts.map((entry) => {
                  const written = copyFor(entry.id);
                  const lead = entry.assets[0];
                  const shared = {
                    asset_id: lead.id,
                    title: lead.title,
                    body: written.body.trim(),
                    hashtags: written.hashtags.split(/[\s,]+/).filter(Boolean),
                  };
                  return entry.kind === "carousel"
                    ? { ...shared, image_paths: entry.assets.map(handoffPath) }
                    : entry.kind === "image"
                      ? { ...shared, image_paths: [handoffPath(lead)] }
                      : { ...shared, video_path: handoffPath(lead) };
                });
                // In batches rather than all at once. A hundred clips is a
                // reasonable selection here and `Promise.all` over all of them
                // opens a hundred parallel writes against one SQLite file,
                // which is how the API comes to block on itself.
                for (let index = 0; index < requests.length; index += QUEUE_BATCH) {
                  await Promise.all(
                    requests.slice(index, index + QUEUE_BATCH).map(post),
                  );
                }
                const count = requests.length;
                setDrafting([]);
                setSelectedAssets({});
                setPicking(false);
                setDraftCopy({});
                setSplitPictures(false);
                setRowMatches({});
                resetDraftProducts();
                return `${count} ${count === 1 ? "post" : "posts"} added to the campaign queue.`;
              });
            }}
          >
            <div className="autopilot-picker-head">
              {/* Counts packages, not files: three pictures riding together
                  are one post, and saying "3" over a single carousel row is
                  how somebody comes to expect three. */}
              <strong>{draftPosts.length === 1
                ? draftPosts[0].kind === "carousel"
                  ? `Carousel of ${draftPosts[0].assets.length} pictures`
                  : draftPosts[0].assets[0].title
                : `${draftPosts.length} posts`}</strong>
              <Button variant="quiet" size="sm" onClick={() => {
                setDrafting([]);
                resetDraftProducts();
              }}>
                {t("autopilot.chooseAnother")}
              </Button>
            </div>
            {/* Said here, where the carousel is being made, rather than left
                for the engine to say after the post is built. Carousel support
                is narrow and belongs to the engine as much as the network: a
                workspace whose Instagram and Threads run through Buffer has
                nowhere to send pictures, even though both networks have
                galleries of their own. */}
            {draftingSplit.images.length > 0 && carouselReach.total > 0 && (
              <p
                className={carouselReach.accepting.length ? "autopilot-note" : "autopilot-refusal"}
                role="status"
              >
                {carouselReach.accepting.length ? <>
                  <strong>This carousel can go to {carouselReach.accepting.length} of {carouselReach.total} destinations.</strong>
                  {" "}
                  {carouselReach.accepting.map((item) => item.label).join(", ")}
                  {carouselReach.refusing.length > 0 && <>
                    {" "}The rest take video only, and this package will be
                    skipped on them: {carouselReach.refusing.map((item) => item.label).join(", ")}.
                  </>}
                </> : <>
                  <strong>No account on this campaign can post a photo carousel.</strong>
                  {" "}
                  {carouselReach.refusing.map((item) => item.label).join(", ")} take
                  video only, so this package would never be posted. Add a
                  destination on an engine that carries carousels, or choose a
                  video instead.
                </>}
              </p>
            )}
            {/* A row per package, each with its own copy.
                One caption used to be written once and spread across every
                selected clip, so five videos became five identical posts -
                the one thing a hundred clips must not be. The disclosure and
                the affiliate link are still added per network at post time,
                so neither is written here. */}
            <p className="autopilot-note">{t("autopilot.copyHelp")}</p>
            <ol className="draft-posts">
              {draftPosts.map((entry) => {
                const lead = entry.assets[0];
                const written = copyFor(entry.id);
                return (
                  <li key={entry.id} className="draft-post">
                    <div className="draft-post-head">
                      {/* The clip itself, because a row named by a filename is
                          not enough to write a caption against - and a hundred
                          Douyin titles are the same shape as each other. */}
                      <AssetThumbnail
                        asset={lead}
                        workspaceId={workspaceId}
                        apiFetch={apiFetch}
                      />
                      <strong>
                        {entry.kind === "carousel"
                          ? `${entry.assets.length} pictures - one carousel`
                          : lead.title}
                      </strong>
                      {entry.kind === "carousel" && (
                        <Button variant="quiet" size="sm"
                          onClick={() => setSplitPictures(true)}>
                          Split into {entry.assets.length} posts
                        </Button>
                      )}
                      {entry.kind === "image" && draftingSplit.images.length > 1 && (
                        <Button variant="quiet" size="sm"
                          onClick={() => setSplitPictures(false)}>
                          Group as one carousel
                        </Button>
                      )}
                    </div>
                    {/* What smart match would attach to this post, shown while
                        it is being written rather than discovered after it is
                        approved. A suggestion, not a commitment: the campaign
                        picks at post time from the offers that still fit. */}
                    {Boolean(rowMatches[lead.id]?.length) && (
                      <ul className="draft-post-offers">
                        {rowMatches[lead.id].slice(0, 2).map((match) => (
                          <li key={match.offer_id}>
                            <span
                              className="campaign-match-score"
                              data-confidence={match.confidence}
                            ><strong>{match.score}</strong><small>%</small></span>
                            <span>{match.product_name}</span>
                            {/* What it pays, through the one formatter that
                                spells this the same way everywhere: the rate
                                is the reason one offer beats another, so it
                                belongs beside the offer. Absent when the
                                network states none, rather than a dash. */}
                            {commissionLabel(match) && (
                              <b>{commissionLabel(match)}</b>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                    <label>
                      <span className="sr-only">{t("autopilot.copy")}</span>
                      <textarea
                        rows={3}
                        maxLength={4000}
                        value={written.body}
                        placeholder={t("autopilot.copyPlaceholder")}
                        onChange={(event) =>
                          setCopyFor(entry.id, { body: event.target.value })}
                      />
                    </label>
                    <label>
                      <span className="sr-only">{t("autopilot.hashtags")}</span>
                      <input
                        value={written.hashtags}
                        placeholder={t("autopilot.hashtagsExample")}
                        onChange={(event) =>
                          setCopyFor(entry.id, { hashtags: event.target.value })}
                      />
                    </label>
                    {/* Reference, never seeded. These are the captions the
                        clips were posted with where they came from - Chinese,
                        on a campaign that posts in Vietnamese - so copying one
                        into the box would put the wrong language into a post.
                        It is here to remind the writer what the clip is. */}
                    {lead.caption && (
                      <p className="draft-post-source">
                        <span>Originally posted as</span>
                        <q>{lead.caption}</q>
                      </p>
                    )}
                  </li>
                );
              })}
            </ol>
            {/* The product decision belongs to the package, made here rather
                than discovered later: what gets approved is a post whose
                products were already decided - smartly or by hand. */}
            <div className="campaign-product-mode">
              <div>
                <strong>Products</strong>
                {/* The precedence, said once: a pin on the package beats the
                    campaign, and nothing else here overrides it. The old
                    wording only mentioned this when products were switched
                    off, which is when it was most surprising and least
                    useful. */}
                <small>Pinning here overrides the campaign for this package.
                  The affiliate link follows the product, per network.</small>
              </div>
              <div className="campaign-mode-options" role="radiogroup"
                aria-label="Products for this post">
                <button type="button" role="radio"
                  aria-checked={draftProductMode === "smart"}
                  className={draftProductMode === "smart" ? "active" : ""}
                  onClick={() => setDraftProductMode("smart")}>
                  {/* Named for what it does, which is not choose. Pinning here
                      writes `offer_ids` on the package and that beats every
                      campaign setting; leaving it alone falls through to the
                      campaign, whose answer may be smart matching, one fixed
                      offer, or no products at all. Calling this "Smart match"
                      promised the first of those three on a campaign that had
                      chosen either of the others. */}
                  <strong>Campaign default</strong>
                  <small>{autopilot.offer_mode === "smart"
                    ? "Smart match — best-fitting offers, chosen when it posts"
                    : autopilot.offer_mode === "manual"
                      ? "One offer, set for this campaign"
                      : "No products — this campaign posts organic"}</small>
                </button>
                <button type="button" role="radio"
                  aria-checked={draftProductMode === "manual"}
                  className={draftProductMode === "manual" ? "active" : ""}
                  onClick={() => void loadDraftMatches()}>
                  <strong>Pin products</strong>
                  <small>Pick from your imported offers</small>
                </button>
              </div>
            </div>
            {draftProductMode === "manual" && (
              <ul className="campaign-product-matches selectable">
                {busy === "draft-products" && (
                  <li className="campaign-media-empty">Analyzing your offers…</li>
                )}
                {(draftMatches ?? []).map((match) => (
                  <li key={match.offer_id}>
                    <label>
                      <input type="checkbox" checked={draftPinned.has(match.offer_id)}
                        disabled={!draftPinned.has(match.offer_id) && draftPinned.size >= 5}
                        onChange={() => setDraftPinned((current) => {
                          const next = new Set(current);
                          if (next.has(match.offer_id)) next.delete(match.offer_id);
                          else next.add(match.offer_id);
                          return next;
                        })} />
                      <span className="campaign-match-score" data-confidence={match.confidence}>
                        <strong>{match.score}</strong><small>% fit</small>
                      </span>
                      <span className="campaign-match-copy">
                        <strong>{match.product_name}</strong>
                        <small>{match.reasons.join(" ")}</small>
                      </span>
                      <Badge
                        tone={match.confidence === "high" ? "good"
                          : match.confidence === "medium" ? "warn" : "neutral"}
                        title={t(`autopilot.matchConfidence.${match.confidence}`)}
                      >
                        {match.confidence}
                      </Badge>
                    </label>
                  </li>
                ))}
                {draftMatches !== null && draftMatches.length === 0 && (
                  <li className="campaign-media-empty">
                    No usable offers yet. Import them in Attribution first.
                  </li>
                )}
              </ul>
            )}
            <Button type="submit" variant="primary" busy={busy === "queue"}>
              {draftPosts.length > 1
                ? `Add ${draftPosts.length} posts`
                : t("autopilot.addToQueue")}
              {draftProductMode === "manual" && draftPinned.size > 0
                ? ` · ${draftPinned.size} ${draftPinned.size === 1 ? "product" : "products"}`
                : ""}
            </Button>
          </form>
        )}
        {queue.length === 0 ? (
          <p className="autopilot-empty">{t("autopilot.noQueue")}</p>
        ) : (
          <ul className="autopilot-queue">
            {queue.map((item) => (
              <li key={item.id} id={`queued-${item.id}`} className={item.state}>
                {/* The clip itself, not just its name: this list is where
                    content is curated, and a thumbnail answers "which video
                    is this" faster than any filename. */}
                <div className="autopilot-queue-thumb">
                  {item.asset_id ? (
                    <AssetThumbnail
                      asset={{
                        id: item.asset_id,
                        title: item.title ?? "Queued media",
                        original_path: "",
                        media_kind: item.video_path ? "video" : "image",
                        duration_ms: null,
                        platform: null,
                        creator: null,
                        width: null,
                        height: null,
                        versions: [{ id: `${item.asset_id}-thumbnail`, kind: "thumbnail" }],
                      }}
                      workspaceId={workspaceId}
                      apiFetch={apiFetch}
                    />
                  ) : (
                    <span className="campaign-pipeline-thumb-empty"><ActionIcon name="play" /></span>
                  )}
                </div>
                <div>
                  <strong>{displayTitle(item.title) ?? item.body.slice(0, 60)}</strong>
                  {item.needs_copy ? (
                    <span className="autopilot-queue-copy autopilot-needs-copy">
                      {/* It is skipped, not sent. This said the opposite - that
                          the placeholder would post - which was true before the
                          scheduler learned to skip unwritten posts and the
                          delivery guard learned to refuse them. */}
                      No copy yet — this post is skipped until somebody writes it.
                    </span>
                  ) : (
                    <span className="autopilot-queue-copy">{item.body}</span>
                  )}
                  {/* One line of facts. This was three: a chip row for what
                      the post is made of, a second chip row for its products
                      in a different chip style, and a plain italic aside in a
                      third - six bands of nine-point text down a row whose
                      title is thirteen. What it is, where it goes, what it
                      carries, in that order and in one style. */}
                  <span className="campaign-queue-facts">
                    <em>{namedFormat(null, item)}</em>
                    <em>{item.times_posted > 0
                      ? t("autopilot.postedTimes", { count: item.times_posted })
                      : t("autopilot.neverPosted")}</em>
                    {destinations.length > 0 && (
                      <em>{destinations.length} {destinations.length === 1 ? "account" : "accounts"}</em>
                    )}
                    {item.first_comment && <em>+ {followUpFieldName.toLowerCase()}</em>}
                    {item.thread.length > 0 && (
                      <em>+ {item.thread.length} {item.thread.length === 1 ? "reply" : "replies"}</em>
                    )}
                  {/* What this post would carry, and when it would carry
                      nothing, why. Every match being low confidence rendered
                      an empty space: the filter dropped them all and the
                      fallback only spoke when there were none at all, so a
                      campaign whose products all scored badly looked exactly
                      like one nobody had analysed. */}
                  {(() => {
                    const ranked = item.offer_match?.matches ?? [];
                    // Mirrors the matcher: pins win, then confident matches,
                    // and failing both the best available still goes on rather
                    // than the post going out bare.
                    const confident = ranked.filter((match) => item.offer_ids.length
                      ? item.offer_ids.includes(match.offer_id)
                      : match.confidence !== "low");
                    const attaching = (confident.length ? confident : ranked.slice(0, 1))
                      .slice(0, autopilot.max_products_per_post);
                    const weak = !confident.length && Boolean(ranked.length);
                    return (
                      <>
                        {attaching.map((match) => (
                          // The caveat is inside the group, not beside it: as a
                          // sibling chip it wrapped to a line of its own under a
                          // long product name and read as a verdict on the row
                          // rather than on the product.
                          <span key={match.offer_id} className="campaign-queue-product">
                            <em className="product">
                              {match.product_name} · {match.score}%
                              {commissionLabel(match) && ` · ${commissionLabel(match)}`}
                            </em>
                            {/* Said rather than left to the percentage. Nothing
                                here cleared the evidence bar, and the best of a
                                weak field is still what goes out. */}
                            {weak && <em className="soft">weak fit</em>}
                          </span>
                        ))}
                        {!ranked.length && <em className="soft">analysis pending</em>}
                      </>
                    );
                  })()}
                  </span>
                  {/* When, where, how it reads there, and what follows it. The
                      row used to answer only the first of those. */}
                  <QueueRehearsal
                    item={item}
                    outings={outings.get(item.id) ?? []}
                    workspaceId={workspaceId}
                    apiFetch={apiFetch}
                    destinations={destinations}
                    slots={slots}
                    /* In the order the scheduler applies them. Copy comes
                       before everything: an unwritten post is skipped whatever
                       else is true of it, and guessing "another post holds
                       every slot" at a post that was never a candidate sent
                       somebody looking for a scheduling problem that was
                       really an empty caption. */
                    noPlanReason={item.needs_copy
                      ? "Not scheduled: no copy written yet. Write it and this post joins the rotation."
                      : item.state !== "approved"
                        ? "Held back: add it to the rotation and the plan appears here."
                        : !destinations.length
                          ? "Nowhere to post it yet. Add an account."
                          : !slots.length
                            ? "No posting times yet. Add one and the plan appears here."
                            : preview
                              ? "Not in this cycle: every slot is taken by another post."
                              : "Loading the plan…"}
                  />
                </div>
                {/* What the row can do, not what column it stores. Every item
                    arrives approved, so an unwritten one wore a green
                    "approved" badge above a warning that it could not be sent -
                    and the badge is the part people read. */}
                {/* The word, and what follows from it. "In rotation" and
                    "needs copy" each name a state without saying whether the
                    scheduler will pick the item up or what has to happen first,
                    which is the only thing the reader wants from them. */}
                <Badge
                  tone={item.needs_copy
                    ? "warn" : item.state === "approved" ? "good" : "neutral"}
                  title={item.needs_copy
                    ? t("autopilot.state.help.needsCopy")
                    : t(`autopilot.state.help.${item.state}`)}
                >
                  {item.needs_copy
                    ? t("autopilot.state.needsCopy")
                    : t(`autopilot.state.${item.state}`)}
                </Badge>
                {canEdit && (
                  <div className="campaign-queue-actions">
                    {item.state !== "approved" && (
                      <Button variant="secondary" size="sm"
                        onClick={() => void run("approve", async () => {
                          await json(await apiFetch(`${base}/queue/${item.id}`, {
                            method: "PATCH",
                            headers: { "content-type": "application/json" },
                            body: JSON.stringify({ state: "approved" }),
                          }));
                          if ((autopilot.queue_approved ?? 0) === 0) {
                            if (!destinations.length) jumpTo("revenue");
                            else if (!slots.length) jumpTo("schedule");
                            else jumpTo("revenue");
                          }
                          return t("autopilot.itemApproved");
                        })}>{t("autopilot.approve")}</Button>
                    )}
                    <Button variant="quiet" size="sm" onClick={() => {
                      setEditing(item);
                      setEditingReplies(item.thread.length ? item.thread : [""]);
                      revealPanel("campaign-edit-content");
                    }}>Edit content</Button>
                    <Button variant="quiet" size="sm" busy={busy === `recommend-${item.id}`}
                      onClick={() => void loadRecommendations(item)}>
                      {item.offer_ids.length ? "Edit products" : "Review products"}
                    </Button>
                    <Button variant="quiet" size="sm" onClick={() => void run("drop", async () => {
                      await json(await apiFetch(`${base}/queue/${item.id}`, { method: "DELETE" }));
                      return t("autopilot.itemRemoved");
                    })}>{t("common.delete")}</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {productItem && recommendations?.item_id === productItem.id && (
          <div className="campaign-item-products" id="campaign-products">
            <div className="campaign-product-heading">
              <div>
                <strong>Products for {productItem.title ?? "this queued post"}</strong>
                {/* What it carries today, before anything is changed. Opening
                    this on a post with nothing pinned showed twelve empty
                    boxes and no sign of what smart matching had already
                    settled on, so the panel read as a chooser for a decision
                    that had in fact been made. */}
                <small>{pinnedOffers.size
                  ? `Pinned: ${pinnedOffers.size} product${pinnedOffers.size === 1 ? "" : "s"}. Untick every box to hand this back to smart matching.`
                  : recommendations.chosen_offer_ids?.length
                    ? `Smart matching attaches ${recommendations.matches
                        .filter((match) => recommendations.chosen_offer_ids?.includes(match.offer_id))
                        .map((match) => match.product_name).join(", ")}. Tick a box to pin something else instead.`
                    : "Smart matching has nothing to attach here. Tick a box to pin one."}</small>
              </div>
              <Button variant="quiet" size="sm" onClick={() => {
                setProductItem(null); setRecommendations(null); setPinnedOffers(new Set());
              }}>Close</Button>
            </div>
            <ul className="campaign-product-matches selectable">
              {recommendations.matches.map((match) => (
                <li key={match.offer_id}>
                  <label>
                    <input type="checkbox" checked={pinnedOffers.has(match.offer_id)}
                      onChange={() => setPinnedOffers((current) => {
                        const next = new Set(current);
                        if (next.has(match.offer_id)) next.delete(match.offer_id);
                        else next.add(match.offer_id);
                        return next;
                      })} />
                    <span className="campaign-match-score" data-confidence={match.confidence}>
                      <strong>{match.score}</strong><small>% fit</small>
                    </span>
                    <span className="campaign-match-copy">
                      <strong>{match.product_name}
                        {commissionLabel(match) && (
                          <b className="campaign-match-pay">{commissionLabel(match)}</b>
                        )}
                      </strong>
                      <small>{match.reasons.join(" ")}</small>
                      <span>{match.matched_terms.slice(0, 6).map((term) => <em key={term}>{term}</em>)}</span>
                    </span>
                    {/* Both marks in one cell, always present. As two grid
                        children the second appeared on some rows and not
                        others, so the confidence badge sat in a different
                        column on every row and the list read as ragged.
                        Anchored right, the "would post" mark grows leftward
                        and confidence stays where the eye left it. */}
                    <span className="campaign-match-marks">
                      {/* Marked when nothing is pinned, because that is when
                          the ranking is a forecast rather than a menu: these
                          are the ones smart match would attach. A pin
                          overrides every one of them, so the mark would lie. */}
                      {!pinnedOffers.size
                        && recommendations.chosen_offer_ids?.includes(match.offer_id) && (
                        <Badge tone="good" className="campaign-match-chosen">would post</Badge>
                      )}
                      <Badge
                        tone={match.confidence === "high" ? "good"
                          : match.confidence === "medium" ? "warn" : "neutral"}
                        title={t(`autopilot.matchConfidence.${match.confidence}`)}
                      >
                        {match.confidence}
                      </Badge>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            <div className="campaign-product-actions">
              <small>{pinnedOffers.size
                ? `${pinnedOffers.size} pinned product${pinnedOffers.size === 1 ? "" : "s"}; these override smart matching for this post.`
                : recommendations.chosen_offer_ids?.length
                  ? `Smart matching would attach the ${recommendations.chosen_offer_ids.length === 1
                      ? "product" : `${recommendations.chosen_offer_ids.length} products`} marked above.`
                  : "Nothing here is confident enough to attach unattended. Pin a product, or leave this post organic."}</small>
              <Button variant="primary" size="sm" busy={busy === "pin-products"}
                onClick={() => void run("pin-products", async () => {
                  await json(await apiFetch(`${base}/queue/${productItem.id}`, {
                    method: "PATCH",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ offer_ids: [...pinnedOffers] }),
                  }));
                  setProductItem(null); setRecommendations(null);
                  return pinnedOffers.size ? "Products pinned to this post." : "This post now uses smart product matching.";
                })}>Save product choice</Button>
            </div>
          </div>
        )}
        {editing && (
          <form className="autopilot-compose" id="campaign-edit-content" onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void run("edit-copy", async () => {
              await json(await apiFetch(`${base}/queue/${editing.id}`, {
                method: "PATCH",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  title: String(form.get("title") ?? "").trim() || null,
                  body: String(form.get("body") ?? "").trim(),
                  hashtags: String(form.get("hashtags") ?? "")
                    .split(/[\s,]+/).filter(Boolean),
                  first_comment: String(form.get("first_comment") ?? "").trim() || null,
                  thread: editingReplies.map((part) => part.trim()).filter(Boolean),
                }),
              }));
              setEditing(null);
              return "Campaign copy updated.";
            });
          }}>
            <div className="autopilot-picker-head">
              <span>
                <strong>Edit post</strong>
                <small>Everything below is one post. The campaign adds the
                  disclosure and the product link to it, differently on each
                  account - what that comes to is spelled out underneath.</small>
              </span>
              <Button variant="quiet" size="sm" onClick={() => setEditing(null)}>Cancel</Button>
            </div>
            {/* Only where a title exists. It is still shown when nothing takes
                one but this post already has a title, because hiding a field
                that holds text is how text gets lost. */}
            {(titleAccounts.length > 0 || editing.title) && (
              <label>Title
                <input name="title" defaultValue={editing.title ?? ""} maxLength={200} />
                <small>{titleAccounts.length
                  ? `Shown on ${[...new Set(titleAccounts.map(
                      (item) => platformLabels[item.platform]))].join(", ")}, and on the schedule.`
                  : "No account on this campaign shows a title. Kept because this post has one."}</small>
              </label>
            )}
            <label>{t("autopilot.copy")}
              <textarea name="body" rows={4} required maxLength={4000}
                defaultValue={editing.body} />
            </label>
            <label>{t("autopilot.hashtags")}
              <input name="hashtags" defaultValue={editing.hashtags.join(" ")} />
            </label>
            {/* Named for the networks it will actually land on. Calling this
                a first comment on a campaign that only posts to Threads
                describes a comment box that network does not have. */}
            <label>{followUpFieldName}
              <FeatureReach
                chosen={[...new Set(destinations.map((item) => item.platform))]}
                supported={[...new Set(destinations
                  .filter((item) => item.follow_up_deliverable)
                  .map((item) => item.platform))]}
              />
              <textarea name="first_comment" rows={3} maxLength={2000}
                defaultValue={editing.first_comment ?? ""}
                placeholder={`Optional ${followUpFieldName.toLowerCase()}, published straight after the post`} />
              <small>{followUpAccounts.length
                ? `Delivered on ${[...new Set(followUpAccounts.map(
                    (item) => platformLabels[item.platform]))].join(", ")}. Where the product link goes here too, your words lead and the link follows them in the same comment.`
                : "No account on this campaign can deliver one. Anything written here is kept but not sent."}</small>
            </label>
            <fieldset className="campaign-reply-editor">
              <legend>Replies / thread
                <FeatureReach
                  chosen={[...new Set(destinations.map((item) => item.platform))]}
                  supported={[...new Set(destinations
                    .filter((item) => item.follow_up_deliverable)
                    .map((item) => item.platform))]}
                />
              </legend>
              <small>Replies publish in this order after the primary post. Product links generated by smart matching appear after these replies.</small>
              {editingReplies.map((reply, index) => (
                <div key={index}>
                  <textarea rows={3} maxLength={5000} value={reply}
                    aria-label={`Reply ${index + 1}`}
                    placeholder={`Reply ${index + 1}`}
                    onChange={(event) => setEditingReplies((current) => current.map(
                      (part, partIndex) => partIndex === index ? event.target.value : part,
                    ))} />
                  <Button type="button" variant="quiet" size="sm"
                    onClick={() => setEditingReplies((current) => current.filter(
                      (_part, partIndex) => partIndex !== index,
                    ))}>Remove</Button>
                </div>
              ))}
              <Button type="button" variant="secondary" size="sm"
                onClick={() => setEditingReplies((current) => [...current, ""])}>
                Add reply
              </Button>
            </fieldset>
            {/* What is being edited is two thirds of the post. The campaign
                supplies the rest, and it used to supply it invisibly: somebody
                writing a caption here had no way to know a disclosure would be
                prepended to it, that the hashtags would be moved below a link,
                or that on TikTok the link would not appear in the post at all. */}
            <section className="campaign-post-anatomy">
              <strong>What goes out</strong>
              <ol>
                <li>
                  <b>Disclosure</b>
                  <span>{autopilot.disclosure
                    ? `Leads the caption whenever a product is attached: "${autopilot.disclosure}"`
                    : "None set. A post with a product attached will be refused until Campaign settings has one."}</span>
                </li>
                <li><b>Your copy</b><span>The caption above, then the hashtags.</span></li>
                <li>
                  <b>The product link</b>
                  <span>{autopilot.offer_mode === "none"
                    ? "Nothing is attached: this campaign posts organically."
                    : "Added per account, in the place that account allows."}</span>
                </li>
                <li>
                  <b>Your follow-up</b>
                  <span>{editing.thread.length || editing.first_comment
                    ? "Publishes after the post, ahead of any generated product replies."
                    : "Nothing written; only generated product replies would follow the post."}</span>
                </li>
              </ol>
              {autopilot.offer_mode !== "none" && destinations.length > 0 && (
                <ul className="campaign-link-map" aria-label="Where the link lands">
                  {destinations.map((item) => (
                    <li key={item.id}>
                      <PlatformIcon platform={item.platform} size={18} />
                      <strong>{item.label}</strong>
                      <Badge tone={placementTone(item.link_placement)}>
                        {t(`autopilot.placement.${item.link_placement}`)}
                      </Badge>
                      <small>{item.link_placement === "bio"
                        ? `Not clickable in the post. The caption points at the profile: "${autopilot.bio_hint}".`
                        : item.link_placement === "first_comment"
                          ? `In the ${followUpKind(item.platform)}, straight after the post.`
                          : item.link_placement === "none"
                            ? "No link goes out here at all."
                            : "In the caption itself, below your copy."}</small>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <Button type="submit" variant="primary" busy={busy === "edit-copy"}>Save post</Button>
          </form>
        )}
      </Card>}
          </div>
          <aside className="campaign-work-side" id="campaign-setup">
      {<Card
        eyebrow={t("autopilot.whereEyebrow")}
        title={t("autopilot.destinations", { count: destinations.length })}
        aside={canEdit ? (
          <Button variant="secondary" size="sm" busy={busy === "accounts"}
            onClick={() => void loadAccounts()}>{t("autopilot.addAccount")}</Button>
        ) : undefined}
      >
        {destinations.length === 0 ? (
          <p className="autopilot-empty">{t("autopilot.noDestinations")}</p>
        ) : (
          <ul className="autopilot-destinations">
            {destinations.map((item) => (
              <li key={item.id}>
                <div className="campaign-account-identity">
                  <PlatformIcon platform={item.platform} size={30} />
                  <span>
                    <strong>{item.label}</strong>
                    <small>{platformLabels[item.platform]} · {item.provider_label ?? item.provider}
                      {accountIdentity({ account: item.connection_account })
                        ? ` · ${accountIdentity({ account: item.connection_account })}`
                        : ""}
                      {/* Only worth saying where it is true: every destination
                          takes video, so "video only" is the exception and
                          "carousels too" is the news. */}
                      {item.accepts_carousel ? " · carousels too" : ""}</small>
                  </span>
                </div>
                {/* The decision, next to the account it applies to. Someone who
                    expects a tappable link on TikTok needs to find out here,
                    not from a post that already went out. */}
                <Badge tone={placementTone(item.link_placement)}>
                  {t(`autopilot.placement.${item.link_placement}`)}
                </Badge>
                <p className="autopilot-placement-reason">{item.link_reason}</p>
                {canEdit && (
                  <div className="autopilot-destination-controls">
                  <label className="autopilot-placement-choice">
                    Link placement
                    <select
                      value={item.link_placement_setting}
                      onChange={(event) => void run("placement", async () => {
                        await json(await apiFetch(
                          `${base}/destinations/${item.id}/placement`,
                          {
                            method: "POST",
                            headers: { "content-type": "application/json" },
                            body: JSON.stringify({
                              link_placement: event.target.value,
                            }),
                          },
                        ));
                        await refresh();
                        return `Link placement updated for ${item.label}.`;
                      })}
                    >
                      <option value="auto">Auto — network decides (recommended)</option>
                      <option value="caption">Always in the caption</option>
                      <option value="first_comment">First comment, where deliverable</option>
                      <option value="bio">Always via bio link</option>
                    </select>
                  </label>
                  {/* The icon, like every other removal in the app. As a word
                      it stretched to a grid column: 106px of button beside a
                      250px select, two pixels shorter than it, which is what
                      made the row look assembled from spare parts. */}
                  <Button
                    data-destination-remove=""
                    variant="quiet"
                    size="sm"
                    title={t("common.delete")}
                    aria-label={t("autopilot.removeDestination", { label: item.label })}
                    onClick={() => void run("remove", async () => {
                      await json(await apiFetch(`${base}/destinations/${item.id}`,
                        { method: "DELETE" }));
                      return t("autopilot.destinationRemoved", { label: item.label });
                    })}
                  ><ActionIcon name="delete" /></Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {adding && (
          <div className="autopilot-account-picker">
            <div className="autopilot-picker-head">
              <strong>{selectedAccounts.size
                ? `${selectedAccounts.size} accounts selected`
                : t("autopilot.chooseAccounts")}</strong>
              <Button variant="quiet" size="sm" onClick={() => setAdding(false)}>
                {t("common.close")}
              </Button>
            </div>
            <div className="autopilot-picker-tools">
              <Button variant="primary" size="sm" disabled={!selectedAccounts.size}
                busy={busy === "add-accounts"} onClick={() => void run("add-accounts", async () => {
                  const chosen = accounts.filter((account) =>
                    selectedAccounts.has(`${account.provider}:${account.id}`));
                  await Promise.all(chosen.map(async (account) => json(await apiFetch(
                    `${base}/destinations`, {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify({
                        provider: account.provider,
                        integration_id: account.id,
                        platform: account.platform,
                        label: account.label,
                      }),
                    }))));
                  setSelectedAccounts(new Set());
                  setAdding(false);
                  // The product decision is in this same area now, so the
                  // only move left is on to the schedule.
                  if (slots.length) void loadRecommendations();
                  else jumpTo("schedule");
                  return `${chosen.length} ${chosen.length === 1 ? "account" : "accounts"} assigned.`;
                })}>Assign selected accounts</Button>
            </div>
            <ul className="autopilot-media-picker">
              {accounts
                .filter((account) => account.available !== false)
                .filter((account) => !destinations.some(
                  (item) => item.integration_id === account.id
                    && item.provider === account.provider))
                .map((account) => (
                  <li key={`${account.provider}:${account.id}`}>
                    <label>
                      <input type="checkbox"
                        checked={selectedAccounts.has(`${account.provider}:${account.id}`)}
                        onChange={() => setSelectedAccounts((current) => {
                          const key = `${account.provider}:${account.id}`;
                          const next = new Set(current);
                          if (next.has(key)) next.delete(key); else next.add(key);
                          return next;
                        })} />
                      <PlatformIcon platform={account.platform} size={28} />
                      <span>
                        <strong>{account.label}</strong>
                        {/* Which login carries it, not just which engine. Two
                            Buffer connections put the same engine name on every
                            row; the account the engine reports is the thing
                            that tells them apart. */}
                        <small>{platformLabels[account.platform]} · {account.provider_label}
                          {accountIdentity({ account: account.connection_account })
                            ? ` · ${accountIdentity({ account: account.connection_account })}`
                            : ""}</small>
                      </span>
                    </label>
                  </li>
                ))}
              {!accounts.some((account) => account.available !== false)
                && <li>{t("autopilot.noAccounts")}</li>}
            </ul>
            <small className="campaign-source-note">Source: available connected accounts in Publish.</small>
          </div>
        )}
      </Card>}
        {<div className="autopilot-settings">
          {/* Products, the disclosure and the profile-link wording moved
              to Campaign settings. All three are the campaign saying what
              it is and how commercial it is, answered once; and the two
              texts follow the post language, which is already there and
              rewrites them when it changes. What is left below is the
              analysis, which is a tool rather than a setting. */}

          {autopilot.offer_mode === "smart" && (
            <div className="campaign-product-intelligence">
              {/* Named for the one thing this block does that nothing else
                  does. It used to be headed "Best-fit products" over a
                  description of how ranking works - which the queue rows now
                  answer per post, and Review products answers per post in
                  detail. Narrowing the pool smart matching may draw from is
                  the decision that lives only here. */}
              <div className="campaign-product-heading">
                <div>
                  <strong>Which products this campaign may use</strong>
                  <small>Smart matching draws from every imported offer.
                    Shortlist to narrow it to a few.</small>
                </div>
                <Button variant="secondary" size="sm" busy={busy === "recommendations"}
                  onClick={() => void loadRecommendations()}>Rank all products</Button>
              </div>
              {autopilot.candidate_offer_ids.length > 0 && (
                <div className="campaign-shortlist-note">
                  Matching is limited to {autopilot.candidate_offer_ids.length} shortlisted product{autopilot.candidate_offer_ids.length === 1 ? "" : "s"}.
                  <Button variant="quiet" size="sm" onClick={() => void save({ candidate_offer_ids: [] })}>Use all offers</Button>
                </div>
              )}
              {recommendations && !recommendations.item_id && (
                <>
                  {/* The four-number strip is gone. Posting times are listed
                      in full in the card below it, platforms are the accounts
                      card above it, products per post is in Campaign settings,
                      and "4 evidence sources" was a number nobody could act
                      on. How products rotate across posts is not said anywhere
                      else, so it stays. */}
                  <p className="campaign-rotation-note">{recommendations.strategy.rotation}</p>
                  <ul className="campaign-product-matches">
                    {recommendations.matches.map((match) => {
                      const shortlisted = autopilot.candidate_offer_ids.includes(match.offer_id);
                      return <li key={match.offer_id}>
                        <div className="campaign-match-score" data-confidence={match.confidence}>
                          <strong>{match.score}</strong><small>% fit</small>
                        </div>
                        <div className="campaign-match-copy">
                          <strong>{match.product_name}</strong>
                          <small>{match.reasons[0]}</small>
                          <span>{match.matched_terms.slice(0, 5).map((term) => <em key={term}>{term}</em>)}</span>
                        </div>
                        <Badge
                        tone={match.confidence === "high" ? "good"
                          : match.confidence === "medium" ? "warn" : "neutral"}
                        title={t(`autopilot.matchConfidence.${match.confidence}`)}
                      >
                          {match.confidence}
                        </Badge>
                        <Button variant={shortlisted ? "secondary" : "quiet"} size="sm" disabled={!canEdit}
                          onClick={() => {
                            const next = shortlisted
                              ? autopilot.candidate_offer_ids.filter((id) => id !== match.offer_id)
                              : [...autopilot.candidate_offer_ids, match.offer_id];
                            void save({ candidate_offer_ids: next });
                          }}>{shortlisted ? "Shortlisted" : "Shortlist"}</Button>
                      </li>;
                    })}
                    {!recommendations.matches.length && <li className="autopilot-empty">No usable imported offers match this campaign yet.</li>}
                  </ul>
                </>
              )}
            </div>
          )}


          {/* The caps, the authority and the ranking axis moved to
              Campaign settings. Every one is set when the campaign is
              described and rarely touched after, and this pane is the one
              somebody works in daily - the queue beside it is the reason
              they open it. What stays here is what changes while running:
              the accounts, the product matching, and the words the posts
              are scaffolded with. */}
        </div>}
      {<Card title="Posting times" aside={
        <Link className="ui-button ui-button-secondary ui-button-sm" href="/publish">
          Edit in Publish
        </Link>
      }>
        <p className="autopilot-lede">
          Shared by every campaign in this workspace; Publish owns them.
        </p>
        <div className="campaign-schedule-readonly">
          <Badge tone="neutral">{scheduleTimezone}</Badge>
          {slots.map((slot) => (
            <span key={slot.id}>{slot.weekday_label} · {slot.time}</span>
          ))}
          {!slots.length && <span>No posting times configured.</span>}
        </div>
      </Card>}
          </aside>
        </div>
      )}

      <EffectEditor
        open={effectOpen}
        workspaceId={workspaceId}
        targets={selectedLibrary.map((asset) => ({
          id: asset.id,
          title: asset.title,
          path: handoffPath(asset),
          mediaKind: asset.media_kind,
        }))}
        assetIds={selectedLibrary.map((asset) => asset.id)}
        canEdit={canEdit}
        apiFetch={apiFetch}
        onClose={() => setEffectOpen(false)}
        onRendered={succeed}
      />

      {view === "posts" && <>
      {/* "Posting timeline", not "Upcoming posts": the committed section below
          keeps recently delivered jobs on screen, and a delivered job under an
          "upcoming" heading reads like a contradiction. */}
      <Card
        eyebrow="Active campaign pipeline"
        title="Posting timeline"
        aside={
          <span className="campaign-timeline-tools">
            <SegmentedControl
              label="Timeline view"
              value={timelineView}
              onChange={setTimelineView}
              options={[
                { value: "list", label: "List" },
                { value: "calendar", label: "Calendar" },
              ]}
            />
            <Button variant="secondary" size="sm" busy={busy === "preview"}
              disabled={!ready.configured}
              onClick={() => void loadPreview()}><ActionIcon name="refresh" />Refresh outlook</Button>
          </span>
        }
      >
        {/* No lede. "Durable publishing jobs that persist across sessions"
            explained the implementation to someone who asked when the post
            goes out; the rows below answer that themselves. */}
        {/* Grouped at the top, per the run-by-exception contract: everything
            the autopilot deferred to a person, with the reason on it. */}
        {/* Held posts live in the approval card at the top of the panel,
            previewed as they will look; the timeline keeps to what has
            posted and what will. */}
        {/* One timeline, past to future.
         *
         * What has posted and what will post is one story. It used to be two:
         * delivered jobs in a flat list that did not say which engine carried
         * them, then a day-grouped outlook that did. Same campaign, same
         * accounts, two designs and two answers to "when".
         *
         * So both become rows of one kind, grouped by day and ordered in time.
         * Whether a post has gone out is a badge on the row rather than which
         * list it landed in. */}
        {timeline.length > 0 && (
          <div className="campaign-pipeline-summary" aria-label="Campaign timeline summary">
            {/* Three numbers that change decisions. Active days and account
                counts were true and useless - both already visible in the
                rows and the tab strip. */}
            <span><strong>{deliveredCount}</strong><small>delivered</small></span>
            <span><strong>{plannedCount}</strong><small>planned</small></span>
            <span className={deliveryWarnings ? "warn" : "good"}>
              <strong>{deliveryWarnings}</strong><small>delivery warnings</small>
            </span>
          </div>
        )}
        {preview && timeline.length === 0 && (
          // The last-run banner above often carries this exact sentence;
          // saying it once is information, twice is noise.
          preview.note !== autopilot.last_note && (
            <p className="autopilot-note" role="status">{preview.note}</p>
          )
        )}
        {timeline.length > 0 && timelineView === "calendar" && (
          <TimelineCalendar
            entries={timeline}
            timezone={readerZone}
            month={calendarMonth
              ?? new Date().toLocaleDateString("en-CA", {
                timeZone: readerZone,
              }).slice(0, 7)}
            onMonthChange={setCalendarMonth}
          />
        )}
        {timeline.length > 0 && timelineView === "list" && (
          <div className="campaign-pipeline">
            {timelineDays.map(([day, entries]) => (
              <section className="campaign-pipeline-day" key={day}>
                <header>
                  <strong>{dayHeading(entries[0].at, readerZone)}</strong>
                  <span>{entries.length} {entries.length === 1 ? "post" : "posts"}</span>
                </header>
                <ol>
                  {entries.map((entry, index) => {
                    const destination = entry.destination ?? destinations.find(
                      (item) => item.id === entry.destination_id) ?? null;
                    const platform = destination?.platform;
                    const thumbnailAsset: LibraryAsset | null = entry.asset_id ? {
                      id: entry.asset_id,
                      title: entry.title ?? "Campaign video",
                      original_path: "",
                      media_kind: "video",
                      duration_ms: null,
                      platform: platform ?? null,
                      creator: null,
                      width: null,
                      height: null,
                      versions: [{ id: `${entry.asset_id}-thumbnail`, kind: "thumbnail" }],
                    } : null;
                    return (
                      <li
                        key={entry.key}
                        className={[
                          entry.problem ? "refused" : "",
                          entry.kind === "delivered" ? `delivered ${entry.status ?? ""}` : "",
                        ].filter(Boolean).join(" ") || undefined}
                      >
                        <div className="campaign-pipeline-time">
                          <time dateTime={entry.at}>{new Date(entry.at).toLocaleTimeString(undefined, {
                            hour: "numeric", minute: "2-digit", timeZone: readerZone,
                          })}</time>
                          <i aria-hidden="true" />
                        </div>
                        <div className="campaign-pipeline-thumb">
                          {thumbnailAsset
                            ? <AssetThumbnail asset={thumbnailAsset} workspaceId={workspaceId} apiFetch={apiFetch} />
                            : <span className="campaign-pipeline-thumb-empty"><ActionIcon name="play" /></span>}
                        </div>
                        <article>
                          {/* Where it went, and what carried it. The engine is
                              named on every row now: with two logins to one
                              engine, "which account" and "through which
                              connection" are different questions. */}
                          <div className="campaign-pipeline-destination">
                            {platform && <PlatformIcon platform={platform} size={24} />}
                            <span>
                              <strong>{entry.page_url ? (
                                <a href={entry.page_url} target="_blank" rel="noreferrer">
                                  {destination?.label ?? entry.destination_id ?? "Former account"}
                                </a>
                              ) : (destination?.label ?? entry.destination_id ?? "Former account")}</strong>
                              <small>{platform ? platformLabels[platform] : "Social account"}
                                {destination?.provider ? ` · ${destination.provider}` : ""}</small>
                            </span>
                            {/* Whether it has gone out, in one badge. This was
                                the difference between the two lists. */}
                            {entry.kind === "delivered" ? (
                              <Badge tone={entry.status === "succeeded" ? "good"
                                : entry.status === "failed" ? "warn" : "neutral"}>
                                {entry.status === "succeeded"
                                  ? `Delivered · ${entry.delivery}`
                                  : entry.status ?? "delivered"}
                              </Badge>
                            ) : (
                              <Badge tone={entry.problem ? "warn" : "neutral"}>
                                {autopilot.delivery === "draft" ? "Planned · review draft"
                                  : autopilot.delivery === "schedule" ? "Planned · scheduled"
                                  : "Planned · publish now"}
                              </Badge>
                            )}
                          </div>
                          <h4>{entry.post_url ? (
                            <a href={entry.post_url} target="_blank" rel="noreferrer">
                              {displayTitle(entry.title) || "Untitled campaign post"}
                            </a>
                          ) : (displayTitle(entry.title) || "Untitled campaign video")}</h4>
                          {/* Which queued post this outing is of, and a way
                              back to it. A post repeats, so the same one
                              appears on the schedule several times, and
                              without this the reader cannot tell that. */}
                          {entry.queue_item_id && queueById.get(entry.queue_item_id) && (
                            <p className="campaign-entry-source">
                              {/* Editable from here, not only findable. This
                                  is where somebody reads the post and decides
                                  it needs changing, and sending them to
                                  another tab to find the row again loses the
                                  thought that started it. */}
                              {canEdit && entry.kind === "planned" && (
                                <button type="button" className="campaign-entry-edit"
                                  onClick={() => {
                                    const item = queueById.get(entry.queue_item_id!)!;
                                    setView("content");
                                    setEditing(item);
                                    setEditingReplies(item.thread.length ? item.thread : [""]);
                                    revealPanel("campaign-edit-content");
                                  }}>Edit this post</button>
                              )}
                              From{" "}
                              <button type="button" onClick={() => {
                                setView("content");
                                window.requestAnimationFrame(() => {
                                  const row = document.getElementById(
                                    `queued-${entry.queue_item_id}`);
                                  row?.scrollIntoView({ behavior: "smooth", block: "center" });
                                  row?.classList.add("just-linked");
                                  window.setTimeout(
                                    () => row?.classList.remove("just-linked"), 1600);
                                });
                              }}>
                                {displayTitle(queueById.get(entry.queue_item_id)!.title)
                                  || "a queued post"}
                              </button>
                              {(queueById.get(entry.queue_item_id)!.times_posted ?? 0) > 0
                                && ` · posted ${queueById.get(entry.queue_item_id)!.times_posted}× before`}
                            </p>
                          )}
                          {entry.problem && (
                            <p className="autopilot-refusal" role="status">
                              <strong>{t("autopilot.wouldBeRefused")}</strong> {entry.problem}
                            </p>
                          )}
                          {entry.last_error && <p className="autopilot-refusal">{entry.last_error}</p>}
                          {entry.route && (
                            <div className={`campaign-affiliate-route ${entry.offer_ids.length ? "attached" : "organic"}`}>
                              <span>
                                <strong>{entry.route.label}</strong>
                                <small>{entry.route.detail}</small>
                              </span>
                              <Badge tone={placementTone(entry.placement ?? "caption")}>
                                {entry.offer_ids.length
                                  ? `${entry.offer_ids.length} ${entry.offer_ids.length === 1 ? "product" : "products"}`
                                  : "No products"}
                              </Badge>
                            </div>
                          )}
                          {entry.product_details.length > 0 && (
                            <ul className="campaign-pipeline-products" aria-label="Attached affiliate products">
                              {entry.product_details.map((product, productIndex) => (
                                <li key={`${product.offer_id}-${productIndex}`}>
                                  <span aria-hidden="true">{productIndex + 1}</span>
                                  <strong>{product.name}</strong>
                                  {/* Where this particular link sits. The
                                      first-comment case fell through to "Post
                                      content", so an entry headed First
                                      comment listed its product as being in
                                      the caption two lines below. */}
                                  <small>{entry.placement === "bio"
                                    ? "Profile bio"
                                    : productIndex > 0 && entry.thread.length
                                      ? `Reply ${productIndex}`
                                      : entry.placement === "first_comment"
                                        ? followUpLabel(entry.destination?.platform, 0)
                                        : "Post content"}
                                    {/* What it pays, beside what it is. The rate
                                        is the reason this offer was attached
                                        rather than another, and the row named
                                        the product without ever saying it. */}
                                    {commissionLabel(product) ? ` · ${commissionLabel(product)}` : ""}</small>
                                </li>
                              ))}
                            </ul>
                          )}
                          <details className="campaign-pipeline-content"
                            open={index === 0 && entry.kind === "planned"}>
                            <summary>{entry.kind === "delivered"
                              ? "See exactly what posted"
                              : "See exactly what will post"}</summary>
                            <div>
                              {/* The media exactly as it went out. Played from
                                  a blob rather than straight off the API, so a
                                  download manager has no request to grab - see
                                  TimelinePlayer. */}
                              {entry.video_path && (
                                <TimelinePlayer
                                  src={`${apiBaseUrl()}/api/workspaces/${workspaceId}/publishing/media/preview?path=${encodeURIComponent(entry.video_path)}`}
                                  title={entry.video_path}
                                />
                              )}
                              {!entry.video_path && entry.image_paths.length > 0 && (
                                <div className="timeline-media-strip">
                                  {entry.image_paths.map((path) => (
                                    // eslint-disable-next-line @next/next/no-img-element
                                    <img
                                      key={path}
                                      className="timeline-media"
                                      src={`${apiBaseUrl()}/api/workspaces/${workspaceId}/publishing/media/preview?path=${encodeURIComponent(path)}`}
                                      alt={path}
                                    />
                                  ))}
                                </div>
                              )}
                              <strong>Post content</strong>
                              <pre>{entry.caption}</pre>
                              {entry.first_comment && <>
                                <strong>First comment · affiliate link</strong>
                                <pre>{entry.first_comment}</pre>
                              </>}
                              {entry.thread.map((reply, replyIndex) => <div key={`${entry.key}-reply-${replyIndex}`}>
                                <strong>Reply {replyIndex + 1} · affiliate link</strong>
                                <pre>{reply}</pre>
                              </div>)}
                            </div>
                          </details>
                          {entry.reason && <small className="campaign-pipeline-reason">{entry.reason}</small>}
                        </article>
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}
          </div>
        )}
        {!preview && !ready.configured && (
          <p className="autopilot-empty">{t("autopilot.previewBlocked")}</p>
        )}
        {/* No deploy bar. The Post automatically switch is the one lever:
            switching on activates the campaign and runs it, and this timeline
            is where what it did shows up. */}
      </Card>
      </>}
      {/* Configuration, so it lives in Setup: the times themselves are
          visible in the timeline where they matter. */}
    </div>
  );
}
