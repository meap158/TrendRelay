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

import dynamic from "next/dynamic";
import { clipLength, handoffPath } from "../../lib/media-rules";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bookmark, Check, ChevronDown, Circle, Eye, Heart, Info, MessageCircle, Share2 } from "lucide-react";

import { apiBaseUrl } from "../../lib/api";
import { AUTHORITIES } from "./authority-options";

import { Button } from "../ui/button";
import { SegmentedControl } from "../ui/segmented";
import { FilterChipStrip } from "../ui/filter-strip";
import { ActionIcon } from "../ui/action-icons";
import { Dialog } from "../ui/dialog";
import { WaitingBlock } from "../ui/waiting-block";
import { SelectionCheckbox } from "../ui/selection-checkbox";
import { SortableHeader, nextSort } from "../ui/sortable-header";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";
import type { SortState } from "../ui/sortable-header";
import { Badge, Card, Switch } from "../ui/primitives";
import { Select } from "../ui/select";
import { useT } from "../i18n-provider";
import { LOCALES } from "../../lib/i18n/locales";
import { EffectEditor } from "../library/effect-editor";
import { ActionMenu, type ActionMenuItem } from "../ui/action-menu";
import {
  LIBRARY_SELECTION_ACTIONS,
  SELECTION_ACTION_ICON,
  SELECTION_ACTION_KEY,
  selectionActionState,
  type LibraryMediaKind,
  type LibrarySelectionActionId,
  type LibrarySelectionTarget,
} from "../../lib/library-selection-actions";

const CaptionEditor = dynamic(() => import("../library/caption-editor").then((m) => m.CaptionEditor), { ssr: false });
const BulkVoiceEditor = dynamic(() => import("../library/bulk-voice-editor").then((m) => m.BulkVoiceEditor), { ssr: false });
const BatchTranscribe = dynamic(() => import("../library/batch-transcribe").then((m) => m.BatchTranscribe), { ssr: false });
import { TimelineImage, TimelinePlayer } from "./timeline-player";
import { accountIdentity, type EngineAccount } from "../publishing-account";
import { profileUrl } from "../../lib/social-profile";
import { commissionLabel, type CommissionBearing } from "../commission";
// The same money the Attribution table prints, so a price reads the same
// in the campaign that promotes the product as in the list it came from.
import { money } from "../attribution/format";

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
  type AssetFilterValues,
} from "../ui/asset-filters";
import {
  SELECT_ALL_ASSET_CEILING,
  useLibraryAssets,
} from "../../lib/use-library-assets";
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
  /** What the account is called on the network, where the engine reports one. */
  handle?: string | null;
  provider: string;
  provider_label: string;
  /** Whose login this account is reached through, as the engine reports it. */
  connection_account?: EngineAccount;
  available?: boolean;
  unavailable_reason?: string | null;
  page_key?: string;
};

type PostingPreset = {
  id: string;
  label: string;
  summary: string;
  kind: "builtin" | "custom";
  slots: { weekday: number; time: string }[];
};

const POSTING_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function scheduleLines(slots: { weekday: number; time: string }[]): string[] {
  const groups = new Map<number, string[]>();
  for (const slot of slots) {
    const times = groups.get(slot.weekday) ?? [];
    if (!times.includes(slot.time)) times.push(slot.time);
    groups.set(slot.weekday, times);
  }
  const displayTime = (value: string) => {
    const [hour = 0, minute = 0] = value.split(":").map(Number);
    return new Date(2000, 0, 1, hour, minute).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
  };
  if (groups.has(-1)) {
    return [(groups.get(-1) ?? []).map(displayTime).join(" · ")];
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left - right)
    .map(([weekday, times]) => `${POSTING_DAY_NAMES[weekday] ?? "Day"}: ${times.map(displayTime).join(" · ")}`);
}

/** A schedule picker whose named presets explain the wall-clock times they
 *  represent. The explanation is hover/focus help on desktop and a tap-open
 *  disclosure on touch screens; either the menu or the disclosure closes on
 *  outside click or Escape. */
function PostingPresetSelect({
  value,
  presets,
  timezone,
  workspaceSlots,
  pagePresetId,
  campaignPresetId,
  inheritLabel,
  onChange,
}: {
  value: string;
  presets: PostingPreset[];
  timezone: string;
  workspaceSlots: Slot[];
  pagePresetId?: string;
  /**
   * The campaign's own hours, when this control is choosing for one account
   * inside it. Passed so "inherit" can show what inheriting would actually
   * land on - which is the campaign's times when it has any, not the page's.
   */
  campaignPresetId?: string;
  /**
   * What inheriting means at this level, when it is not what resolving says.
   *
   * A campaign inherits per account, so there is no single schedule to name
   * and the caller supplies the wording. Everywhere else the label is left to
   * be worked out below, from whichever level actually answers.
   */
  inheritLabel?: string;
  onChange: (value: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState<"above" | "below">("below");
  const [shownInfo, setShownInfo] = useState<string | null>(null);
  const [pinnedInfo, setPinnedInfo] = useState<string | null>(null);
  const pagePreset = presets.find((preset) => preset.id === pagePresetId);
  const campaignPreset = presets.find((preset) => preset.id === campaignPresetId);
  // Same order the server resolves in: the campaign's own hours outrank the
  // page assignment, so "inherit" has to show the campaign's when it has any -
  // otherwise this row promises times the scheduler will not use.
  const inheritedFrom = campaignPreset ?? pagePreset;
  const inheritedSlots = inheritedFrom?.slots ?? workspaceSlots;
  const choices = [
    {
      id: "",
      // Named after whatever inheriting would land on, rather than a fixed
      // "page / workspace" - which stopped being true the moment a campaign
      // could hold hours of its own, and would have had this row promising
      // times the scheduler was not going to use.
      label: inheritLabel ?? (
        campaignPreset
          ? "Inherit this campaign's schedule"
          : pagePreset
            ? "Inherit this page's schedule"
            : "Inherit the workspace schedule"
      ),
      summary: campaignPreset
        ? `Uses “${campaignPreset.label}”, this campaign's own times.`
        : pagePreset
          ? `Uses “${pagePreset.label}”, assigned to this page.`
          : "Uses the workspace posting times.",
      slots: inheritedSlots,
    },
    ...presets,
  ];
  const selected = choices.find((choice) => choice.id === value) ?? choices[0]!;
  /* Never empty, so the panel's presence is not itself a layout change: the row
     being hovered or arrowed onto, else one held up for comparison, else the
     schedule already chosen - which is the useful thing to see on opening. */
  const detailed = choices.find(
    (choice) => choice.id === (shownInfo ?? pinnedInfo ?? value),
  ) ?? selected;
  const detailLines = scheduleLines(detailed.slots);

  function close() {
    setOpen(false);
    setShownInfo(null);
    setPinnedInfo(null);
  }

  function reveal() {
    const rect = trigger.current?.getBoundingClientRect();
    if (rect) {
      const below = window.innerHeight - rect.bottom;
      setSide(below < 260 && rect.top > below ? "above" : "below");
    }
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    const resized = () => close();
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", resized);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", resized);
    };
  }, [open]);

  function keys(event: React.KeyboardEvent) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
      trigger.current?.focus();
      return;
    }
    if (!open && ["ArrowDown", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      reveal();
      requestAnimationFrame(() => root.current?.querySelector<HTMLButtonElement>(".posting-preset-choice")?.focus());
      return;
    }
    if (!open || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const items = [...(root.current?.querySelectorAll<HTMLButtonElement>(".posting-preset-choice") ?? [])];
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0
      : event.key === "End" ? items.length - 1
        : event.key === "ArrowUp" ? Math.max(0, current - 1)
          : Math.min(items.length - 1, current + 1);
    items[next]?.focus();
  }

  return (
    <div className="posting-preset-select" ref={root} onKeyDown={keys}>
      <button
        type="button"
        ref={trigger}
        className="search-select-trigger posting-preset-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => open ? close() : reveal()}
      >
        <span>{selected.label}</span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      {open && (
        <div className="posting-preset-menu" data-side={side}>
          {/* The list and the detail are siblings, not nested.
            *
            * Expanding the times inside the hovered row pushed every row under
            * it down by about eighty pixels - so hovering one option moved the
            * next one out from under the pointer heading for it. One panel
            * below the list instead: it is always present, always showing
            * something, so revealing times never changes the list's layout. */}
          <div className="posting-preset-list" role="menu" aria-label="Posting-time presets">
            {choices.map((choice) => (
              <div
                className="posting-preset-option"
                key={choice.id || "__inherit"}
                /* The whole row shows its times, not just the icon.
                 *
                 * Reading five schedules meant hitting a 30px target five times
                 * to learn what any of them meant - and arrowing through with a
                 * keyboard revealed nothing at all, because only the icon
                 * carried the handlers. `onFocus` sits here rather than on the
                 * button because React's focus events bubble, so arrowing onto
                 * a choice shows its times the way hovering does. */
                onMouseEnter={() => setShownInfo(choice.id)}
                onMouseLeave={() => setShownInfo(null)}
                onFocus={() => setShownInfo(choice.id)}
                onBlur={() => setShownInfo(null)}
              >
                <button
                  type="button"
                  className="posting-preset-info"
                  /* No longer the way in - the row is. What is left is pinning:
                     holding one schedule up while the pointer goes elsewhere,
                     which is how two of them get compared. */
                  aria-label={`${pinnedInfo === choice.id ? "Unpin" : "Pin"} times for ${choice.label}`}
                  aria-pressed={pinnedInfo === choice.id}
                  onClick={() => setPinnedInfo(pinnedInfo === choice.id ? null : choice.id)}
                >
                  <Info size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="posting-preset-choice"
                  role="menuitemradio"
                  aria-checked={choice.id === value}
                  /* Its own hidden description rather than the shared panel: one
                     node whose text changes as the pointer moves would describe
                     whichever option was last hovered, not this one. */
                  aria-describedby={`posting-preset-times-${choice.id || "inherit"}`}
                  onClick={() => {
                    onChange(choice.id);
                    close();
                    trigger.current?.focus();
                  }}
                >
                  {choice.label}
                </button>
                <span className="sr-only" id={`posting-preset-times-${choice.id || "inherit"}`}>
                  {`${choice.summary} ${scheduleLines(choice.slots).join(", ") || "No posting times set"}. ${timezone}.`}
                </span>
              </div>
            ))}
          </div>
          {/* Announced through each option's own description above, so this is
              the sighted reader's copy and is skipped by a screen reader rather
              than read out a second time. */}
          <div className="posting-preset-detail" aria-hidden="true">
            <p>{detailed.summary}</p>
            {detailLines.length
              ? detailLines.map((line) => <strong key={line}>{line}</strong>)
              : <strong>No posting times set</strong>}
            <small>{timezone}</small>
          </div>
        </div>
      )}
    </div>
  );
}

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
  page_key?: string | null;
  /** Null inherits the page assignment and then the workspace schedule. */
  posting_preset_id?: string | null;
  posting_schedule?: {
    source: "destination" | "campaign" | "page" | "workspace";
    preset_id: string | null;
    label: string;
    slot_count: number;
  };
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
  /** This post's own wording. Null where it uses the campaign's. */
  disclosure: string | null;
  bio_hint: string | null;
  state: "draft" | "approved" | "paused" | "retired";
  position: number;
  times_posted: number;
  last_posted_at: string | null;
  offer_ids: string[];
  offer_match: {
    matches?: OfferMatch[];
    strategy?: MatchStrategy;
    selected_offer_ids?: string[];
    /** What this post would actually carry, resolved by the matcher. */
    chosen_offer_ids?: string[];
  };
};

type Autopilot = {
  enabled: boolean;
  /** Whether a disclosure is added at all. Off unless the campaign asks. */
  disclose: boolean;
  delivery: "draft" | "schedule" | "now";
  /** How much the campaign may do alone; run by exception is the default. */
  authority: "assist" | "auto_draft" | "run_by_exception" | "autonomous";
  /** How near this campaign is to being allowed to post without a person. */
  graduation?: {
    published: number;
    required: number;
    unresolved: number;
    ready: boolean;
  };
  /** What ranking optimises for. */
  priority: "reach" | "discussion" | "revenue" | "balanced";
  offer_id: string | null;
  offer_mode: "smart" | "manual" | "none";
  candidate_offer_ids: string[];
  max_products_per_post: number;
  disclosure: string;
  bio_hint: string;
  min_recycle_days: number;
  /** Whether a post may go out more than once on the same account at all. */
  repeat_posts: boolean;
  /** Whether smart matching spreads itself across the tagged products. */
  rotate_products: boolean;
  daily_cap_per_account: number;
  weekly_post_cap: number | null;
  /** The campaign's own posting times. Null lets each account inherit. */
  posting_preset_id: string | null;
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
  /**
   * Postings the queue still holds, or null when repeats make it endless.
   *
   * A campaign that does not repeat spends itself: each written post has one
   * posting per account and then it is done.
   */
  remaining_outings: number | null;
  /** How often a post goes to an account that is not currently leading. */
  exploration_every: number;
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

/**
 * A post being written, composed exactly as each account would receive it.
 *
 * The same function the scheduler publishes through, so this is not a
 * rendering of the rules - it is the rules' own answer.
 */
type ComposedPost = {
  accounts: {
    destination_id: string;
    label: string;
    platform: PublishingPlatform;
    placement?: "caption" | "first_comment" | "bio" | "none";
    placement_reason?: string;
    title?: string | null;
    caption?: string;
    first_comment?: string | null;
    thread?: string[];
    /** Why this account would not take the post at all, if it would not. */
    refused: string | null;
  }[];
  products: { offer_id: string; name: string; link: string }[];
  selection: string;
  /** What this post will actually use, campaign's or its own. */
  disclosure: string;
  bio_hint: string;
  campaign_disclosure: string;
  campaign_bio_hint: string;
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

/** A product this campaign is allowed to promote. */
type TaggedProduct = {
  offer_id: string;
  product_id: string;
  name: string;
  brand?: string | null;
  category?: string | null;
  marketplace?: string | null;
  network: string;
  availability: string;
  commission_bps?: number | null;
  commission_flat_cents?: number | null;
  currency?: string | null;
  price_cents?: number | null;
  /** When it was tagged to this campaign, which is the list's own order. */
  tagged_at?: string | null;
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
  /** Delivered rows only: its measured engagement, once read back. */
  metrics: PostMetrics | null;
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

/** A post's own engagement, once the engine has been read back. Every field is
    optional: a platform reports what it reports, and a metric it does not know
    is absent rather than zero. */
type PostMetrics = {
  views?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  saves?: number;
  watch_seconds?: number;
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
  /** The post's measured engagement, or null until it has been read back. */
  metrics: PostMetrics | null;
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
/**
 * How many tagged products the panel itself draws.
 *
 * Enough to recognise what the campaign is about; not so many that the setting
 * below them is off the screen. A campaign curated from an import can hold a
 * hundred, and a hundred rows between one heading and the next is a page
 * nobody scrolls past - the rest is a click away, where a long list can be
 * searched and ordered instead of scrolled.
 */
/** How the posting timeline is drawn. */
type TimelineView = "list" | "calendar" | "grid";

const TAGGED_SHOWN = 6;

/** The columns the tagged-product table can be ordered by. */
type TaggedSortKey = "name" | "source" | "price" | "rate" | "availability" | "added";

/** Rows per page in that dialog. */
const TAGGED_PER_PAGE = 20;

const QUEUE_BATCH = 8;

/** One request's worth of clips, which is the assets endpoint's own ceiling. */
/** Rows of the ready-to-post queue shown per page. */
const QUEUE_PAGE_SIZE = 50;

/**
 * How many clips the picker will hold at once.
 *
 * The shared library hook's own select-all bound: a select-all pages until it
 * has them, held as whole assets because the composer renders each one.
 * Beyond it the filter is the better tool, and the bar says so rather than
 * stopping short and letting the number look like the whole match.
 */
const PICKER_CEILING = SELECT_ALL_ASSET_CEILING;

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
  onSelectEntry,
  onOpenDay,
}: {
  entries: TimelineEntry[];
  timezone: string;
  /** The month on display, as YYYY-MM in the schedule's own timezone. */
  month: string;
  onMonthChange: (next: string) => void;
  /** Open one post - the editor for a planned one, the permalink for a sent
      one. A chip is the thing a reader points at to act on that post. */
  onSelectEntry: (entry: TimelineEntry) => void;
  /** Open the full day, when a cell holds more than it can show. */
  onOpenDay: (dayKey: string) => void;
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
                <button type="button" key={entry.key}
                  className={`campaign-calendar-chip ${chipTone(entry)}`}
                  title={displayTitle(entry.title) ?? entry.caption.slice(0, 80)}
                  onClick={() => onSelectEntry(entry)}>
                  <b>{new Date(entry.at).toLocaleTimeString(undefined, {
                    hour: "2-digit", minute: "2-digit", hour12: false,
                    timeZone: timezone,
                  })}</b>
                  {entry.destination?.platform && (
                    <PlatformIcon platform={entry.destination.platform} size={12} />
                  )}
                  <span>{displayTitle(entry.title) || entry.caption.slice(0, 40) || "Post"}</span>
                </button>
              ))}
              {key && dayEntries.length > 3 && (
                <button type="button" className="campaign-calendar-more"
                  onClick={() => onOpenDay(key)}>+{dayEntries.length - 3} more</button>
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

/**
 * How a delivered post reads, by the clock.
 *
 * Handing a post to the engine is not publishing it: a scheduled post sits on
 * the engine as Scheduled until its time passes, and is Published only after.
 * Failure is its own state. So a succeeded delivery is read against the post's
 * own time rather than called done the moment the job returned. (A post still
 * in TrendRelay's queue is Planned, and is labelled where it is rendered.)
 */
function deliveredStatus(entry: TimelineEntry): { label: string; tone: "good" | "warn" | "neutral" | "info" } {
  if (entry.status === "failed") return { label: "Failed", tone: "warn" };
  if (entry.status === "succeeded") {
    return new Date(entry.at).getTime() <= Date.now()
      ? { label: "Published", tone: "good" }
      : { label: "Scheduled", tone: "info" };
  }
  const raw = entry.status ?? "delivered";
  return { label: raw.charAt(0).toUpperCase() + raw.slice(1), tone: "neutral" };
}

/** A big count in a small space: 1500 -> 1.5k, so a row of them stays a row. */
function compactCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value % 1_000_000 ? 1 : 0)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value % 1_000 ? 1 : 0)}k`;
  return String(Math.round(value));
}

/**
 * A delivered post's engagement, as the icons a platform shows it with.
 *
 * The eye, heart, speech bubble, share and bookmark read the way they do under
 * a real post, so a glance lands without reading labels - the count is the
 * content, the icon is what it counts. Only what the platform reported: a
 * metric it does not know is left out rather than shown as a zero, which would
 * read as "nobody" when the truth is "unknown". Renders nothing until a post
 * has been read back at all.
 */
const METRIC_ICON = {
  views: Eye,
  likes: Heart,
  comments: MessageCircle,
  shares: Share2,
  saves: Bookmark,
} as const;
const METRIC_ORDER = ["views", "likes", "comments", "shares", "saves"] as const;

function PostMetricsRow({ metrics }: { metrics: PostMetrics }) {
  const shown = METRIC_ORDER.filter((key) => typeof metrics[key] === "number");
  if (!shown.length) return null;
  return (
    <div className="campaign-metrics" aria-label="Post engagement">
      {shown.map((key) => {
        const Icon = METRIC_ICON[key];
        const value = metrics[key] as number;
        return (
          <span key={key} title={`${value.toLocaleString()} ${key}`}>
            <Icon size={13} strokeWidth={2} aria-hidden="true" />
            {compactCount(value)}
          </span>
        );
      })}
    </div>
  );
}

/**
 * What "Planned" means for this campaign, as the badge's own explanation.
 *
 * The badge used to spell the delivery mode out beside the word - "Planned ·
 * scheduled", "Planned · review draft" - which repeated one campaign-wide
 * setting on every row that shares it, and made the two-word label the widest
 * thing in the row. The state a row is in is Planned; how the campaign
 * delivers reads better as the badge's description than as most of its label.
 */
function plannedMeaning(delivery: Autopilot["delivery"]): string {
  if (delivery === "draft") {
    return "Planned: waiting for you to approve the draft before it is sent.";
  }
  if (delivery === "schedule") {
    return "Planned: handed to the network to post at this time.";
  }
  return "Planned: sent as soon as the campaign reaches it.";
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
 * The rules this campaign posts by, in its own numbers.
 *
 * Every one of these was decided somewhere - a setting, a rank, a constant in
 * the scheduler - and none of them was written down where the person watching
 * the queue could read it. So a campaign that posted nothing looked broken
 * rather than governed, and a campaign that posted looked arbitrary. The
 * numbers are the campaign's own rather than an example: a rest interval
 * described as "a few weeks" is not a rule anybody can predict from.
 *
 * Deliberately not a summary of what happened - the run note says that. This
 * says what will happen, which is the half a schedule cannot show.
 */
function PostingStrategy({
  autopilot,
  destinations,
  slots,
  timezone,
  canEdit,
  busy,
  onChange,
}: {
  autopilot: Autopilot;
  destinations: Destination[];
  slots: Slot[];
  /** Named beside the times, because "09:00" is not a time without one. */
  timezone: string;
  canEdit: boolean;
  busy: boolean;
  onChange: (changes: Partial<Autopilot>) => void;
}) {
  const accounts = destinations.length;
  const assigned = destinations.filter((item) => item.posting_schedule?.preset_id);
  const scheduleLabels = [...new Set(destinations.map(
    (item) => item.posting_schedule?.label ?? "Workspace posting times",
  ))];
  const perDay = slots.length || assigned.reduce(
    (count, item) => Math.max(count, item.posting_schedule?.slot_count ?? 0), 0,
  );
  return (
    <ol className="campaign-strategy">
      <li>
        <b>When</b>
        {/* The times themselves, not a count of them. They had a card of their
            own directly below this one, which said "shared by every campaign
            in this workspace" underneath a rule that had just said it - two
            headers and two ledes to list five times. */}
        <span>
          {assigned.length ? (
            <>
              <span className="campaign-strategy-times">
                {scheduleLabels.map(
                  (label) => <em key={label}>{label}</em>,
                )}
                <em className="zone">{timezone}</em>
              </span>
              Assigned per page; each account is considered only at its own times. {" "}
              <Link className="campaign-strategy-link" href="/publish">Edit in Publish</Link>
            </>
          ) : perDay ? (
            <>
              <span className="campaign-strategy-times">
                {slots.map((slot) => (
                  <em key={slot.id}>{slot.weekday_label} {slot.time}</em>
                ))}
                <em className="zone">{timezone}</em>
              </span>
              Shared by every campaign in this workspace; Publish owns them.{" "}
              <Link className="campaign-strategy-link" href="/publish">Edit in Publish</Link>
            </>
          ) : (
            <>No posting times set, so nothing is scheduled.{" "}
              <Link className="campaign-strategy-link" href="/publish">Add them in Publish</Link></>
          )}
        </span>
      </li>
      <li>
        <b>Which account</b>
        <span>{accounts > 1
          ? `Each time goes to whichever of the ${accounts} accounts is performing best, and every ${autopilot.exploration_every}${
              autopilot.exploration_every === 2 ? "nd" : autopilot.exploration_every === 3 ? "rd" : "th"
            } post goes to another one so the others can earn their way up. If the chosen account cannot take that time, the next one gets it.`
          : accounts === 1
            ? "Every posting time goes to the one account on this campaign."
            : "No accounts on this campaign yet."}</span>
      </li>
      <li>
        <b>Which post</b>
        <span>The first in the queue that is ready for that account: in
          rotation, copy written, and media the network accepts.</span>
      </li>
      <li>
        <b>Which product</b>
        {/* The rule that decides what a post earns from, next to the one that
            decides what it says. Ranking alone is deterministic, so without a
            rotation the best-fitting product wins every post in a run and a
            campaign with forty tagged products promotes two. */}
        <span>
          {autopilot.rotate_products
            ? <>Each post takes the best-fitting product that has not had its
                turn, up to {autopilot.max_products_per_post} per post. Once
                every product has had one, the round starts again.</>
            : <>Each post takes its best-fitting product, up to{" "}
                {autopilot.max_products_per_post} per post - which is usually
                the same one every time, since the ranking does not change.</>}
          {canEdit && (
            <button type="button" className="campaign-strategy-toggle"
              disabled={busy}
              onClick={() => onChange({ rotate_products: !autopilot.rotate_products })}>
              {autopilot.rotate_products
                ? "Always use the best fit instead"
                : "Take turns between products"}
            </button>
          )}
        </span>
      </li>
      <li>
        <b>Repeats</b>
        {/* The one rule people want to change while reading it. It decides
            whether an audience sees the same video twice, and reading it in a
            card and setting it in a dialog two clicks away is how somebody
            ends up unsure which of the two they actually did. */}
        <span>
          {autopilot.repeat_posts
            ? <>A post that goes out returns to the back of the queue. It will
                not go to the <em>same</em> account again for{" "}
                <input
                  className="campaign-strategy-days"
                  type="number" min={1} max={365}
                  defaultValue={autopilot.min_recycle_days}
                  disabled={!canEdit || busy}
                  aria-label="Days before a post may return to the same account"
                  onBlur={(event) => {
                    const days = Number(event.currentTarget.value);
                    if (days >= 1 && days <= 365 && days !== autopilot.min_recycle_days) {
                      onChange({ min_recycle_days: days });
                    }
                  }}
                /> days, though another account can take it sooner - the same
                clip on two accounts is two audiences.</>
            : <>Each post goes out <em>once per account</em>. The same video
                and caption never reach the same audience twice unless you turn
                repeats on, though another account can still take it.</>}
          {canEdit && (
            <button type="button" className="campaign-strategy-toggle"
              disabled={busy}
              onClick={() => onChange({ repeat_posts: !autopilot.repeat_posts })}>
              {autopilot.repeat_posts ? "Post each once instead" : "Allow repeats"}
            </button>
          )}
        </span>
      </li>
      <li>
        <b>Limits</b>
        <span>At most {autopilot.daily_cap_per_account} post
          {autopilot.daily_cap_per_account === 1 ? "" : "s"} a day per account,
          counting what other campaigns send there
          {autopilot.weekly_post_cap
            ? `, and ${autopilot.weekly_post_cap} a week across the campaign.`
            : "."}</span>
      </li>
      <li>
        <b>Before it sends</b>
        <span>{autopilot.authority === "autonomous"
          ? "Posts go out without you, except any carrying a weakly matched product - those always wait."
          : "Every post waits in Approval, exactly as it will be sent. Nothing reaches an engine before you approve it."}</span>
        {/* How to stop approving every post, where the approving is explained.
            The bar existed only as a refusal: choosing Autonomous answered 409
            with the numbers in it, so the one way to learn what was being
            counted was to try something the page had already discouraged. */}
        {autopilot.authority !== "autonomous" && autopilot.graduation && (
          <small className="campaign-graduation">
            {autopilot.graduation.ready
              ? "This campaign has earned Autonomous: switch it in settings and only weakly matched products will wait."
              : `Autonomous unlocks at ${autopilot.graduation.required} provider-confirmed posts `
                + `- ${autopilot.graduation.published} so far`
                + (autopilot.graduation.unresolved
                  ? `, with ${autopilot.graduation.unresolved} uncertain deliver${autopilot.graduation.unresolved === 1 ? "y" : "ies"} to resolve first.`
                  : ".")}
          </small>
        )}
      </li>
    </ol>
  );
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
          {destinations.length > 0 && (
            <p className="campaign-would-when">
              Posting schedules: {destinations.map((destination) => (
                `${destination.label} — ${destination.posting_schedule?.label
                  ?? "Workspace posting times"}`
              )).join("; ")}.
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
      <summary title="Show each posting, previewed as it will go out">
        {/* A disclosure with `display: flex` loses the native marker, so this
            row read as text that happened to be blue. The chevron says it
            opens; the title says what is behind it. */}
        <ActionIcon name="expand" size={12} />
        <span>Goes out {outings.length}×</span>
        {/* The first two spelled out, the rest counted. Enough to recognise the
            plan without the summary line wrapping to three rows. */}
        <small>{outings.slice(0, 2).map((post) =>
          `${when(post.at)} · ${post.destination
            ? platformLabels[post.destination.platform] : "Account"}`
            + ` · ${formatName(post, item)}`).join("  ·  ")}
          {outings.length > 2 && `  ·  +${outings.length - 2} more`}</small>
      </summary>
      <div className="campaign-rehearsal-list campaign-outings">
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
  /**
   * Whether the first load failed, as opposed to not having finished.
   *
   * Both leave `autopilot` null, and they want opposite things on screen: one
   * is a wait somebody should see progress for, the other is over and has
   * already been announced by `fail`. Without this the panel would show a mark
   * that spins for as long as the tab is open.
   */
  const [firstLoadFailed, setFirstLoadFailed] = useState(false);
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  /** The queued posts by id, shared by timeline actions and schedule labels. */
  const queueById = new Map(queue.map((item) => [item.id, item]));
  /**
   * The queued posts ticked for a batch action.
   *
   * Held as ids rather than items so a refresh cannot leave stale copies in
   * it; what the buttons act on is filtered against the live queue at render,
   * so a post deleted from under the selection simply stops counting instead
   * of being sent to the API and coming back missing.
   */
  const [queuePicked, setQueuePicked] = useState<Set<string>>(new Set());
  /** Rendered one page at a time: a 372-post queue mounted at once is the lag. */
  /**
   * The tag a row wears, by the same rule its badge follows.
   *
   * `needs_copy` wins over the stored state there, so it wins here too - a
   * chip and the badge it stands for must never disagree about which pile a
   * post is in.
   */
  const tagOf = (item: QueueItem) => (item.needs_copy ? "needsCopy" : item.state);

  const [queueTag, setQueueTag] = usePersistedState<string>(
    "campaigns.queueTag", "all", (value): value is string => typeof value === "string",
  );

  const queueTagCounts = queue.reduce<Record<string, number>>((counts, item) => {
    counts[tagOf(item)] = (counts[tagOf(item)] ?? 0) + 1;
    return counts;
  }, {});

  /**
   * Chips for the tags this queue actually has, in the order a post travels.
   *
   * Empty tags are left out rather than shown at zero: a queue where nothing
   * is paused should not spend a chip saying so, and four dead chips make the
   * two live ones harder to find. "All" is always there because clearing the
   * filter has to be one click.
   */
  const QUEUE_TAGS: { key: string; tone: string }[] = [
    { key: "needsCopy", tone: "chip-warn" },
    { key: "approved", tone: "chip-good" },
    { key: "draft", tone: "chip-neutral" },
    { key: "paused", tone: "chip-neutral" },
    { key: "retired", tone: "chip-neutral" },
  ];
  const queueChips = [
    { key: "all", label: t("common.all"), count: queue.length, tone: "chip-all" },
    ...QUEUE_TAGS
      .filter(({ key }) => queueTagCounts[key])
      .map(({ key, tone }) => ({
        key,
        label: t(`autopilot.state.${key}`),
        count: queueTagCounts[key],
        tone,
      })),
  ];

  // A tag that emptied - the last unwritten post got its copy - would
  // otherwise leave the list filtered to nothing by a chip no longer on
  // screen. Falling back to everything is the only honest reading of a
  // filter whose subject has gone.
  const activeQueueTag = queueChips.some((chip) => chip.key === queueTag) ? queueTag : "all";
  const shownQueue = activeQueueTag === "all"
    ? queue
    : queue.filter((item) => tagOf(item) === activeQueueTag);

  // Selection follows what is on screen: ticking "all" while a filter is up
  // means all of these, not all of a list the filter is hiding.
  const pickedQueue = shownQueue.filter((item) => queuePicked.has(item.id));
  const allQueueSelected = shownQueue.length > 0 && pickedQueue.length === shownQueue.length;
  const [queuePage, setQueuePage] = useState(0);
  // Pages of what the filter leaves, not of the whole queue: narrowing to
  // eight unwritten posts should be one page, not page one of three with two
  // of them empty. `safeQueuePage` already clamps, so a filter that shortens
  // the list past the current page lands on the last one rather than on
  // nothing - which is why changing a filter needs no page reset of its own.
  const queuePages = Math.max(1, Math.ceil(shownQueue.length / QUEUE_PAGE_SIZE));
  const safeQueuePage = Math.min(queuePage, queuePages - 1);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(new Set());
  const [offers, setOffers] = useState<Offer[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendations | null>(null);
  const [productItem, setProductItem] = useState<QueueItem | null>(null);
  /** The products this campaign may promote, and whether the picker is open. */
  const [tagged, setTagged] = useState<TaggedProduct[]>([]);
  const [addingProducts, setAddingProducts] = useState(false);
  /**
   * The whole tagged list, when somebody asks for it.
   *
   * The panel shows the first few. A campaign curated from an import can hold
   * a hundred products, and a hundred rows between the heading and the next
   * setting is a page nobody scrolls past - so the rest lives here, with the
   * things a long list actually needs: a search, an order, and a way to remove
   * more than one.
   */
  const [reviewingProducts, setReviewingProducts] = useState(false);
  const [productQuery, setProductQuery] = useState("");
  const [productSort, setProductSort] = useState<SortState<TaggedSortKey>>({
    key: "added", direction: "desc",
  });
  const [productFilter, setProductFilter] = useState<"all" | "available" | "unavailable">("all");
  const [productPage, setProductPage] = useState(0);
  const [pickedProducts, setPickedProducts] = useState<Set<string>>(new Set());
  const [productSearch, setProductSearch] = useState("");
  const [pinnedOffers, setPinnedOffers] = useState<Set<string>>(new Set());
  const [slots, setSlots] = useState<Slot[]>([]);
  const [postingPresets, setPostingPresets] = useState<PostingPreset[]>([]);
  const [pageAssignments, setPageAssignments] = useState<Record<string, string>>({});
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
    {
      note: string; posts: PreviewPost[]; deployed: DeployedPost[]; problems: number;
      /** How many days the plan looked ahead, so a full window can name itself. */
      horizon_days?: number;
    } | null
  >(null);
  /** Posts the authority rules deferred to a person, reason attached. */
  const [exceptions, setExceptions] = useState<HeldExecution[]>([]);
  const [busy, setBusy] = useState("");
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);
  // The shared library loop - the same hook the Publish picker reads with,
  // so a capability added there arrives here without this file changing.
  // No media-kind baseline: a campaign can post a clip or a carousel, so the
  // picker opens on everything and the filter row above it narrows. Starting
  // on "video" was what made pictures invisible even after the queue learned
  // to hold them. Audio is dropped on arrival - a campaign posts a clip or a
  // carousel, so a sound file has nothing to become here.
  const picker = useLibraryAssets<LibraryAsset>({
    workspaceId, apiFetch,
    enabled: picking,
    keep: (asset) => asset.media_kind !== "audio",
  });
  const library = picker.assets;
  const libraryFacets = picker.facets;
  const libraryFilters = picker.filters;
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
  /**
   * Which Library editor the picked clips were sent to.
   *
   * The same registry the Library reads, so an action declared once is offered
   * in both places. Choosing media for a campaign is exactly when somebody
   * notices a clip needs reading, captioning or voicing, and sending them back
   * to the Library to do it - then back here to find the selection gone - was
   * the whole friction.
   */
  const [selectionAction, setSelectionAction] = useState<LibrarySelectionActionId | null>(null);
  const [editing, setEditing] = useState<QueueItem | null>(null);
  const [editingReplies, setEditingReplies] = useState<string[]>([]);
  // The campaign's own wording, overridden for this post. Empty means the
  // campaign's, which is why these are strings rather than nullable: the field
  // shows the campaign's text and clearing it is how you go back to it.
  const [editingDisclosure, setEditingDisclosure] = useState("");
  const [editingBioHint, setEditingBioHint] = useState("");
  const [composed, setComposed] = useState<ComposedPost | null>(null);
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
  /**
   * Which pane is open, and it is remembered.
   *
   * It used to be chosen per campaign - an active one opened on Schedule, a
   * draft on Queue & setup - on the reasoning that the campaign knows better
   * than a preference does. That reads well with one campaign and badly with
   * three: every switch between running campaigns threw you back to Schedule,
   * including the switch you made to compare two queues.
   *
   * So it holds where it was put, and opens on Queue & setup, which is the
   * half that answers "what is in this campaign" for a campaign you have not
   * looked at yet.
   */
  const [view, setView] = usePersistedState<"posts" | "content">(
    "trendrelay.campaigns.workTab",
    "content",
    oneOf("posts", "content"),
  );
  const automaticPreview = useRef(false);
  // The references this mirrors (Buffer, Zernio) offer the same posts as a
  // list and as a calendar; the list answers "what went out", the calendar
  // answers "how does the month look". Null month means the current one.
  // Remembered, because it is a preference somebody expresses by clicking once
  // and expects to hold. Kept in state alone it reset on every visit, so the
  // first thing to do on the page was to make the same choice again. Per
  // browser and per surface, which is what local storage is for - it is not a
  // setting anybody should have to manage, and losing it costs one click.
  const [timelineView, setTimelineView] = usePersistedState<TimelineView>(
    "trendrelay.campaigns.timelineView", "list",
    oneOf<TimelineView>("list", "calendar", "grid"),
  );
  const [calendarMonth, setCalendarMonth] = useState<string | null>(null);
  // Which day the calendar's "see more" opened, as YYYY-MM-DD in the reader's
  // zone. The drawer lists that day in full; the calendar cell only has room
  // for the first few.
  const [openDay, setOpenDay] = useState<string | null>(null);
  // The day drawer's checkbox selection, as queue-item ids. Only planned posts
  // - the ones still ahead of the engine - can be picked, because deleting a
  // post that has already gone out means nothing.
  const [selectedDayPosts, setSelectedDayPosts] = useState<Set<string>>(new Set());
  // Where an edit was opened from, so closing the modal returns there. The
  // editor lives in the content view; opening it from the timeline switches
  // there under the overlay, and this is what sends the reader back to the
  // calendar or grid they were reading rather than stranding them in Setup.
  const [editReturn, setEditReturn] = useState<"posts" | null>(null);
  // The held post being rewritten before its decision, if any.
  const [editingHeld, setEditingHeld] = useState<HeldExecution | null>(null);
  /** Held posts picked for one approval. Empty means nothing is selected. */
  const [picked, setPicked] = useState<Set<string>>(new Set());
  /**
   * What the last batch did, per post, because a total is not an answer.
   *
   * Only the ones it could not do: whether the batch was approving or
   * refusing, what a reader needs beside a row is why that row is still here.
   */
  const [batchResults, setBatchResults] = useState<
    { execution_id: string; problem?: string }[]
  >([]);

  const base = `/api/workspaces/${workspaceId}/campaigns/${campaignId}`;
  /**
   * The tagged list as the dialog shows it: searched, filtered, ordered, paged.
   *
   * All of it in the browser, because all of it is already here - the campaign
   * sends its products in one payload, and a hundred rows is nothing to sort.
   * Asking the server would add a round trip per keystroke to answer a question
   * the page can already answer.
   */
  const shownProducts = useMemo(() => {
    const needle = productQuery.trim().toLowerCase();
    const matching = tagged.filter((product) => {
      if (productFilter === "available" && product.availability === "unavailable") {
        return false;
      }
      if (productFilter === "unavailable" && product.availability !== "unavailable") {
        return false;
      }
      if (!needle) return true;
      return [
        product.name, product.brand, product.category,
        product.marketplace, product.network,
      ].some((field) => (field ?? "").toLowerCase().includes(needle));
    });
    // Compared as one type per column, so a missing figure sorts as absent
    // rather than as zero - an offer with no commission recorded is not an
    // offer that pays nothing.
    const value = (product: TaggedProduct): string | number => {
      switch (productSort.key) {
        case "name": return product.name.toLowerCase();
        case "source": return (product.marketplace ?? product.network ?? "").toLowerCase();
        case "rate": return product.commission_bps ?? -1;
        case "price": return product.price_cents ?? -1;
        case "availability": return product.availability ?? "";
        default: return product.tagged_at ?? "";
      }
    };
    const ordered = [...matching].sort((left, right) => {
      const a = value(left);
      const b = value(right);
      const order = typeof a === "number" && typeof b === "number"
        ? a - b
        : String(a).localeCompare(String(b));
      return productSort.direction === "asc" ? order : -order;
    });
    return ordered;
  }, [tagged, productQuery, productFilter, productSort]);

  function changeProductSort(column: TaggedSortKey) {
    setProductSort((current) => nextSort(current, column));
    setProductPage(0);
  }

  const productPages = Math.max(1, Math.ceil(shownProducts.length / TAGGED_PER_PAGE));
  const productPageSafe = Math.min(productPage, productPages - 1);
  const productSlice = shownProducts.slice(
    productPageSafe * TAGGED_PER_PAGE, (productPageSafe + 1) * TAGGED_PER_PAGE,
  );


  /** The same media the Publish composer plays, streamed from the same roots. */
  const previewMediaUrl = (path: string) =>
    `${apiBaseUrl()}/api/workspaces/${workspaceId}/publishing/media/preview`
    + `?path=${encodeURIComponent(path)}`;

  /**
   * Whether a plan is on screen, as a ref rather than read off state.
   *
   * `run` would otherwise have to depend on `preview`, which rebuilds it
   * every time a plan loads - and every handler holding the old one would
   * be one render behind.
   */
  const showingPlan = useRef(false);

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
      void refresh().catch((reason) => {
        setFirstLoadFailed(true);
        fail(explainFailure(reason, "Autopilot unavailable."));
      });
      // Everything the readiness check needs, loaded once. Each of these is a
      // different subsystem, and the point of the checklist is that it names
      // which one is missing rather than reporting a single blank "not ready".
      void apiFetch(`/api/workspaces/${workspaceId}/publishing/slots`)
        .then((response) => json<{
          slots: Slot[]; timezone: string; presets: PostingPreset[];
          page_assignments?: Record<string, string>;
        }>(response))
        .then((body) => {
          setSlots(body.slots);
          setPostingPresets(body.presets ?? []);
          setPageAssignments(body.page_assignments ?? {});
          setScheduleTimezone(body.timezone || "UTC");
        })
        .catch(() => { setSlots([]); });
      void apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`)
        .then((response) => json<{ offers: Offer[] }>(response))
        .then((body) => setOffers(body.offers))
        .catch(() => setOffers([]));
    });
  }, [refresh, apiFetch, workspaceId, fail]);

  // The inbox is the panel's front door now - approving held posts is the
  // operator's recurring job - so it loads with the page rather than behind
  // a pane. Deferred out of the effect body, like the initial refresh.
  useEffect(() => {
    queueMicrotask(() => {
      void loadExceptions();
    });
  }, [loadExceptions]);

  // A running campaign keeps producing posts to approve, and switching it on
  // produces the first of them - neither of which should need a page reload to
  // appear. So while it is enabled the held list is reloaded at once and then
  // on a gentle interval, paused whenever the tab is not being looked at.
  useEffect(() => {
    if (!autopilot?.enabled) return;
    void loadExceptions();
    const tick = () => {
      if (document.visibilityState !== "hidden") void loadExceptions();
    };
    const timer = window.setInterval(tick, 20000);
    return () => window.clearInterval(timer);
  }, [autopilot?.enabled, loadExceptions]);

  /**
   * One decision about one held post.
   *
   * Three answers rather than two. "Skip" and "Decline" are both no, and the
   * difference is what becomes of the post: skipping frees this outing and
   * the post is proposed again next pass, declining takes it out of the
   * rotation. Only the second stops a post nobody wants returning to this
   * inbox every cycle.
   */
  async function decideException(
    executionId: string,
    action: "approve" | "dismiss",
    { publishNow = false, stopProposing = false } = {},
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
              : { stop_proposing: stopProposing },
          ),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "The decision was refused.");
      succeed(action === "dismiss"
        ? stopProposing
          ? "Declined. The post is paused, so it stops being proposed until you put it back."
          : "Skipped this time. Its slot and clip are free, and the post returns next cycle."
        : publishNow
          ? "Approved and publishing now."
          : "Approved. The post is queued exactly as you approved it.");
      await loadExceptions();
      // The queue shows the paused post, so it has to be re-read to show it.
      if (stopProposing) await refresh();
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
      const body = await json<{ accounts: Array<Omit<Account, "id"> & {
        integration_id: string;
      }> }>(await apiFetch(
        `${base}/autopilot/account-recommendations`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      ));
      setAccounts(body.accounts.map((account) => ({
        ...account, id: account.integration_id,
      })));
      // Account discovery is also when legacy destinations learn the stable
      // consolidated page key. Refresh so their inherited schedule is shown
      // immediately, rather than on the next visit.
      await refresh();
      setSelectedAccounts(new Set());
      setAdding(true);
    } catch (reason) {
      fail(explainFailure(reason, "Could not load accounts."));
    } finally {
      setBusy("");
    }
  }

  /** Open the picker. The shared hook fetches on open and on every filter
      change; what stays here is only what this surface adds - the composer
      reset, and the selection. */
  function loadLibrary() {
    setDrafting([]);
    setPicking(true);
  }

  // Arriving from Library's "Add to campaign": open the picker on exactly the
  // handed-off clips, selected, then scrub the parameter so a refresh or a
  // shared link does not re-run the hand-off.
  const addParamRef = useRef(false);
  useEffect(() => {
    if (!workspaceId || addParamRef.current) return;
    const wanted = new URLSearchParams(window.location.search).get("add");
    if (!wanted) return;
    addParamRef.current = true;
    const ids = [...new Set(wanted.split(",").filter(Boolean))];
    window.history.replaceState(null, "", "/campaigns");
    setView("content");
    void picker.fetchByIds(ids)
      .then((assets) => {
        setSelectedAssets(Object.fromEntries(assets.map((asset) => [asset.id, asset])));
        setPicking(true);
      })
      .catch(() => fail("The handed-off clips could not be loaded."));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once per workspace on arrival
  }, [workspaceId]);

  /**
   * Tick everything the filter matches, not merely everything on screen.
   *
   * The hook pages whole assets to its stated ceiling, because the composer
   * is handed rows rather than ids; what stays here is turning the walk's
   * result into this surface's selection. Null means the filter changed
   * under the walk, and a selection of a query nobody is looking at is not
   * made.
   */
  async function selectAllMatching() {
    const collected = await picker.fetchAllMatching();
    if (collected) {
      setSelectedAssets(Object.fromEntries(collected.map((asset) => [asset.id, asset])));
    }
  }

  // The hook's own failure line, surfaced the way every other failure in
  // this panel is. Keyed on the message so one failure is one toast.
  const pickerFailure = picker.failure;
  useEffect(() => {
    if (pickerFailure) fail(pickerFailure);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fail is stable in practice; keying on the message
  }, [pickerFailure]);

  const loadPreview = useCallback(async (announce = true) => {
    setBusy("preview");
    try {
      const body = await json<{
        note: string; posts: PreviewPost[]; deployed: DeployedPost[]; problems: number;
        horizon_days?: number;
      }>(await apiFetch(`${base}/autopilot/preview`, { method: "POST" }));
      setPreview(body);
      showingPlan.current = true;
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

  const run = useCallback(async (label: string, work: () => Promise<string>) => {
    setBusy(label);
    // Whether a plan was on screen before this changed it. Cleared below
    // because a plan drawn before the change is wrong, but clearing was all
    // this did: the outlook went blank and stayed blank until somebody pressed
    // Refresh outlook, so approving a post looked like it had removed it from
    // the calendar. Reloaded only when it was already showing, so a campaign
    // nobody has previewed still costs nothing.
    const replanning = label !== "preview" && showingPlan.current;
    try {
      if (label !== "preview") {
        setPreview(null);
        showingPlan.current = false;
      }
      succeed(await work());
      await refresh();
      // And the page that owns the campaign list, because its rows count
      // things this panel changes. The badge beside a campaign's name counts
      // posts held for approval; approving one left it reading the old number
      // until the tab was reloaded, which is the one moment somebody is
      // certain the number moved.
      //
      // Every action goes through here, so this is the one place it belongs -
      // an action that forgot to say so is the bug this replaces, and a list
      // of which actions count is a list that goes stale.
      if (label !== "preview") await onCampaignChanged();
      if (replanning) await loadPreview(false);
    } catch (reason) {
      fail(explainFailure(reason, "That did not work."));
    } finally {
      setBusy("");
    }
  }, [refresh, succeed, fail, loadPreview, onCampaignChanged]);

  async function save(changes: Partial<Autopilot>, { confirm = false } = {}) {
    if (!autopilot) return;
    const next = { ...autopilot, ...changes };
    await run("settings", async () => {
      const saved = await json<{ held?: { recomposed: number; kept: number } }>(
        await apiFetch(`${base}/autopilot`, {
          method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          enabled: next.enabled,
          offer_id: next.offer_id,
          offer_mode: next.offer_mode,
          candidate_offer_ids: next.candidate_offer_ids,
          max_products_per_post: next.max_products_per_post,
          // Sent because the settings PUT is a whole replacement: omitting it
          // sends the field's default, and the default is off - so changing
          // the delivery mode from the header would have switched a campaign's
          // disclosure off on the way past.
          disclose: next.disclose,
          disclosure: next.disclosure,
          bio_hint: next.bio_hint,
          min_recycle_days: next.min_recycle_days,
          repeat_posts: next.repeat_posts,
          rotate_products: next.rotate_products,
          daily_cap_per_account: next.daily_cap_per_account,
          weekly_post_cap: next.weekly_post_cap,
          posting_preset_id: next.posting_preset_id,
          delivery: next.delivery,
          authority: next.authority,
          priority: next.priority,
          post_language: next.post_language,
          confirm_external_action: confirm,
        }),
      }));
      const settled = next.enabled && !autopilot.enabled
        ? t("autopilot.switchedOn")
        : t("autopilot.saved");
      // What the change reached, when it reached anything. A count is the
      // difference between "saved" and knowing three posts in the inbox were
      // rewritten to match.
      const reached = saved?.held;
      if (!reached?.recomposed && !reached?.kept) return settled;
      const parts: string[] = [];
      if (reached.recomposed) {
        parts.push(`${reached.recomposed} waiting post${
          reached.recomposed === 1 ? "" : "s"} updated to match`);
      }
      if (reached.kept) {
        parts.push(`${reached.kept} left as ${
          reached.kept === 1 ? "it was" : "they were"} - edited by hand`);
      }
      return `${settled} ${parts.join("; ")}.`;
    });
  }

  /**
   * Refuse everything picked, in one decision.
   *
   * The half that was missing. Approving fourteen was one action and refusing
   * the other twelve was twelve, so an inbox where two posts are worth having
   * cost more to clear than to fill.
   *
   * No confirmation for the skip: nothing leaves the machine and the posts
   * come straight back. Declining asks, because it changes the queue.
   */
  async function dismissPicked({ stopProposing = false } = {}) {
    const ids = [...picked];
    if (!ids.length) return;
    if (stopProposing && !window.confirm(
      `Decline ${ids.length} post${ids.length === 1 ? "" : "s"}? `
      + "Each is cancelled and its post paused, so they stop being proposed "
      + "until you put them back in the queue."
    )) return;
    await run("dismiss-batch", async () => {
      const body = await json<{
        dismissed: number;
        refused: number;
        results: { execution_id: string; dismissed: boolean; problem?: string }[];
      }>(await apiFetch(`${base}/autopilot/executions/dismiss`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ execution_ids: ids, stop_proposing: stopProposing }),
      }));
      setBatchResults(body.results.filter((row) => !row.dismissed));
      setPicked(new Set(body.results.filter((row) => !row.dismissed)
        .map((row) => row.execution_id)));
      await refresh();
      const verb = stopProposing ? "declined" : "skipped";
      return body.refused
        ? `${body.dismissed} ${verb}, ${body.refused} left held.`
        : `${body.dismissed} post${body.dismissed === 1 ? "" : "s"} ${verb}.`;
    });
  }

  /**
   * Approve everything picked, in one confirmed decision.
   *
   * The confirmation is over a list somebody has read, not a weaker promise
   * about what reaches an engine: each post is still approved on its own
   * terms, and one that is unfinished is reported and left held rather than
   * failing the others with it.
   */
  async function approvePicked({ publishNow = false } = {}) {
    const ids = [...picked];
    if (!ids.length) return;
    if (!window.confirm(
      `Approve ${ids.length} post${ids.length === 1 ? "" : "s"}? `
      + (publishNow
        ? "Each is published immediately, to its own account, exactly as shown."
        : "Each goes to its own account, exactly as shown.")
    )) return;
    await run("approve-batch", async () => {
      const body = await json<{
        approved: number;
        refused: number;
        results: { execution_id: string; approved: boolean; problem?: string }[];
      }>(await apiFetch(`${base}/autopilot/executions/approve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          execution_ids: ids,
          confirm_external_action: true,
          publish_now: publishNow,
        }),
      }));
      setBatchResults(body.results.filter((row) => !row.approved));
      // Only the ones that went; anything refused stays picked, because it is
      // still there and still the thing to deal with.
      setPicked(new Set(body.results.filter((row) => !row.approved)
        .map((row) => row.execution_id)));
      await refresh();
      return body.refused
        ? `${body.approved} approved, ${body.refused} left held.`
        : `${body.approved} post${body.approved === 1 ? "" : "s"} approved.`;
    });
  }

  /**
   * What this campaign may promote.
   *
   * Its own list, not the workspace's: matching ranks these and nothing else,
   * so this is the answer to "why did nothing attach" as much as it is a list.
   */
  const loadTagged = useCallback(async () => {
    // Soft, like every other loader on this panel. A tag list that cannot be
    // read is a missing list, not a broken screen - and thrown from an effect
    // it took the whole campaign down behind a runtime error, which is a
    // worse answer to "the API is not up" than showing the rest of the page.
    try {
      const body = await json<{ products: TaggedProduct[] }>(
        await apiFetch(`${base}/products`),
      );
      setTagged(body.products);
    } catch {
      setTagged([]);
    }
  }, [apiFetch, base]);

  async function tagProducts(offerIds: string[]) {
    if (!offerIds.length) return;
    await run("tag-products", async () => {
      const body = await json<{ tagged: number; already: number; products: TaggedProduct[] }>(
        await apiFetch(`${base}/products`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ offer_ids: offerIds }),
        }),
      );
      setTagged(body.products);
      // Refreshed because what may be attached has changed, and the queue rows
      // show what each post would carry.
      await refresh();
      return body.tagged
        ? `${body.tagged} product${body.tagged === 1 ? "" : "s"} added.`
        : "Already on this campaign.";
    });
  }

  async function untagProduct(offerId: string, name: string) {
    if (!window.confirm(
      `Stop this campaign promoting ${name}? Posts already sent keep their links.`
    )) return;
    await run("tag-products", async () => {
      const body = await json<{ products: TaggedProduct[] }>(
        await apiFetch(`${base}/products/${offerId}`, { method: "DELETE" }),
      );
      setTagged(body.products);
      await refresh();
      return `${name} removed.`;
    });
  }

  /**
   * Remove several at once, from the list they were chosen in.
   *
   * Curating a hundred imported products down to the ones a campaign is
   * actually about was a hundred confirmations otherwise.
   */
  async function untagPicked() {
    const ids = [...pickedProducts];
    if (!ids.length) return;
    if (!window.confirm(
      `Stop this campaign promoting ${ids.length} product${ids.length === 1 ? "" : "s"}? `
      + "Posts already sent keep their links."
    )) return;
    await run("tag-products", async () => {
      const body = await json<{ untagged: number; products: TaggedProduct[] }>(
        await apiFetch(`${base}/products/remove`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ offer_ids: ids }),
        }),
      );
      setTagged(body.products);
      setPickedProducts(new Set());
      await refresh();
      return `${body.untagged} product${body.untagged === 1 ? "" : "s"} removed.`;
    });
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

  /** Load a post's own wording into the editor, blank meaning the campaign's. */
  function openEditorWording(item: QueueItem) {
    setEditingDisclosure(item.disclosure ?? "");
    setEditingBioHint(item.bio_hint ?? "");
    setComposed(null);
  }

  /**
   * Compose the post being edited, as every account would receive it.
   *
   * Asked of the server on every pause in typing rather than assembled here.
   * The disclosure leads, the product and its link go wherever that network
   * allows, the hashtags move below both, written replies come before
   * generated ones - and each of those is a rule with a reason that already
   * exists in one place. A copy of it in the browser would be a second
   * implementation that is right until the day it is not, which is the day
   * somebody trusts this panel and posts something else.
   */
  const composeDraft = useCallback(async (draft: Record<string, unknown>) => {
    try {
      setComposed(await json<ComposedPost>(
        await apiFetch(`${base}/queue/composition`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(draft),
        }),
      ));
    } catch {
      // Quiet, like the row matcher: somebody writing a caption should not be
      // handed an error about the panel underneath it.
      setComposed(null);
    }
  }, [apiFetch, base]);

  const editForm = useRef<HTMLFormElement | null>(null);
  const composeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * Recompose shortly after typing stops.
   *
   * Read off the form rather than from state, because these fields are
   * uncontrolled - a caption re-rendered on every keystroke is a caption that
   * loses the cursor. The pause is long enough that a sentence is one request
   * and short enough that the preview feels like part of the same act.
   */
  const scheduleCompose = useCallback(() => {
    if (composeTimer.current) clearTimeout(composeTimer.current);
    composeTimer.current = setTimeout(() => {
      const form = editForm.current;
      if (!form || !editing) return;
      const values = new FormData(form);
      void composeDraft({
        item_id: editing.id,
        title: String(values.get("title") ?? "").trim() || null,
        body: String(values.get("body") ?? "").trim(),
        hashtags: String(values.get("hashtags") ?? "").split(/[\s,]+/).filter(Boolean),
        first_comment: String(values.get("first_comment") ?? "").trim() || null,
        thread: editingReplies.map((part) => part.trim()).filter(Boolean),
        offer_ids: editing.offer_ids,
        disclosure: editingDisclosure.trim() || null,
        bio_hint: editingBioHint.trim() || null,
      });
    }, 350);
  }, [composeDraft, editing, editingReplies, editingDisclosure, editingBioHint]);

  // On opening, and after every change this component owns rather than the
  // form: the replies, and the two wordings.
  useEffect(() => {
    if (!editing) return;
    scheduleCompose();
    return () => {
      if (composeTimer.current) clearTimeout(composeTimer.current);
    };
  }, [editing, scheduleCompose]);

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
          // No count of its own: how many products a post carries is the
          // campaign's setting, and asking for a different number here is how
          // the preview came to show two where the campaign allows one.
          body: JSON.stringify({ asset_ids: ids.slice(0, 100) }),
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

  /**
   * Open the post editor as a modal, loaded from a queue item.
   *
   * The editor's markup lives in the content view, so opening it from the
   * timeline (calendar, grid, or the day drawer) switches there first; the
   * overlay covers that, and `from` is what closes the loop back afterwards.
   * A single door means the calendar chip, the drawer row and the queue row's
   * Edit button all reach the exact same form rather than three near-copies.
   */
  function openPostEditor(item: QueueItem, from: "posts" | null = null) {
    if (from) setEditReturn(from);
    setOpenDay(null);
    setView("content");
    setEditing(item);
    setEditingReplies(item.thread.length ? item.thread : [""]);
    openEditorWording(item);
  }

  /** Close the editor and return to whichever view opened it. */
  function closePostEditor() {
    setEditing(null);
    if (editReturn) {
      setView(editReturn);
      setEditReturn(null);
    }
  }

  /**
   * Act on a post picked from the calendar or the day drawer.
   *
   * A planned post opens the editor - that is the whole point of the click,
   * per the request. A delivered one has nowhere to be edited, so it opens
   * where it lives: its permalink. Anything else (a planned post a reader
   * cannot edit, or one with no link) falls back to opening its day in full.
   */
  function selectTimelineEntry(entry: TimelineEntry) {
    if (entry.kind === "planned" && entry.queue_item_id && canEdit) {
      const item = queueById.get(entry.queue_item_id);
      if (item) {
        openPostEditor(item, "posts");
        return;
      }
    }
    const url = entry.post_url ?? entry.page_url;
    if (url) {
      window.open(url, "_blank", "noreferrer");
      return;
    }
    setOpenDay(new Date(entry.at).toLocaleDateString("en-CA", { timeZone: readerZone }));
  }

  /** Close the day drawer, dropping whatever was ticked with it. */
  function closeDay() {
    setOpenDay(null);
    setSelectedDayPosts(new Set());
  }

  /** Tick or untick one planned post in the day drawer. */
  function toggleDayPost(queueItemId: string) {
    setSelectedDayPosts((current) => {
      const next = new Set(current);
      if (next.has(queueItemId)) next.delete(queueItemId);
      else next.add(queueItemId);
      return next;
    });
  }

  /** Delete every ticked planned post, then let the timeline refresh. */
  function deleteSelectedDayPosts() {
    const ids = [...selectedDayPosts];
    if (!ids.length) return;
    void run("drop", async () => {
      for (const id of ids) {
        await json(await apiFetch(`${base}/queue/${id}`, { method: "DELETE" }));
      }
      setSelectedDayPosts(new Set());
      return ids.length === 1
        ? "Removed 1 planned post."
        : `Removed ${ids.length} planned posts.`;
    });
  }

  /** The open day's tickable posts - the planned, deletable ones. A delivered
      post is not among them, so "select all" never picks something it cannot
      act on. */
  function selectableDayIds(entries: TimelineEntry[]) {
    return entries
      .filter((entry) => entry.kind === "planned" && entry.queue_item_id
        && canEdit && queueById.has(entry.queue_item_id))
      .map((entry) => entry.queue_item_id as string);
  }

  /** Tick every selectable post, or clear them if all are already ticked. */
  function toggleAllDayPosts(ids: string[]) {
    setSelectedDayPosts((current) => {
      const all = ids.length > 0 && ids.every((id) => current.has(id));
      return all ? new Set() : new Set(ids);
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

  const destinationSlotCounts = destinations.map((item) => (
    item.posting_schedule?.slot_count ?? slots.length
  ));
  const hasPostingTimes = slots.length > 0
    || destinationSlotCounts.some((count) => count > 0);
  const dailyScheduleCapacity = destinationSlotCounts.reduce(
    (total, count) => total + Math.min(count, autopilot?.daily_cap_per_account ?? count), 0,
  );

  const ready = (() => {
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
        met: hasPostingTimes,
        label: t("autopilot.needSlots"),
        section: "schedule" as const,
      },
    ];
    return {
      rows,
      all: rows.every((row) => row.met),
      configured: rows.filter((row) => row.id !== "active").every((row) => row.met),
    };
  })();

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

  // What this campaign may promote, read once the panel knows which campaign
  // it is. Nothing else on the screen can be judged without it: an empty
  // ranking means one thing when the list is empty and another when it is not.
  const loadedTags = useRef(false);
  useEffect(() => {
    if (loadedTags.current) return;
    loadedTags.current = true;
    void loadTagged();
  }, [loadTagged]);

  // Switching campaigns remounts this panel by key, so everything below is
  // fetched again from nothing. Rendering null meanwhile dropped the whole
  // lower half of the page for as long as the round trip took and then put it
  // back - the flash. The previous campaign's panel is deliberately not held
  // on screen instead: this one approves and publishes, and showing campaign
  // A's posts under campaign B's heading is worse than showing a wait.
  if (!autopilot) {
    return firstLoadFailed ? null : <WaitingBlock message={t("common.loading")} />;
  }

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
   * Whether every clip on screen is ticked.
   *
   * "Loaded" rather than "matching": this is the box beside the grid, and it
   * ticks what is on screen. Everything the filter matches is its own control,
   * because reaching the rest means fetching it - the picker hands whole
   * assets to the composer, so it pages rather than collecting ids the way the
   * Library's select-all can.
   */
  const allLoadedSelected = library.length > 0
    && library.every((asset) => Boolean(selectedAssets[asset.id]));
  const selectedPictures = selectedLibrary.filter(
    (asset) => asset.media_kind === "image",
  ).length;

  /**
   * The public page a destination posts as, where one can be worked out.
   *
   * The handle comes off the account list the picker already loads, matched on
   * the id the destination stores - nothing new is fetched for this. A
   * destination whose account has not been read yet, or whose network has no
   * single address for a handle, keeps its name as plain text.
   */
  function destinationProfile(item: Destination): string | null {
    const account = accounts.find(
      (candidate) => candidate.id === item.integration_id
        && candidate.provider === item.provider,
    );
    // The engine's own handle where it reported one, and the destination's
    // label where it did not. The label is what the posting timeline already
    // links a delivered post by, so the same account was clickable there and
    // plain text here - and the engines that report no handle at all are
    // exactly the ones where the label is the only thing anybody has.
    //
    // `profileUrl` still refuses anything that is not handle-shaped, so a page
    // named in words - "Tủ Xinh Của Nàng" - stays plain rather than becoming a
    // link to a profile that does not exist.
    return profileUrl(item.platform, account?.handle)
      ?? profileUrl(item.platform, item.label);
  }

  /**
   * The ticked posts that are still in the queue.
   *
   * Filtered rather than pruned in an effect: a post removed underneath the
   * selection stops counting on the next render, with no state to keep in step
   * and no chance of sending an id the API will only report back as missing.
   */

  function toggleQueuePick(id: string) {
    setQueuePicked((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  /** Everything, or nothing - the same two-state box the Library and the picker use. */
  function toggleAllQueue() {
    setQueuePicked(allQueueSelected ? new Set() : new Set(shownQueue.map((item) => item.id)));
  }

  async function batchQueue(action: "approve" | "hold" | "remove") {
    const ids = pickedQueue.map((item) => item.id);
    if (!ids.length) return;
    if (action === "remove" && !window.confirm(
      `Remove ${ids.length} post${ids.length === 1 ? "" : "s"} from this campaign's queue?`
      + "\n\nPosts already published are not affected.",
    )) return;
    await run(`queue-batch-${action}`, async () => {
      const body = await json<{ changed: string[]; missing: string[] }>(
        await apiFetch(`${base}/queue/batch`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ item_ids: ids, action }),
        }),
      );
      setQueuePicked(new Set());
      const count = body.changed.length;
      const verb = action === "approve"
        ? "added to the rotation"
        : action === "hold" ? "held back" : "removed";
      // The missing count is said rather than swallowed: it is the difference
      // between "I ticked twelve and eleven happened" being a bug and being
      // somebody else deleting a row while this was open.
      return `${count} post${count === 1 ? "" : "s"} ${verb}.`
        + (body.missing.length ? ` ${body.missing.length} were already gone.` : "");
    });
  }

  /**
   * Abandon the composition, and everything gathered for it.
   *
   * Distinct from "Choose another clip" beside the heading, which keeps the
   * composition and goes back to the picker to change what is in it. This is
   * the way out: the same reset a successful submit performs, without the
   * posting - so the picker closes and nothing is left half-chosen behind it.
   *
   * Confirmed only when there is writing to lose. A dialog in front of an
   * empty form is a dialog people learn to dismiss without reading, which is
   * exactly when it stops protecting the case that matters.
   */
  function cancelDraft() {
    const written = Object.values(draftCopy).some(
      (copy) => copy.body.trim() || copy.hashtags.trim(),
    );
    if (written && !window.confirm(
      "Discard these posts and the copy written for them?",
    )) return;
    setDrafting([]);
    setSelectedAssets({});
    setPicking(false);
    setDraftCopy({});
    setSplitPictures(false);
    setRowMatches({});
    resetDraftProducts();
  }

  /**
   * What the picked clips are, in the shape every Library editor takes.
   *
   * `handoffPath` rather than the original for the effect editor, so a clip
   * that already carries a rendered cut is stacked onto that cut instead of
   * silently starting again from the source.
   */
  const selectionTargets: LibrarySelectionTarget[] = selectedLibrary.map((asset) => ({
    id: asset.id,
    title: asset.title,
    mediaKind: (asset.media_kind === "image" || asset.media_kind === "audio" ? asset.media_kind : "video") as LibraryMediaKind,
  }));

  /**
   * The same menu the Library carries, built from the same registry.
   *
   * Only "Apply effects" was offered here, so choosing media for a campaign
   * meant leaving for the Library to caption or voice a clip and coming back
   * to a selection that no longer existed.
   */
  const selectionActionItems: ActionMenuItem[] = LIBRARY_SELECTION_ACTIONS.map((action) => {
    const state = selectionActionState(action, selectionTargets);
    const suffix = SELECTION_ACTION_KEY[action.id];
    return {
      id: action.id,
      label: t(`library.selectionAction${suffix}`),
      description: t(`library.selectionAction${suffix}Help`),
      disabled: !canEdit || !state.enabled,
      disabledReason: state.compatible.length === 0
        ? t("library.actionNoCompatible")
        : state.overLimit && action.maxItems
          ? t("library.actionLimit", { count: action.maxItems })
          : undefined,
      icon: <ActionIcon name={SELECTION_ACTION_ICON[action.id]} />,
    };
  });

  /** Replace the selection with everything loaded, or clear it - as the Library does. */
  function toggleAllLoaded() {
    setSelectedAssets(
      allLoadedSelected
        ? {}
        : Object.fromEntries(library.map((asset) => [asset.id, asset])),
    );
  }
  /**
   * The kind row, and the counts beside it.
   *
   * Read from the facets rather than the page of results, because the API
   * computes them with the media kind left out of its own filter - so each
   * count is what choosing that kind would actually show, under whatever else
   * is already narrowed. The hook's `total` is not used for this: it counts audio
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
      metrics: item.metrics,
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
      metrics: null,
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
  // Everything on the day the "see more" drawer opened, in the same zone the
  // grid grouped by, so the drawer and the cell agree on which day a post is on.
  const openDayEntries = openDay
    ? timeline.filter(
        (entry) => new Date(entry.at).toLocaleDateString("en-CA", { timeZone: readerZone }) === openDay,
      )
    : [];
  // The day's tickable posts and whether all / some are ticked, so the drawer's
  // "select all" can show a full, an indeterminate, or an empty box.
  const daySelectableIds = openDay ? selectableDayIds(openDayEntries) : [];
  const allDaySelected = daySelectableIds.length > 0
    && daySelectableIds.every((id) => selectedDayPosts.has(id));
  const someDaySelected = selectedDayPosts.size > 0 && !allDaySelected;
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
            {/* Beside the switch, not in the settings dialog. These three are
                one sentence - whether it runs, how much it does alone, and
                what running does - and the middle of it was two clicks away in
                a form about what the campaign is for. Read together they
                explain each other; read apart, none of them explains
                anything. */}
            <label className="autopilot-delivery">
              <span>
                Authority
                {/* The unlock progress, where the choice is made rather than
                    only where a refused switch would have explained it: the
                    chip says how close Autonomous is, and its hover carries the
                    full count and what still has to resolve. */}
                {autopilot.graduation && autopilot.authority !== "autonomous" && (
                  <em
                    className="autopilot-authority-hint"
                    title={autopilot.graduation.ready
                      ? "This campaign has earned Autonomous - switch to it here and only weakly matched products will wait."
                      : `Autonomous unlocks at ${autopilot.graduation.required} provider-confirmed posts - ${autopilot.graduation.published} so far`
                        + (autopilot.graduation.unresolved
                          ? `, with ${autopilot.graduation.unresolved} uncertain deliver${autopilot.graduation.unresolved === 1 ? "y" : "ies"} to resolve first.`
                          : ".")}
                  >
                    {autopilot.graduation.ready
                      ? "Autonomous ready"
                      : `${autopilot.graduation.published}/${autopilot.graduation.required} to Autonomous`}
                  </em>
                )}
              </span>
              <Select
                value={autopilot.authority}
                disabled={!canEdit}
                onChange={(event) =>
                  void save({ authority: event.target.value as Autopilot["authority"] })}
              >
                {AUTHORITIES.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
            </label>
            <label className="autopilot-delivery">
              <span>{t("autopilot.delivery")}</span>
              <Select
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
              </Select>
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
        {destinations.length > 0 && dailyScheduleCapacity > 0 && (
          <p className="autopilot-expansion" role="status">
            {(() => {
              const perDay = dailyScheduleCapacity;
              const posts = autopilot.queue_ready;
              return (
                <>
                  <strong>{posts} {posts === 1 ? "post" : "posts"}</strong>
                  {posts === 1 ? " goes to " : " go to "}
                  <strong>{destinations.length} {destinations.length === 1 ? "account" : "accounts"}</strong>
                  {", up to "}
                  <strong>{perDay} {perDay === 1 ? "post" : "posts"} a day</strong>
                  {` (bounded by each page’s posting times and the per-account daily cap).`}
                  {autopilot.offer_mode === "none"
                    ? " No affiliate link is attached."
                    : " Each post carries its affiliate link where that link can be clicked."}
                  {/* The ceiling is true and, on a queue that does not repeat,
                      beside the point: "up to 10 a day" against three postings
                      in total describes a rate nothing can sustain. What runs
                      out first is what somebody needs to know. */}
                  {autopilot.remaining_outings !== null && (
                    <>
                      {" "}
                      {autopilot.remaining_outings === 0 ? (
                        <b className="autopilot-expansion-warn">
                          The queue is spent: every post has been to every account.
                          Add posts, or allow repeats.
                        </b>
                      ) : (
                        <>Repeats are off, so{" "}
                          <strong>{autopilot.remaining_outings}{" "}
                            {autopilot.remaining_outings === 1 ? "posting" : "postings"}</strong>
                          {" "}remain before the queue is spent
                          {perDay > 0 && autopilot.remaining_outings < perDay
                            ? " - under a day at that rate."
                            : perDay > 0
                              ? ` - about ${Math.floor(autopilot.remaining_outings / perDay)} day(s) at that rate.`
                              : "."}
                        </>
                      )}
                    </>
                  )}
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
              : Math.max(slots.length, ...destinationSlotCounts, 0)}</strong>
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
                <span aria-hidden="true">{row.met
                  ? <Check size={14} strokeWidth={3} />
                  : <Circle size={9} strokeWidth={3} fill="currentColor" />}</span>
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
          {/* "Nothing is published until you approve it" is rule 6 of the
              posting strategy, on the same screen. What is left is the part
              that is not written there. */}
          <p className="autopilot-lede">What you see is exactly what will go
            out. A post that is not finished can’t be approved — it shows what
            to fix first.</p>
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
          {/* What the buttons do, once above the list rather than under every
              post in it. The same four sentences repeated down a page of ten
              held posts is forty lines of instruction for four controls. */}
          {canEdit && exceptions.length > 0 && (
            <small className="campaign-approval-actions-hint">
              <strong>Approve</strong> sends it on the campaign’s schedule ·{" "}
              <strong>Publish now</strong> sends it immediately ·{" "}
              <strong>Edit</strong> changes what it says, media stays frozen ·{" "}
              <strong>Skip this time</strong> frees the slot and clip; the post
              returns next cycle ·{" "}
              <strong>Decline</strong> also pauses the post, so it stops being
              proposed until you put it back.
            </small>
          )}
          {/* Aligned with the checkboxes down the list, so ticking posts and
              acting on them read as one thing - rather than a checkbox on the
              left of each row and its buttons off in the card's top corner. */}
          {canEdit && exceptions.length > 1 && (
            <div className="campaign-approval-toolbar" data-active={picked.size > 0 || undefined}>
              <label className="campaign-approval-selectall">
                <input
                  type="checkbox"
                  ref={(el) => { if (el) el.indeterminate = picked.size > 0 && picked.size < exceptions.length; }}
                  checked={picked.size === exceptions.length}
                  aria-label="Select all held posts"
                  onChange={() => setPicked(picked.size === exceptions.length
                    ? new Set()
                    : new Set(exceptions.map((item) => item.id)))}
                />
                Select all
              </label>
              {picked.size > 0 && (
                <>
                  <strong>{picked.size} selected</strong>
                  <Button variant="primary" size="sm" busy={busy === "approve-batch"}
                    onClick={() => void approvePicked()}>Approve {picked.size}</Button>
                  <Button variant="secondary" size="sm" busy={busy === "approve-batch"}
                    onClick={() => void approvePicked({ publishNow: true })}>Publish now</Button>
                  <Button variant="quiet" size="sm" busy={busy === "dismiss-batch"}
                    onClick={() => void dismissPicked()}>Skip</Button>
                  <Button variant="quiet" size="sm" busy={busy === "dismiss-batch"}
                    onClick={() => void dismissPicked({ stopProposing: true })}>Decline</Button>
                  <button type="button" className="campaign-approval-clear"
                    onClick={() => setPicked(new Set())}>Clear</button>
                </>
              )}
            </div>
          )}
          <ul className="campaign-approval-list">
            {exceptions.map((item) => (
              <li key={item.id}>
                {/* Where this one lands, before the post itself. Several held
                    posts are often the same video for different accounts -
                    same thumbnail, same caption, different network, different
                    time, and the link in a different place - so a reader
                    scrolling them needs the difference at the top of each,
                    not in a line of grey under the preview. */}
                <header className="campaign-approval-where">
                  {canEdit && (
                    <input
                      type="checkbox"
                      checked={picked.has(item.id)}
                      aria-label={`Select the post for ${item.destination_label ?? "this account"}`}
                      onChange={() => setPicked((current) => {
                        const next = new Set(current);
                        if (next.has(item.id)) next.delete(item.id);
                        else next.add(item.id);
                        return next;
                      })}
                    />
                  )}
                  {item.platform && (
                    <PlatformIcon platform={item.platform as PublishingPlatform} size={18} />
                  )}
                  <strong>{item.destination_label ?? item.platform ?? "destination"}</strong>
                  <small>{item.platform
                    ? platformLabels[item.platform as PublishingPlatform] : ""}
                    {item.scheduled_at
                      ? ` · ${new Date(item.scheduled_at).toLocaleString()}`
                      : ""}</small>
                  {item.placement && (
                    <Badge tone={placementTone(item.placement)}>
                      {t(`autopilot.placement.${item.placement}`)}
                    </Badge>
                  )}
                </header>
                <HeldPreview item={item} workspaceId={workspaceId} apiFetch={apiFetch} />
                <div className="campaign-approval-facts">
                  {batchResults.find((row) => row.execution_id === item.id) && (
                    <p className="autopilot-refusal" role="status">
                      <strong>Left held.</strong>{" "}
                      {batchResults.find((row) => row.execution_id === item.id)!.problem}
                    </p>
                  )}
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
                            {/* Both answers to "no", because they are not the
                                same answer. Skipping frees this outing and the
                                post comes back next pass; declining takes it
                                out of the rotation, which is the only one that
                                stops a post nobody wants arriving here every
                                cycle. Only "Skip" used to exist, under a name
                                that read like the milder of two options with
                                no second option to be milder than. */}
                            <Button variant="quiet" size="sm"
                              busy={busy === `dismiss-${item.id}`}
                              onClick={() => void decideException(item.id, "dismiss")}
                            >Skip this time</Button>
                            <Button variant="quiet" size="sm"
                              busy={busy === `dismiss-${item.id}`}
                              onClick={() => {
                                if (!window.confirm(
                                  "Decline this post? It is cancelled and paused, "
                                  + "so it stops being proposed until you put it "
                                  + "back in the queue.",
                                )) return;
                                void decideException(item.id, "dismiss", { stopProposing: true });
                              }}
                            >Decline</Button>
                          </span>
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
        className="campaign-queue-card"
        eyebrow={t("autopilot.queueEyebrow")}
        title={t("autopilot.queue", {
          ready: autopilot.queue_ready, total: autopilot.queue_total,
        })}
        aside={canEdit ? (
          <>
            {/* In the heading, not in the selection bar below it.
                The bar grows a row of actions the moment anything is ticked,
                and the strip sat between the count and those actions - so
                selecting a post shoved the chips sideways under the cursor
                that had just clicked one. This heading does not change with
                the selection, so the chips stay where they were put.

                Only when there is more than one pile to choose between: a
                queue that is entirely "in rotation" has nothing to filter. */}
            {queueChips.length > 2 && (
              <FilterChipStrip
                chips={queueChips}
                selected={activeQueueTag}
                onSelect={setQueueTag}
                ariaLabel={t("autopilot.queueFilterLabel")}
                className="queue-filter-strip"
                dense
              />
            )}
            <Button variant="secondary" size="sm"
              onClick={() => loadLibrary()}><ActionIcon name="clip" />{t("autopilot.addFromLibrary")}</Button>
          </>
        ) : undefined}
        toolbar={canEdit && queue.length > 0 ? (
          <div className={`campaign-queue-bar${pickedQueue.length ? " active" : ""}`}>
            <span
              className="library-pick"
              role="checkbox"
              tabIndex={0}
              aria-checked={allQueueSelected}
              aria-label={allQueueSelected
                ? "Clear selection"
                : `Select all ${shownQueue.length} queued posts`}
              onClick={toggleAllQueue}
              onKeyDown={(event) => {
                if (event.key !== " " && event.key !== "Enter") return;
                event.preventDefault();
                toggleAllQueue();
              }}
            >{allQueueSelected && <ActionIcon name="confirm" size={12} />}</span>
            <strong>
              {pickedQueue.length
                ? `${pickedQueue.length} selected`
                : `Select from ${shownQueue.length} post${shownQueue.length === 1 ? "" : "s"}`}
            </strong>
            {pickedQueue.length > 0 && (
              <>
                {pickedQueue.some((item) => item.state !== "approved") && (
                  <Button variant="secondary" size="sm"
                    busy={busy === "queue-batch-approve"}
                    onClick={() => void batchQueue("approve")}>
                    {t("autopilot.approve")}
                  </Button>
                )}
                {pickedQueue.some((item) => item.state === "approved") && (
                  <Button variant="quiet" size="sm"
                    busy={busy === "queue-batch-hold"}
                    onClick={() => void batchQueue("hold")}>
                    Hold back
                  </Button>
                )}
                <Button variant="danger" size="sm"
                  busy={busy === "queue-batch-remove"}
                  onClick={() => void batchQueue("remove")}>
                  {t("common.delete")}
                </Button>
                <Button variant="quiet" size="sm" onClick={() => setQueuePicked(new Set())}>
                  Clear selection
                </Button>
              </>
            )}
          </div>
        ) : undefined}
      >
        {/* The rules moved to "How this campaign posts", beside this card.
            Two sentences of them here restated the two the card states in
            order and in the campaign's own numbers, which is the version
            somebody can predict from. */}

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
                    onClick={() => picker.setFilters({ ...libraryFilters, mediaKind: value }, true)}
                  >{label} <span>{(count ?? 0).toLocaleString()}</span></button>
                );
              })}
            </div>
            <AssetFilters
              values={libraryFilters}
              facets={libraryFacets}
              fields={["query", "effect", "channel", "platform", "length"]}
              cleared={{}}
              onChange={(next) => picker.setFilters(next)}
            />
            <div className="campaign-media-actions">
              {/* The same control the Library page carries, in the same shape,
                  so ticking everything works here the way it does there. */}
              <span
                className="library-pick"
                role="checkbox"
                tabIndex={0}
                aria-checked={allLoadedSelected}
                aria-label={allLoadedSelected
                  ? "Clear selection"
                  : `Select all ${library.length} loaded`}
                onClick={toggleAllLoaded}
                onKeyDown={(event) => {
                  // A span is not a control, so it does not answer Space and
                  // Enter by itself. Both, because a checkbox takes Space and
                  // people reach for Enter anyway.
                  if (event.key !== " " && event.key !== "Enter") return;
                  event.preventDefault();
                  toggleAllLoaded();
                }}
              >{allLoadedSelected && <ActionIcon name="confirm" size={12} />}</span>
              <span className="campaign-media-summary">{selectedLibrary.length
                ? [
                    `${selectedLibrary.length} ready for campaign actions`,
                    // Pictures post as one carousel, so how many there are is
                    // the shape of the post rather than a count of files -
                    // and after "select all" it is worth being able to see it
                    // without counting ticks.
                    selectedPictures > 1 ? `${selectedPictures} pictures as one carousel` : "",
                    matchingCount > library.length
                      ? `${library.length} of ${matchingCount.toLocaleString()} loaded`
                      : "",
                    // Only past the ceiling is narrowing still the answer.
                    matchingCount > PICKER_CEILING
                      ? `${PICKER_CEILING.toLocaleString()} at a time — narrow the filter to reach the rest`
                      : "",
                  ].filter(Boolean).join(" · ")
                : `Select clips to edit or add to the campaign${
                    matchingCount > library.length
                      ? ` · showing ${library.length} of ${matchingCount.toLocaleString()}`
                      : ""
                  }`}</span>
              {/* The pair the Library carries: the box ticks what is loaded,
                  this reaches the rest. Named with the real number so it is
                  never mistaken for the count already on screen. */}
              {matchingCount > library.length && library.length < PICKER_CEILING && (
                <Button variant="secondary" size="sm" busy={picker.loading === "all"}
                  onClick={() => void selectAllMatching()}>
                  Select all {Math.min(matchingCount, PICKER_CEILING).toLocaleString()} matching
                </Button>
              )}
              {matchingCount > library.length && library.length < PICKER_CEILING && (
                <Button variant="quiet" size="sm" busy={picker.loading === "more"}
                  onClick={() => void picker.loadMore()}>Load more</Button>
              )}
              {/* Every editing action the Library offers, not only effects. */}
              <ActionMenu
                label={t("library.selectionActions")}
                ariaLabel={t("library.selectionActionsLabel")}
                icon={<ActionIcon name="edit" />}
                items={selectionActionItems}
                disabled={!canEdit || !selectedLibrary.length}
                onSelect={(id) => setSelectionAction(id as LibrarySelectionActionId)}
              />
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
                    <b className="campaign-media-check" aria-hidden="true"><Check size={14} strokeWidth={3} /></b>
                  </label>
                </li>
              ))}
              {!library.length && (
                <li className="campaign-media-empty">
                  {picker.loading === "list" ? "Reading the library…" : t("autopilot.noClips")}
                </li>
              )}
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
                    {" "}The rest take video only, and this post will be
                    skipped on them: {carouselReach.refusing.map((item) => item.label).join(", ")}.
                  </>}
                </> : <>
                  <strong>No account on this campaign can post a photo carousel.</strong>
                  {" "}
                  {carouselReach.refusing.map((item) => item.label).join(", ")} take
                  video only, so this post would never be posted. Add a
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
                <small>Pinning here overrides the campaign for this post.
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
            {/* A way out, not only a way on. The form offered Add and nothing
                else, so a composition begun by mistake could be left only by
                adding the posts and deleting them again, or by navigating away
                and hoping. Cancel first and the action last, as the campaign
                dialogs read. */}
            <div className="autopilot-compose-actions">
              <Button type="button" variant="quiet" onClick={cancelDraft}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" variant="primary" busy={busy === "queue"}>
                {draftPosts.length > 1
                  ? `Add ${draftPosts.length} posts`
                  : t("autopilot.addToQueue")}
                {draftProductMode === "manual" && draftPinned.size > 0
                  ? ` · ${draftPinned.size} ${draftPinned.size === 1 ? "product" : "products"}`
                  : ""}
              </Button>
            </div>
          </form>
        )}
        {queue.length === 0 ? (
          <p className="autopilot-empty">{t("autopilot.noQueue")}</p>
        ) : (
          <>
          <ul className={canEdit ? "autopilot-queue selectable" : "autopilot-queue"}>
            {shownQueue.slice(safeQueuePage * QUEUE_PAGE_SIZE, (safeQueuePage + 1) * QUEUE_PAGE_SIZE).map((item) => (
              <li key={item.id} id={`queued-${item.id}`} className={item.state}>
                {/* A span, not a div: `.autopilot-queue > li > div` is a grid
                    rule that catches any div wrapper added inside these rows. */}
                {canEdit && (
                  <span
                    className="library-pick"
                    role="checkbox"
                    tabIndex={0}
                    aria-checked={queuePicked.has(item.id)}
                    aria-label={`Select ${displayTitle(item.title) ?? "this post"}`}
                    onClick={() => toggleQueuePick(item.id)}
                    onKeyDown={(event) => {
                      if (event.key !== " " && event.key !== "Enter") return;
                      event.preventDefault();
                      toggleQueuePick(item.id);
                    }}
                  >{queuePicked.has(item.id) && <ActionIcon name="confirm" size={12} />}</span>
                )}
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
                    // What the matcher resolved for this post, not a second
                    // guess at it. This used to mirror the selection rules
                    // here - pins, then confident matches, then the best
                    // available - which meant reading them off a ranking that
                    // is identical for every post: the rotation the preview
                    // had just spread across twenty rows showed up as the same
                    // product twenty times.
                    const chosen = item.offer_match?.chosen_offer_ids ?? [];
                    const attaching = chosen.length
                      ? chosen
                        .map((offerId) => ranked.find((match) => match.offer_id === offerId))
                        .filter((match): match is OfferMatch => Boolean(match))
                      : [];
                    const weak = attaching.every((match) => match.confidence === "low")
                      && Boolean(attaching.length);
                    return (
                      <>
                        {attaching.map((match) => (
                          // The caveat is inside the group, not beside it: as a
                          // sibling chip it wrapped to a line of its own under a
                          // long product name and read as a verdict on the row
                          // rather than on the product.
                          <span key={match.offer_id} className="campaign-queue-product">
                            {/* The name truncates, the rate does not. In one
                                chip the commission was the tail of a long
                                product name and the first thing an ellipsis
                                ate - which is the half of it worth reading:
                                the name says which product, the rate says
                                whether the post is worth making. */}
                            <em className="product">{match.product_name}</em>
                            <em className="rate">{match.score}%
                              {commissionLabel(match) && ` · ${commissionLabel(match)}`}</em>
                            {/* Said rather than left to the percentage. Nothing
                                here cleared the evidence bar, and the best of a
                                weak field is still what goes out. */}
                            {weak && <em className="soft">weak fit</em>}
                          </span>
                        ))}
                        {!ranked.length
                          ? <em className="soft">analysis pending</em>
                          : !attaching.length && <em className="soft">no product attached</em>}
                      </>
                    );
                  })()}
                  </span>
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
                            else if (!hasPostingTimes) jumpTo("schedule");
                            else jumpTo("revenue");
                          }
                          return t("autopilot.itemApproved");
                        })}>{t("autopilot.approve")}</Button>
                    )}
                    <Button variant="quiet" size="sm"
                      onClick={() => openPostEditor(item)}>Edit content</Button>
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
                {/* A row of its own, spanning every column. Nested in the copy
                    column it was 140px wider than the track holding it, so an
                    open rehearsal drew straight over the status badge and the
                    buttons beside it. */}
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
                        : !hasPostingTimes
                          ? "No posting times yet. Add one and the plan appears here."
                          : preview
                            // Says which window is full, and that being
                            // outside it is normal. "Every slot is taken by
                            // another post" describes a queue larger than
                            // the outlook - which is most queues - but reads
                            // as a fault, so a full week of correct planning
                            // looked like dozens of posts going nowhere.
                            ? `Waiting its turn: the next ${preview.horizon_days ?? 7} days are `
                              + "already full. It stays in rotation and takes the first free slot after that."
                            : "Loading the plan…"}
                />
              </li>
            ))}
          </ul>
          {queuePages > 1 && (
            <nav className="autopilot-pager" aria-label={t("common.posts")}>
              <Button variant="quiet" size="sm" disabled={safeQueuePage === 0}
                onClick={() => setQueuePage(Math.max(0, safeQueuePage - 1))}>
                {t("common.previous")}
              </Button>
              <span>{t("library.previewPosition", { position: safeQueuePage + 1, total: queuePages })}</span>
              <Button variant="quiet" size="sm" disabled={safeQueuePage >= queuePages - 1}
                onClick={() => setQueuePage(Math.min(queuePages - 1, safeQueuePage + 1))}>
                {t("common.next")}
              </Button>
            </nav>
          )}
          </>
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
          <Dialog
            open
            size="wide"
            onClose={closePostEditor}
            title="Edit scheduled post"
            description="Everything below is one post. The campaign adds the disclosure and the product link to it, differently on each account - what that comes to is composed underneath, exactly as each one will receive it."
          >
          <form className="autopilot-compose" id="campaign-edit-content"
            ref={editForm} onInput={scheduleCompose} onSubmit={(event) => {
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
                  // Empty goes back to the campaign's wording rather than
                  // storing an empty disclosure, which is the one thing a post
                  // with a product attached may not have.
                  disclosure: editingDisclosure.trim() || null,
                  bio_hint: editingBioHint.trim() || null,
                }),
              }));
              closePostEditor();
              return "Campaign copy updated.";
            });
          }}>
            {/* The title, description and close now come from the Dialog frame,
                so the form opens straight into its first field. */}
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
                or that on TikTok the link would not appear in the post at all.
                It was then described in prose, which is a manual for a machine
                that is right here and can be asked. This is its answer: the
                same composer the scheduler publishes through, run on what is
                in the form. */}
            <section className="campaign-post-anatomy">
              <div className="campaign-anatomy-head">
                <strong>What goes out</strong>
                <small>{composed?.products.length
                  ? `With ${composed.products.map((item) => item.name).join(", ")} attached.`
                  : autopilot.offer_mode === "none"
                    ? "This campaign posts organically; no product is attached."
                    : "No product is confident enough to attach; the post would go out on its own."}</small>
              </div>
              <div className="campaign-wording">
                {/* Only where the campaign adds one. A wording box above a
                    caption that will not carry it describes a field that
                    governs nothing, and the switch is the campaign's to set. */}
                {autopilot.disclose ? (
                  <label>Disclosure
                    <input value={editingDisclosure} maxLength={300}
                      placeholder={composed?.campaign_disclosure ?? autopilot.disclosure}
                      onChange={(event) => setEditingDisclosure(event.target.value)} />
                    <small>{editingDisclosure.trim() ? (
                      <>This post only.{" "}
                        <button type="button" className="campaign-wording-reset"
                          onClick={() => setEditingDisclosure("")}>
                          Use the campaign&apos;s
                        </button></>
                    ) : autopilot.disclosure
                      ? "The campaign's wording. Leads the caption whenever a product is attached."
                      : "Switched on but not written. Any account carrying a product will refuse the post until there is one."}</small>
                  </label>
                ) : (
                  <p className="campaign-wording-off">
                    This campaign adds no disclosure. Posts carrying a product go
                    out unmarked, which Campaign settings can change.
                  </p>
                )}
                {/* Only where a bio placement is in play. On a campaign whose
                    accounts all take links, this field governs nothing. */}
                {destinations.some((item) => item.link_placement === "bio") && (
                  <label>Bio wording
                    <input value={editingBioHint} maxLength={120}
                      placeholder={composed?.campaign_bio_hint ?? autopilot.bio_hint}
                      onChange={(event) => setEditingBioHint(event.target.value)} />
                    <small>{editingBioHint.trim() ? (
                      <>This post only.{" "}
                        <button type="button" className="campaign-wording-reset"
                          onClick={() => setEditingBioHint("")}>
                          Use the campaign&apos;s
                        </button></>
                    ) : "The campaign's wording, where no link in a post is clickable."}</small>
                  </label>
                )}
              </div>
              {destinations.length === 0 ? (
                <p className="autopilot-empty">
                  No account on this campaign yet, so there is nothing to compose for.
                </p>
              ) : !composed ? (
                <p className="autopilot-empty">Composing what each account receives…</p>
              ) : (
                <ul className="campaign-composed" aria-label="What each account receives">
                  {composed.accounts.map((account) => (
                    <li key={account.destination_id}>
                      <div className="campaign-composed-head">
                        <PlatformIcon platform={account.platform} size={18} />
                        <strong>{account.label}</strong>
                        {account.placement && (
                          <Badge tone={placementTone(account.placement)}>
                            {t(`autopilot.placement.${account.placement}`)}
                          </Badge>
                        )}
                      </div>
                      {account.refused ? (
                        <p className="campaign-composed-refused">{account.refused}</p>
                      ) : (
                        <>
                          {account.title && (
                            <p className="campaign-composed-title">{account.title}</p>
                          )}
                          <pre className="campaign-composed-text">{account.caption}</pre>
                          {account.first_comment && (
                            <div className="campaign-composed-part">
                              <b>{followUpKind(account.platform)}</b>
                              <pre className="campaign-composed-text">
                                {account.first_comment}
                              </pre>
                            </div>
                          )}
                          {(account.thread ?? []).map((reply, index) => (
                            <div className="campaign-composed-part" key={index}>
                              <b>Reply {index + 1}</b>
                              <pre className="campaign-composed-text">{reply}</pre>
                            </div>
                          ))}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <Button type="submit" variant="primary" busy={busy === "edit-copy"}>Save post</Button>
          </form>
          </Dialog>
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
        {/* The campaign's own rhythm, above the accounts it applies to.
            Without it, running two campaigns on different hours meant setting
            the same preset on every destination and remembering to set it
            again on each one added later - so the campaign, which is the
            thing that has a rhythm, was the one place that could not say so.
            Any single account below can still disagree; that row says which
            of the two it is following. */}
        {canEdit && (
          <label className="campaign-schedule-default">
            <span>
              <strong>Posting times for this campaign</strong>
              <small>Applies to every account below that has not been given
                its own.</small>
            </span>
            <PostingPresetSelect
              value={autopilot.posting_preset_id ?? ""}
              presets={postingPresets}
              timezone={scheduleTimezone}
              workspaceSlots={slots}
              inheritLabel="Each account's own schedule"
              onChange={(postingPresetId) => void save({
                posting_preset_id: postingPresetId || null,
              })}
            />
          </label>
        )}
        {destinations.length === 0 ? (
          <p className="autopilot-empty">{t("autopilot.noDestinations")}</p>
        ) : (
          <ul className="autopilot-destinations">
            {destinations.map((item) => (
              <li key={item.id}>
                <div className="campaign-account-identity">
                  <PlatformIcon platform={item.platform} size={30} />
                  <span>
                    {/* The name opens the account it names, where the network
                        has one address for the handle the engine reported.
                        Where it does not, the name stays plain text rather
                        than becoming a link that opens the wrong page. */}
                    {destinationProfile(item) ? (
                      <a
                        className="campaign-account-link"
                        href={destinationProfile(item) ?? undefined}
                        target="_blank"
                        rel="noreferrer"
                        title={`Open ${item.label} on ${platformLabels[item.platform]}`}
                      >
                        <strong>{item.label}</strong>
                        <ActionIcon name="link" size={12} />
                      </a>
                    ) : (
                      <strong>{item.label}</strong>
                    )}
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
                <p className="autopilot-schedule-reason">
                  Posting schedule: <strong>{item.posting_schedule?.label
                    ?? "Workspace posting times"}</strong>
                  {/* Which of the four is in force. Worth saying rather than
                      leaving to be inferred from the times: four levels
                      resolve here, and "these are the hours" is a different
                      statement from "these are the hours, and here is why". */}
                  {item.posting_schedule?.source === "page" ? " · assigned to this page" : ""}
                  {item.posting_schedule?.source === "campaign" ? " · this campaign's own times" : ""}
                  {item.posting_schedule?.source === "destination" ? " · set for this account" : ""}
                </p>
                {canEdit && (
                  <div className="autopilot-destination-controls">
                  <label className="autopilot-placement-choice">
                    Posting times
                    <PostingPresetSelect
                      value={item.posting_preset_id ?? ""}
                      presets={postingPresets}
                      timezone={scheduleTimezone}
                      workspaceSlots={slots}
                      pagePresetId={pageAssignments[item.page_key ?? ""]}
                      campaignPresetId={autopilot.posting_preset_id ?? undefined}
                      onChange={(postingPresetId) => void run("schedule", async () => {
                        await json(await apiFetch(
                          `${base}/destinations/${item.id}/schedule`,
                          {
                            method: "POST",
                            headers: { "content-type": "application/json" },
                            body: JSON.stringify({
                              posting_preset_id: postingPresetId || null,
                            }),
                          },
                        ));
                        return `Posting schedule updated for ${item.label}.`;
                      })}
                    />
                  </label>
                  <label className="autopilot-placement-choice">
                    Link placement
                    <Select
                      value={item.link_placement_setting}
                      onChange={(event) => void run("placement", async () => {
                        const saved = await json<{
                          held?: { recomposed: number; kept: number };
                          planned?: number;
                        }>(await apiFetch(
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
                        // Where the link goes decides what the caption says,
                        // so this reaches the posts already waiting for this
                        // account - and says how many.
                        const reached = saved?.held;
                        // And the planned ones, which are rebuilt from the
                        // campaign every time the outlook is drawn and so
                        // follow this without anything being rewritten. Said
                        // out loud because nothing on screen shows it: a
                        // campaign with nothing frozen used to report reaching
                        // no posts at all, which read as a setting that did
                        // nothing.
                        const planned = saved?.planned ?? 0;
                        return `Link placement updated for ${item.label}.`
                          + (reached?.recomposed
                            ? ` ${reached.recomposed} waiting post${
                              reached.recomposed === 1 ? "" : "s"} rewritten.`
                            : "")
                          + (planned
                            ? ` ${planned} planned post${
                              planned === 1 ? "" : "s"} follow${
                              planned === 1 ? "s" : ""} the new setting.`
                            : "")
                          + (reached?.kept
                            ? ` ${reached.kept} left as edited by hand.`
                            : "");
                      })}
                    >
                      <option value="auto">Auto — network decides (recommended)</option>
                      <option value="caption">Always in the caption</option>
                      <option value="first_comment">First comment, where deliverable</option>
                      <option value="bio">Always via bio link</option>
                    </Select>
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
                        page_key: account.page_key,
                      }),
                    }))));
                  setSelectedAccounts(new Set());
                  setAdding(false);
                  // The product decision is in this same area now, so the
                  // only move left is on to the schedule.
                  if (slots.length || chosen.some((account) => (
                    Boolean(pageAssignments[account.page_key ?? ""])
                  ))) void loadRecommendations();
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
                        <small className="campaign-account-schedule">
                          Posting schedule: {postingPresets.find((preset) => (
                            preset.id === pageAssignments[account.page_key ?? ""]
                          ))?.label ?? "Workspace posting times"}
                        </small>
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
              {/* What this campaign may promote, which is now a fact rather
                  than a hint. Nothing here means nothing is attached: smart
                  matching ranks these products and no others, and a pin can
                  only name one of them. The list used to be a "shortlist" that
                  narrowed an otherwise unlimited pool, which nobody filled in
                  - so campaigns ranked the whole catalogue. */}
              <div className="campaign-product-heading">
                <div>
                  <strong>Products this campaign may promote</strong>
                  <small>{tagged.length
                    ? `Smart matching chooses from these ${tagged.length}. Nothing else is offered, here or on a post.`
                    : "None yet. Until one is added, posts go out with no product attached."}</small>
                </div>
                {/* Both doors, together. Reviewing is only offered once there
                    is more here than the panel shows - a dialog over six rows
                    is a click that changes nothing. */}
                {tagged.length > TAGGED_SHOWN && (
                  <Button variant="quiet" size="sm"
                    onClick={() => setReviewingProducts(true)}>
                    Review all {tagged.length}
                  </Button>
                )}
                {canEdit && (
                  <Button variant={tagged.length ? "secondary" : "primary"} size="sm"
                    busy={busy === "offers"}
                    onClick={() => setAddingProducts(true)}>
                    <ActionIcon name="add" />Add products
                  </Button>
                )}
              </div>

              {tagged.length > 0 && (
                <ul className="campaign-tagged-products">
                  {tagged.slice(0, TAGGED_SHOWN).map((product) => (
                    <li key={product.offer_id}>
                      <span>
                        <strong>{product.name}</strong>
                        <small>{[
                          product.brand,
                          product.category,
                          product.marketplace ?? product.network,
                          commissionLabel(product),
                        ].filter(Boolean).join(" · ")}</small>
                      </span>
                      {/* Said where it matters: an unavailable product stays
                          tagged and stops being attached, which is a different
                          thing from having been removed. */}
                      {product.availability === "unavailable" && (
                        <Badge tone="warn">unavailable</Badge>
                      )}
                      {canEdit && (
                        <Button variant="quiet" size="sm" busy={busy === "tag-products"}
                          onClick={() => void untagProduct(product.offer_id, product.name)}>
                          Remove
                        </Button>
                      )}
                    </li>
                  ))}
                  {/* The rest, said in one line rather than drawn in ninety.
                      Inside the list, so it reads as the end of it rather than
                      as a control that belongs to the section. */}
                  {tagged.length > TAGGED_SHOWN && (
                    <li className="campaign-tagged-more">
                      <span>{tagged.length - TAGGED_SHOWN} more</span>
                      <Button variant="quiet" size="sm"
                        onClick={() => setReviewingProducts(true)}>Review all</Button>
                    </li>
                  )}
                </ul>
              )}

              {/* The whole list, where a long one can be worked with.
                  A dialog rather than more rows: what the panel owes the
                  reader is what this campaign is about, and ninety more rows
                  answer that no better than six while pushing everything
                  after them off the screen. */}
              <Dialog
                open={reviewingProducts}
                title="Products this campaign may promote"
                description={`${tagged.length} tagged. Smart matching chooses from these and nothing else.`}
                size="wide"
                onClose={() => {
                  setReviewingProducts(false);
                  setPickedProducts(new Set());
                  setProductQuery("");
                  setProductPage(0);
                }}
              >
                <div className="campaign-product-review">
                  {/* One row of controls, because they are one question asked
                      four ways: which of these am I looking at. */}
                  <div className="campaign-product-controls">
                    <input
                      type="search"
                      value={productQuery}
                      placeholder="Search name, brand, category"
                      aria-label="Search tagged products"
                      onChange={(event) => {
                        setProductQuery(event.target.value);
                        setProductPage(0);
                      }}
                    />
                    <label>
                      <span>Show</span>
                      <Select value={productFilter}
                        onChange={(event) => {
                          setProductFilter(event.target.value as typeof productFilter);
                          setProductPage(0);
                        }}>
                        <option value="all">All</option>
                        <option value="available">Available</option>
                        <option value="unavailable">Unavailable</option>
                      </Select>
                    </label>
                  </div>
                  {/* The count, the selection and the paginator on one line -
                      each of them is a few words about the list rather than a
                      section of its own. */}
                  <div className="campaign-product-status">
                    <small>
                      {shownProducts.length === tagged.length
                        ? `${tagged.length} products`
                        : `${shownProducts.length} of ${tagged.length}`}
                      {pickedProducts.size > 0 && ` · ${pickedProducts.size} selected`}
                    </small>
                    {canEdit && pickedProducts.size > 0 && (
                      <Button variant="secondary" size="sm" busy={busy === "tag-products"}
                        onClick={() => void untagPicked()}>
                        Remove {pickedProducts.size}
                      </Button>
                    )}
                    {productPages > 1 && (
                      <span className="campaign-product-pager">
                        <Button variant="quiet" size="sm" disabled={productPageSafe === 0}
                          onClick={() => setProductPage(productPageSafe - 1)}>Back</Button>
                        <small>{productPageSafe + 1} / {productPages}</small>
                        <Button variant="quiet" size="sm"
                          disabled={productPageSafe >= productPages - 1}
                          onClick={() => setProductPage(productPageSafe + 1)}>Next</Button>
                      </span>
                    )}
                  </div>
                  {shownProducts.length === 0 ? (
                    <p className="autopilot-empty">
                      Nothing here matches that. Clear the search to see all
                      {" "}{tagged.length}.
                    </p>
                  ) : (
                    /* A table, because these are columns: a name, where it
                       came from, what it costs, what it pays. Sorted from the
                       headings themselves - an "order by" dropdown beside a
                       grid of values is a second way to say what the headings
                       already are. */
                    <div className="campaign-tagged-scroll">
                      <table className="product-table campaign-tagged-table">
                        <thead>
                          <tr>
                            <th scope="col" className="product-choose">
                              <SelectionCheckbox
                                aria-label="Select everything shown"
                                checked={productSlice.length > 0
                                  && productSlice.every((row) => pickedProducts.has(row.offer_id))}
                                indeterminate={
                                  productSlice.some((row) => pickedProducts.has(row.offer_id))
                                  && !productSlice.every((row) => pickedProducts.has(row.offer_id))}
                                disabled={!productSlice.length}
                                onChange={(event) => setPickedProducts((current) => {
                                  const next = new Set(current);
                                  for (const row of productSlice) {
                                    if (event.target.checked) next.add(row.offer_id);
                                    else next.delete(row.offer_id);
                                  }
                                  return next;
                                })}
                              />
                            </th>
                            <SortableHeader<TaggedSortKey> column="name" label="Product"
                              sort={productSort} onSort={changeProductSort} />
                            <SortableHeader<TaggedSortKey> column="source" label="Source"
                              sort={productSort} onSort={changeProductSort} />
                            <SortableHeader<TaggedSortKey> column="price" label="Price"
                              sort={productSort} onSort={changeProductSort} className="numeric" />
                            <SortableHeader<TaggedSortKey> column="rate" label="Rate"
                              sort={productSort} onSort={changeProductSort} className="numeric" />
                            <SortableHeader<TaggedSortKey> column="availability" label="Status"
                              sort={productSort} onSort={changeProductSort} />
                            <SortableHeader<TaggedSortKey> column="added" label="Added"
                              sort={productSort} onSort={changeProductSort} />
                            {canEdit && <th scope="col"><span className="sr-only">Remove</span></th>}
                          </tr>
                        </thead>
                        <tbody>
                          {productSlice.map((product) => (
                            <tr key={product.offer_id}>
                              <td className="product-choose">
                                <SelectionCheckbox
                                  aria-label={`Select ${product.name}`}
                                  checked={pickedProducts.has(product.offer_id)}
                                  onChange={(event) => setPickedProducts((current) => {
                                    const next = new Set(current);
                                    if (event.target.checked) next.add(product.offer_id);
                                    else next.delete(product.offer_id);
                                    return next;
                                  })}
                                />
                              </td>
                              <td className="campaign-tagged-name">
                                <strong>{product.name}</strong>
                                {[product.brand, product.category].filter(Boolean).length > 0 && (
                                  <small>{[product.brand, product.category]
                                    .filter(Boolean).join(" · ")}</small>
                                )}
                              </td>
                              <td>{product.marketplace ?? product.network ?? "—"}</td>
                              <td className="numeric">{product.price_cents !== null && product.price_cents !== undefined
                                ? money(product.price_cents, product.currency ?? "USD")
                                : "—"}</td>
                              <td className="numeric">{commissionLabel(product) || "—"}</td>
                              <td>
                                {product.availability === "unavailable"
                                  ? <Badge tone="warn">unavailable</Badge>
                                  : <span className="campaign-tagged-fine">available</span>}
                              </td>
                              <td>{product.tagged_at
                                ? new Date(product.tagged_at).toLocaleDateString()
                                : "—"}</td>
                              {canEdit && (
                                <td>
                                  <Button variant="quiet" size="sm" busy={busy === "tag-products"}
                                    onClick={() => void untagProduct(product.offer_id, product.name)}>
                                    Remove
                                  </Button>
                                </td>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </Dialog>

              {addingProducts && (
                <div className="campaign-product-picker">
                  <div className="autopilot-picker-head">
                    <span>
                      <strong>Add products</strong>
                      <small>Imported offers from Attribution. Adding one here
                        is the same as tagging it to this campaign there.</small>
                    </span>
                    <Button variant="quiet" size="sm"
                      onClick={() => { setAddingProducts(false); setProductSearch(""); }}>
                      {t("common.close")}
                    </Button>
                  </div>
                  <input
                    className="campaign-product-search"
                    value={productSearch}
                    placeholder="Search imported products…"
                    aria-label="Search imported products"
                    onChange={(event) => setProductSearch(event.target.value)}
                  />
                  {(() => {
                    const held = new Set(tagged.map((product) => product.offer_id));
                    const term = productSearch.trim().toLowerCase();
                    const available = offers
                      .filter((offer) => !held.has(offer.id))
                      .filter((offer) => !term
                        || offer.product.name.toLowerCase().includes(term)
                        || (offer.product.brand ?? "").toLowerCase().includes(term));
                    if (!offers.length) {
                      return <p className="autopilot-empty">
                        No products imported yet. Import them in Attribution.
                      </p>;
                    }
                    if (!available.length) {
                      return <p className="autopilot-empty">{term
                        ? "No imported product matches that."
                        : "Every imported product is already on this campaign."}</p>;
                    }
                    return (
                      <>
                        {/* Everything matching, in one action. Tagging forty
                            products one at a time is the reason people give up
                            and leave the campaign empty. */}
                        {term && available.length > 1 && (
                          <Button variant="secondary" size="sm" busy={busy === "tag-products"}
                            onClick={() => void tagProducts(available.map((offer) => offer.id))}>
                            Add all {available.length} matching
                          </Button>
                        )}
                        <ul className="campaign-product-choices">
                          {available.slice(0, 60).map((offer) => (
                            <li key={offer.id}>
                              <span>
                                <strong>{offer.product.name}</strong>
                                <small>{[
                                  offer.product.brand,
                                  offer.product.marketplace ?? offer.network,
                                  commissionLabel(offer),
                                ].filter(Boolean).join(" · ")}</small>
                              </span>
                              <Button variant="secondary" size="sm"
                                busy={busy === "tag-products"}
                                onClick={() => void tagProducts([offer.id])}>Add</Button>
                            </li>
                          ))}
                        </ul>
                        {available.length > 60 && (
                          <p className="autopilot-empty">
                            {available.length - 60} more. Search to narrow them.
                          </p>
                        )}
                      </>
                    );
                  })()}
                </div>
              )}

              {/* The ranking, on demand. It explains which of the tagged
                  products would be chosen and why, which is a different
                  question from which ones are allowed. */}
              <div className="campaign-product-heading">
                <div>
                  <small>See how these rank against this campaign’s content.</small>
                </div>
                <Button variant="quiet" size="sm" busy={busy === "recommendations"}
                  disabled={!tagged.length}
                  title={tagged.length ? undefined : "Add a product first."}
                  onClick={() => void loadRecommendations()}>Rank them</Button>
              </div>
              {recommendations && !recommendations.item_id && (
                <>
                  <p className="campaign-rotation-note">{recommendations.strategy.rotation}</p>
                  <ul className="campaign-product-matches">
                    {recommendations.matches.map((match) => (
                      <li key={match.offer_id}>
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
                      </li>
                    ))}
                    {!recommendations.matches.length && (
                      <li className="autopilot-empty">
                        Nothing here matches this campaign’s content yet.
                      </li>
                    )}
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
      {<Card title="How this campaign posts">
        <PostingStrategy
          autopilot={autopilot}
          destinations={destinations}
          slots={slots}
          timezone={scheduleTimezone}
          canEdit={canEdit}
          busy={busy === "settings"}
          onChange={(changes) => void save(changes)}
        />
      </Card>}
          </aside>
        </div>
      )}

      <EffectEditor
        open={effectOpen || selectionAction === "effects"}
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
        onClose={() => { setEffectOpen(false); setSelectionAction(null); }}
        onRendered={succeed}
      />
      {/* Keyed on the selection so reopening with different clips builds the
          dialog again rather than reusing the last run's state. */}
      {selectedLibrary.length > 0 && (
        <BatchTranscribe
          key={`picker-transcribe-${selectedLibrary.map((asset) => asset.id).join("-")}`}
          open={selectionAction === "transcribe"}
          workspaceId={workspaceId}
          targets={selectionTargets}
          canEdit={canEdit}
          apiFetch={apiFetch}
          onClose={() => setSelectionAction(null)}
          onQueued={(text: string) => succeed(text)}
        />
      )}
      {selectedLibrary.length > 0 && (
        <CaptionEditor
          key={`picker-captions-${selectedLibrary.map((asset) => asset.id).join("-")}`}
          open={selectionAction === "captions"}
          workspaceId={workspaceId}
          targets={selectionTargets}
          canEdit={canEdit}
          apiFetch={apiFetch}
          onClose={() => setSelectionAction(null)}
          onQueued={(text: string) => succeed(text)}
        />
      )}
      {selectedLibrary.length > 0 && (
        <BulkVoiceEditor
          key={`picker-voice-${selectedLibrary.map((asset) => asset.id).join("-")}`}
          open={selectionAction === "voiceover"}
          workspaceId={workspaceId}
          targets={selectionTargets}
          canEdit={canEdit}
          apiFetch={apiFetch}
          onClose={() => setSelectionAction(null)}
          onQueued={(text: string) => succeed(text)}
        />
      )}

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
                { value: "grid", label: "Grid" },
                { value: "calendar", label: "Calendar" },
              ]}
            />
            <Button variant="secondary" size="sm" busy={busy === "preview"} spinsIcon
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
            onSelectEntry={selectTimelineEntry}
            onOpenDay={setOpenDay}
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
                              <Badge tone={deliveredStatus(entry).tone}>
                                {deliveredStatus(entry).label}
                              </Badge>
                            ) : (
                              <Badge tone={entry.problem ? "warn" : "neutral"}
                                title={plannedMeaning(autopilot.delivery)}>
                                Planned
                              </Badge>
                            )}
                            {/* Beside the badge, which is where the grid puts
                                the same action. It used to live in the muted
                                source line below as ten-pixel undecorated
                                text, deliberately styled not to read as a link
                                so it would not pair with the one next to it -
                                and the result was an action nobody could find.
                                A row that can be edited says so with a
                                button. */}
                            {canEdit && entry.kind === "planned"
                              && entry.queue_item_id
                              && queueById.has(entry.queue_item_id) && (
                              <Button variant="quiet" size="sm"
                                onClick={() => openPostEditor(
                                  queueById.get(entry.queue_item_id!)!, "posts")}>
                                Edit
                              </Button>
                            )}
                          </div>
                          <h4>{entry.post_url ? (
                            <a href={entry.post_url} target="_blank" rel="noreferrer">
                              {displayTitle(entry.title) || "Untitled campaign post"}
                            </a>
                          ) : (displayTitle(entry.title) || "Untitled campaign video")}</h4>
                          {entry.kind === "delivered" && entry.metrics && (
                            <PostMetricsRow metrics={entry.metrics} />
                          )}
                          {/* Which queued post this outing is of, and a way
                              back to it. A post repeats, so the same one
                              appears on the schedule several times, and
                              without this the reader cannot tell that. */}
                          {entry.queue_item_id && queueById.get(entry.queue_item_id) && (
                            <p className="campaign-entry-source">
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
                                    <TimelineImage
                                      key={path}
                                      src={`${apiBaseUrl()}/api/workspaces/${workspaceId}/publishing/media/preview?path=${encodeURIComponent(path)}`}
                                      path={path}
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
        {timeline.length > 0 && timelineView === "grid" && (
          <div className="campaign-grid">
            {timeline.map((entry) => {
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
              const editable = entry.kind === "planned" && Boolean(entry.queue_item_id)
                && canEdit && queueById.has(entry.queue_item_id!);
              return (
                <article key={entry.key} className={[
                  "campaign-grid-card",
                  entry.kind === "delivered" ? `delivered ${entry.status ?? ""}` : "",
                  entry.problem ? "refused" : "",
                ].filter(Boolean).join(" ")}>
                  <button type="button" className="campaign-grid-media"
                    onClick={() => selectTimelineEntry(entry)}
                    title={displayTitle(entry.title) ?? entry.caption.slice(0, 80)}>
                    {thumbnailAsset
                      ? <AssetThumbnail asset={thumbnailAsset} workspaceId={workspaceId} apiFetch={apiFetch} />
                      : <span className="campaign-grid-media-empty"><ActionIcon name="play" /></span>}
                    {platform && (
                      <span className="campaign-grid-platform">
                        <PlatformIcon platform={platform} size={16} />
                      </span>
                    )}
                  </button>
                  <div className="campaign-grid-body">
                    <strong>{displayTitle(entry.title) || entry.caption.slice(0, 60) || "Untitled post"}</strong>
                    <small>
                      {new Date(entry.at).toLocaleString(undefined, {
                        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                        timeZone: readerZone,
                      })}
                      {destination?.label ? ` · ${destination.label}` : ""}
                    </small>
                    {entry.kind === "delivered" && entry.metrics && (
                      <PostMetricsRow metrics={entry.metrics} />
                    )}
                  </div>
                  <div className="campaign-grid-foot">
                    {entry.kind === "delivered" ? (
                      <Badge tone={deliveredStatus(entry).tone}>
                        {deliveredStatus(entry).label}
                      </Badge>
                    ) : (
                      <Badge tone={entry.problem ? "warn" : "neutral"}
                        title={plannedMeaning(autopilot.delivery)}>
                        Planned
                      </Badge>
                    )}
                    {editable ? (
                      <Button variant="quiet" size="sm"
                        onClick={() => openPostEditor(queueById.get(entry.queue_item_id!)!, "posts")}>
                        Edit
                      </Button>
                    ) : entry.post_url ? (
                      <a className="campaign-grid-open" href={entry.post_url}
                        target="_blank" rel="noreferrer">Open</a>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        )}
        {!preview && !ready.configured && (
          <p className="autopilot-empty">{t("autopilot.previewBlocked")}</p>
        )}
        {/* No deploy bar. The Post automatically switch is the one lever:
            switching on activates the campaign and runs it, and this timeline
            is where what it did shows up. */}
      </Card>
      {/* "See more": the calendar cell shows a day's first posts; this shows
          the day in full. A planned row edits, a delivered one opens where it
          went - the same door the chips use. */}
      {openDay && (
        <Dialog
          open
          size="wide"
          onClose={closeDay}
          title={new Date(`${openDay}T00:00:00`).toLocaleDateString(undefined, {
            weekday: "long", month: "long", day: "numeric", year: "numeric",
          })}
          description={`${openDayEntries.length} ${openDayEntries.length === 1 ? "post" : "posts"} this day`}
        >
          {/* The bar and the list are one thing - it selects what is directly
              below it - so they are wrapped and spaced together rather than
              sitting as two dialog sections a full gap apart. */}
          <div className="campaign-day-picker">
          {/* The selection bar keeps its own row whether anything is ticked or
              not, so ticking the first post never shoves the list down. */}
          <div className="campaign-day-tools" data-active={selectedDayPosts.size > 0 || undefined}>
            {daySelectableIds.length > 0 && (
              <label className="campaign-day-selectall">
                <input type="checkbox"
                  ref={(el) => { if (el) el.indeterminate = someDaySelected; }}
                  checked={allDaySelected}
                  onChange={() => toggleAllDayPosts(daySelectableIds)}
                  aria-label="Select all planned posts this day" />
                Select all
              </label>
            )}
            {selectedDayPosts.size > 0 && (
              <>
                <span className="campaign-day-tools-gap" />
                <strong>{selectedDayPosts.size} selected</strong>
                <button type="button" className="campaign-day-clear"
                  onClick={() => setSelectedDayPosts(new Set())}>Clear</button>
                <Button variant="danger" size="sm" busy={busy === "drop"}
                  onClick={deleteSelectedDayPosts}>Delete selected</Button>
              </>
            )}
          </div>
          <ul className="campaign-day-list">
            {openDayEntries.map((entry) => {
              const destination = entry.destination ?? destinations.find(
                (item) => item.id === entry.destination_id) ?? null;
              const platform = destination?.platform;
              const selectable = entry.kind === "planned" && Boolean(entry.queue_item_id)
                && canEdit && queueById.has(entry.queue_item_id!);
              const selected = selectable && selectedDayPosts.has(entry.queue_item_id!);
              return (
                <li key={entry.key}
                  data-selected={selected || undefined}
                  className={[
                    "campaign-day-row",
                    entry.kind === "delivered" ? `delivered ${entry.status ?? ""}` : "",
                    entry.problem ? "refused" : "",
                  ].filter(Boolean).join(" ")}>
                  {/* The checkbox column is always present - empty on a
                      delivered post, which cannot be deleted - so ticking one
                      never nudges the row beside it. */}
                  <span className="campaign-day-check">
                    {selectable && (
                      <input type="checkbox" checked={selected}
                        aria-label="Select this post"
                        onChange={() => toggleDayPost(entry.queue_item_id!)} />
                    )}
                  </span>
                  {/* The row opens the post: the editor for a planned one, its
                      permalink for a delivered one. */}
                  <button type="button" className="campaign-day-main"
                    onClick={() => selectTimelineEntry(entry)}>
                    <time>{new Date(entry.at).toLocaleTimeString(undefined, {
                      hour: "numeric", minute: "2-digit", timeZone: readerZone,
                    })}</time>
                    {/* Which clip, not only which filename. These titles are
                        the original filename - a date, a run of hashtags and
                        an id - so on a day of five posts the rows differ in
                        their least readable part. A frame answers "which one"
                        before the text is read at all.

                        The platform mark rides the corner rather than sitting
                        beside it, the way the grid card does, so the two of
                        them cost one column instead of two. */}
                    <span className="campaign-day-thumb">
                      {entry.asset_id ? (
                        <AssetThumbnail
                          asset={{
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
                          }}
                          workspaceId={workspaceId}
                          apiFetch={apiFetch}
                        />
                      ) : (
                        <span className="campaign-day-thumb-empty"><ActionIcon name="play" /></span>
                      )}
                      {platform && (
                        <span className="campaign-day-thumb-mark">
                          <PlatformIcon platform={platform} size={12} />
                        </span>
                      )}
                    </span>
                    <span className="campaign-day-body">
                      <strong>{displayTitle(entry.title) || entry.caption.slice(0, 80) || "Untitled post"}</strong>
                      <small>{destination?.label ?? entry.destination_id ?? "Social account"}</small>
                    </span>
                  </button>
                  {entry.kind === "delivered" ? (
                    <Badge tone={deliveredStatus(entry).tone}>
                      {deliveredStatus(entry).label}
                    </Badge>
                  ) : (
                    <Badge tone={entry.problem ? "warn" : "neutral"}
                      title={plannedMeaning(autopilot.delivery)}>Planned</Badge>
                  )}
                </li>
              );
            })}
          </ul>
          </div>
        </Dialog>
      )}
      </>}
      {/* Configuration, so it lives in Setup: the times themselves are
          visible in the timeline where they matter. */}
    </div>
  );
}
