"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { useLocale } from "../i18n-provider";
import { LOCALES } from "../../lib/i18n/locales";
import { apiBaseUrl } from "../../lib/api";
import { AutopilotPanel } from "./autopilot-panel";
import { StatusToasts, useStatus } from "../ui/status";
import { Badge } from "../ui/primitives";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { SearchSelect } from "../ui/search-select";
import { ActionIcon } from "../ui/action-icons";
import { handoffPath } from "../../lib/media-rules";
import {
  upcomingSlots,
  type Slot,
} from "../publish/composer";
import {
  platformLabels,
  type PublishingPlatform,
} from "../publishing-icons";

type Workspace = { id: string; name: string; role: string };
type Campaign = {
  id: string;
  name: string;
  objective: string;
  audience: string;
  markets: string[];
  languages: string[];
  affiliate_url?: string | null;
  status: "draft" | "active" | "archived";
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
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignId, setCampaignId] = useState("");
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
  const [offers, setOffers] = useState<CampaignOffer[]>([]);
  const [newCampaignOfferId, setNewCampaignOfferId] = useState("");
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);
  const selectedCampaign = campaigns.find((item) => item.id === campaignId);
  const canCreateCampaign = ["owner", "editor"].includes(selectedWorkspace?.role ?? "");
  const canCreatePlan = ["owner", "editor", "approver"].includes(selectedWorkspace?.role ?? "");
  const canApprove = ["owner", "approver"].includes(selectedWorkspace?.role ?? "");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  const refresh = useCallback(async (nextWorkspaceId: string) => {
    if (!nextWorkspaceId) return;
    const [campaignBody, calendarBody] = await Promise.all([
      json<{ campaigns: Campaign[] }>(
        await apiFetch(`/api/workspaces/${nextWorkspaceId}/campaigns`),
      ),
      json<{ plans: PublicationPlan[] }>(
        await apiFetch(`/api/workspaces/${nextWorkspaceId}/campaigns/calendar`),
      ),
    ]);
    setCampaigns(campaignBody.campaigns);
    setPlans(calendarBody.plans);
    setCampaignId((current) =>
      campaignBody.campaigns.some((item) => item.id === requestedCampaign.current)
        ? requestedCampaign.current
        : campaignBody.campaigns.some((item) => item.id === current)
        ? current
        : (campaignBody.campaigns[0]?.id ?? ""),
    );
  }, [apiFetch]);

  useEffect(() => {
    queueMicrotask(() => {
      const params = new URLSearchParams(window.location.search);
      requestedCampaign.current = params.get("campaign") ?? "";
    });
  }, []);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        if (cancelled) return;
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          fail(reason instanceof Error ? reason.message : "Could not load workspaces.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, user, fail]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    Promise.all([
      apiFetch(`/api/workspaces/${workspaceId}/campaigns`).then((response) =>
        json<{ campaigns: Campaign[] }>(response),
      ),
      apiFetch(`/api/workspaces/${workspaceId}/campaigns/calendar`).then((response) =>
        json<{ plans: PublicationPlan[] }>(response),
      ),
    ])
      .then(([campaignBody, calendarBody]) => {
        if (cancelled) return;
        setCampaigns(campaignBody.campaigns);
        setPlans(calendarBody.plans);
        setCampaignId((current) =>
          campaignBody.campaigns.some((item) => item.id === requestedCampaign.current)
            ? requestedCampaign.current
            : campaignBody.campaigns.some((item) => item.id === current)
            ? current
            : (campaignBody.campaigns[0]?.id ?? ""),
        );
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          fail(reason instanceof Error ? reason.message : "Could not load campaigns.");
        }
      });
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId, fail]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`)
      .then((response) => json<{ offers: CampaignOffer[] }>(response))
      .then((body) => { if (!cancelled) setOffers(body.offers ?? []); })
      .catch(() => { if (!cancelled) setOffers([]); });
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

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
            offer_id: newCampaignOfferId || null,
          }),
        }),
      );
      formElement.reset();
      await refresh(workspaceId);
      setCampaignId(body.campaign.id);
      setNewCampaignOpen(false);
      setNewCampaignOfferId("");
      succeed("Campaign created. Add approved media to its campaign queue.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Campaign creation failed.");
    } finally {
      setBusy(null);
    }
  }


  function openCampaignSettings(campaign: Campaign) {
    setSettingsFor(campaign);
  }

  async function saveCampaignSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!settingsFor) return;
    setBusy("settings");
    fail(null);
    succeed(null);
    try {
      const form = new FormData(event.currentTarget);
      await json<{ campaign: Campaign }>(
        await apiFetch(`/api/workspaces/${workspaceId}/campaigns/${settingsFor.id}`, {
          method: "POST",
          body: JSON.stringify({
            name: form.get("name"),
            objective: form.get("objective"),
            audience: form.get("audience"),
            languages: [form.get("language")].filter(Boolean),
          }),
        }),
      );
      await refresh(workspaceId);
      setSettingsFor(null);
      succeed("Campaign settings saved.");
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

  return (
    <main className="campaign-page">
      <header className="campaign-heading">
        <div>
          <p className="section-kicker">{t("campaigns.eyebrow")}</p>
          <h1>{t("campaigns.heading")}</h1>
          <p>{t("campaigns.intro")}</p>
        </div>
        <label className="workspace-control">
          Workspace
          <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name} / {workspace.role}
              </option>
            ))}
          </select>
        </label>
      </header>

      <section className="campaign-layout">
        <aside className="campaign-sidebar">
          <div className="card-heading">
            <div><p className="section-kicker">{t("campaigns.listHeading")}</p><h2>{campaigns.length} total</h2></div>
          </div>
          <div className="campaign-list">
            {campaigns.map((campaign) => (
              <button
                className={campaign.id === campaignId ? "selected" : ""}
                key={campaign.id}
                onClick={() => setCampaignId(campaign.id)}
                type="button"
              >
                <strong>{campaign.name}</strong>
                {/* The language it posts in, not the market it was never asked
                    for. Every campaign reported "global" once markets stopped
                    being collected, which is a word that told you nothing. */}
                <span>{campaign.status} · {
                  POST_LANGUAGES.find((item) => item.value === campaign.languages[0])?.label
                  ?? campaign.languages[0]
                  ?? "English"
                }</span>
              </button>
            ))}
            {!campaigns.length && <p>{t("campaigns.empty")}</p>}
          </div>
          {canCreateCampaign && (
            <Button variant="primary" onClick={() => setNewCampaignOpen(true)}>
              <ActionIcon name="add" />{t("campaigns.create")}
            </Button>
          )}
        </aside>

        <div className="campaign-workspace">
          {selectedCampaign ? (
            <>
              <section className="campaign-summary">
                <div>
                  <p className="section-kicker">{selectedCampaign.status}</p>
                  <h2>{selectedCampaign.name}</h2>
                  <p>{selectedCampaign.objective}</p>
                  <small>Audience: {selectedCampaign.audience}</small>
                </div>
                <div className="campaign-status-actions">
                  <Link href={`/attribution?campaign=${encodeURIComponent(selectedCampaign.id)}`}><ActionIcon name="link" />{t("campaigns.measureRevenue")}</Link>
                  {canCreateCampaign && selectedCampaign.status === "archived" && (
                    <Button variant="secondary" size="sm" onClick={() => void setCampaignStatus("draft")}><ActionIcon name="play" />Restore</Button>
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
                  <div>
                    <strong>Create a one-off post in Publish</strong>
                    <p>Publish owns one-time media, destination, post type, comments, replies, and scheduling. Campaigns keeps the recurring pipeline focused.</p>
                  </div>
                  <Link className="ui-button ui-button-secondary ui-button-sm"
                    href={`/publish?campaign=${encodeURIComponent(selectedCampaign.id)}`}>
                    Open Publish
                  </Link>
                </div>
            </>
          ) : (
            <section className="empty-console">
              <h2>{t("campaigns.createToStart")}</h2>
              <p>{t("campaigns.whatItConnects")}</p>
            </section>
          )}
        </div>
      </section>
      <Dialog
        open={newCampaignOpen}
        title={t("campaigns.create")}
        description="Set the campaign goal once. Media, accounts, schedule, and deployment come next in this workspace."
        onClose={() => setNewCampaignOpen(false)}
      >
        <form className="campaign-dialog-form" onSubmit={createCampaign}>
          <label>{t("campaigns.name")}<input name="name" required maxLength={160} autoFocus /></label>
          {/* Chosen rather than composed. Both of these are read by product
              matching, so what goes in them has to be a sentence about the
              campaign - which is a lot to ask of an empty textarea, and the
              reason most of them ended up thin. The list carries the phrasing;
              anything it does not cover is still typed. */}
          <div className="campaign-dialog-grid">
            <label>{t("campaigns.objective")}
              <select
                name={objectiveChoice ? "objective" : undefined}
                value={objectiveChoice}
                onChange={(event) => setObjectiveChoice(event.target.value)}
              >
                {CAMPAIGN_GOALS.map((goal) => <option key={goal} value={goal}>{goal}</option>)}
                <option value="">Something else…</option>
              </select>
              {!objectiveChoice && (
                <textarea name="objective" rows={2} required maxLength={1000}
                  placeholder="What should this campaign achieve?" />
              )}
            </label>
            <label>{t("campaigns.audience")}
              <select
                name={audienceChoice ? "audience" : undefined}
                value={audienceChoice}
                onChange={(event) => setAudienceChoice(event.target.value)}
              >
                {CAMPAIGN_AUDIENCES.map((who) => <option key={who} value={who}>{who}</option>)}
                <option value="">Something else…</option>
              </select>
              {!audienceChoice && (
                <textarea name="audience" rows={2} required maxLength={1000}
                  placeholder="Who are these posts for?" />
              )}
            </label>
          </div>
          <label>{t("campaigns.postLanguage")}
            <select name="language" defaultValue={POST_LANGUAGES.some((item) => item.value === locale) ? locale : "en"}>
              {POST_LANGUAGES.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
            <small>The language the disclosure, bio hint and product labels are written in. Your own copy is always your own.</small>
          </label>
          <label>Affiliate offer from Attribution
            <SearchSelect
              value={newCampaignOfferId}
              options={offers.map((offer) => ({
                value: offer.id,
                label: offer.product.name,
                description: offerDescription(offer),
                keywords: `${offer.product.brand ?? ""} ${offer.product.marketplace ?? ""} ${offer.network} ${offer.affiliate_url}`,
              }))}
              onChange={setNewCampaignOfferId}
              placeholder="Let smart matching choose"
              searchPlaceholder="Search imported offers…"
              emptyLabel="No offers imported in Attribution"
            />
            <small>Optional. Pin one product to every post, or leave this clear for automatic matching from Attribution.</small>
          </label>
          <div className="campaign-dialog-actions">
            <Button type="button" variant="quiet" onClick={() => setNewCampaignOpen(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={busy === "campaign"}>{t("campaigns.createButton")}</Button>
          </div>
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
      >
        {settingsFor && (
          <form className="campaign-dialog-form" onSubmit={saveCampaignSettings}>
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
              <select name="language" defaultValue={settingsFor.languages[0] ?? "en"}>
                {POST_LANGUAGES.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
              <small>Changes the disclosure and bio hint too, unless you have written your own.</small>
            </label>
            <div className="campaign-dialog-actions">
              <Button type="button" variant="quiet" onClick={() => setSettingsFor(null)}>{t("common.cancel")}</Button>
              <Button type="submit" variant="primary" busy={busy === "settings"}>{t("common.save")}</Button>
            </div>
          </form>
        )}
      </Dialog>
      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
