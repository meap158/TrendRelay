"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";

type Workspace = { id: string; name: string; role: string };
type Platform = "tiktok" | "instagram" | "youtube";
type PublishJob = {
  id: string;
  status: string;
  error?: string | null;
  created_at: string;
  payload: { preview?: { external_action?: string }; request?: { caption?: string } };
  result?: Record<string, unknown> | null;
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Publishing request failed.");
  return body;
}

export default function PublishPage() {
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [targets, setTargets] = useState<Record<Platform, string>>({
    tiktok: "",
    instagram: "",
    youtube: "",
  });
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [integrations, setIntegrations] = useState<unknown>(null);
  const [jobs, setJobs] = useState<PublishJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = workspaces.find((workspace) => workspace.id === workspaceId);
  const canExecute = selected?.role === "owner" || selected?.role === "approver";

  const loadJobs = useCallback(async (id: string) => {
    const body = await json<{ jobs: PublishJob[] }>(
      await apiFetch(`/api/workspaces/${id}/publishing/postiz/jobs`),
    );
    setJobs(body.jobs);
  }, [apiFetch]);

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

  useEffect(() => {
    if (!workspaceId) return;
    queueMicrotask(() => void loadJobs(workspaceId).catch(() => undefined));
    const timer = window.setInterval(() => void loadJobs(workspaceId).catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [loadJobs, workspaceId]);

  function requestFrom(form: FormData, confirm: boolean) {
    const selectedTargets = (Object.entries(targets) as [Platform, string][])
      .filter(([, integrationId]) => integrationId.trim())
      .map(([platform, integrationId]) => ({ platform, integration_id: integrationId.trim() }));
    if (!selectedTargets.length) throw new Error("Enter at least one connected integration ID.");
    return {
      workspace_id: workspaceId,
      video_path: form.get("video_path"),
      caption: form.get("caption"),
      title: form.get("title") || null,
      date: new Date(String(form.get("date"))).toISOString(),
      schedule: form.get("schedule") === "on",
      made_with_ai: form.get("made_with_ai") === "on",
      targets: selectedTargets,
      confirm_external_action: confirm,
    };
  }

  async function submit(formElement: HTMLFormElement, execute: boolean) {
    if (!workspaceId) return;
    setBusy(true);
    setError(null);
    try {
      const body = requestFrom(new FormData(formElement), execute);
      if (execute) {
        await json(await apiFetch(`/api/workspaces/${workspaceId}/publishing/postiz/jobs`, {
          method: "POST",
          body: JSON.stringify(body),
        }));
        await loadJobs(workspaceId);
      } else {
        const result = await json<{ preview: Record<string, unknown> }>(await apiFetch(
          `/api/workspaces/${workspaceId}/publishing/postiz/preview`,
          { method: "POST", body: JSON.stringify(body) },
        ));
        setPreview(result.preview);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Publishing request failed.");
    } finally {
      setBusy(false);
    }
  }

  async function discover() {
    if (!workspaceId) return;
    setBusy(true);
    setError(null);
    try {
      setIntegrations(await json(await apiFetch(
        `/api/workspaces/${workspaceId}/publishing/postiz/integrations`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      )));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not discover integrations.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <main className="publish-page"><p>Checking your session...</p></main>;
  if (!user) return <main className="publish-page"><Link className="primary-link" href="/sign-in?next=%2Fpublish">Sign in to publish</Link></main>;

  return (
    <main className="publish-page">
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>Publish</strong></nav>
      <header><p className="eyebrow">GOVERNED DISTRIBUTION</p><h1>Preview first. Publish once.</h1><p className="lede">Create private Postiz drafts by default or schedule approved short videos across connected accounts.</p></header>
      {error && <p className="registry-error" role="alert">{error}</p>}
      <section className="publish-layout">
        <form className="publish-form" onSubmit={(event) => { event.preventDefault(); void submit(event.currentTarget, false); }}>
          <label>Workspace<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} required>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} / {workspace.role}</option>)}</select></label>
          <button type="button" disabled={busy || !canExecute} onClick={discover}>Discover connected accounts</button>
          {integrations !== null && <pre className="payload-preview">{JSON.stringify(integrations, null, 2)}</pre>}
          <label>Approved local MP4 path<input name="video_path" placeholder=".data\media\approved-clip.mp4" required /><small>Media must be under a configured publishing media directory.</small></label>
          <label>Title<input name="title" maxLength={200} /></label>
          <label>Caption<textarea name="caption" rows={6} maxLength={5000} required /></label>
          <label>Date and time<input name="date" type="datetime-local" required /></label>
          <div className="publish-targets">{(["tiktok", "instagram", "youtube"] as Platform[]).map((platform) => <label key={platform}>{platform} integration ID<input value={targets[platform]} onChange={(event) => setTargets({ ...targets, [platform]: event.target.value })} /></label>)}</div>
          <div className="publish-options"><label><input name="schedule" type="checkbox" /> Schedule instead of draft</label><label><input name="made_with_ai" type="checkbox" /> Disclose AI-generated media</label></div>
          <div className="publish-actions"><button disabled={busy}>Generate dry-run preview</button><button type="button" className="danger-action" disabled={busy || !canExecute} onClick={(event) => { const form = event.currentTarget.form; if (form && window.confirm("Upload this video and create the remote Postiz draft or schedule?")) void submit(form, true); }}>Confirm and publish</button></div>
          {!canExecute && selected && <small>Only workspace owners and approvers can execute publishing.</small>}
        </form>
        <aside className="publish-side">
          <article><h2>Dry-run payload</h2>{preview ? <pre className="payload-preview">{JSON.stringify(preview, null, 2)}</pre> : <p>No preview generated yet.</p>}</article>
          <article><h2>Durable operations</h2><div className="record-list">{jobs.map((job) => <div key={job.id}><strong>{job.payload.request?.caption ?? job.id}</strong><span>{job.status}</span>{job.error && <small>{job.error}</small>}</div>)}</div></article>
        </aside>
      </section>
    </main>
  );
}
