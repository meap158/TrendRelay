"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";

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
  platform: "tiktok" | "instagram" | "youtube" | "douyin" | "other";
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

function localDateDefault(): string {
  const date = new Date(Date.now() + 24 * 60 * 60 * 1000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function CampaignsPage() {
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignId, setCampaignId] = useState("");
  const [plans, setPlans] = useState<PublicationPlan[]>([]);
  const [videoPath, setVideoPath] = useState("");
  const [packages, setPackages] = useState<Record<string, ManualPackage>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

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
      campaignBody.campaigns.some((item) => item.id === current)
        ? current
        : (campaignBody.campaigns[0]?.id ?? ""),
    );
  }, [apiFetch]);

  useEffect(() => {
    queueMicrotask(() => {
      const params = new URLSearchParams(window.location.search);
      setVideoPath(params.get("video") ?? "");
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
          setError(reason instanceof Error ? reason.message : "Could not load workspaces.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, user]);

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
          campaignBody.campaigns.some((item) => item.id === current)
            ? current
            : (campaignBody.campaigns[0]?.id ?? ""),
        );
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load campaigns.");
        }
      });
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  async function createCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("campaign");
    setError(null);
    setMessage(null);
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
            affiliate_url: form.get("affiliate_url") || null,
          }),
        }),
      );
      formElement.reset();
      await refresh(workspaceId);
      setCampaignId(body.campaign.id);
      setMessage("Campaign created. Add its first publication plan.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Campaign creation failed.");
    } finally {
      setBusy(null);
    }
  }

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!campaignId) return;
    setBusy("plan");
    setError(null);
    setMessage(null);
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
              video_path: form.get("video_path"),
              cover_path: form.get("cover_path") || null,
              caption: form.get("caption"),
              hashtags: values(form.get("hashtags")),
              affiliate_url: form.get("affiliate_url") || null,
              disclosure: form.get("disclosure"),
              scheduled_at: new Date(String(form.get("scheduled_at"))).toISOString(),
              timezone,
            }),
          },
        ),
      );
      setVideoPath("");
      formElement.reset();
      await refresh(workspaceId);
      setMessage("Publication plan is ready for owner or approver review.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Publication plan failed.");
    } finally {
      setBusy(null);
    }
  }

  async function setCampaignStatus(status: Campaign["status"]) {
    if (!campaignId) return;
    setBusy(`campaign-${status}`);
    setError(null);
    try {
      await json(
        await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${campaignId}/status`,
          { method: "POST", body: JSON.stringify({ status }) },
        ),
      );
      await refresh(workspaceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Campaign status update failed.");
    } finally {
      setBusy(null);
    }
  }

  async function decide(plan: PublicationPlan, decision: "approve" | "reject") {
    if (!window.confirm(`${decision === "approve" ? "Approve" : "Reject"} “${plan.title}”?`)) {
      return;
    }
    setBusy(plan.id);
    setError(null);
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
      setError(reason instanceof Error ? reason.message : "Approval failed.");
    } finally {
      setBusy(null);
    }
  }

  async function exportPackage(plan: PublicationPlan) {
    if (!window.confirm(`Build a local manual posting package for “${plan.title}”?`)) return;
    setBusy(`package-${plan.id}`);
    setError(null);
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
      setMessage("Manual posting package created locally.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Package export failed.");
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
      setError(reason instanceof Error ? reason.message : "Could not open package folder.");
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
    setMessage("Caption, hashtags, disclosure, and link copied.");
  }

  if (loading) {
    return <main className="campaign-page"><div className="loading-panel">Loading campaigns…</div></main>;
  }
  if (!user) {
    return <main className="campaign-page"><Link href="/sign-in?next=%2Fcampaigns">Sign in to manage campaigns</Link></main>;
  }

  const visiblePlans = plans.filter((plan) => !campaignId || plan.campaign_id === campaignId);

  return (
    <main className="campaign-page">
      <header className="campaign-heading">
        <div>
          <p className="section-kicker">CAMPAIGN OPERATIONS</p>
          <h1>Plan once. Approve once. Publish anywhere.</h1>
          <p>Connect strategy, approved media, posting time, affiliate disclosure, and fallback delivery.</p>
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
      {error && <p className="inline-error" role="alert">{error}</p>}
      {message && <p className="campaign-message" role="status">{message}</p>}

      <section className="campaign-layout">
        <aside className="campaign-sidebar">
          <div className="card-heading">
            <div><p className="section-kicker">CAMPAIGNS</p><h2>{campaigns.length} total</h2></div>
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
            {!campaigns.length && <p>No campaigns yet.</p>}
          </div>
          {canCreateCampaign && (
            <details className="campaign-create">
              <summary>New campaign</summary>
              <form onSubmit={createCampaign}>
                <label>Name<input name="name" required maxLength={160} /></label>
                <label>Objective<textarea name="objective" rows={3} required /></label>
                <label>Audience<textarea name="audience" rows={3} required /></label>
                <label>Markets<input name="markets" placeholder="TH, US" /></label>
                <label>Languages<input name="languages" placeholder="en, th" /></label>
                <label>Affiliate URL<input name="affiliate_url" type="url" /></label>
                <button className="primary-button" disabled={busy === "campaign"}>Create campaign</button>
              </form>
            </details>
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
                  <Link href={`/attribution?campaign=${encodeURIComponent(selectedCampaign.id)}`}>Measure revenue</Link>
                  {canCreateCampaign && selectedCampaign.status !== "active" && (
                    <button onClick={() => void setCampaignStatus("active")}>Activate</button>
                  )}
                  {canCreateCampaign && selectedCampaign.status !== "archived" && (
                    <button onClick={() => void setCampaignStatus("archived")}>Archive</button>
                  )}
                </div>
              </section>

              {canCreatePlan && selectedCampaign.status !== "archived" && (
                <details className="plan-create" open={visiblePlans.length === 0}>
                  <summary>Add publication plan</summary>
                  <form key={selectedCampaign.id} onSubmit={createPlan}>
                    <div className="plan-form-grid">
                      <label>Title<input name="title" required maxLength={200} /></label>
                      <label>Platform<select name="platform" defaultValue="tiktok"><option>tiktok</option><option>instagram</option><option>youtube</option><option>douyin</option><option>other</option></select></label>
                      <label>Suggested time<input name="scheduled_at" type="datetime-local" defaultValue={localDateDefault()} required /></label>
                    </div>
                    <label>Approved MP4 path<input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} required /></label>
                    <label>Optional cover path<input name="cover_path" /></label>
                    <label>Caption<textarea name="caption" rows={5} required /></label>
                    <div className="plan-form-grid">
                      <label>Hashtags<input name="hashtags" placeholder="travel, espresso" /></label>
                      <label>Affiliate URL<input name="affiliate_url" type="url" defaultValue={selectedCampaign.affiliate_url ?? ""} /></label>
                      <label>Disclosure<input name="disclosure" defaultValue="#ad" required /></label>
                    </div>
                    <small>Times use {timezone}. New plans require owner or approver review.</small>
                    <button className="primary-button" disabled={busy === "plan"}>Send for approval</button>
                  </form>
                </details>
              )}

              <section className="calendar-board">
                <div className="card-heading">
                  <div><p className="section-kicker">CONTENT CALENDAR</p><h2>{visiblePlans.length} planned posts</h2></div>
                </div>
                {!visiblePlans.length && <div className="quiet-empty"><strong>No publication plans</strong><span>Add approved media and a posting time.</span></div>}
                {visiblePlans.map((plan) => {
                  const manualPackage = packages[plan.id];
                  return (
                    <article className="calendar-entry" key={plan.id}>
                      <time>
                        <strong>{new Date(plan.scheduled_at).toLocaleDateString([], { month: "short", day: "numeric" })}</strong>
                        <span>{new Date(plan.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: plan.timezone })}</span>
                      </time>
                      <div className="calendar-copy">
                        <div><span className={`plan-state ${plan.state}`}>{plan.state.replace("_", " ")}</span><span>{plan.platform}</span></div>
                        <h3>{plan.title}</h3>
                        <p>{plan.caption}</p>
                        <small>{plan.video_path}</small>
                        <div className="calendar-actions">
                          {plan.state === "needs_approval" && canApprove && (
                            <>
                              <button disabled={busy === plan.id} onClick={() => void decide(plan, "approve")}>Approve</button>
                              <button disabled={busy === plan.id} onClick={() => void decide(plan, "reject")}>Reject</button>
                            </>
                          )}
                          {plan.state === "approved" && (
                            <>
                              <button onClick={() => void copyPostingText(plan)}>Copy post</button>
                              <button disabled={busy === `package-${plan.id}`} onClick={() => void exportPackage(plan)}>Export package</button>
                              <Link href={`/publish?video=${encodeURIComponent(plan.video_path)}`}>Use Postiz</Link>
                              {plan.deep_link && <a href={plan.deep_link} target="_blank" rel="noreferrer">Open {plan.platform}</a>}
                            </>
                          )}
                        </div>
                        {manualPackage && (
                          <div className="package-result">
                            <div><strong>{manualPackage.path}</strong><small>{size(manualPackage.bytes)} · SHA-256 {manualPackage.sha256.slice(0, 12)}</small></div>
                            <button onClick={() => void openFolder(manualPackage.folder)}>Open folder</button>
                          </div>
                        )}
                      </div>
                    </article>
                  );
                })}
              </section>
            </>
          ) : (
            <section className="empty-console">
              <h2>Create a campaign to start the calendar.</h2>
              <p>A campaign connects the objective, audience, affiliate destination, approved media, and publication plan.</p>
            </section>
          )}
        </div>
      </section>
    </main>
  );
}
