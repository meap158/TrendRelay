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
import { ActionIcon } from "../ui/action-icons";
import { Dialog } from "../ui/dialog";
import { Badge, Card, Switch } from "../ui/primitives";
import { SearchSelect } from "../ui/search-select";
import { useT } from "../i18n-provider";
import { LOCALES } from "../../lib/i18n/locales";
import { EffectEditor } from "../library/effect-editor";
import { TimelinePlayer } from "./timeline-player";
import {
  AssetFilters,
  EMPTY_FACETS,
  assetFilterParams,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import {
  AssetThumbnail,
  type Slot,
} from "../publish/composer";
import {
  PlatformIcon,
  platformLabels,
  type PublishingPlatform,
} from "../publishing-icons";

type Account = {
  id: string;
  platform: PublishingPlatform;
  label: string;
  provider: string;
  provider_label: string;
  available?: boolean;
  unavailable_reason?: string | null;
};

type Destination = {
  id: string;
  provider: string;
  integration_id: string;
  platform: PublishingPlatform;
  label: string;
  enabled: boolean;
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
};

type HeldExecution = {
  id: string;
  scheduled_at: string | null;
  destination_label: string | null;
  platform: string | null;
  caption: string;
  title: string | null;
  held_reason: string | null;
  reason: string;
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
  product_details: { offer_id: string; name: string }[];
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
  product_details: { offer_id: string; name: string }[];
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
  strategy: MatchStrategy;
};

function offerDescription(offer: Offer): string {
  const commission = offer.commission_bps
    ? `${(offer.commission_bps / 100).toLocaleString()}% commission`
    : offer.commission_flat_cents
      ? `${offer.currency ?? ""} ${(offer.commission_flat_cents / 100).toLocaleString()} commission`.trim()
      : null;
  return [offer.product.marketplace, offer.network, commission].filter(Boolean).join(" · ");
}

type LibraryAsset = {
  id: string;
  title: string;
  original_path: string;
  media_kind: string;
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

/** A media title without its file extension: ".mp4" tells a reader nothing
    the thumbnail beside it does not, and reads like a path rather than a
    post. Only the display is trimmed; the stored title keeps its name. */
function displayTitle(value: string | null): string | null {
  return value ? value.replace(/\.(mp4|mov|webm|mkv|avi|jpg|jpeg|png|webp)$/i, "") : value;
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
    return {
      label: "First comment",
      detail: "The post publishes first, then the tracked product link is added as its first comment.",
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
  const draftingPackages = draftingSplit.videos.length + (draftingSplit.images.length ? 1 : 0);
  const [selectedAssets, setSelectedAssets] = useState<Record<string, LibraryAsset>>({});
  const [effectOpen, setEffectOpen] = useState(false);
  const [editing, setEditing] = useState<QueueItem | null>(null);
  const [editingReplies, setEditingReplies] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  // `revenue` is the merged Destinations + Monetization area. The old
  // `accounts` and `settings` names survive in the readiness rows, which is why
  // `jumpTo` translates them rather than every caller being rewritten.
  const [section, setSection] = useState<"media" | "revenue" | "schedule">(
    campaignStatus === "active" ? "schedule" : "media",
  );
  const searchTimer = useRef<number | null>(null);
  const automaticPreview = useRef(false);

  const base = `/api/workspaces/${workspaceId}/campaigns/${campaignId}`;

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

  // The inbox belongs to the Timeline: held posts are timeline entries that
  // have not earned their place yet, so they load when the timeline shows.
  // Deferred out of the effect body, the same way the initial refresh is.
  useEffect(() => {
    if (section !== "schedule") return;
    queueMicrotask(() => {
      void loadExceptions();
    });
  }, [section, loadExceptions]);

  async function decideException(executionId: string, action: "approve" | "dismiss") {
    setBusy(`${action}-${executionId}`);
    try {
      const response = await apiFetch(
        `${base}/autopilot/executions/${executionId}/${action}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(
            action === "approve" ? { confirm_external_action: true } : {},
          ),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "The decision was refused.");
      succeed(action === "approve"
        ? "Approved. The post is queued exactly as it was frozen."
        : "Dismissed. Its slot and its clip are free again.");
      await loadExceptions();
    } catch (reason) {
      fail(explainFailure(reason, "The decision was refused."));
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
        section: null as typeof section | null,
      },
      {
        id: "destinations",
        met: destinations.length > 0,
        label: t("autopilot.needDestinations"),
        section: "accounts" as const,
      },
      {
        id: "queue",
        met: (autopilot?.queue_approved ?? 0) > 0,
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
  }, [campaignStatus, destinations.length, autopilot?.queue_approved, slots.length, t]);

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
    const area = target === "accounts" || target === "settings" ? "revenue" : target;
    if (area !== "media" && area !== "revenue" && area !== "schedule") return;
    setSection(area);
    if (area === "revenue") {
      if (!accounts.length) void loadAccounts();
      if (!recommendations || recommendations.item_id) void loadRecommendations();
    }
    if (area === "schedule" && ready.configured && !preview) void loadPreview(false);
  }

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
      const key = new Date(entry.at).toLocaleDateString("en-CA", { timeZone: scheduleTimezone });
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
      <Card
        eyebrow={t("autopilot.eyebrow")}
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
        <p className="autopilot-lede">{t("autopilot.lede")}</p>

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
              const packages = autopilot.queue_approved;
              return (
                <>
                  <strong>{packages} {packages === 1 ? "package" : "packages"}</strong>
                  {packages === 1 ? " goes to " : " go to "}
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

        {/* Readiness rows still name the areas they came from. Translating
            here keeps that vocabulary working without every row knowing the
            tabs were merged. */}
        <nav className="campaign-work-tabs" aria-label="Campaign workspace">
          <button type="button" className={section === "media" ? "active" : ""}
            onClick={() => jumpTo("media")}>
            <span>Content</span><strong>{autopilot.queue_total}</strong><small>post packages</small>
          </button>
          <button type="button" className={section === "revenue" ? "active" : ""}
            onClick={() => jumpTo("revenue")}>
            <span>Distribution &amp; revenue</span>
            <strong>{destinations.length}</strong>
            <small>{autopilot.offer_mode === "smart"
              ? "accounts · smart offers"
              : autopilot.offer_mode === "manual"
                ? (autopilot.offer_id ? "accounts · 1 offer" : "accounts · no offer")
                : "accounts · offers off"}</small>
          </button>
          {/* Last, because it is what the two choices above produce rather than
              a third choice of its own. */}
          <button type="button" className={section === "schedule" ? "active" : ""}
            onClick={() => jumpTo("schedule")}>
            {/* Committed jobs still waiting to go out are upcoming posts too;
                only what has already delivered or failed leaves the count. */}
            <span>Timeline</span><strong>{preview
              ? preview.posts.length + preview.deployed.filter((item) =>
                  item.status === "queued" || item.status === "running").length
              : slots.length}</strong>
            <small>{preview ? "upcoming posts" : "posting times"}</small>
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

        {section === "revenue" && <div className="autopilot-settings">
          <div className="campaign-product-mode">
            <div>
              <strong>Affiliate product matching</strong>
              <small>Choose how products are assigned to each post. Smart matching is the recommended default.</small>
            </div>
            <div className="campaign-mode-options" role="radiogroup" aria-label="Affiliate product matching">
              {(["smart", "manual", "none"] as const).map((mode) => (
                <button key={mode} type="button" role="radio"
                  aria-checked={autopilot.offer_mode === mode}
                  className={autopilot.offer_mode === mode ? "active" : ""}
                  disabled={!canEdit}
                  onClick={() => void save({
                    offer_mode: mode,
                    offer_id: mode === "manual" ? autopilot.offer_id : null,
                  })}>
                  <strong>{mode === "smart" ? "Smart match" : mode === "manual" ? "One product" : "No products"}</strong>
                  <small>{mode === "smart" ? "Fit content automatically" : mode === "manual" ? "Use one offer everywhere" : "Organic posts only"}</small>
                </button>
              ))}
            </div>
          </div>

          {autopilot.offer_mode === "manual" && <label>{t("autopilot.offer")}
            <SearchSelect
              value={autopilot.offer_id ?? ""}
              disabled={!canEdit}
              onChange={(value) => void save({ offer_id: value || null })}
              placeholder={t("autopilot.noOffer")}
              searchPlaceholder="Search imported offers…"
              options={offers.map((offer) => ({
                value: offer.id,
                label: offer.product.name,
                description: offerDescription(offer),
                keywords: `${offer.product.brand ?? ""} ${offer.product.marketplace ?? ""} ${offer.network} ${offer.affiliate_url}`,
              }))}
            />
            <small>{t("autopilot.offerHelp")} Source: imported offers in Attribution.</small>
          </label>}

          {autopilot.offer_mode === "smart" && (
            <div className="campaign-product-intelligence">
              <div className="campaign-product-heading">
                <div>
                  <strong>Best-fit products</strong>
                  <small>Ranked from campaign goals, approved copy, hashtags, media metadata, creative analysis, and transcripts.</small>
                </div>
                <Button variant="secondary" size="sm" busy={busy === "recommendations"}
                  onClick={() => void loadRecommendations()}>Analyze campaign</Button>
              </div>
              {autopilot.candidate_offer_ids.length > 0 && (
                <div className="campaign-shortlist-note">
                  Matching is limited to {autopilot.candidate_offer_ids.length} shortlisted product{autopilot.candidate_offer_ids.length === 1 ? "" : "s"}.
                  <Button variant="quiet" size="sm" onClick={() => void save({ candidate_offer_ids: [] })}>Use all offers</Button>
                </div>
              )}
              {recommendations && !recommendations.item_id && (
                <>
                  <div className="campaign-strategy-summary">
                    <span><strong>{recommendations.strategy.posting_slots}</strong> posting times</span>
                    <span><strong>{recommendations.strategy.platforms.length}</strong> platforms</span>
                    <span><strong>{recommendations.strategy.recommended_products_per_post}</strong> auto products/post</span>
                    <span><strong>{recommendations.strategy.evidence_sources.length}</strong> evidence sources</span>
                  </div>
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
                        <Badge tone={match.confidence === "high" ? "good" : match.confidence === "medium" ? "warn" : "neutral"}>
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

          <label>{t("autopilot.disclosure")}
            <input
              defaultValue={autopilot.disclosure}
              disabled={!canEdit}
              maxLength={500}
              onBlur={(event) => {
                if (event.target.value !== autopilot.disclosure) {
                  void save({ disclosure: event.target.value });
                }
              }}
            />
            {/* Not a preference. Stated here so nobody spends time looking for
                the setting that turns it off. */}
            <small>{t("autopilot.disclosureHelp")}</small>
          </label>

          <label>Profile-link wording
            <input
              defaultValue={autopilot.bio_hint}
              disabled={!canEdit}
              maxLength={120}
              onBlur={(event) => {
                if (event.target.value !== autopilot.bio_hint) {
                  void save({ bio_hint: event.target.value });
                }
              }}
            />
            <small>
              Used for Instagram, TikTok, and other destinations where post links are not clickable.
              TrendRelay does not change the account profile automatically, so verify its bio link before deployment.
            </small>
          </label>

          <div className="autopilot-numbers">
            {autopilot.offer_mode === "smart" && <label>Products per post
              <input type="number" min={1} max={5}
                defaultValue={autopilot.max_products_per_post}
                disabled={!canEdit}
                onBlur={(event) => void save({ max_products_per_post: Number(event.target.value) })} />
              <small>Bio-only networks still use one and rotate products across posts.</small>
            </label>}
            <label>{t("autopilot.rest")}
              <input
                type="number"
                min={1}
                max={365}
                defaultValue={autopilot.min_recycle_days}
                disabled={!canEdit}
                onBlur={(event) => void save({ min_recycle_days: Number(event.target.value) })}
              />
              <small>{t("autopilot.restHelp")}</small>
            </label>
            <label>{t("autopilot.cap")}
              <input
                type="number"
                min={1}
                max={24}
                defaultValue={autopilot.daily_cap_per_account}
                disabled={!canEdit}
                onBlur={(event) =>
                  void save({ daily_cap_per_account: Number(event.target.value) })}
              />
              <small>{t("autopilot.capHelp")}</small>
            </label>
            <label>Authority
              <select
                value={autopilot.authority}
                disabled={!canEdit}
                onChange={(event) =>
                  void save({ authority: event.target.value as Autopilot["authority"] })}
              >
                <option value="assist">Assist — approve every post</option>
                <option value="auto_draft">Auto-draft — approve, then engine drafts only</option>
                <option value="run_by_exception">Run by exception (recommended)</option>
                <option value="autonomous">Autonomous — earned after 10 confirmed posts</option>
              </select>
              <small>Every post below Autonomous waits on the Timeline for your
                approval, and only a finished post — real copy, its affiliate
                link, media its network accepts — can be approved.</small>
            </label>
            <label>Optimise for
              <select
                value={autopilot.priority}
                disabled={!canEdit}
                onChange={(event) =>
                  void save({ priority: event.target.value as Autopilot["priority"] })}
              >
                <option value="balanced">Balanced — blend measured axes</option>
                <option value="revenue">Revenue — earnings per click</option>
                <option value="reach">Reach — views per post</option>
                <option value="discussion">Discussion — comments per post</option>
              </select>
              <small>Ranking only uses an axis once it has enough evidence;
                until then destinations rotate.</small>
            </label>
            <label>Post language
              <select
                value={autopilot.post_language}
                disabled={!canEdit}
                onChange={(event) =>
                  void save({ post_language: event.target.value })}
              >
                {/* The languages TrendRelay speaks, from the list that defines
                    them. Spelled out here, this offered two while the campaign
                    form offered a different set. */}
                {LOCALES.map((item) => (
                  <option key={item.code} value={item.code}>{item.label}</option>
                ))}
              </select>
              <small>The language of composed scaffolding — the disclosure
                default, the bio hint, product labels. Your own copy is always
                your own.</small>
            </label>
            <label>Weekly post cap
              <input
                type="number"
                min={1}
                max={200}
                placeholder="No cap"
                defaultValue={autopilot.weekly_post_cap ?? ""}
                disabled={!canEdit}
                onBlur={(event) => void save({
                  weekly_post_cap: event.target.value
                    ? Number(event.target.value)
                    : null,
                })}
              />
              <small>Across every destination, over a rolling week. Empty
                leaves the per-account caps as the only limit.</small>
            </label>
          </div>
        </div>}
      </Card>

      {section === "revenue" && <Card
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
                    <small>{platformLabels[item.platform]} · {item.provider}</small>
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
                        <small>{platformLabels[account.platform]} · {account.provider_label}</small>
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

      {section === "media" && <Card
        eyebrow={t("autopilot.queueEyebrow")}
        title={t("autopilot.queue", {
          approved: autopilot.queue_approved, total: autopilot.queue_total,
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
                onClick={() => setDrafting(selectedLibrary)}>Write campaign copy</Button>
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
              const form = new FormData(event.currentTarget);
              // Copy is optional now. A package can be picked today and
              // written later; the API stands a placeholder in and marks it,
              // which is what the badge on the queue reads from.
              const body = String(form.get("body") ?? "").trim();
              void run("queue", async () => {
                const hashtags = String(form.get("hashtags") ?? "")
                  .split(/[\s,]+/).filter(Boolean);
                const post = async (payload: Record<string, unknown>) =>
                  json(await apiFetch(`${base}/queue`, {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ ...payload, body, hashtags }),
                  }));
                // One package per clip, and one package for all the pictures.
                await Promise.all([
                  ...draftingSplit.videos.map((asset) => post({
                    asset_id: asset.id,
                    video_path: handoffPath(asset),
                    title: asset.title,
                  })),
                  ...(draftingSplit.images.length ? [post({
                    asset_id: draftingSplit.images[0].id,
                    image_paths: draftingSplit.images.map(handoffPath),
                    title: draftingSplit.images[0].title,
                  })] : []),
                ]);
                const count = draftingPackages;
                setDrafting([]);
                setSelectedAssets({});
                setPicking(false);
                return `${count} ${count === 1 ? "package" : "packages"} added to the campaign queue.`;
              });
            }}
          >
            <div className="autopilot-picker-head">
              <strong>{drafting.length === 1
                ? drafting[0].title
                : draftingSplit.images.length && !draftingSplit.videos.length
                  ? `Carousel of ${draftingSplit.images.length} pictures`
                  : `${draftingPackages} ${draftingPackages === 1 ? "package" : "packages"}`}</strong>
              <Button variant="quiet" size="sm" onClick={() => setDrafting([])}>
                {t("autopilot.chooseAnother")}
              </Button>
            </div>
            <label>{t("autopilot.copy")}
              {/* Not required. Media is often chosen before anybody has
                  written for it, and forcing both into one sitting is what
                  made people paste something they did not mean. */}
              <textarea name="body" rows={4} maxLength={4000}
                placeholder={t("autopilot.copyPlaceholder")} />
              {/* The disclosure and the link are added per network at post
                  time, so writing either here would duplicate them. */}
              <small>{t("autopilot.copyHelp")}</small>
            </label>
            <label>{t("autopilot.hashtags")}
              <input name="hashtags" placeholder={t("autopilot.hashtagsExample")} />
            </label>
            <Button type="submit" variant="primary" busy={busy === "queue"}>
              {t("autopilot.addToQueue")}
            </Button>
          </form>
        )}
        {queue.length === 0 ? (
          <p className="autopilot-empty">{t("autopilot.noQueue")}</p>
        ) : (
          <ul className="autopilot-queue">
            {queue.map((item) => (
              <li key={item.id} className={item.state}>
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
                      No copy yet — this placeholder posts unless somebody writes it.
                    </span>
                  ) : (
                    <span className="autopilot-queue-copy">{item.body}</span>
                  )}
                  <span className="campaign-content-package" aria-label="Configured post package">
                    <em>Post</em>
                    {item.first_comment && <em>First comment</em>}
                    {item.thread.length > 0 && (
                      <em>{item.thread.length} {item.thread.length === 1 ? "reply" : "replies"}</em>
                    )}
                  </span>
                  <span className="campaign-queue-products">
                    {(item.offer_match?.matches ?? [])
                      .filter((match) => item.offer_ids.length
                        ? item.offer_ids.includes(match.offer_id)
                        : match.confidence !== "low")
                      .slice(0, autopilot.max_products_per_post)
                      .map((match) => (
                        <em key={match.offer_id}>{match.product_name} · {match.score}%</em>
                      ))}
                    {!(item.offer_match?.matches ?? []).length && <em>Product analysis pending</em>}
                  </span>
                  <small>
                    {item.times_posted > 0
                      ? t("autopilot.postedTimes", { count: item.times_posted })
                      : t("autopilot.neverPosted")}
                  </small>
                </div>
                <Badge tone={item.state === "approved" ? "good" : "neutral"}>
                  {t(`autopilot.state.${item.state}`)}
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
          <div className="campaign-item-products">
            <div className="campaign-product-heading">
              <div>
                <strong>Products for {productItem.title ?? "this queued post"}</strong>
                <small>Leave every box clear for smart matching, or pin specific products to this post.</small>
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
                      <strong>{match.product_name}</strong>
                      <small>{match.reasons.join(" ")}</small>
                      <span>{match.matched_terms.slice(0, 6).map((term) => <em key={term}>{term}</em>)}</span>
                    </span>
                    <Badge tone={match.confidence === "high" ? "good" : match.confidence === "medium" ? "warn" : "neutral"}>
                      {match.confidence}
                    </Badge>
                  </label>
                </li>
              ))}
            </ul>
            <div className="campaign-product-actions">
              <small>{pinnedOffers.size
                ? `${pinnedOffers.size} pinned product${pinnedOffers.size === 1 ? "" : "s"}; these override smart matching for this post.`
                : "Smart matching will choose the strongest evidence-backed products when each post is planned."}</small>
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
          <form className="autopilot-compose" onSubmit={(event) => {
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
                <strong>Edit post package</strong>
                <small>Configure the primary post and optional follow-up content. Affiliate links remain routed safely per destination.</small>
              </span>
              <Button variant="quiet" size="sm" onClick={() => setEditing(null)}>Cancel</Button>
            </div>
            <label>Title
              <input name="title" defaultValue={editing.title ?? ""} maxLength={200} />
              <small>Used by destinations that require a title and in the campaign timeline.</small>
            </label>
            <label>{t("autopilot.copy")}
              <textarea name="body" rows={4} required maxLength={4000}
                defaultValue={editing.body} />
            </label>
            <label>{t("autopilot.hashtags")}
              <input name="hashtags" defaultValue={editing.hashtags.join(" ")} />
            </label>
            <label>First comment
              <textarea name="first_comment" rows={3} maxLength={2000}
                defaultValue={editing.first_comment ?? ""}
                placeholder="Optional comment published immediately after the post" />
              <small>The timeline will warn when a selected publishing engine cannot post it.</small>
            </label>
            <fieldset className="campaign-reply-editor">
              <legend>Replies / thread</legend>
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
            <Button type="submit" variant="primary" busy={busy === "edit-copy"}>Save post package</Button>
          </form>
        )}
      </Card>}

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

      {section === "schedule" && <>
      {/* "Posting timeline", not "Upcoming posts": the committed section below
          keeps recently delivered jobs on screen, and a delivered job under an
          "upcoming" heading reads like a contradiction. */}
      <Card
        eyebrow="Active campaign pipeline"
        title="Posting timeline"
        aside={
          <Button variant="secondary" size="sm" busy={busy === "preview"}
            disabled={!ready.configured}
            onClick={() => void loadPreview()}><ActionIcon name="refresh" />Refresh outlook</Button>
        }
      >
        <p className="autopilot-lede">
          A rolling seven-day outlook calculated by the same scheduler that deploys the campaign.
          Preview items are calculated; committed items are durable publishing jobs that persist across sessions.
        </p>
        {/* Grouped at the top, per the run-by-exception contract: everything
            the autopilot deferred to a person, with the reason on it. */}
        {exceptions.length > 0 && (
          <section className="campaign-committed-pipeline" aria-label="Posts waiting for approval">
            <header>
              <div>
                <strong>Waiting for approval</strong>
                <small>Nothing reaches an engine before it is approved here,
                  exactly as frozen</small>
              </div>
              <Badge tone="warn">{exceptions.length} held</Badge>
            </header>
            <ul className="campaign-exception-list">
              {exceptions.map((item) => (
                <li key={item.id}>
                  <div>
                    <strong>{item.title || item.caption.slice(0, 80)}</strong>
                    <small>
                      {item.destination_label ?? item.platform ?? "destination"}
                      {item.scheduled_at
                        ? ` · ${new Date(item.scheduled_at).toLocaleString()}`
                        : ""}
                    </small>
                    <small>{item.held_reason}</small>
                  </div>
                  {canEdit && (
                    <span className="campaign-exception-actions">
                      <Button variant="primary" size="sm"
                        busy={busy === `approve-${item.id}`}
                        onClick={() => void decideException(item.id, "approve")}
                      >Approve</Button>
                      <Button variant="quiet" size="sm"
                        busy={busy === `dismiss-${item.id}`}
                        onClick={() => void decideException(item.id, "dismiss")}
                      >Dismiss</Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
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
            <span><strong>{deliveredCount}</strong><small>delivered</small></span>
            <span><strong>{plannedCount}</strong><small>planned</small></span>
            <span><strong>{timelineDays.length}</strong><small>active days</small></span>
            <span><strong>{timelineAccounts}</strong><small>accounts</small></span>
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
        {timeline.length > 0 && (
          <div className="campaign-pipeline">
            {timelineDays.map(([day, entries]) => (
              <section className="campaign-pipeline-day" key={day}>
                <header>
                  <strong>{dayHeading(entries[0].at, scheduleTimezone)}</strong>
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
                            hour: "numeric", minute: "2-digit", timeZone: scheduleTimezone,
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
                                  {destination?.label ?? entry.destination_id ?? "Former destination"}
                                </a>
                              ) : (destination?.label ?? entry.destination_id ?? "Former destination")}</strong>
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
                                  <small>{entry.placement === "bio"
                                    ? "Profile bio"
                                    : productIndex > 0 && entry.thread.length ? `Reply ${productIndex}` : "Post content"}</small>
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
      <Card eyebrow="Workspace schedule" title="Posting times" aside={
        <Link className="ui-button ui-button-secondary ui-button-sm" href="/publish">
          Edit in Publish
        </Link>
      }>
        <p className="autopilot-lede">
          These times are shared by every campaign in this workspace. Campaigns reads them for its outlook; Publish is their single source of truth.
        </p>
        <div className="campaign-schedule-readonly">
          <Badge tone="neutral">{scheduleTimezone}</Badge>
          {slots.map((slot) => (
            <span key={slot.id}>{slot.weekday_label} · {slot.time}</span>
          ))}
          {!slots.length && <span>No posting times configured.</span>}
        </div>
      </Card>
      </>}
    </div>
  );
}
