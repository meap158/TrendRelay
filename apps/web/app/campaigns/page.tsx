"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AUTHORITIES } from "./authority-options";
import { useAuth } from "../auth-provider";
import { useWorkspace } from "../workspace-provider";
import { useLocale } from "../i18n-provider";
import { LOCALES } from "../../lib/i18n/locales";
import { apiBaseUrl } from "../../lib/api";
import { StatusToasts, useStatus } from "../ui/status";
import { Badge } from "../ui/primitives";
import { Button, buttonClass } from "../ui/button";
import { WaitingScreen } from "../ui/waiting-screen";
import { Dialog } from "../ui/dialog";
import { SearchSelect } from "../ui/search-select";
import { Select } from "../ui/select";
import { ActionIcon, type ActionName } from "../ui/action-icons";
import { WaitingBlock } from "../ui/waiting-block";
import { handoffPath } from "../../lib/media-rules";
import { isDefaultScaffolding, scaffoldingFor } from "../../lib/campaign-scaffolding";
import {
  upcomingSlots,
  type Slot,
} from "../publish/composer";
import {
  platformLabels,
  type PublishingPlatform,
} from "../publishing-icons";
import {
  readTabSnapshot,
  refreshTabSnapshot,
} from "../../lib/tab-snapshots";

// The autopilot panel is the largest view in the app and renders only for the
// selected campaign, below the list. Load it as its own chunk so the campaign
// list and chrome paint without waiting on it. Client-only, so ssr:false.
/**
 * The same wait the panel shows itself, for the moment before it exists.
 *
 * Two different gaps end up here. This one is the chunk arriving, and happens
 * once; the panel's own is its data arriving, and happens on every campaign
 * switch. Both used to render nothing, so the page below the campaign list
 * simply vanished and came back.
 */
function PanelWaiting() {
  const { t } = useLocale();
  return <WaitingBlock message={t("common.loading")} />;
}

const AutopilotPanel = dynamic(
  () => import("./autopilot-panel").then((m) => m.AutopilotPanel),
  { ssr: false, loading: () => <PanelWaiting /> },
);

type Campaign = {
  id: string;
  name: string;
  objective: string;
  audience: string;
  markets: string[];
  languages: string[];
  affiliate_url?: string | null;
  status: "draft" | "active" | "archived";
  // How many products this campaign may promote. One product can be tagged to
  // several campaigns, so this counts what is tagged here, not a share of some
  // total. Optional because only the list endpoint fills it in.
  tagged_products?: number;
  /** Posts this campaign is holding for approval right now. Only the list
      endpoint fills it in; shown on the sidebar row when there are any. */
  held_count?: number;
};
/** How hard a campaign is run. Stored on its autopilot, set from its settings. */
type CampaignPolicy = {
  max_products_per_post: number;
  min_recycle_days: number;
  repeat_posts: boolean;
  rotate_products: boolean;
  daily_cap_per_account: number;
  weekly_post_cap: number | null;
  authority: string;
  priority: string;
  offer_mode: "smart" | "manual" | "none";
  offer_id: string | null;
  disclose: boolean;
  disclosure: string;
  /** Posts already frozen and waiting, which these settings will reach. */
  held?: { waiting: number; edited: number; recomposable: number };
  bio_hint: string;
};

type PublicationPlan = {
  id: string;
  campaign_id: string;
  title: string;
  platform: PublishingPlatform | "douyin" | "other";
  provider?: string | null;
  integration_id?: string | null;
  destination_label?: string | null;
  offer_id?: string | null;
  video_path: string;
  cover_path?: string | null;
  caption: string;
  hashtags: string[];
  affiliate_url?: string | null;
  disclosure: string;
  deep_link?: string | null;
  scheduled_at: string;
  timezone: string;
  state: "needs_approval" | "approved" | "rejected" | "cancelled";
};
type ConnectedAccount = {
  id: string;
  label: string;
  platform: PublishingPlatform;
  provider: string;
  provider_label: string;
  available?: boolean;
  unavailable_reason?: string | null;
};
type CampaignDestination = {
  provider: string;
  integration_id: string;
  enabled: boolean;
};
type CampaignOffer = {
  id: string;
  network: string;
  affiliate_url: string;
  commission_bps?: number | null;
  commission_flat_cents?: number | null;
  currency?: string | null;
  availability?: string;
  product: {
    name: string;
    brand?: string | null;
    marketplace?: string | null;
  };
};

/**
 * What a campaign is trying to do, offered as choices rather than a blank box.
 *
 * These are stored verbatim as the objective, which is the heaviest single
 * piece of evidence product matching reads - heavier than the campaign name or
 * the audience - so each one has to read as a real sentence about the work, not
 * as a category label. Anything not on the list is still typed by hand.
 */
const CAMPAIGN_GOALS = [
  "Drive affiliate sales from short-form video",
  "Grow reach and find new followers",
  "Build trust by demonstrating products in use",
  "Move seasonal and promotional stock",
];

/** Who the posts are for, the second-heaviest matching signal. */
const CAMPAIGN_AUDIENCES = [
  "Students and young professionals",
  "Parents shopping for the household",
  "Home and lifestyle shoppers",
  "Deal-seekers comparing prices",
];

/**
 * The languages a campaign can post in: the ones TrendRelay itself speaks.
 *
 * Taken from the interface's own list rather than a copy, so the picker here
 * cannot drift from the language switcher. Each has scaffolding written for it
 * on the API side - disclosure, bio hint, product label - which a test holds
 * level with this list. The field used to be free text suggesting "en, th",
 * and anything unrecognised was quietly dropped, so a Thai campaign looked
 * accepted and then posted in English.
 */
const POST_LANGUAGES = LOCALES.map((item) => ({ value: item.code, label: item.label }));

/** What a post attaches when it does not pin its own product. */
type OfferMode = "smart" | "manual" | "none";
type CampaignScope = "current" | "archived" | "all";

const CAMPAIGN_STATUS_ICON: Record<Campaign["status"], ActionName> = {
  draft: "edit",
  active: "play",
  archived: "archive",
};
type CampaignsSnapshot = { campaigns: Campaign[]; plans: PublicationPlan[] };

const OFFER_MODES: readonly (readonly [OfferMode, string, string])[] = [
  ["smart", "Smart match", "Fit content automatically"],
  ["manual", "One product", "Use one offer everywhere"],
  ["none", "No products", "Organic posts only"],
];

/**
 * How much a campaign may do alone - described by what it actually does.
 *
 * "Run by exception" read as though it held only exceptions, and it does not:
 * every level below Autonomous holds every post for approval. That is the
 * pipeline's rule rather than a setting, but the label promised otherwise, so
 * an operator picked the recommended option and then approved every post by
 * hand wondering which rule they kept tripping.
 */
const PRIORITIES: readonly (readonly [string, string])[] = [
  ["balanced", "Balanced — blend measured axes"],
  ["revenue", "Revenue — earnings per click"],
  ["reach", "Reach — views per post"],
  ["discussion", "Discussion — comments per post"],
];

/**
 * What the campaign posts in, when nobody has said yet.
 *
 * The interface's own language, if a campaign can be posted in it. Somebody
 * running TrendRelay in Vietnamese is far more likely to be posting in
 * Vietnamese than in English, and this is the one default that decides the
 * disclosure, the profile-link wording and the product labels.
 */
function defaultLanguage(locale: string): string {
  return POST_LANGUAGES.some((item) => item.value === locale) ? locale : "en";
}

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Campaign request failed.");
  return body;
}

function offerDescription(offer: CampaignOffer): string {
  const commission = offer.commission_bps
    ? `${(offer.commission_bps / 100).toLocaleString()}% commission`
    : offer.commission_flat_cents
      ? `${offer.currency ?? ""} ${(offer.commission_flat_cents / 100).toLocaleString()} commission`.trim()
      : null;
  return [offer.product.marketplace, offer.network, commission].filter(Boolean).join(" · ");
}

function planPlatformLabel(platform: PublicationPlan["platform"]): string {
  if (platform === "douyin") return "Douyin";
  if (platform === "other") return "Other";
  return platformLabels[platform];
}


export default function CampaignsPage() {
  const { t, locale } = useLocale();
  const { loading, user, apiFetch } = useAuth();
  const { workspaces, workspaceId, loading: workspaceLoading } = useWorkspace();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  // Whether the campaign list has answered at least once. `loading` above is the
  // auth probe, which only runs on first load, so on a tab switch it is already
  // false and the empty "create a campaign" hero would paint over the fetch.
  // This tells "still loading" apart from "loaded, and there are none".
  const [loadedCampaignWorkspaceId, setLoadedCampaignWorkspaceId] = useState("");
  const [campaignId, setCampaignId] = useState("");
  // Archived work stays out of the operating list until somebody explicitly
  // asks for it. "Current" includes drafts and active campaigns: both still
  // need attention, while an archive is historical by definition.
  const [campaignScope, setCampaignScope] = useState<CampaignScope>("current");
  // Refresh is a network concern and must not rerun when this local filter
  // changes. The ref lets a completed refresh respect the latest view without
  // turning the view switch into another request.
  const campaignScopeRef = useRef<CampaignScope>("current");
  const requestedCampaign = useRef("");
  const [plans, setPlans] = useState<PublicationPlan[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  // The chosen preset, or "" for the one that opens a box to type in.
  const [objectiveChoice, setObjectiveChoice] = useState(CAMPAIGN_GOALS[0]);
  const [audienceChoice, setAudienceChoice] = useState(CAMPAIGN_AUDIENCES[0]);
  // The campaign being edited, held as its own copy so an abandoned edit
  // changes nothing and the list keeps showing what is actually saved.
  const [settingsFor, setSettingsFor] = useState<Campaign | null>(null);
  const [policy, setPolicy] = useState<CampaignPolicy | null>(null);
  /** Held apart from the form because the offer picker appears only for one
      of the three modes, and a radio group cannot drive that on its own. */
  const [offerMode, setOfferMode] = useState<"smart" | "manual" | "none">("smart");
  /** SearchSelect is controlled, so the chosen offer cannot ride the form. */
  const [offerChoice, setOfferChoice] = useState("");
  /** The settings dialog's copy of the two strings the post language rewrites,
      for the same reason the create dialog holds its own. */
  const [settingsScaffolding, setSettingsScaffolding] = useState(
    { disclosure: "", bioHint: "" },
  );
  const [offers, setOffers] = useState<CampaignOffer[]>([]);
  const [newCampaignOfferId, setNewCampaignOfferId] = useState("");
  /** The create dialog's own copies of the three fields a form cannot carry:
      a mode that decides whether another field exists, and two strings that
      are rewritten when the post language changes. */
  const [newOfferMode, setNewOfferMode] = useState<OfferMode>("smart");
  const [newLanguage, setNewLanguage] = useState<string>(defaultLanguage(locale));
  const [newScaffolding, setNewScaffolding] = useState(
    () => scaffoldingFor(defaultLanguage(locale)),
  );
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);
  const selectedCampaign = campaigns.find((item) => item.id === campaignId);
  const currentCampaigns = useMemo(
    () => campaigns.filter((item) => item.status !== "archived"),
    [campaigns],
  );
  const archivedCampaigns = useMemo(
    () => campaigns.filter((item) => item.status === "archived"),
    [campaigns],
  );
  const visibleCampaigns = campaignScope === "all"
    ? campaigns
    : campaignScope === "archived"
      ? archivedCampaigns
      : currentCampaigns;
  const canCreateCampaign = ["owner", "editor"].includes(selectedWorkspace?.role ?? "");
  const canCreatePlan = ["owner", "editor", "approver"].includes(selectedWorkspace?.role ?? "");
  const canApprove = ["owner", "approver"].includes(selectedWorkspace?.role ?? "");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const campaignsReady = workspaceId
    ? loadedCampaignWorkspaceId === workspaceId
    : !workspaceLoading;

  const refresh = useCallback(async (nextWorkspaceId: string) => {
    if (!nextWorkspaceId) return;
    const snapshot = await refreshTabSnapshot<CampaignsSnapshot>(
      `campaigns:${nextWorkspaceId}`,
      async () => {
        const [campaignBody, calendarBody] = await Promise.all([
          json<{ campaigns: Campaign[] }>(
            await apiFetch(`/api/workspaces/${nextWorkspaceId}/campaigns`),
          ),
          json<{ plans: PublicationPlan[] }>(
            await apiFetch(`/api/workspaces/${nextWorkspaceId}/campaigns/calendar`),
          ),
        ]);
        return { campaigns: campaignBody.campaigns, plans: calendarBody.plans };
      },
    );
    setCampaigns(snapshot.campaigns);
    setPlans(snapshot.plans);
    const requested = snapshot.campaigns.find(
      (item) => item.id === requestedCampaign.current,
    );
    // A URL target chooses the initial view; it is not a permanent filter lock.
    // Once consumed, the operator can switch back to Current normally.
    if (requested) requestedCampaign.current = "";
    const effectiveScope = requested?.status === "archived"
      ? "archived"
      : campaignScopeRef.current;
    if (effectiveScope !== campaignScopeRef.current) {
      campaignScopeRef.current = effectiveScope;
      setCampaignScope(effectiveScope);
    }
    const visible = snapshot.campaigns.filter((item) =>
      effectiveScope === "all"
      || (effectiveScope === "archived") === (item.status === "archived"),
    );
    setCampaignId((current) => requested?.id
      ?? (visible.some((item) => item.id === current) ? current : visible[0]?.id ?? ""));
  }, [apiFetch]);

  useEffect(() => {
    queueMicrotask(() => {
      const params = new URLSearchParams(window.location.search);
      requestedCampaign.current = params.get("campaign") ?? "";
    });
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    const cached = readTabSnapshot<CampaignsSnapshot>(`campaigns:${workspaceId}`);
    if (cached) {
      queueMicrotask(() => {
        if (cancelled) return;
        setCampaigns(cached.campaigns);
        setPlans(cached.plans);
        const requested = cached.campaigns.find(
          (item) => item.id === requestedCampaign.current,
        );
        if (requested) requestedCampaign.current = "";
        const effectiveScope = requested?.status === "archived"
          ? "archived"
          : campaignScopeRef.current;
        if (effectiveScope !== campaignScopeRef.current) {
          campaignScopeRef.current = effectiveScope;
          setCampaignScope(effectiveScope);
        }
        const visible = cached.campaigns.filter((item) =>
          effectiveScope === "all"
          || (effectiveScope === "archived") === (item.status === "archived"),
        );
        setCampaignId((current) => requested?.id
          ?? (visible.some((item) => item.id === current)
            ? current
            : visible[0]?.id ?? ""));
        setLoadedCampaignWorkspaceId(workspaceId);
      });
    }
    queueMicrotask(() => {
      void refresh(workspaceId)
        .then(() => {
          if (cancelled) return;
          setLoadedCampaignWorkspaceId(workspaceId);
        })
        .catch((reason: unknown) => {
          if (!cancelled) {
            setLoadedCampaignWorkspaceId(workspaceId);
            fail(reason instanceof Error ? reason.message : "Could not load campaigns.");
          }
        });
    });
    return () => { cancelled = true; };
  }, [refresh, workspaceId, fail]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    const key = `campaign-offers:${workspaceId}`;
    const cached = readTabSnapshot<CampaignOffer[]>(key);
    if (cached) queueMicrotask(() => { if (!cancelled) setOffers(cached); });
    refreshTabSnapshot(key, () =>
      apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`)
        .then((response) => json<{ offers: CampaignOffer[] }>(response))
        .then((body) => body.offers ?? []),
    )
      .then((rows) => { if (!cancelled) setOffers(rows); })
      .catch(() => { if (!cancelled) setOffers([]); });
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  function closeNewCampaign() {
    setNewCampaignOpen(false);
    resetNewCampaign();
  }

  /** Back to what a fresh dialog shows, so an abandoned draft is not the
      starting point of the next campaign. */
  function resetNewCampaign() {
    const language = defaultLanguage(locale);
    setNewCampaignOfferId("");
    setNewOfferMode("smart");
    setNewLanguage(language);
    setNewScaffolding(scaffoldingFor(language));
    setObjectiveChoice(CAMPAIGN_GOALS[0]);
    setAudienceChoice(CAMPAIGN_AUDIENCES[0]);
  }

  /**
   * Follow the post language, unless the wording has been written by hand.
   *
   * The API does this on save; doing it here too is what makes the default
   * visible while the campaign is still being described, rather than a
   * surprise discovered in the settings dialog afterwards. The test for
   * "still ours" asks every language, not just the one being left, so
   * en -> vi -> fr ends in French.
   */
  function changeNewLanguage(language: string) {
    setNewLanguage(language);
    setNewScaffolding((current) => {
      const fresh = scaffoldingFor(language);
      return {
        disclosure: isDefaultScaffolding("disclosure", current.disclosure)
          ? fresh.disclosure : current.disclosure,
        bioHint: isDefaultScaffolding("bioHint", current.bioHint)
          ? fresh.bioHint : current.bioHint,
      };
    });
  }

  async function createCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("campaign");
    fail(null);
    succeed(null);
    try {
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      const body = await json<{ campaign: Campaign }>(
        await apiFetch(`/api/workspaces/${workspaceId}/campaigns`, {
          method: "POST",
          body: JSON.stringify({
            name: form.get("name"),
            objective: form.get("objective"),
            audience: form.get("audience"),
            // One language, chosen from the two the composer writes. Markets is
            // no longer asked for: nothing scored it, and a free-text country
            // list was a question with no consequence.
            languages: [form.get("language")].filter(Boolean),
            // Only pinned when the mode pins one, so switching away from
            // "One product" does not leave a stale offer behind it. The same
            // rule the settings dialog applies.
            offer_id: newOfferMode === "manual" ? (newCampaignOfferId || null) : null,
            offer_mode: newOfferMode,
            // How it posts. Every one optional at the API, so an untouched
            // section sends the values it was showing and a campaign created
            // without opening it gets exactly the defaults it always did.
            max_products_per_post: Number(form.get("max_products_per_post")),
            daily_cap_per_account: Number(form.get("daily_cap_per_account")),
            weekly_post_cap: form.get("weekly_post_cap")
              ? Number(form.get("weekly_post_cap")) : null,
            authority: form.get("authority"),
            priority: form.get("priority"),
            disclose: form.get("disclose") === "on",
            disclosure: newScaffolding.disclosure,
            bio_hint: newScaffolding.bioHint,
          }),
        }),
      );
      formElement.reset();
      await refresh(workspaceId);
      setCampaignId(body.campaign.id);
      setNewCampaignOpen(false);
      resetNewCampaign();
      succeed("Campaign created. Add approved media to its campaign queue.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Campaign creation failed.");
    } finally {
      setBusy(null);
    }
  }


  /**
   * What the campaign is, and how hard it is run, in one dialog.
   *
   * The policy lives on the autopilot row because that is what reads it, and
   * it is fetched when the dialog opens rather than folded into every campaign
   * payload: this is the one screen that needs it, and it is opened by hand.
   */
  function openCampaignSettings(campaign: Campaign) {
    setSettingsFor(campaign);
    setPolicy(null);
    void (async () => {
      try {
        const body = await json<{ autopilot: CampaignPolicy }>(await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${campaign.id}/autopilot`,
        ));
        setPolicy(body.autopilot);
        setOfferMode(body.autopilot.offer_mode);
        setOfferChoice(body.autopilot.offer_id ?? "");
        setSettingsScaffolding({
          disclosure: body.autopilot.disclosure,
          bioHint: body.autopilot.bio_hint,
        });
      } catch {
        // The identity half of the dialog still works without it, and a
        // campaign with no autopilot yet has no policy to show.
        setPolicy(null);
      }
    })();
  }

  async function saveCampaignSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!settingsFor) return;
    setBusy("settings");
    fail(null);
    succeed(null);
    try {
      const form = new FormData(event.currentTarget);
      const payload = await json<{
        campaign: Campaign;
        held?: { recomposed: number; kept: number };
      }>(
        await apiFetch(`/api/workspaces/${workspaceId}/campaigns/${settingsFor.id}`, {
          method: "POST",
          body: JSON.stringify({
            name: form.get("name"),
            objective: form.get("objective"),
            audience: form.get("audience"),
            languages: [form.get("language")].filter(Boolean),
            // Only sent when the dialog had them to show. Every one is
            // optional at the API, so a campaign whose autopilot has not been
            // created yet corrects its wording without inventing a policy.
            ...(policy ? {
              max_products_per_post: Number(form.get("max_products_per_post")),
              daily_cap_per_account: Number(form.get("daily_cap_per_account")),
              priority: form.get("priority"),
              weekly_post_cap: form.get("weekly_post_cap")
                ? Number(form.get("weekly_post_cap")) : null,
              clear_weekly_cap: !form.get("weekly_post_cap"),
              offer_mode: offerMode,
              // Only meaningful for the one-offer mode, and cleared otherwise
              // so a mode change does not leave a stale pin behind it.
              offer_id: offerMode === "manual" ? (offerChoice || null) : null,
              min_recycle_days: Number(form.get("min_recycle_days")),
              repeat_posts: form.get("repeat_posts") === "on",
              rotate_products: form.get("rotate_products") === "on",
              disclose: form.get("disclose") === "on",
              disclosure: form.get("disclosure"),
              bio_hint: form.get("bio_hint"),
            } : {}),
          }),
        }),
      );
      await refresh(workspaceId);
      setSettingsFor(null);
      // What it reached, not only that it saved.
      const reached = payload.held;
      succeed(reached?.recomposed
        ? `Campaign settings saved. ${reached.recomposed} waiting post${
          reached.recomposed === 1 ? "" : "s"} updated to match`
          + (reached.kept ? `; ${reached.kept} left as edited by hand.` : ".")
        : "Campaign settings saved.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Could not save campaign settings.");
    } finally {
      setBusy(null);
    }
  }

  async function setCampaignStatus(status: Campaign["status"]) {
    if (!campaignId) return;
    setBusy(`campaign-${status}`);
    fail(null);
    try {
      await json(
        await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${campaignId}/status`,
          { method: "POST", body: JSON.stringify({ status }) },
        ),
      );
      await refresh(workspaceId);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Campaign status update failed.");
    } finally {
      setBusy(null);
    }
  }

  function changeCampaignScope(next: CampaignScope) {
    campaignScopeRef.current = next;
    setCampaignScope(next);
    const visible = campaigns.filter((item) =>
      next === "all" || (next === "archived") === (item.status === "archived"),
    );
    if (!visible.some((item) => item.id === campaignId)) {
      setCampaignId(visible[0]?.id ?? "");
    }
  }

  // The same two gates every other tab has, and this one had until a refactor
  // took them out with the hand-planned posts. Without them the page drew its
  // whole chrome - an empty workspace picker, an empty campaign list, a panel
  // with no campaign - while the session was still being checked, so arriving
  // looked like a workspace with nothing in it.
  if (loading) {
    return <WaitingScreen className="campaign-page" message={t("campaigns.loading")} />;
  }
  if (!user) {
    return (
      <main className="campaign-page">
        <Link className={buttonClass({ variant: "primary" })}
          href="/sign-in?next=%2Fcampaigns">{t("campaigns.signInPrompt")}</Link>
      </main>
    );
  }

  return (
    <main className="campaign-page">
      <header className="campaign-heading">
        <div>
          <p className="section-kicker">{t("campaigns.eyebrow")}</p>
          <h1>{t("campaigns.heading")}</h1>
          {/* One line, like every other tab's heading: enough to say what this
              is for. The panel's own summary still restates it with this
              campaign's numbers - this only keeps the heading from reading as a
              bare word where the others carry a sentence. */}
          <p className="lede">Run a campaign end to end — what it posts, where it goes, and how hard it runs.</p>
        </div>
      </header>

      <section className="campaign-layout">
        <aside className="campaign-sidebar">
          <div className="card-heading">
            <div>
              <p className="section-kicker">{t("campaigns.listHeading")}</p>
              <h2>{campaignsReady
                ? t("campaigns.currentCount", { count: currentCampaigns.length })
                : "—"}</h2>
            </div>
            {campaignsReady && (
              <label className="campaign-scope-filter">
                <span className="sr-only">{t("campaigns.visibility")}</span>
                <Select
                  aria-label={t("campaigns.visibility")}
                  value={campaignScope}
                  onChange={(event) => changeCampaignScope(event.target.value as CampaignScope)}
                >
                  <option value="current">{t("campaigns.scopeCurrent", { count: currentCampaigns.length })}</option>
                  <option value="archived">{t("campaigns.scopeArchived", { count: archivedCampaigns.length })}</option>
                  <option value="all">{t("campaigns.scopeAll", { count: campaigns.length })}</option>
                </Select>
              </label>
            )}
          </div>
          <div className="campaign-list">
            {!campaignsReady ? (
              <WaitingBlock className="waiting-block-compact" message={t("common.loading")} />
            ) : visibleCampaigns.map((campaign) => (
              <button
                className={campaign.id === campaignId ? "selected" : ""}
                key={campaign.id}
                onClick={() => setCampaignId(campaign.id)}
                type="button"
              >
                <strong>
                  <span className={`campaign-status-icon ${campaign.status}`} aria-hidden="true">
                    <ActionIcon name={CAMPAIGN_STATUS_ICON[campaign.status]} size={14} />
                  </span>
                  <span className="campaign-list-name">{campaign.name}</span>
                  {/* What needs a person, where the campaign is chosen: a count
                      of posts held for approval, shown only when there are any. */}
                  {(campaign.held_count ?? 0) > 0 && (
                    <em className="campaign-list-held"
                      title={`${campaign.held_count} post${campaign.held_count === 1 ? "" : "s"} waiting for your approval`}
                    >{campaign.held_count}</em>
                  )}
                </strong>
                {/* The language it posts in, not the market it was never asked
                    for. Every campaign reported "global" once markets stopped
                    being collected, which is a word that told you nothing. */}
                <span>{campaign.status} · {
                  POST_LANGUAGES.find((item) => item.value === campaign.languages[0])?.label
                  ?? campaign.languages[0]
                  ?? "English"
                } · {t("attribution.productCount", { count: campaign.tagged_products ?? 0 })}</span>
              </button>
            ))}
            {campaignsReady && !visibleCampaigns.length && (
              <p>{campaignScope === "archived"
                ? t("campaigns.noArchived")
                : campaignScope === "current" && archivedCampaigns.length
                  ? t("campaigns.archivedAvailable")
                  : t("campaigns.empty")}</p>
            )}
          </div>
          {campaignsReady && canCreateCampaign && (
            <Button variant="primary" onClick={() => setNewCampaignOpen(true)}>
              <ActionIcon name="add" />{t("campaigns.create")}
            </Button>
          )}
        </aside>

        <div className="campaign-workspace">
          {!campaignsReady ? (
            <WaitingBlock message={t("common.loading")} />
          ) : selectedCampaign ? (
            <>
              <section className="campaign-summary">
                <div>
                  <p className="section-kicker">{selectedCampaign.status}</p>
                  <h2>{selectedCampaign.name}</h2>
                  <p>{selectedCampaign.objective}</p>
                  <small>Audience: {selectedCampaign.audience} · {t("attribution.productCount", { count: selectedCampaign.tagged_products ?? 0 })}</small>
                </div>
                <div className="campaign-status-actions">
                  <Link href={`/attribution?campaign=${encodeURIComponent(selectedCampaign.id)}`}><ActionIcon name="link" />{t("campaigns.measureRevenue")}</Link>
                  {canCreateCampaign && selectedCampaign.status === "archived" && (
                    <Button variant="secondary" size="sm" onClick={() => void setCampaignStatus("draft")}><ActionIcon name="play" />{t("campaigns.restore")}</Button>
                  )}
                  {canCreateCampaign && selectedCampaign.status !== "archived" && (
                    <Button variant="secondary" size="sm" onClick={() => void setCampaignStatus("archived")}><ActionIcon name="archive" />{t("campaigns.archive")}</Button>
                  )}
                  {/* Icon-only: the two beside it are the ones you reach for,
                      and this is the one you reach for rarely. Its name is on
                      the label rather than beside it for the same reason. */}
                  {canCreateCampaign && (
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={t("campaigns.settings")}
                      title={t("campaigns.settings")}
                      onClick={() => openCampaignSettings(selectedCampaign)}
                    ><ActionIcon name="setup" /></Button>
                  )}
                </div>
              </section>

              {/* One timeline. The hand-planned posts render inside the
                  autopilot's posting timeline rather than as a second calendar
                  below it: what will post and what has posted is one story,
                  told in one place. */}
              <AutopilotPanel
                key={selectedCampaign.id}
                workspaceId={workspaceId}
                campaignId={selectedCampaign.id}
                campaignStatus={selectedCampaign.status}
                canEdit={Boolean(canCreatePlan)}
                apiFetch={apiFetch}
                succeed={succeed}
                fail={fail}
                onCampaignChanged={() => refresh(workspaceId)}
              />

              {/* All that survives of the manual workflow: a way to reach the
                  thing that owns it. Publish is the one-off composer and the
                  single source of truth for connections and posting times, so a
                  second form here could only drift from it. The one that was
                  here was already `hidden` - unreachable markup still carrying
                  its own state, handlers and media picker. */}
              <div className="campaign-one-off-handoff">
                  {/* One line, no lecture about what Publish owns. */}
                  <strong>Need a one-off post instead?</strong>
                  <Link className="ui-button ui-button-secondary ui-button-sm"
                    href={`/publish?campaign=${encodeURIComponent(selectedCampaign.id)}`}>
                    Open Publish
                  </Link>
                </div>
            </>
          ) : (
            <section className="empty-console">
              <h2>{campaignScope === "archived"
                ? t("campaigns.noArchived")
                : campaignScope === "current" && archivedCampaigns.length
                  ? t("campaigns.noCurrent")
                  : t("campaigns.createToStart")}</h2>
              <p>{campaignScope === "current" && archivedCampaigns.length
                ? t("campaigns.archivedHiddenHelp")
                : t("campaigns.whatItConnects")}</p>
              {campaignScope === "current" && archivedCampaigns.length > 0 && (
                <Button variant="secondary" onClick={() => changeCampaignScope("archived")}>
                  <ActionIcon name="archive" />{t("campaigns.viewArchived", {
                    count: archivedCampaigns.length,
                  })}
                </Button>
              )}
            </section>
          )}
        </div>
      </section>
      <Dialog
        open={newCampaignOpen}
        title={t("campaigns.create")}
        description="Set the campaign goal once. Media, accounts, schedule, and deployment come next in this workspace."
        onClose={closeNewCampaign}
      >
        <form className="campaign-dialog-form" onSubmit={createCampaign}>
          {/* Above the fields, and first in the DOM rather than moved there by
              `order`. Reordering visually would leave the keyboard tabbing to
              a Create button that is no longer where it appears, which is the
              one thing worse than scrolling for it. `autoFocus` below still
              puts the caret in the name field, so typing starts where it did. */}
          <div className="campaign-dialog-actions">
            <Button type="button" variant="quiet" onClick={closeNewCampaign}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={busy === "campaign"}>{t("campaigns.createButton")}</Button>
          </div>
          <label>{t("campaigns.name")}<input name="name" required maxLength={160} autoFocus /></label>
          {/* Chosen rather than composed. Both of these are read by product
              matching, so what goes in them has to be a sentence about the
              campaign - which is a lot to ask of an empty textarea, and the
              reason most of them ended up thin. The list carries the phrasing;
              anything it does not cover is still typed. */}
          <div className="campaign-dialog-grid">
            <label>{t("campaigns.objective")}
              <Select
                name={objectiveChoice ? "objective" : undefined}
                value={objectiveChoice}
                onChange={(event) => setObjectiveChoice(event.target.value)}
              >
                {CAMPAIGN_GOALS.map((goal) => <option key={goal} value={goal}>{goal}</option>)}
                <option value="">Something else…</option>
              </Select>
              {!objectiveChoice && (
                <textarea name="objective" rows={2} required maxLength={1000}
                  placeholder="What should this campaign achieve?" />
              )}
            </label>
            <label>{t("campaigns.audience")}
              <Select
                name={audienceChoice ? "audience" : undefined}
                value={audienceChoice}
                onChange={(event) => setAudienceChoice(event.target.value)}
              >
                {CAMPAIGN_AUDIENCES.map((who) => <option key={who} value={who}>{who}</option>)}
                <option value="">Something else…</option>
              </Select>
              {!audienceChoice && (
                <textarea name="audience" rows={2} required maxLength={1000}
                  placeholder="Who are these posts for?" />
              )}
            </label>
          </div>
          {/* Controlled, because the disclosure and the profile-link wording
              below are rewritten when it changes. */}
          <label>{t("campaigns.postLanguage")}
            <Select name="language" value={newLanguage}
              onChange={(event) => changeNewLanguage(event.target.value)}>
              {POST_LANGUAGES.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </Select>
            <small>The language the disclosure, bio hint and product labels are written in. Your own copy is always your own.</small>
          </label>
          {/* The same three modes the settings dialog offers, in the same
              shape. This was a bare offer picker, which could say "pin this
              one" and "match automatically" but had no way at all to say "no
              products" - so an organic campaign could not be created, only
              created wrongly and then corrected. */}
          <div className="campaign-product-mode">
            <div>
              <strong>Products</strong>
              <small>What a post attaches when it does not pin its own.
                Pinning in the composer overrides this for that post.</small>
            </div>
            <div className="campaign-mode-options" role="radiogroup"
              aria-label="Affiliate product matching">
              {OFFER_MODES.map(([mode, title, hint]) => (
                <button key={mode} type="button" role="radio"
                  aria-checked={newOfferMode === mode}
                  className={newOfferMode === mode ? "active" : ""}
                  onClick={() => setNewOfferMode(mode)}>
                  <strong>{title}</strong><small>{hint}</small>
                </button>
              ))}
            </div>
          </div>
          {newOfferMode === "manual" && (
            <label>Offer
              <SearchSelect
                value={newCampaignOfferId}
                options={offers.map((offer) => ({
                  value: offer.id,
                  label: offer.product.name,
                  description: offerDescription(offer),
                  keywords: `${offer.product.brand ?? ""} ${offer.product.marketplace ?? ""} ${offer.network} ${offer.affiliate_url}`,
                }))}
                onChange={setNewCampaignOfferId}
                placeholder="Choose an imported offer"
                searchPlaceholder="Search imported offers…"
                emptyLabel="No offers imported in Attribution"
              />
              <small>Source: imported offers in Attribution.</small>
            </label>
          )}
          {/* Folded away rather than left out. These are the same settings, in
              the same order and with the same wording, as the settings dialog
              - but every one has a working default, and a first-run form that
              opens with nine of them asks somebody to decide things they have
              no basis to decide yet. Open it and they are all here; ignore it
              and the campaign is created exactly as it always was. */}
          <details className="campaign-dialog-more">
            <summary>
              <strong>How it posts</strong>
              <small>Caps, authority, disclosure. Sensible defaults already set.</small>
            </summary>
            <div className="campaign-dialog-grid">
              <label>Products per post
                <input type="number" name="max_products_per_post" min={1} max={5} defaultValue={1} />
                <small>Bio-only networks still use one and rotate across posts.</small>
              </label>
              <label>Posts per account per day
                <input type="number" name="daily_cap_per_account" min={1} max={24} defaultValue={5} />
                <small>A ceiling, not a target.</small>
              </label>
              <label>Weekly post cap
                <input type="number" name="weekly_post_cap" min={1} max={200} placeholder="No cap" />
                <small>Across every destination. Empty leaves the per-account caps.</small>
              </label>
            </div>
            <label>Authority
              <Select name="authority" defaultValue="run_by_exception">
                {AUTHORITIES.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
              <small>How much of the posting runs without you.</small>
            </label>
            {/* Written in the campaign's language and rewritten when it
                changes, until somebody types their own. The API has always
                chosen these; showing them here is what makes them editable
                before the campaign exists rather than after. */}
            <label className="campaign-dialog-check">
              <input type="checkbox" name="disclose" />
              <span>
                Add an affiliate disclosure
                <small>Off unless you ask for it. The FTC&apos;s endorsement guides
                  ask for one near the endorsement, and TikTok, Meta and YouTube
                  each require paid promotion to be marked.</small>
              </span>
            </label>
            <label>Disclosure wording
              <input name="disclosure" maxLength={280} value={newScaffolding.disclosure}
                onChange={(event) => setNewScaffolding((current) => ({
                  ...current, disclosure: event.target.value,
                }))} />
              <small>Used when the switch above is on.</small>
            </label>
            <label>Profile-link wording
              <input name="bio_hint" maxLength={120} value={newScaffolding.bioHint}
                onChange={(event) => setNewScaffolding((current) => ({
                  ...current, bioHint: event.target.value,
                }))} />
              <small>Used where a link in a post is not clickable.</small>
            </label>
            <label>Optimise for
              <Select name="priority" defaultValue="balanced">
                {PRIORITIES.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
              <small>Ranking uses an axis only once it has evidence.</small>
            </label>
          </details>
        </form>
      </Dialog>
      {/* The same questions the campaign was created with, answerable again.
          Free text rather than the creation form's lists: by the time somebody
          opens this they have a particular correction in mind, and a list would
          only be in the way of it. The pinned offer is not here - destinations
          own that once a campaign is running. */}
      <Dialog
        open={Boolean(settingsFor)}
        title={t("campaigns.settings")}
        description="What this campaign is for, and the language it posts in. Product matching reads the goal and the audience, so keeping them accurate is what keeps its picks sensible."
        onClose={() => setSettingsFor(null)}
        /* Save beside the way out, and only one way out. Cancel sat at the top
           of the form doing exactly what the × does - nothing is abandoned by
           closing a settings panel, since nothing is saved until Save - so it
           was the same button written twice. Outside the form and bound to it
           by id, which is what keeps it in the header while it still submits. */
        headerAction={settingsFor ? (
          <Button type="submit" form="campaign-settings-form" variant="primary"
            size="sm" busy={busy === "settings"}>{t("common.save")}</Button>
        ) : undefined}
      >
        {settingsFor && (
          <form id="campaign-settings-form" className="campaign-dialog-form"
            onSubmit={saveCampaignSettings}>
            <label>{t("campaigns.name")}
              <input name="name" required maxLength={160} defaultValue={settingsFor.name} />
            </label>
            <label>{t("campaigns.objective")}
              <textarea name="objective" rows={2} required maxLength={1000}
                defaultValue={settingsFor.objective} />
            </label>
            <label>{t("campaigns.audience")}
              <textarea name="audience" rows={2} required maxLength={1000}
                defaultValue={settingsFor.audience} />
            </label>
            <label>{t("campaigns.postLanguage")}
              <Select name="language" defaultValue={settingsFor.languages[0] ?? "en"}
                onChange={(event) => setSettingsScaffolding((current) => {
                  const fresh = scaffoldingFor(event.target.value);
                  return {
                    disclosure: isDefaultScaffolding("disclosure", current.disclosure)
                      ? fresh.disclosure : current.disclosure,
                    bioHint: isDefaultScaffolding("bioHint", current.bioHint)
                      ? fresh.bioHint : current.bioHint,
                  };
                })}>
                {POST_LANGUAGES.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </Select>
              <small>Changes the disclosure and bio hint too, unless you have written your own.</small>
            </label>
            {/* How hard it is run. Set once when the campaign is described
                and rarely touched after, which is why it is here rather than
                beside the queue somebody works in every day. */}
            {policy && <>
              <div className="campaign-dialog-grid">
                {/* One to five, which is what the table accepts. This said 0
                    to 10, so both ends passed the form and the API and then
                    broke on valid_autopilot_product_count - asking for six
                    products returned a 500 rather than a limit. Zero was never
                    how to ask for no products; that is the mode below. */}
                <label>Products per post
                  <input type="number" name="max_products_per_post" min={1} max={5}
                    defaultValue={policy.max_products_per_post} />
                  <small>Bio-only networks still use one and rotate across posts.</small>
                </label>
                {/* The rest interval moved to "How this campaign posts",
                    where the rule it belongs to is written out. Set in two
                    places it was also *stated* in only one of them, and the
                    number here governed nothing at all on a campaign that
                    posts each item once. The three caps share one grid rather
                    than leaving the hole its departure opened. */}
                <label>Posts per account per day
                  <input type="number" name="daily_cap_per_account" min={1} max={24}
                    defaultValue={policy.daily_cap_per_account} />
                  <small>A ceiling, not a target.</small>
                </label>
                <label>Weekly post cap
                  <input type="number" name="weekly_post_cap" min={1} max={200}
                    placeholder="No cap" defaultValue={policy.weekly_post_cap ?? ""} />
                  <small>Across every destination. Empty leaves the per-account caps.</small>
                </label>

              </div>
              <div className="campaign-product-mode">
                <div>
                  <strong>Products</strong>
                  <small>What a post attaches when it does not pin its own.
                    Pinning in the composer overrides this for that post.</small>
                </div>
                <div className="campaign-mode-options" role="radiogroup"
                  aria-label="Affiliate product matching">
                  {OFFER_MODES.map(([mode, title, hint]) => (
                    <button key={mode} type="button" role="radio"
                      aria-checked={offerMode === mode}
                      className={offerMode === mode ? "active" : ""}
                      onClick={() => setOfferMode(mode)}>
                      <strong>{title}</strong><small>{hint}</small>
                    </button>
                  ))}
                </div>
              </div>
              {offerMode === "manual" && (
                <label>Offer
                  <SearchSelect
                    value={offerChoice}
                    onChange={setOfferChoice}
                    placeholder="Choose an imported offer"
                    searchPlaceholder="Search imported offers…"
                    options={offers.map((offer) => ({
                      value: offer.id,
                      label: offer.product.name,
                      description: offer.network,
                    }))}
                  />
                  <small>Source: imported offers in Attribution.</small>
                </label>
              )}
              {/* The words every caption is scaffolded with. They live beside
                  the language above, which rewrites them when it changes
                  unless they have been edited. */}
              {/* Controlled, so changing the language above rewrites these on
                  screen. The API already did it on save, which meant the field
                  showed the old language until the dialog was reopened. */}
              {/* The size of the change, before it is made. These settings
                  decide what a post says, and posts frozen before now kept the
                  wording they were frozen with - so "3 posts are waiting" is
                  the difference between changing a rule and knowing what it
                  reaches. Only when there is something to reach. */}
              {(policy.held?.waiting ?? 0) > 0 && (
                <p className="campaign-dialog-reach" role="status">
                  <strong>{policy.held?.waiting}</strong>{" "}
                  {policy.held?.waiting === 1 ? "post is" : "posts are"} waiting
                  for approval. Saving rewrites{" "}
                  {policy.held?.recomposable === policy.held?.waiting
                    ? (policy.held?.waiting === 1 ? "it" : "them")
                    : `${policy.held?.recomposable} of them`}{" "}
                  to match these settings.
                  {(policy.held?.edited ?? 0) > 0 && (
                    <>{" "}The {policy.held?.edited === 1 ? "other was" : "others were"}{" "}
                      edited by hand and {policy.held?.edited === 1 ? "stays" : "stay"}{" "}
                      as written.</>
                  )}
                </p>
              )}
              {/* A switch and a wording, because they are two questions.
                  Whether a post carries a disclosure is a decision about
                  compliance; what it says is a matter of phrasing, and the
                  phrasing is kept whichever way the switch is set. */}
              <label className="campaign-dialog-check">
                <input type="checkbox" name="disclose" defaultChecked={policy.disclose} />
                <span>
                  Add an affiliate disclosure
                  <small>Leads every caption on every network when a product is
                    attached. Off means posts carry no disclosure at all: the
                    FTC&apos;s endorsement guides ask for one near the endorsement,
                    and TikTok, Meta and YouTube each require paid promotion to
                    be marked, so this is your call and your liability.</small>
                </span>
              </label>
              <label>Disclosure wording
                <input name="disclosure" maxLength={280}
                  value={settingsScaffolding.disclosure}
                  onChange={(event) => setSettingsScaffolding((current) => ({
                    ...current, disclosure: event.target.value,
                  }))} />
                <small>Kept whether or not it is switched on, so turning it back
                  on does not mean writing it again.</small>
              </label>
              <label>Profile-link wording
                <input name="bio_hint" maxLength={120}
                  value={settingsScaffolding.bioHint}
                  onChange={(event) => setSettingsScaffolding((current) => ({
                    ...current, bioHint: event.target.value,
                  }))} />
                <small>Used where a link in a post is not clickable.</small>
              </label>
              {/* Back where the campaign is described. These three moved out
                  to the posting-strategy list, which explains each rule beside
                  a toggle - a good place to read them and a strange one to be
                  the only place they exist, since this dialog is where somebody
                  goes to change what a campaign does. The list still toggles
                  them; both show the same value.

                  The interval sits under the rule it belongs to rather than
                  among the caps: it governs nothing until repeats are on, and
                  a number that far from its switch reads like another ceiling. */}
              <label className="campaign-dialog-check">
                <input type="checkbox" name="repeat_posts"
                  defaultChecked={policy.repeat_posts} />
                <span>
                  Let a post go out more than once
                  <span className="campaign-dialog-hint">Off means each post
                    goes to each account once.</span>
                </span>
              </label>
              <label>Rest between repeats
                <input type="number" name="min_recycle_days" min={1} max={365}
                  defaultValue={policy.min_recycle_days} />
                <small>Days before the same post may return to the same
                  account. Governs nothing while repeats are off.</small>
              </label>
              <label className="campaign-dialog-check">
                <input type="checkbox" name="rotate_products"
                  defaultChecked={policy.rotate_products} />
                <span>
                  Take turns between products
                  <span className="campaign-dialog-hint">Each post takes the
                    best-fitting product that has not had its turn. Off means
                    the best fit wins every post, which is usually the same
                    one.</span>
                </span>
              </label>
              <label>Optimise for
                <Select name="priority" defaultValue={policy.priority}>
                  {PRIORITIES.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </Select>
                <small>Ranking uses an axis only once it has evidence.</small>
              </label>
            </>}
          </form>
        )}
      </Dialog>
      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
