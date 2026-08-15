"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { useT } from "../i18n-provider";
import { AutopilotPanel } from "./autopilot-panel";
import { StatusToasts, useStatus } from "../ui/status";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { SearchSelect } from "../ui/search-select";
import { ActionIcon } from "../ui/action-icons";
import { clipLength, handoffPath, type AssetVersion } from "../../lib/media-rules";
import { upcomingSlots, type Slot } from "../publish/composer";
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
type ManualPackage = {
  path: string;
  folder: string;
  bytes: number;
  sha256: string;
  manifest: {
    caption: string;
    hashtags: string[];
    affiliate_url?: string | null;
    disclosure: string;
    deep_link?: string | null;
    scheduled_at: string;
    timezone: string;
  };
};
type LibraryClip = {
  id: string;
  title: string;
  original_path: string;
  media_kind: string;
  duration_ms: number | null;
  versions: AssetVersion[];
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

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Campaign request failed.");
  return body;
}

function values(input: FormDataEntryValue | null): string[] {
  return String(input ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function destinationKey(account: Pick<ConnectedAccount, "provider" | "id">): string {
  return `${account.provider}\u001f${account.id}`;
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

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function CampaignsPage() {
  const t = useT();
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignId, setCampaignId] = useState("");
  const requestedCampaign = useRef("");
  const [plans, setPlans] = useState<PublicationPlan[]>([]);
  const [videoPath, setVideoPath] = useState("");
  const [planClips, setPlanClips] = useState<LibraryClip[]>([]);
  const [planClip, setPlanClip] = useState<LibraryClip | null>(null);
  const [planPickerOpen, setPlanPickerOpen] = useState(false);
  const [packages, setPackages] = useState<Record<string, ManualPackage>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  const [offers, setOffers] = useState<CampaignOffer[]>([]);
  const [newCampaignOfferId, setNewCampaignOfferId] = useState("");
  const [planAccounts, setPlanAccounts] = useState<ConnectedAccount[]>([]);
  const [planAccountKey, setPlanAccountKey] = useState("");
  const [planOfferId, setPlanOfferId] = useState("");
  const [planSlotOptions, setPlanSlotOptions] = useState<
    { value: string; label: string; day: string }[]
  >([]);
  const [planScheduledAt, setPlanScheduledAt] = useState("");
  const [planSourcesCampaign, setPlanSourcesCampaign] = useState("");
  const [planSourcesLoading, setPlanSourcesLoading] = useState(false);
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);
  const selectedCampaign = campaigns.find((item) => item.id === campaignId);
  const canCreateCampaign = ["owner", "editor"].includes(selectedWorkspace?.role ?? "");
  const canCreatePlan = ["owner", "editor", "approver"].includes(selectedWorkspace?.role ?? "");
  const canApprove = ["owner", "approver"].includes(selectedWorkspace?.role ?? "");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const selectedPlanAccount = planAccounts.find(
    (account) => destinationKey(account) === planAccountKey,
  ) ?? null;

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
      setVideoPath(params.get("video") ?? "");
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
            markets: values(form.get("markets")),
            languages: values(form.get("languages")),
            offer_id: newCampaignOfferId || null,
          }),
        }),
      );
      formElement.reset();
      await refresh(workspaceId);
      setCampaignId(body.campaign.id);
      setNewCampaignOpen(false);
      setNewCampaignOfferId("");
      succeed("Campaign created. Add its first publication plan.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Campaign creation failed.");
    } finally {
      setBusy(null);
    }
  }

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!campaignId) return;
    setBusy("plan");
    fail(null);
    succeed(null);
    try {
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      await json(
        await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${campaignId}/plans`,
          {
            method: "POST",
            body: JSON.stringify({
              title: form.get("title"),
              platform: form.get("platform"),
              provider: form.get("provider") || null,
              integration_id: form.get("integration_id") || null,
              destination_label: form.get("destination_label") || null,
              video_path: form.get("video_path"),
              cover_path: form.get("cover_path") || null,
              caption: form.get("caption"),
              hashtags: values(form.get("hashtags")),
              offer_id: form.get("offer_id") || null,
              disclosure: form.get("disclosure"),
              scheduled_at: new Date(String(form.get("scheduled_at"))).toISOString(),
              timezone,
            }),
          },
        ),
      );
      setVideoPath("");
      setPlanClip(null);
      formElement.reset();
      await refresh(workspaceId);
      succeed("Publication plan is ready for owner or approver review.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Publication plan failed.");
    } finally {
      setBusy(null);
    }
  }

  async function choosePlanMedia() {
    setBusy("plan-library");
    try {
      const body = await json<{ assets: LibraryClip[] }>(await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets?media_kind=video&limit=40`,
      ));
      setPlanClips(body.assets ?? []);
      setPlanPickerOpen(true);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The Library could not be opened.");
    } finally {
      setBusy(null);
    }
  }

  async function loadPlanSources({ force = false } = {}) {
    if (!workspaceId || !campaignId || planSourcesLoading) return;
    if (!force && planSourcesCampaign === campaignId) return;
    setPlanSourcesLoading(true);
    try {
      const [accountBody, autopilotBody, slotBody, offerBody] = await Promise.all([
        json<{ accounts: ConnectedAccount[] }>(await apiFetch(
          `/api/workspaces/${workspaceId}/publishing/integrations/all`,
          { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
        )),
        json<{
          autopilot: { offer_id: string | null };
          destinations: CampaignDestination[];
        }>(await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${campaignId}/autopilot`,
        )),
        json<{ slots: Slot[] }>(await apiFetch(
          `/api/workspaces/${workspaceId}/publishing/slots`,
        )),
        json<{ offers: CampaignOffer[] }>(await apiFetch(
          `/api/workspaces/${workspaceId}/opportunities/offers`,
        )),
      ]);
      const available = accountBody.accounts.filter((account) => account.available !== false);
      const assigned = autopilotBody.destinations
        .filter((destination) => destination.enabled)
        .map((destination) => available.find((account) => (
          account.provider === destination.provider
          && account.id === destination.integration_id
        )))
        .filter((account): account is ConnectedAccount => Boolean(account));
      const preferredAccount = assigned[0] ?? (available.length === 1 ? available[0] : null);
      const slotOptions = upcomingSlots(slotBody.slots, new Date(), 12);
      const campaignOffer = offerBody.offers.find(
        (offer) => offer.affiliate_url === selectedCampaign?.affiliate_url,
      );
      setPlanAccounts(available);
      setPlanAccountKey(preferredAccount ? destinationKey(preferredAccount) : "");
      setOffers(offerBody.offers);
      setPlanOfferId(autopilotBody.autopilot.offer_id ?? campaignOffer?.id ?? "");
      setPlanSlotOptions(slotOptions);
      setPlanScheduledAt(slotOptions[0]?.value ?? "");
      setPlanSourcesCampaign(campaignId);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Prepared campaign sources could not be read.");
    } finally {
      setPlanSourcesLoading(false);
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

  async function decide(plan: PublicationPlan, decision: "approve" | "reject") {
    if (!window.confirm(`${decision === "approve" ? "Approve" : "Reject"} “${plan.title}”?`)) {
      return;
    }
    setBusy(plan.id);
    fail(null);
    try {
      await json(
        await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${plan.campaign_id}/plans/`
          + `${plan.id}/decision`,
          { method: "POST", body: JSON.stringify({ decision }) },
        ),
      );
      await refresh(workspaceId);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Approval failed.");
    } finally {
      setBusy(null);
    }
  }

  async function exportPackage(plan: PublicationPlan) {
    if (!window.confirm(`Build a local manual posting package for “${plan.title}”?`)) return;
    setBusy(`package-${plan.id}`);
    fail(null);
    try {
      const body = await json<{ package: ManualPackage }>(
        await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${plan.campaign_id}/plans/`
          + `${plan.id}/manual-package`,
          {
            method: "POST",
            body: JSON.stringify({ confirm_external_action: true }),
          },
        ),
      );
      setPackages((current) => ({ ...current, [plan.id]: body.package }));
      succeed("Manual posting package created locally.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Package export failed.");
    } finally {
      setBusy(null);
    }
  }

  async function openFolder(folder: string) {
    try {
      await json(
        await apiFetch("/api/tools/open-folder", {
          method: "POST",
          body: JSON.stringify({ path: folder }),
        }),
      );
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Could not open package folder.");
    }
  }

  async function copyPostingText(plan: PublicationPlan) {
    const text = [
      plan.caption,
      plan.hashtags.map((tag) => `#${tag}`).join(" "),
      plan.disclosure,
      plan.affiliate_url ?? "",
    ].filter(Boolean).join("\n\n");
    await navigator.clipboard.writeText(text);
    succeed("Caption, hashtags, disclosure, and link copied.");
  }

  if (loading) {
    return <main className="campaign-page"><div className="loading-panel">{t("campaigns.loading")}</div></main>;
  }
  if (!user) {
    return <main className="campaign-page"><Link href="/sign-in?next=%2Fcampaigns">{t("campaigns.signInPrompt")}</Link></main>;
  }

  const visiblePlans = plans.filter((plan) => !campaignId || plan.campaign_id === campaignId);

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
                <span>{campaign.status} · {campaign.markets.join(", ") || "global"}</span>
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
                  {canCreateCampaign && selectedCampaign.status !== "active" && (
                    <Button variant="secondary" size="sm" onClick={() => void setCampaignStatus("active")}><ActionIcon name="play" />{t("campaigns.activate")}</Button>
                  )}
                  {canCreateCampaign && selectedCampaign.status !== "archived" && (
                    <Button variant="secondary" size="sm" onClick={() => void setCampaignStatus("archived")}><ActionIcon name="archive" />{t("campaigns.archive")}</Button>
                  )}
                </div>
              </section>

              {/* Between the campaign and its one-off plans: this is how the
                  campaign actually runs, and the plans below it are the manual
                  exception rather than the norm. */}
              <AutopilotPanel
                key={selectedCampaign.id}
                workspaceId={workspaceId}
                campaignId={selectedCampaign.id}
                campaignStatus={selectedCampaign.status}
                canEdit={Boolean(canCreatePlan)}
                apiFetch={apiFetch}
                succeed={succeed}
                fail={fail}
              />

              <details key={workspaceId} className="campaign-manual-work" onToggle={(event) => {
                if (event.currentTarget.open) void loadPlanSources();
              }}>
                <summary>
                  <span>One-off approvals</span>
                  <small>{visiblePlans.length} planned posts · advanced workflow</small>
                </summary>
              {canCreatePlan && selectedCampaign.status !== "archived" && (
                <details className="plan-create" open={visiblePlans.length === 0}>
                  <summary>{t("campaigns.addPlan")}</summary>
                  <form key={selectedCampaign.id} onSubmit={createPlan}>
                    <div className="plan-form-grid">
                      <label>{t("publish.title")}<input name="title" required maxLength={200} /></label>
                      <label>Destination account from Publish
                        <SearchSelect
                          value={planAccountKey}
                          options={planAccounts.map((account) => ({
                            value: destinationKey(account),
                            label: account.label,
                            description: `${platformLabels[account.platform]} · ${account.provider_label}`,
                            keywords: `${account.platform} ${account.provider} ${account.provider_label}`,
                          }))}
                          onChange={setPlanAccountKey}
                          placeholder={planSourcesLoading ? "Reading Publish accounts…" : "Choose a connected account"}
                          searchPlaceholder="Search accounts or platforms…"
                          emptyLabel="No connected Publish accounts"
                          disabled={planSourcesLoading || !planAccounts.length}
                        />
                        <small>The exact account and publishing engine are carried into Publish.</small>
                      </label>
                      <label>{t("campaigns.suggestedTime")}
                        <select name="scheduled_at" value={planScheduledAt}
                          onChange={(event) => setPlanScheduledAt(event.target.value)}
                          disabled={planSourcesLoading || !planSlotOptions.length} required>
                          <option value="" disabled>
                            {planSourcesLoading ? "Reading saved posting times…" : "Choose a saved posting time"}
                          </option>
                          {planSlotOptions.map((slot) => (
                            <option key={slot.value} value={slot.value}>{slot.day} · {slot.label}</option>
                          ))}
                        </select>
                        <small>{planSlotOptions.length
                          ? `From the posting times saved in Publish · ${timezone}`
                          : "No posting times are saved. Add one in Publish → Schedule."}</small>
                      </label>
                    </div>
                    <input type="hidden" name="platform" value={selectedPlanAccount?.platform ?? ""} />
                    <input type="hidden" name="provider" value={selectedPlanAccount?.provider ?? ""} />
                    <input type="hidden" name="integration_id" value={selectedPlanAccount?.id ?? ""} />
                    <input type="hidden" name="destination_label" value={selectedPlanAccount?.label ?? ""} />
                    <div className="plan-media-field">
                      <span>Media from Library</span>
                      <input type="hidden" name="video_path" value={videoPath} />
                      <div>
                        <strong>{planClip?.title ?? (videoPath ? "Library handoff" : "No clip selected")}</strong>
                        <Button type="button" variant="secondary" size="sm"
                          busy={busy === "plan-library"} onClick={() => void choosePlanMedia()}>
                          <ActionIcon name="clip" />Choose from Library
                        </Button>
                      </div>
                      {planClip && <small>{clipLength(planClip.duration_ms) || "video"} · {planClip.versions.some((version) => ["blurred", "edited"].includes(version.kind)) ? "edited cut" : "original"}</small>}
                    </div>
                    {planPickerOpen && (
                      <div className="plan-media-picker">
                        <div className="autopilot-picker-head"><strong>Choose an approved clip</strong><Button type="button" variant="quiet" size="sm" onClick={() => setPlanPickerOpen(false)}>Close</Button></div>
                        <ul>
                          {planClips.map((asset) => <li key={asset.id}>
                            <span><strong>{asset.title}</strong><small>{clipLength(asset.duration_ms) || "video"}</small></span>
                            <Button type="button" variant="quiet" size="sm" onClick={() => {
                              setPlanClip(asset);
                              setVideoPath(handoffPath(asset));
                              setPlanPickerOpen(false);
                            }}>Select</Button>
                          </li>)}
                          {!planClips.length && <li>No video clips are ready in the Library.</li>}
                        </ul>
                      </div>
                    )}
                    <input type="hidden" name="cover_path" value="" />
                    <label>{t("publish.caption")}<textarea name="caption" rows={5} required /></label>
                    <div className="plan-form-grid">
                      <label>{t("library.hashtags")}<input name="hashtags" placeholder="travel, espresso" /></label>
                      <label>Affiliate offer from Attribution
                        <SearchSelect
                          value={planOfferId}
                          options={offers.map((offer) => ({
                            value: offer.id,
                            label: offer.product.name,
                            description: offerDescription(offer),
                            keywords: `${offer.product.brand ?? ""} ${offer.product.marketplace ?? ""} ${offer.network} ${offer.affiliate_url}`,
                          }))}
                          onChange={setPlanOfferId}
                          placeholder="No affiliate offer"
                          searchPlaceholder="Search Attribution offers…"
                          emptyLabel="No offers imported in Attribution"
                          disabled={planSourcesLoading}
                        />
                        <input type="hidden" name="offer_id" value={planOfferId} />
                        <small>The tracked link is copied from the selected offer; there is no URL to retype.</small>
                      </label>
                      <label>{t("publish.disclosure")}<input name="disclosure" defaultValue="#ad" required /></label>
                    </div>
                    <p className="campaign-source-note">
                      Library media · Publish account · saved posting time · Attribution offer. New plans require owner or approver review.
                    </p>
                    <div className="campaign-plan-actions">
                      <Button type="button" variant="quiet" size="sm"
                        busy={planSourcesLoading} onClick={() => void loadPlanSources({ force: true })}>
                        <ActionIcon name="refresh" />Refresh connected sources
                      </Button>
                      <Button type="submit" variant="primary" busy={busy === "plan"}
                        disabled={!videoPath || !selectedPlanAccount || !planScheduledAt}>
                        {t("publish.sendForApproval")}
                      </Button>
                    </div>
                  </form>
                </details>
              )}

              <section className="calendar-board">
                <div className="card-heading">
                  <div><p className="section-kicker">{t("campaigns.calendar")}</p><h2>{visiblePlans.length} planned posts</h2></div>
                </div>
                {!visiblePlans.length && <div className="quiet-empty"><strong>{t("campaigns.noPlans")}</strong><span>{t("campaigns.addApprovedMedia")}</span></div>}
                {visiblePlans.map((plan) => {
                  const manualPackage = packages[plan.id];
                  return (
                    <article className="calendar-entry" key={plan.id}>
                      <time>
                        <strong>{new Date(plan.scheduled_at).toLocaleDateString([], { month: "short", day: "numeric" })}</strong>
                        <span>{new Date(plan.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: plan.timezone })}</span>
                      </time>
                      <div className="calendar-copy">
                        <div>
                          <span className={`plan-state ${plan.state}`}>{plan.state.replace("_", " ")}</span>
                          <span>{plan.destination_label ?? planPlatformLabel(plan.platform)}</span>
                          {plan.destination_label && <small>{planPlatformLabel(plan.platform)}{plan.provider ? ` · ${plan.provider}` : ""}</small>}
                        </div>
                        <h3>{plan.title}</h3>
                        <p>{plan.caption}</p>
                        <small>{plan.video_path}</small>
                        <div className="calendar-actions">
                          {plan.state === "needs_approval" && canApprove && (
                            <>
                              <Button variant="primary" size="sm" busy={busy === plan.id} onClick={() => void decide(plan, "approve")}>{t("campaigns.approve")}</Button>
                              <Button variant="danger" size="sm" busy={busy === plan.id} onClick={() => void decide(plan, "reject")}>{t("campaigns.reject")}</Button>
                            </>
                          )}
                          {plan.state === "approved" && (
                            <>
                              <Button variant="quiet" size="sm" onClick={() => void copyPostingText(plan)}>{t("campaigns.copyPost")}</Button>
                              <Button variant="quiet" size="sm" busy={busy === `package-${plan.id}`} onClick={() => void exportPackage(plan)}>{t("campaigns.exportPackage")}</Button>
                              <Link href={`/publish?campaign=${encodeURIComponent(plan.campaign_id)}&plan=${encodeURIComponent(plan.id)}`}>{t("nav.publish")}</Link>
                              {plan.deep_link && <a href={plan.deep_link} target="_blank" rel="noreferrer">Open {plan.platform}</a>}
                            </>
                          )}
                        </div>
                        {manualPackage && (
                          <div className="package-result">
                            <div><strong>{manualPackage.path}</strong><small>{size(manualPackage.bytes)} · SHA-256 {manualPackage.sha256.slice(0, 12)}</small></div>
                            <Button variant="quiet" size="sm" onClick={() => void openFolder(manualPackage.folder)}>{t("downloads.openFolder")}</Button>
                          </div>
                        )}
                      </div>
                    </article>
                  );
                })}
              </section>
              </details>
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
          <div className="campaign-dialog-grid">
            <label>{t("campaigns.objective")}<textarea name="objective" rows={3} required /></label>
            <label>{t("campaigns.audience")}<textarea name="audience" rows={3} required /></label>
          </div>
          <div className="campaign-dialog-grid">
            <label>{t("campaigns.markets")}<input name="markets" placeholder="TH, US" /></label>
            <label>{t("campaigns.languages")}<input name="languages" placeholder="en, th" /></label>
          </div>
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
              placeholder="No default affiliate offer"
              searchPlaceholder="Search imported offers…"
              emptyLabel="No offers imported in Attribution"
            />
            <small>Optional. Imported and managed in Attribution; the tracked link is filled automatically.</small>
          </label>
          <div className="campaign-dialog-actions">
            <Button type="button" variant="quiet" onClick={() => setNewCampaignOpen(false)}>Cancel</Button>
            <Button type="submit" variant="primary" busy={busy === "campaign"}>{t("campaigns.createButton")}</Button>
          </div>
        </form>
      </Dialog>
      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
