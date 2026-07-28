"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { useJobs } from "../jobs-provider";

type Workspace = { id: string; name: string; role: string };
type Platform = "tiktok" | "instagram" | "youtube";
type Account = { id: string; label: string; platform: Platform };
type Connection = {
  provider_installed: boolean;
  provider_active: boolean;
  authenticated: boolean;
  authentication_method: string | null;
  next_step: string;
};

const platforms: Platform[] = ["tiktok", "instagram", "youtube"];

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Publishing request failed.");
  return body;
}

function platformName(platform: Platform) {
  return platform === "tiktok" ? "TikTok" : platform === "youtube" ? "YouTube" : "Instagram";
}

export default function PublishPage() {
  const { loading, user, apiFetch } = useAuth();
  const { jobs: allJobs, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [videoPath, setVideoPath] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [targets, setTargets] = useState<Record<Platform, string>>({ tiktok: "", instagram: "", youtube: "" });
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = workspaces.find((workspace) => workspace.id === workspaceId);
  const canExecute = selected?.role === "owner" || selected?.role === "approver";
  const jobs = allJobs.filter((job) => job.category === "publish").map((job) => job.raw);

  useEffect(() => {
    queueMicrotask(() => setVideoPath(new URLSearchParams(window.location.search).get("video") ?? ""));
  }, []);

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    if (!user) return;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load workspaces."));
  }, [apiFetch, user]);

  async function refreshConnection() {
    if (!workspaceId) return;
    const body = await json<{ connection: Connection }>(await apiFetch(`/api/workspaces/${workspaceId}/publishing/postiz/connection`));
    setConnection(body.connection);
  }

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/publishing/postiz/connection`)
      .then((response) => json<{ connection: Connection }>(response))
      .then((body) => { if (!cancelled) setConnection(body.connection); })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not check Postiz setup.");
      });
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  function requestFrom(form: FormData, confirm: boolean) {
    const selectedTargets = platforms
      .filter((platform) => targets[platform])
      .map((platform) => ({ platform, integration_id: targets[platform] }));
    if (!selectedTargets.length) throw new Error("Select at least one connected social account.");
    const localDate = String(form.get("date"));
    if (!localDate) throw new Error("Choose a date and time.");
    return {
      workspace_id: workspaceId,
      video_path: form.get("video_path"),
      caption: form.get("caption"),
      title: form.get("title") || null,
      date: new Date(localDate).toISOString(),
      schedule: form.get("schedule") === "on",
      made_with_ai: form.get("made_with_ai") === "on",
      targets: selectedTargets,
      confirm_external_action: confirm,
    };
  }

  async function refreshAccounts() {
    if (!workspaceId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await json<{ accounts: Account[] }>(await apiFetch(
        `/api/workspaces/${workspaceId}/publishing/postiz/integrations`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      ));
      setAccounts(result.accounts);
      setTargets((current) => Object.fromEntries(platforms.map((platform) => [
        platform,
        result.accounts.some((account) => account.id === current[platform]) ? current[platform] : "",
      ])) as Record<Platform, string>);
      setNotice(result.accounts.length ? "Connected accounts refreshed. Choose where this video should go." : "No supported accounts found yet. Connect TikTok, Instagram, or YouTube in Postiz, then refresh.");
      await refreshConnection();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not refresh connected accounts.");
    } finally {
      setBusy(false);
    }
  }

  async function startSetup(action: "launch-auth" | "open-dashboard") {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await json<{ result: { message: string } }>(await apiFetch(`/api/tools/postiz-agent/setup/${action}`, {
        method: "POST",
        body: JSON.stringify({ confirm_external_action: true }),
      }));
      setNotice(result.result.message);
      await refreshConnection();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start Postiz setup.");
    } finally {
      setBusy(false);
    }
  }

  async function submit(formElement: HTMLFormElement, execute: boolean) {
    if (!workspaceId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const body = requestFrom(new FormData(formElement), execute);
      if (execute) {
        await json(await apiFetch(`/api/workspaces/${workspaceId}/publishing/postiz/jobs`, { method: "POST", body: JSON.stringify(body) }));
        await refreshJobs();
        setNotice("Postiz job created. Track its status below or from Jobs.");
      } else {
        const result = await json<{ preview: Record<string, unknown> }>(await apiFetch(
          `/api/workspaces/${workspaceId}/publishing/postiz/preview`,
          { method: "POST", body: JSON.stringify(body) },
        ));
        setPreview(result.preview);
        setNotice("Dry-run ready. Review the delivery plan before creating the Postiz draft or schedule.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Publishing request failed.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <main className="publish-page"><p>Checking your session...</p></main>;
  if (!user) return <main className="publish-page"><Link className="primary-link" href="/sign-in?next=%2Fpublish">Sign in to publish</Link></main>;

  return (
    <main className="publish-page">
      <header className="publish-heading">
        <div><p className="eyebrow">DISTRIBUTION DESK</p><h1>Publish the approved clip.</h1><p className="lede">Connect social accounts once in Postiz. Then choose them by name, preview the delivery, and create a draft or schedule.</p></div>
        <Link className="secondary-link" href="/tools">Manage tools</Link>
      </header>
      <div aria-live="polite">{notice && <p className="registry-message">{notice}</p>}{error && <p className="registry-error" role="alert">{error}</p>}</div>

      <section className="postiz-setup" aria-labelledby="postiz-setup-title">
        <div className="section-heading"><div><p className="eyebrow">POSTIZ CONNECTION</p><h2 id="postiz-setup-title">Set up once, publish without account IDs.</h2></div><span className={connection?.authenticated ? "connection-badge ready" : "connection-badge"}>{connection?.authenticated ? "Authorized" : "Setup needed"}</span></div>
        <div className="postiz-steps">
          <article className={connection?.provider_installed && connection?.provider_active ? "ready" : ""}><span>01</span><div><strong>Enable Postiz</strong><p>Install and activate the Postiz tool for this workspace.</p></div>{connection?.provider_installed && connection?.provider_active ? <small>Ready</small> : <Link href="/tools">Open tools</Link>}</article>
          <article className={connection?.authenticated ? "ready" : ""}><span>02</span><div><strong>Authorize Postiz</strong><p>Complete the device-login window. Credentials remain with Postiz on this computer.</p></div><button type="button" disabled={busy || !canExecute || !connection?.provider_installed || !connection?.provider_active} onClick={() => void startSetup("launch-auth")}>{connection?.authenticated ? "Authorize again" : "Authorize"}</button></article>
          <article className={accounts.length ? "ready" : ""}><span>03</span><div><strong>Connect pages and profiles</strong><p>Use Postiz’s secure dashboard for each platform, then refresh the accounts below.</p></div><div className="step-actions"><button type="button" disabled={busy || !canExecute || !connection?.authenticated} onClick={() => void startSetup("open-dashboard")}>Open Postiz</button><button type="button" className="quiet-action" disabled={busy || !canExecute || !connection?.authenticated} onClick={() => void refreshAccounts()}>Refresh accounts</button></div></article>
        </div>
        {!canExecute && selected && <p className="setup-note">Only workspace owners and approvers can change Postiz connections or publish. You can still review the setup.</p>}
      </section>

      <section className="publish-layout">
        <form className="publish-form" onSubmit={(event) => { event.preventDefault(); void submit(event.currentTarget, false); }}>
          <div className="section-heading"><div><p className="eyebrow">CREATE DELIVERY</p><h2>Delivery details</h2></div><span>{accounts.length} account{accounts.length === 1 ? "" : "s"} available</span></div>
          <label>Workspace<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} required>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} / {workspace.role}</option>)}</select></label>
          <label>Approved local MP4 path<input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} placeholder=".data\media\approved-clip.mp4" required /><small>Media must be under a configured publishing media directory.</small></label>
          <label>Title<input name="title" maxLength={200} /></label>
          <label>Caption<textarea name="caption" rows={6} maxLength={5000} required /></label>
          <label>Date and time<input name="date" type="datetime-local" required /></label>
          <fieldset className="account-picker"><legend>Publishing destinations</legend><p>Choose the connected profile or page for each platform. This project never asks for a social password.</p><div className="platform-grid">{platforms.map((platform) => {
            const platformAccounts = accounts.filter((account) => account.platform === platform);
            return <section key={platform} className="platform-card"><div><strong>{platformName(platform)}</strong><span>{platformAccounts.length ? `${platformAccounts.length} connected` : "Not connected"}</span></div>{platformAccounts.length ? <div className="account-options">{platformAccounts.map((account) => <button type="button" key={account.id} aria-pressed={targets[platform] === account.id} className={targets[platform] === account.id ? "selected" : ""} onClick={() => setTargets({ ...targets, [platform]: targets[platform] === account.id ? "" : account.id })}>{account.label}</button>)}</div> : <button type="button" className="text-action" disabled={busy || !canExecute || !connection?.authenticated} onClick={() => void startSetup("open-dashboard")}>Connect in Postiz</button>}</section>;
          })}</div></fieldset>
          <div className="publish-options"><label><input name="schedule" type="checkbox" /> Schedule instead of draft</label><label><input name="made_with_ai" type="checkbox" /> Disclose AI-generated media</label></div>
          <div className="publish-actions"><button disabled={busy}>Generate dry-run preview</button><button type="button" className="danger-action" disabled={busy || !canExecute} onClick={(event) => { const form = event.currentTarget.form; if (form && window.confirm("Create the remote Postiz draft or schedule for the selected accounts?")) void submit(form, true); }}>Confirm and publish</button></div>
        </form>
        <aside className="publish-side">
          <article><h2>Dry-run delivery</h2>{preview ? <dl className="preview-list">{Object.entries(preview).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl> : <p>Generate a dry-run to inspect the Postiz delivery before creating anything remotely.</p>}</article>
          <article><h2>Publishing jobs</h2>{jobs.length ? <div className="record-list">{jobs.map((job) => <div key={job.id}><strong>{job.payload.request?.caption ?? job.id}</strong><span>{job.status}</span>{job.error && <small>{job.error}</small>}</div>)}</div> : <p>No Postiz jobs yet.</p>}</article>
        </aside>
      </section>
    </main>
  );
}