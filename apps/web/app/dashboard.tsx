"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "./auth-provider";

type Workspace = { id: string; name: string; role: string };
type Artifact = { path: string; name: string; size_bytes: number };
type DownloadJob = {
  id: string;
  status: string;
  error?: string | null;
  created_at: string;
  payload: { request?: { urls?: string[] }; output_root?: string };
  result?: { artifacts?: Artifact[]; summary?: string } | null;
};
type MediaStatus = {
  douyin: { installed: boolean; active: boolean; revision?: string };
  tiktok: { installed: boolean; active: boolean; reason: string };
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Request failed.");
  return body;
}

function sourceUrls(value: string): string[] {
  return Array.from(new Set(value.match(/https?:\/\/[^\s<>"']+/g) ?? [])).map((url) =>
    url.replace(/[.,;:!?\])}]+$/, ""),
  );
}

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function Dashboard() {
  const { loading, user, apiFetch, signOut } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [platform, setPlatform] = useState<"douyin" | "tiktok">("douyin");
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("post");
  const [limit, setLimit] = useState(20);
  const [status, setStatus] = useState<MediaStatus | null>(null);
  const [jobs, setJobs] = useState<DownloadJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (id: string) => {
    const [statusBody, jobsBody] = await Promise.all([
      json<MediaStatus>(await apiFetch(`/api/workspaces/${id}/media/status`)),
      json<{ jobs: DownloadJob[] }>(await apiFetch(`/api/workspaces/${id}/media/downloads`)),
    ]);
    setStatus(statusBody);
    setJobs(jobsBody.jobs);
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
    queueMicrotask(() => void refresh(workspaceId).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Media service unavailable.")));
    const timer = window.setInterval(() => void refresh(workspaceId).catch(() => undefined), 4000);
    return () => window.clearInterval(timer);
  }, [refresh, workspaceId]);

  async function fetchMedia(event: FormEvent) {
    event.preventDefault();
    if (platform !== "douyin") return;
    const urls = sourceUrls(input);
    if (!urls.length) {
      setError("Paste at least one complete Douyin URL or copied share message.");
      return;
    }
    if (!window.confirm(`Fetch media from ${urls.length} Douyin source${urls.length === 1 ? "" : "s"}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await json(await apiFetch(`/api/workspaces/${workspaceId}/media/douyin/downloads`, {
        method: "POST",
        body: JSON.stringify({
          workspace_id: workspaceId,
          urls,
          mode,
          limit,
          incremental: true,
          confirm_external_action: true,
        }),
      }));
      setInput("");
      await refresh(workspaceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Download could not start.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <main className="console-page"><div className="loading-panel">Loading workspace…</div></main>;
  if (!user) return <main className="console-page"><section className="empty-console"><strong>TrendRelay</strong><h1>Sign in to manage media.</h1><p>Fetch source videos, prepare clips, and send approved posts from one workspace.</p><Link className="primary-button" href="/sign-in?next=%2F">Sign in</Link></section></main>;

  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);
  const providerReady = Boolean(status?.douyin.installed && status?.douyin.active);

  return <main className="console-page">
    <header className="app-toolbar">
      <Link className="app-brand" href="/"><span>TR</span><strong>TrendRelay</strong></Link>
      <nav className="app-nav"><Link className="active" href="/">Pipeline</Link><Link href="/research">Research</Link><Link href="/studio">Studio</Link><Link href="/publish">Publish</Link><Link href="/tools">Tools</Link></nav>
      <button className="text-button" onClick={() => void signOut()}>Sign out</button>
    </header>

    <section className="console-heading">
      <div><p className="section-kicker">CONTENT OPERATIONS</p><h1>Media pipeline</h1><p>Fetch a source, prepare it, then publish. Nothing more complicated than that.</p></div>
      <label className="workspace-control">Workspace<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}</select></label>
    </section>

    {!workspaceId && <section className="empty-console"><h2>Create a workspace first</h2><p>A workspace owns media, approvals, and publishing history.</p><Link className="primary-button" href="/workspaces">Create workspace</Link></section>}
    {workspaceId && <>
      <section className="pipeline-steps" aria-label="Content workflow">
        <div className="current"><span>1</span><div><strong>Fetch media</strong><small>Douyin source links</small></div></div>
        <Link href="/studio"><span>2</span><div><strong>Prepare clips</strong><small>Trim and approve</small></div></Link>
        <Link href="/publish"><span>3</span><div><strong>Publish</strong><small>Postiz drafts or schedules</small></div></Link>
      </section>

      {error && <p className="inline-error" role="alert">{error}</p>}
      <section className="console-grid">
        <form className="operation-card acquire-card" onSubmit={fetchMedia}>
          <div className="card-heading"><div><span className="step-label">STEP 1</span><h2>Fetch source videos</h2></div><span className={`provider-pill ${providerReady ? "ready" : "setup"}`}>{providerReady ? "Douyin ready" : "Setup needed"}</span></div>
          <div className="source-tabs">
            <button type="button" className={platform === "douyin" ? "selected" : ""} onClick={() => setPlatform("douyin")}>Douyin</button>
            <button type="button" className={platform === "tiktok" ? "selected" : ""} onClick={() => setPlatform("tiktok")}>TikTok <small>not configured</small></button>
          </div>
          {platform === "douyin" ? <>
            <label className="field-label">Video, profile, or copied share links<textarea value={input} onChange={(event) => setInput(event.target.value)} rows={7} placeholder={"Paste one or more Douyin links here…\nhttps://www.douyin.com/video/…"} /></label>
            <div className="compact-fields">
              <label className="field-label">Fetch<select value={mode} onChange={(event) => setMode(event.target.value)}><option value="post">Published posts</option><option value="like">Liked videos</option><option value="mix">Collections</option><option value="music">Music videos</option></select></label>
              <label className="field-label">Maximum items<input type="number" min="1" max="100" value={limit} onChange={(event) => setLimit(Number(event.target.value))} /></label>
            </div>
            {!providerReady && <p className="setup-note">Douyin Downloader must be installed and active. <Link href="/tools">Open Tools</Link></p>}
            <button className="primary-button" disabled={busy || !providerReady || !input.trim()}>{busy ? "Starting…" : "Fetch videos"}</button>
            <small className="rights-note">Only fetch media you are authorized to retain and reuse.</small>
          </> : <div className="provider-empty"><h3>TikTok acquisition needs a provider</h3><p>TrendRelay will not pretend this is available. Add and review a TikTok downloader before enabling this source.</p><Link className="secondary-button" href="/tools">Review providers</Link></div>}
        </form>

        <aside className="operation-card queue-card">
          <div className="card-heading"><div><span className="step-label">RECENT</span><h2>Media queue</h2></div><button className="icon-button" onClick={() => void refresh(workspaceId)} aria-label="Refresh">↻</button></div>
          <div className="media-queue">
            {jobs.length === 0 && <div className="quiet-empty"><strong>No downloads yet</strong><span>Paste a Douyin link to begin.</span></div>}
            {jobs.map((job) => <article key={job.id} className="media-job">
              <div className="job-row"><span className={`job-status ${job.status}`}>{job.status}</span><time>{new Date(job.created_at).toLocaleString()}</time></div>
              <strong>{job.payload.request?.urls?.[0] ?? job.id}</strong>
              {job.error && <small className="job-error">{job.error}</small>}
              {job.result?.artifacts?.map((artifact) => <div className="artifact-row" key={artifact.path}>
                <div><strong>{artifact.name}</strong><small>{size(artifact.size_bytes)}</small></div>
                <div><Link href={`/studio?source=${encodeURIComponent(artifact.path)}`}>Prepare</Link><Link href={`/publish?video=${encodeURIComponent(artifact.path)}`}>Publish</Link></div>
              </div>)}
            </article>)}
          </div>
        </aside>
      </section>

      <section className="quick-actions">
        <div><span>Workspace</span><strong>{selectedWorkspace?.name}</strong></div>
        <Link href="/research"><span>Find ideas</span><strong>Run trend research →</strong></Link>
        <Link href="/studio"><span>Local files</span><strong>Prepare existing media →</strong></Link>
        <Link href="/publish"><span>Ready media</span><strong>Open publishing →</strong></Link>
      </section>
    </>}
  </main>;
}
