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

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "../ui/button";
import { Badge, Card, Switch } from "../ui/primitives";
import { useT } from "../i18n-provider";

type Account = {
  id: string;
  platform: string;
  label: string;
  provider: string;
  provider_label: string;
};

type Destination = {
  id: string;
  provider: string;
  integration_id: string;
  platform: string;
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
};

type Autopilot = {
  enabled: boolean;
  delivery: "draft" | "schedule" | "now";
  offer_id: string | null;
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
  placement: string;
  reason: string;
};

type Offer = {
  id: string;
  network: string;
  product: { name: string };
};

type LibraryAsset = {
  id: string;
  title: string;
  original_path: string;
  media_kind: string;
  duration_ms: number | null;
};

/** Seconds, rounded, for a clip length nobody needs to the millisecond. */
function clipLength(ms: number | null): string | null {
  if (!ms) return null;
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

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
  const [offers, setOffers] = useState<Offer[]>([]);
  const [slotCount, setSlotCount] = useState<number | null>(null);
  const [preview, setPreview] = useState<{ note: string; posts: PreviewPost[] } | null>(null);
  const [busy, setBusy] = useState("");
  const [adding, setAdding] = useState(false);
  const [library, setLibrary] = useState<LibraryAsset[]>([]);
  /** The clip being written up. Null when the composer is closed. */
  const [drafting, setDrafting] = useState<LibraryAsset | null>(null);
  const [picking, setPicking] = useState(false);

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
        .then((response) => json<{ slots: unknown[] }>(response))
        .then((body) => setSlotCount(body.slots.length))
        .catch(() => setSlotCount(0));
      void apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`)
        .then((response) => json<{ offers: Offer[] }>(response))
        .then((body) => setOffers(body.offers))
        .catch(() => setOffers([]));
    });
  }, [refresh, apiFetch, workspaceId, fail]);

  async function loadAccounts() {
    setBusy("accounts");
    try {
      const body = await json<{ accounts: Account[] }>(await apiFetch(
        `/api/workspaces/${workspaceId}/publishing/integrations/all`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      ));
      setAccounts(body.accounts);
      setAdding(true);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Could not load accounts.");
    } finally {
      setBusy("");
    }
  }

  async function loadLibrary() {
    setBusy("library");
    try {
      // Video only, and only what the library considers ready. The queue posts
      // unattended, so an asset still being processed has no business in it.
      const body = await json<{ assets: LibraryAsset[] }>(await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets?media_kind=video&limit=40`,
      ));
      setLibrary(body.assets ?? []);
      setDrafting(null);
      setPicking(true);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The library could not be read.");
    } finally {
      setBusy("");
    }
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
        met: (slotCount ?? 0) > 0,
        label: t("autopilot.needSlots"),
        href: "/publish",
      },
    ];
    return { rows, all: rows.every((row) => row.met) };
  }, [campaignStatus, destinations.length, autopilot?.queue_approved, slotCount, t]);

  if (!autopilot) return null;

  const unmet = ready.rows.filter((row) => !row.met);

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

        <div className="autopilot-settings">
          <label>{t("autopilot.offer")}
            <select
              value={autopilot.offer_id ?? ""}
              disabled={!canEdit}
              onChange={(event) => void save({ offer_id: event.target.value || null })}
            >
              <option value="">{t("autopilot.noOffer")}</option>
              {offers.map((offer) => (
                <option key={offer.id} value={offer.id}>
                  {offer.product.name} · {offer.network}
                </option>
              ))}
            </select>
            <small>{t("autopilot.offerHelp")}</small>
          </label>

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
        </div>
      </Card>

      <Card
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
                <div>
                  <strong>{item.label}</strong>
                  <small>{item.platform} · {item.provider}</small>
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
              <strong>{t("autopilot.chooseAccounts")}</strong>
              <Button variant="quiet" size="sm" onClick={() => setAdding(false)}>
                {t("common.close")}
              </Button>
            </div>
            <ul>
              {accounts
                .filter((account) => !destinations.some(
                  (item) => item.integration_id === account.id
                    && item.provider === account.provider))
                .map((account) => (
                  <li key={`${account.provider}:${account.id}`}>
                    <span>
                      <strong>{account.label}</strong>
                      <small>{account.platform} · {account.provider_label}</small>
                    </span>
                    <Button variant="quiet" size="sm" onClick={() => void run("add", async () => {
                      await json(await apiFetch(`${base}/destinations`, {
                        method: "POST",
                        headers: { "content-type": "application/json" },
                        body: JSON.stringify({
                          provider: account.provider,
                          integration_id: account.id,
                          platform: account.platform,
                          label: account.label,
                        }),
                      }));
                      return t("autopilot.destinationAdded", { label: account.label });
                    })}>{t("autopilot.add")}</Button>
                  </li>
                ))}
              {!accounts.length && <li>{t("autopilot.noAccounts")}</li>}
            </ul>
          </div>
        )}
      </Card>

      <Card
        eyebrow={t("autopilot.queueEyebrow")}
        title={t("autopilot.queue", {
          approved: autopilot.queue_approved, total: autopilot.queue_total,
        })}
        aside={canEdit ? (
          <Button variant="secondary" size="sm" busy={busy === "library"}
            onClick={() => void loadLibrary()}>{t("autopilot.addFromLibrary")}</Button>
        ) : undefined}
      >
        <p className="autopilot-lede">{t("autopilot.queueHelp")}</p>

        {/* Pick the clip, then write the copy for it. Two steps rather than one
            form with a path field: the path is not something anyone should be
            typing, and the copy is the part that deserves the room. */}
        {picking && !drafting && (
          <div className="autopilot-account-picker">
            <div className="autopilot-picker-head">
              <strong>{t("autopilot.chooseClip")}</strong>
              <Button variant="quiet" size="sm" onClick={() => setPicking(false)}>
                {t("common.close")}
              </Button>
            </div>
            <ul>
              {library.map((asset) => (
                <li key={asset.id}>
                  <span>
                    <strong>{asset.title}</strong>
                    <small>{clipLength(asset.duration_ms) ?? asset.media_kind}</small>
                  </span>
                  <Button variant="quiet" size="sm" onClick={() => setDrafting(asset)}>
                    {t("autopilot.writeCopy")}
                  </Button>
                </li>
              ))}
              {!library.length && <li>{t("autopilot.noClips")}</li>}
            </ul>
          </div>
        )}

        {drafting && (
          <form
            className="autopilot-compose"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const body = String(form.get("body") ?? "").trim();
              if (!body) return;
              void run("queue", async () => {
                await json(await apiFetch(`${base}/queue`, {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify({
                    asset_id: drafting.id,
                    video_path: drafting.original_path,
                    title: drafting.title,
                    body,
                    hashtags: String(form.get("hashtags") ?? "")
                      .split(/[\s,]+/).filter(Boolean),
                  }),
                }));
                setDrafting(null);
                setPicking(false);
                return t("autopilot.queued");
              });
            }}
          >
            <div className="autopilot-picker-head">
              <strong>{drafting.title}</strong>
              <Button variant="quiet" size="sm" onClick={() => setDrafting(null)}>
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
                  <small>
                    {item.times_posted > 0
                      ? t("autopilot.postedTimes", { count: item.times_posted })
                      : t("autopilot.neverPosted")}
                  </small>
                </div>
                <Badge tone={item.state === "approved" ? "good" : "neutral"}>
                  {t(`autopilot.state.${item.state}`)}
                </Badge>
                {canEdit && item.state !== "approved" && (
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
                {canEdit && (
                  <Button variant="quiet" size="sm" onClick={() => void run("drop", async () => {
                    await json(await apiFetch(`${base}/queue/${item.id}`, { method: "DELETE" }));
                    return t("autopilot.itemRemoved");
                  })}>{t("common.delete")}</Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        eyebrow={t("autopilot.nextEyebrow")}
        title={t("autopilot.next")}
        aside={
          <Button variant="secondary" size="sm" busy={busy === "preview"}
            onClick={() => void run("preview", async () => {
              const body = await json<{ note: string; posts: PreviewPost[] }>(
                await apiFetch(`${base}/autopilot/preview`, { method: "POST" }));
              setPreview(body);
              return body.note;
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
                  <li key={`${post.destination_id}-${index}`}>
                    <div className="autopilot-preview-head">
                      <strong>{new Date(post.at).toLocaleString()}</strong>
                      <span>{destination?.label ?? post.destination_id}</span>
                      <Badge tone={placementTone(post.placement)}>
                        {t(`autopilot.placement.${post.placement}`)}
                      </Badge>
                    </div>
                    <pre>{post.caption}</pre>
                    {post.first_comment && (
                      <pre className="autopilot-first-comment">{post.first_comment}</pre>
                    )}
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
      </Card>
    </div>
  );
}
