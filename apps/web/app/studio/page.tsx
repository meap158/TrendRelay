"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiBaseUrl } from "../../lib/api";

import { useAuth } from "../auth-provider";
import { buttonClass } from "../ui/button";
import { useJobs } from "../jobs-provider";
import { WorkspaceSectionNav } from "../workspace-section-nav";

type Workspace = { id: string; name: string; role: string };
type Production = { id: string; title: string; status: string; source: { path: string }; execution?: { enabled?: boolean } };
type BlurJob = {
  id: string;
  status: string;
  error?: string | null;
  result?: {
    output?: string;
    coverage?: number;
    faces_tracked?: number;
    warning?: string | null;
    preview?: boolean;
  } | null;
};
type BlurStatus = {
  status: { available: boolean; reason: string | null; opencv_version: string | null };
  jobs: BlurJob[];
};

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
  const [blur, setBlur] = useState<BlurStatus | null>(null);
  const [blurBusy, setBlurBusy] = useState<string | null>(null);
  // The blurred render is what Publish sends unless this is changed, so the
  // choice is explicit and always visible rather than a hidden default.
  const [blurUse, setBlurUse] = useState<"blurred" | "original">("blurred");

  const loadBlur = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/face-blur/status`,
      );
      if (response.ok) setBlur((await response.json()) as BlurStatus);
    } catch {
      // Status is advisory; the tool explains itself when it is used.
    }
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/media/library/face-blur/status`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload: BlurStatus | null) => { if (!cancelled && payload) setBlur(payload); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

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

  if (loading) return <main className="publish-page studio-page"><p>Checking your session...</p></main>;
  if (!user) return <main className="publish-page studio-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Fstudio">Sign in to open Studio</Link></main>;

  async function blurFaces(previewOnly: boolean) {
    if (!sourcePath.trim()) {
      setError("Enter the approved local media path first.");
      return;
    }
    setBlurBusy(previewOnly ? "preview" : "render");
    setError("");
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/face-blur/jobs`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source_path: sourcePath.trim(),
            preview_seconds: previewOnly ? 6 : null,
            confirm_external_action: true,
          }),
        },
      );
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "Face blurring could not start.");
      await loadBlur();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Face blurring failed.");
    } finally {
      setBlurBusy(null);
    }
  }

  const latestBlur = blur?.jobs?.[0];
  const blurOutput = latestBlur?.result?.output;

  return <main className="publish-page studio-page">
    <WorkspaceSectionNav area="library" />
    <header><p className="eyebrow">GOVERNED LOCAL PRODUCTION</p><h1>Turn approved assets into clips</h1><p className="lede">Create immutable OpenMontage preflights, then render deterministic short clips locally without provider credentials or network calls.</p></header>
    {error && <p className="registry-error" role="alert">{error}</p>}
    <section className="publish-layout">
      <form className="publish-form" onSubmit={(event) => { event.preventDefault(); void propose(event.currentTarget); }}>
        <label>Workspace<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} / {workspace.role}</option>)}</select></label>
        <label>Production title<input name="title" required minLength={2} /></label>
        <label>Approved local media path<input name="source_asset" required value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="C:\media\source.mp4" /></label>
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
        <article className="blur-tool">
          <h2>Blur faces</h2>
          {blur && !blur.status.available ? (
            <p className="setup-note">{blur.status.reason}</p>
          ) : (
            <>
              <p className="setup-note">
                Detects every face and burns the blur into the pixels. The result is a new
                file; the original is never modified.
              </p>
              <div className="blur-actions">
                <button
                  type="button"
                  disabled={blurBusy !== null || !workspaceId}
                  onClick={() => void blurFaces(true)}
                >{blurBusy === "preview" ? "Rendering…" : "Preview 6s"}</button>
                <button
                  type="button"
                  className={buttonClass({ variant: "primary" })}
                  disabled={blurBusy !== null || !workspaceId}
                  onClick={() => void blurFaces(false)}
                >{blurBusy === "render" ? "Blurring…" : "Blur whole clip"}</button>
              </div>
              <label className="blur-use">
                Hand off to Publish
                <select value={blurUse} onChange={(event) => setBlurUse(event.target.value as "blurred" | "original")}>
                  <option value="blurred">Blurred version (default)</option>
                  <option value="original">Original, faces visible</option>
                </select>
                <small>
                  {blurUse === "blurred"
                    ? "Campaigns and Publish will send the blurred render."
                    : "Campaigns and Publish will send the untouched original, with faces visible."}
                </small>
              </label>
              {latestBlur && (
                <div className="blur-result">
                  <strong>{latestBlur.status}{latestBlur.result?.preview ? " · preview" : ""}</strong>
                  {latestBlur.error && <small className="blur-warning">{latestBlur.error}</small>}
                  {latestBlur.result?.coverage !== undefined && (
                    <small>
                      {Math.round((latestBlur.result.coverage ?? 0) * 100)}% of frames covered
                      {latestBlur.result.faces_tracked
                        ? ` · ${latestBlur.result.faces_tracked} face${latestBlur.result.faces_tracked === 1 ? "" : "s"} tracked`
                        : ""}
                    </small>
                  )}
                  {latestBlur.result?.warning && (
                    <small className="blur-warning">{latestBlur.result.warning}</small>
                  )}
                  {blurOutput && latestBlur.status === "succeeded" && (
                    <video
                      className="blur-preview"
                      controls
                      preload="metadata"
                      src={`${apiBaseUrl()}/api/workspaces/${workspaceId}/media/library/face-blur/media?path=${encodeURIComponent(blurOutput)}`}
                    />
                  )}
                </div>
              )}
            </>
          )}
        </article>
        <article><h2>Runtime</h2><pre className="payload-preview">{JSON.stringify(runtime, null, 2)}</pre></article>
        <article><h2>Productions</h2><div className="record-list">{productions.map((production) => <div key={production.id}><strong>{production.title}</strong><span>{production.status}</span><small>{production.source.path}</small>{production.status === "awaiting_approval" && <button disabled={busy || !canApprove} onClick={() => void approve(production.id)}>Approve plan</button>}{production.execution?.enabled && <button disabled={busy || !canApprove} onClick={() => void render(production.id)}>Render clip plan</button>}</div>)}</div></article>
        <article><h2>Render jobs</h2><div className="record-list">{renders.map((job) => <div key={job.id}><strong>{job.id}</strong><span>{job.status}</span>{job.error && <small>{job.error}</small>}{job.result?.artifacts?.map((artifact: { path: string; label: string }) => <small key={artifact.path}>{artifact.label}: {artifact.path}</small>)}</div>)}</div></article>
      </aside>
    </section>
  </main>;
}
