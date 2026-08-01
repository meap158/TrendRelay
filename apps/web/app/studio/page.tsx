"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { useJobs } from "../jobs-provider";
import { WorkspaceSectionNav } from "../workspace-section-nav";

type Workspace = { id: string; name: string; role: string };
type Production = { id: string; title: string; status: string; source: { path: string }; execution?: { enabled?: boolean } };
type Segment = { label: string; start_seconds: number; end_seconds: number };

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Studio request failed.");
  return body;
}

export default function StudioPage() {
  const { loading, user, apiFetch } = useAuth();
  const { jobs: allJobs, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [productions, setProductions] = useState<Production[]>([]);
  const [runtime, setRuntime] = useState<Record<string, unknown> | null>(null);
  const [segments, setSegments] = useState<Segment[]>([{ label: "Hook", start_seconds: 0, end_seconds: 15 }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const renders = allJobs.filter(j => j.category === "render").map(j => j.raw);

  const selected = workspaces.find((item) => item.id === workspaceId);
  const canApprove = selected?.role === "owner" || selected?.role === "approver";

  const refresh = useCallback(async (id: string) => {
    const [statusBody, records] = await Promise.all([
      json<{ runtime: Record<string, unknown> }>(await apiFetch(`/api/workspaces/${id}/studio/status`)),
      json<{ productions: Production[] }>(await apiFetch(`/api/workspaces/${id}/studio/productions`)),
    ]);
    setRuntime(statusBody.runtime);
    setProductions(records.productions);
    refreshJobs();
  }, [apiFetch, refreshJobs]);

  useEffect(() => {
    queueMicrotask(() => setSourcePath(new URLSearchParams(window.location.search).get("source") ?? ""));
  }, []);

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    if (!user) return;
    apiFetch("/api/workspaces").then((response) => json<{ workspaces: Workspace[] }>(response)).then((body) => {
      setWorkspaces(body.workspaces);
      setWorkspaceId(body.workspaces[0]?.id ?? "");
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load workspaces."));
  }, [apiFetch, user]);

  useEffect(() => {
    if (!workspaceId) return;
    queueMicrotask(() => void refresh(workspaceId).catch(() => undefined));
  }, [refresh, workspaceId]);

  async function propose(form: HTMLFormElement) {
    setBusy(true); setError(null);
    const data = new FormData(form);
    try {
      await json(await apiFetch(`/api/workspaces/${workspaceId}/studio/productions`, {
        method: "POST",
        body: JSON.stringify({
          workspace_id: workspaceId,
          title: data.get("title"),
          source_asset: data.get("source_asset"),
          source_rights: data.get("source_rights"),
          pipeline: data.get("pipeline"),
          target_platforms: ["tiktok", "instagram", "youtube"],
          clip_count: segments.length,
          budget_usd: Number(data.get("budget_usd")),
          confirm_external_action: true,
        }),
      }));
      await refresh(workspaceId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not create preflight."); }
    finally { setBusy(false); }
  }

  async function approve(productionId: string) {
    if (!window.confirm("Approve the immutable source and zero-cost local production plan?")) return;
    setBusy(true); setError(null);
    try {
      await json(await apiFetch(`/api/workspaces/${workspaceId}/studio/productions/${productionId}/approval`, {
        method: "POST", body: JSON.stringify({ approved_by: "authenticated-user", confirm_external_action: true }),
      }));
      await refresh(workspaceId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not approve production."); }
    finally { setBusy(false); }
  }

  async function render(productionId: string) {
    if (!window.confirm("Render these clips locally with OpenMontage and FFmpeg?")) return;
    setBusy(true); setError(null);
    try {
      await json(await apiFetch(`/api/workspaces/${workspaceId}/studio/renders`, {
        method: "POST",
        body: JSON.stringify({ workspace_id: workspaceId, production_id: productionId, segments, confirm_external_action: true }),
      }));
      await refresh(workspaceId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not submit render."); }
    finally { setBusy(false); }
  }

  if (loading) return <main className="publish-page"><p>Checking your session...</p></main>;
  if (!user) return <main className="publish-page"><Link className="primary-link" href="/sign-in?next=%2Fstudio">Sign in to open Studio</Link></main>;

  return <main className="publish-page">
    <WorkspaceSectionNav area="library" />
    <header><p className="eyebrow">GOVERNED LOCAL PRODUCTION</p><h1>Turn approved assets into clips</h1><p className="lede">Create immutable OpenMontage preflights, then render deterministic short clips locally without provider credentials or network calls.</p></header>
    {error && <p className="registry-error" role="alert">{error}</p>}
    <section className="publish-layout">
      <form className="publish-form" onSubmit={(event) => { event.preventDefault(); void propose(event.currentTarget); }}>
        <label>Workspace<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} / {workspace.role}</option>)}</select></label>
        <label>Production title<input name="title" required minLength={2} /></label>
        <label>Approved local media path<input name="source_asset" required value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="C:\media\source.mp4" /></label>
        <label>Rights basis<select name="source_rights"><option value="owned">Owned</option><option value="licensed">Licensed</option><option value="public-domain">Public domain</option></select></label>
        <label>Pipeline<select name="pipeline"><option value="clip-factory">Clip factory</option><option value="podcast-repurpose">Podcast repurpose</option></select></label>
        <label>Budget cap (USD)<input name="budget_usd" type="number" min="1" max="100" step="0.01" defaultValue="1" /></label>
        <h2>Manual clip plan</h2>
        {segments.map((segment, index) => <div className="segment-row" key={index}>
          <label>Label<input value={segment.label} onChange={(event) => setSegments(segments.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item))} /></label>
          <label>Start<input type="number" min="0" step="0.1" value={segment.start_seconds} onChange={(event) => setSegments(segments.map((item, itemIndex) => itemIndex === index ? { ...item, start_seconds: Number(event.target.value) } : item))} /></label>
          <label>End<input type="number" min="0.1" step="0.1" value={segment.end_seconds} onChange={(event) => setSegments(segments.map((item, itemIndex) => itemIndex === index ? { ...item, end_seconds: Number(event.target.value) } : item))} /></label>
        </div>)}
        <button type="button" disabled={segments.length >= 20} onClick={() => setSegments([...segments, { label: `Clip ${segments.length + 1}`, start_seconds: 0, end_seconds: 15 }])}>Add clip</button>
        <button disabled={busy || !workspaceId}>Create immutable preflight</button>
      </form>
      <aside className="publish-side">
        <article><h2>Runtime</h2><pre className="payload-preview">{JSON.stringify(runtime, null, 2)}</pre></article>
        <article><h2>Productions</h2><div className="record-list">{productions.map((production) => <div key={production.id}><strong>{production.title}</strong><span>{production.status}</span><small>{production.source.path}</small>{production.status === "awaiting_approval" && <button disabled={busy || !canApprove} onClick={() => void approve(production.id)}>Approve plan</button>}{production.execution?.enabled && <button disabled={busy || !canApprove} onClick={() => void render(production.id)}>Render clip plan</button>}</div>)}</div></article>
        <article><h2>Render jobs</h2><div className="record-list">{renders.map((job) => <div key={job.id}><strong>{job.id}</strong><span>{job.status}</span>{job.error && <small>{job.error}</small>}{job.result?.artifacts?.map((artifact: { path: string; label: string }) => <small key={artifact.path}>{artifact.label}: {artifact.path}</small>)}</div>)}</div></article>
      </aside>
    </section>
  </main>;
}
