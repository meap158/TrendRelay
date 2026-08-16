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

import { Button } from "../ui/button";
import { ActionIcon } from "../ui/action-icons";
import { Badge, Card, Switch } from "../ui/primitives";
import { SearchSelect } from "../ui/search-select";
import { useT } from "../i18n-provider";
import { EffectEditor } from "../library/effect-editor";
import {
  AssetFilters,
  EMPTY_FACETS,
  assetFilterParams,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import {
  AssetThumbnail,
  SlotEditor,
  type Slot,
  type SlotPreset,
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
  tracking_code: string | null;
  link_placement: "caption" | "first_comment" | "bio" | "none";
  link_reason: string;
};

type QueueItem = {
  id: string;
  video_path: string;
  title: string | null;
  body: string;
  hashtags: string[];
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
  offer_id: string | null;
  offer_mode: "smart" | "manual" | "none";
  candidate_offer_ids: string[];
  max_products_per_post: number;
  disclosure: string;
  bio_hint: string;
  min_recycle_days: number;
  daily_cap_per_account: number;
  posts_scheduled: number;
  last_run_at: string | null;
  last_note: string | null;
  destinations: number;
  queue_total: number;
  queue_approved: number;
};

type PreviewPost = {
  destination_id: string;
  queue_item_id: string;
  at: string;
  caption: string;
  first_comment: string | null;
  thread: string[];
  offer_ids: string[];
  products: string[];
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

export function AutopilotPanel({
  workspaceId,
  campaignId,
  campaignStatus,
  canEdit,
  apiFetch,
  succeed,
  fail,
}: {
  workspaceId: string;
  campaignId: string;
  campaignStatus: string;
  canEdit: boolean;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  succeed: (message: string) => void;
  fail: (message: string) => void;
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
  const [slotPresets, setSlotPresets] = useState<SlotPreset[]>([]);
  const [preview, setPreview] = useState<
    { note: string; posts: PreviewPost[]; problems: number } | null
  >(null);
  const [busy, setBusy] = useState("");
  const [adding, setAdding] = useState(false);
  const [library, setLibrary] = useState<LibraryAsset[]>([]);
  const [libraryFacets, setLibraryFacets] = useState<AssetFacets>(EMPTY_FACETS);
  const [libraryFilters, setLibraryFilters] = useState<AssetFilterValues>({ mediaKind: "video" });
  const [libraryTotal, setLibraryTotal] = useState(0);
  /** The clips sharing this campaign copy. Empty when the composer is closed. */
  const [drafting, setDrafting] = useState<LibraryAsset[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<Record<string, LibraryAsset>>({});
  const [effectOpen, setEffectOpen] = useState(false);
  const [editing, setEditing] = useState<QueueItem | null>(null);
  const [picking, setPicking] = useState(false);
  const [section, setSection] = useState<"media" | "accounts" | "schedule" | "settings">("media");
  const searchTimer = useRef<number | null>(null);

  const base = `/api/workspaces/${workspaceId}/campaigns/${campaignId}`;

  const refresh = useCallback(async () => {
    const body = await json<{
      autopilot: Autopilot; destinations: Destination[]; queue: QueueItem[];
    }>(await apiFetch(`${base}/autopilot`));
    setAutopilot(body.autopilot);
    setDestinations(body.destinations);
    setQueue(body.queue);
  }, [apiFetch, base]);

  useEffect(() => {
    queueMicrotask(() => {
      void refresh().catch((reason) =>
        fail(reason instanceof Error ? reason.message : "Autopilot unavailable."));
      // Everything the readiness check needs, loaded once. Each of these is a
      // different subsystem, and the point of the checklist is that it names
      // which one is missing rather than reporting a single blank "not ready".
      void apiFetch(`/api/workspaces/${workspaceId}/publishing/slots`)
        .then((response) => json<{ slots: Slot[]; presets: SlotPreset[] }>(response))
        .then((body) => { setSlots(body.slots); setSlotPresets(body.presets); })
        .catch(() => { setSlots([]); setSlotPresets([]); });
      void apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`)
        .then((response) => json<{ offers: Offer[] }>(response))
        .then((body) => setOffers(body.offers))
        .catch(() => setOffers([]));
    });
  }, [refresh, apiFetch, workspaceId, fail]);

  useEffect(() => () => {
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
  }, []);

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
      fail(reason instanceof Error ? reason.message : "Could not load accounts.");
    } finally {
      setBusy("");
    }
  }

  async function loadLibrary(filters: AssetFilterValues = libraryFilters) {
    setBusy("library");
    try {
      // Video only, and only what the library considers ready. The queue posts
      // unattended, so an asset still being processed has no business in it.
      const params = assetFilterParams({ ...filters, mediaKind: "video" });
      params.set("limit", "100");
      const body = await json<{
        assets: LibraryAsset[]; facets?: AssetFacets; total?: number;
      }>(await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets?${params.toString()}`,
      ));
      setLibrary(body.assets ?? []);
      if (body.facets) setLibraryFacets(body.facets);
      setLibraryTotal(body.total ?? body.assets?.length ?? 0);
      setDrafting([]);
      setPicking(true);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The library could not be read.");
    } finally {
      setBusy("");
    }
  }

  function filterLibrary(next: AssetFilterValues) {
    const normalized = { ...next, mediaKind: "video" as const };
    setLibraryFilters(normalized);
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => void loadLibrary(normalized), 220);
  }

  const run = useCallback(async (label: string, work: () => Promise<string>) => {
    setBusy(label);
    try {
      succeed(await work());
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "That did not work.");
    } finally {
      setBusy("");
    }
  }, [refresh, succeed, fail]);

  async function saveSlots(entries: { weekday: number; time: string }[]) {
    await run("slots", async () => {
      const body = await json<{ slots: Slot[]; presets: SlotPreset[] }>(await apiFetch(
        `/api/workspaces/${workspaceId}/publishing/slots`, {
          method: "POST",
          body: JSON.stringify({ slots: entries }),
        },
      ));
      setSlots(body.slots);
      setSlotPresets(body.presets);
      return "Posting times updated for this campaign workspace.";
    });
  }

  async function save(changes: Partial<Autopilot>, { confirm = false } = {}) {
    if (!autopilot) return;
    const next = { ...autopilot, ...changes };
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
          delivery: next.delivery,
          confirm_external_action: confirm,
        }),
      }));
      return next.enabled && !autopilot.enabled
        ? t("autopilot.switchedOn")
        : t("autopilot.saved");
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
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Products could not be analyzed.");
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
        href: null as string | null,
      },
      {
        id: "destinations",
        met: destinations.length > 0,
        label: t("autopilot.needDestinations"),
        href: null,
      },
      {
        id: "queue",
        met: (autopilot?.queue_approved ?? 0) > 0,
        label: t("autopilot.needApproved"),
        href: null,
      },
      {
        id: "slots",
        met: slots.length > 0,
        label: t("autopilot.needSlots"),
        href: null,
      },
    ];
    return { rows, all: rows.every((row) => row.met) };
  }, [campaignStatus, destinations.length, autopilot?.queue_approved, slots.length, t]);

  if (!autopilot) return null;

  const unmet = ready.rows.filter((row) => !row.met);
  const selectedLibrary = Object.values(selectedAssets);

  return (
    <div className="autopilot">
      <Card
        eyebrow={t("autopilot.eyebrow")}
        title={t("autopilot.heading")}
        aside={
          <Switch
            checked={autopilot.enabled}
            disabled={!canEdit || (!ready.all && !autopilot.enabled)}
            label={t("autopilot.switch")}
            onChange={(next) => {
              if (next && !window.confirm(t("autopilot.confirmOn"))) return;
              void save({ enabled: next }, { confirm: true });
            }}
          />
        }
      >
        <p className="autopilot-lede">{t("autopilot.lede")}</p>

        <nav className="campaign-work-tabs" aria-label="Campaign workspace">
          <button type="button" className={section === "media" ? "active" : ""}
            onClick={() => setSection("media")}>
            <span>Media</span><strong>{autopilot.queue_total}</strong><small>clips queued</small>
          </button>
          <button type="button" className={section === "accounts" ? "active" : ""}
            onClick={() => {
              setSection("accounts");
              if (!accounts.length) void loadAccounts();
            }}>
            <span>Accounts</span><strong>{destinations.length}</strong><small>destinations</small>
          </button>
          <button type="button" className={section === "schedule" ? "active" : ""}
            onClick={() => setSection("schedule")}>
            <span>Schedule</span><strong>{slots.length}</strong><small>posting times</small>
          </button>
          <button type="button" className={section === "settings" ? "active" : ""}
            onClick={() => {
              setSection("settings");
              if (!recommendations || recommendations.item_id) void loadRecommendations();
            }}>
            <span>Products</span>
            <strong>{autopilot.offer_mode === "smart" ? "Smart" : autopilot.offer_mode === "manual" ? (autopilot.offer_id ? "1" : "—") : "Off"}</strong>
            <small>affiliate matching</small>
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
                {!row.met && row.href && <Link href={row.href}>{t("autopilot.fixIt")}</Link>}
              </li>
            ))}
          </ul>
        )}

        {autopilot.last_note && (
          <p className="autopilot-note" role="status">
            <strong>{t("autopilot.lastRun")}</strong> {autopilot.last_note}
          </p>
        )}

        {section === "settings" && <div className="autopilot-settings">
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
            <label>{t("autopilot.delivery")}
              <select
                value={autopilot.delivery}
                disabled={!canEdit}
                onChange={(event) =>
                  void save({ delivery: event.target.value as Autopilot["delivery"] })}
              >
                <option value="draft">{t("autopilot.deliveryDraft")}</option>
                <option value="schedule">{t("autopilot.deliverySchedule")}</option>
              </select>
              <small>{t("autopilot.deliveryHelp")}</small>
            </label>
          </div>
        </div>}
      </Card>

      {section === "accounts" && <Card
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
                  <Button variant="quiet" size="sm" onClick={() => void run("remove", async () => {
                    await json(await apiFetch(`${base}/destinations/${item.id}`,
                      { method: "DELETE" }));
                    return t("autopilot.destinationRemoved", { label: item.label });
                  })}>{t("common.delete")}</Button>
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

        {/* Pick the clip, then write the copy for it. Two steps rather than one
            form with a path field: the path is not something anyone should be
            typing, and the copy is the part that deserves the room. */}
        {picking && drafting.length === 0 && (
          <div className="campaign-media-browser">
            <div className="campaign-media-browser-head">
              <div>
                <strong>{selectedLibrary.length
                ? `${selectedLibrary.length} clips selected`
                : t("autopilot.chooseClip")}</strong>
                <small>{libraryTotal.toLocaleString()} matching videos · showing {library.length}</small>
              </div>
              <Button variant="quiet" size="sm" onClick={() => setPicking(false)}>
                {t("common.close")}
              </Button>
            </div>
            <AssetFilters
              values={libraryFilters}
              facets={libraryFacets}
              fields={["query", "effect", "channel", "platform", "length"]}
              cleared={{ mediaKind: "video" }}
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
        )}

        {drafting.length > 0 && (
          <form
            className="autopilot-compose"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const body = String(form.get("body") ?? "").trim();
              if (!body) return;
              void run("queue", async () => {
                const hashtags = String(form.get("hashtags") ?? "")
                  .split(/[\s,]+/).filter(Boolean);
                await Promise.all(drafting.map(async (asset) => json(await apiFetch(`${base}/queue`, {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify({
                    asset_id: asset.id,
                    video_path: handoffPath(asset),
                    title: asset.title,
                    body,
                    hashtags,
                  }),
                }))));
                const count = drafting.length;
                setDrafting([]);
                setSelectedAssets({});
                setPicking(false);
                return `${count} ${count === 1 ? "clip" : "clips"} added to the campaign queue.`;
              });
            }}
          >
            <div className="autopilot-picker-head">
              <strong>{drafting.length === 1 ? drafting[0].title : `${drafting.length} selected clips`}</strong>
              <Button variant="quiet" size="sm" onClick={() => setDrafting([])}>
                {t("autopilot.chooseAnother")}
              </Button>
            </div>
            <label>{t("autopilot.copy")}
              <textarea name="body" rows={4} required maxLength={4000}
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
                <div>
                  <strong>{item.title ?? item.body.slice(0, 60)}</strong>
                  <span className="autopilot-queue-copy">{item.body}</span>
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
                          return t("autopilot.itemApproved");
                        })}>{t("autopilot.approve")}</Button>
                    )}
                    <Button variant="quiet" size="sm" onClick={() => setEditing(item)}>Edit copy</Button>
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
                  body: String(form.get("body") ?? "").trim(),
                  hashtags: String(form.get("hashtags") ?? "")
                    .split(/[\s,]+/).filter(Boolean),
                }),
              }));
              setEditing(null);
              return "Campaign copy updated.";
            });
          }}>
            <div className="autopilot-picker-head">
              <strong>Edit copy for {editing.title ?? "queued clip"}</strong>
              <Button variant="quiet" size="sm" onClick={() => setEditing(null)}>Cancel</Button>
            </div>
            <label>{t("autopilot.copy")}
              <textarea name="body" rows={4} required maxLength={4000}
                defaultValue={editing.body} />
            </label>
            <label>{t("autopilot.hashtags")}
              <input name="hashtags" defaultValue={editing.hashtags.join(" ")} />
            </label>
            <Button type="submit" variant="primary" busy={busy === "edit-copy"}>Save copy</Button>
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
      <Card
        eyebrow="Campaign rhythm"
        title="Posting times"
      >
        <SlotEditor
          slots={slots}
          presets={slotPresets}
          timezone={Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"}
          canEdit={canEdit}
          busy={busy === "slots"}
          onSave={(entries) => void saveSlots(entries)}
        />
      </Card>
      <Card
        eyebrow={t("autopilot.nextEyebrow")}
        title={t("autopilot.next")}
        aside={
          <Button variant="secondary" size="sm" busy={busy === "preview"}
            onClick={() => void run("preview", async () => {
              const body = await json<{
                note: string; posts: PreviewPost[]; problems: number;
              }>(await apiFetch(`${base}/autopilot/preview`, { method: "POST" }));
              setPreview(body);
              return body.problems
                ? t("autopilot.previewProblems", { count: body.problems })
                : body.note;
            })}>{t("autopilot.showNext")}</Button>
        }
      >
        {/* The trust-builder. Captions, times and placement exactly as they
            would go out, created by nothing. */}
        <p className="autopilot-lede">{t("autopilot.nextHelp")}</p>
        {preview && (
          preview.posts.length === 0 ? (
            <p className="autopilot-note" role="status">{preview.note}</p>
          ) : (
            <ol className="autopilot-preview">
              {preview.posts.map((post, index) => {
                const destination = destinations.find(
                  (item) => item.id === post.destination_id);
                return (
                  <li
                    key={`${post.destination_id}-${index}`}
                    className={post.problem ? "refused" : undefined}
                  >
                    <div className="autopilot-preview-head">
                      <strong>{new Date(post.at).toLocaleString()}</strong>
                      <span>{destination?.label ?? post.destination_id}</span>
                      <Badge tone={placementTone(post.placement)}>
                        {t(`autopilot.placement.${post.placement}`)}
                      </Badge>
                    </div>
                    {/* Above the caption, not below it. The caption is what
                        this row is for reading; a refusal is what it is for
                        acting on, and a reason to act belongs before the thing
                        it acts on. */}
                    {post.problem && (
                      <p className="autopilot-refusal" role="status">
                        <strong>{t("autopilot.wouldBeRefused")}</strong> {post.problem}
                      </p>
                    )}
                    {post.products.length > 0 && (
                      <div className="campaign-preview-products">
                        <strong>Matched products</strong>
                        {post.products.map((name) => <Badge key={name} tone="good">{name}</Badge>)}
                      </div>
                    )}
                    <pre>{post.caption}</pre>
                    {post.first_comment && (
                      <pre className="autopilot-first-comment">{post.first_comment}</pre>
                    )}
                    {post.thread.map((reply, replyIndex) => (
                      <pre className="autopilot-thread-reply" key={`${replyIndex}-${reply}`}>
                        Reply {replyIndex + 1} · {reply}
                      </pre>
                    ))}
                    <small>{post.reason}</small>
                  </li>
                );
              })}
            </ol>
          )
        )}
        {!preview && unmet.length > 0 && (
          <p className="autopilot-empty">{t("autopilot.previewBlocked")}</p>
        )}
        <div className="campaign-deploy-bar">
          <div>
            <strong>{ready.all ? "Ready to deploy" : `${unmet.length} setup items remaining`}</strong>
            <small>{autopilot.delivery === "draft"
              ? "Creates reviewable drafts in the assigned social accounts."
              : "Schedules the next posts at the posting times above."}</small>
          </div>
          <Button variant="primary" disabled={!canEdit || !ready.all}
            busy={busy === "deploy"} onClick={() => {
              if (!window.confirm(
                `Deploy this campaign now using ${autopilot.delivery} delivery?`,
              )) return;
              void run("deploy", async () => {
                if (!autopilot.enabled) {
                  await json(await apiFetch(`${base}/autopilot`, {
                    method: "PUT",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({
                      enabled: true,
                      offer_id: autopilot.offer_id,
                      offer_mode: autopilot.offer_mode,
                      candidate_offer_ids: autopilot.candidate_offer_ids,
                      max_products_per_post: autopilot.max_products_per_post,
                      disclosure: autopilot.disclosure,
                      bio_hint: autopilot.bio_hint,
                      min_recycle_days: autopilot.min_recycle_days,
                      daily_cap_per_account: autopilot.daily_cap_per_account,
                      delivery: autopilot.delivery,
                      confirm_external_action: true,
                    }),
                  }));
                }
                const body = await json<{ note: string; posts: unknown[] }>(await apiFetch(
                  `${base}/autopilot/run`, { method: "POST" },
                ));
                return body.note || `${body.posts.length} posts deployed.`;
              });
            }}>Deploy campaign</Button>
        </div>
      </Card>
      </>}
    </div>
  );
}
