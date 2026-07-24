"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { useAuth } from "./auth-provider";
import { useJobs } from "./jobs-provider";

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
  douyin: {
    installed: boolean;
    active: boolean;
    revision?: string;
    cookies_ready?: boolean;
  };
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

function effectiveStatus(job: DownloadJob): string {
  return job.status === "succeeded" && (job.result?.artifacts?.length ?? 0) === 0
    ? "empty"
    : job.status;
}

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function Dashboard() {
  const { loading, user, apiFetch } = useAuth();
  const { jobs: allJobs, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [platform, setPlatform] = useState<"douyin" | "tiktok">("douyin");
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("post");
  const [limit, setLimit] = useState(20);
  const [status, setStatus] = useState<MediaStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const jobs = allJobs.filter(j => j.category === "fetch").map(j => j.raw); // use the raw specific payload

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    const fetchStatus = async () => {
      try {
        const statusBody = await json<MediaStatus>(await apiFetch(`/api/workspaces/${workspaceId}/media/status`));
        setStatus(statusBody);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Media service unavailable.");
      }
    };
    fetchStatus();
    const timer = setInterval(fetchStatus, 4000);
    return () => clearInterval(timer);
  }, [apiFetch, workspaceId]);

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
      await refreshJobs();
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
  const cookiesReady = status?.douyin.cookies_ready !== false;
  const canFetch = providerReady && cookiesReady;

  return <main className="console-page">
    <section className="console-heading">
      <div>
        <h1 style={{ fontSize: '20px', margin: 0, fontWeight: 500 }}>Media pipeline</h1>
        <p style={{ margin: '4px 0 0', color: 'var(--muted)', fontSize: '12px' }}>Fetch sources, prepare clips, and manage publication queues.</p>
      </div>
      <label className="workspace-control" style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 'auto' }}>
        <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Workspace</span>
        <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} style={{ width: '220px', padding: '6px 12px', height: '32px' }}>
          {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}
        </select>
      </label>
    </section>

    {!workspaceId && <section className="empty-console"><h2>Create a workspace first</h2><p>A workspace owns media, approvals, and publishing history.</p><Link className="primary-button" href="/workspaces">Create workspace</Link></section>}
    {workspaceId && <>
      <section className="pipeline-steps" aria-label="Content workflow" style={{ minHeight: '48px', margin: '0 0 20px', padding: 0, display: 'flex' }}>
        <div className="current" style={{ flex: 1, borderRight: '1px solid var(--line)', padding: '12px 16px' }}><span>1</span><div><strong>Fetch media</strong><small>Douyin source links</small></div></div>
        <Link href="/studio" style={{ flex: 1, borderRight: '1px solid var(--line)', padding: '12px 16px' }}><span>2</span><div><strong>Prepare clips</strong><small>Trim and approve</small></div></Link>
        <Link href="/publish" style={{ flex: 1, padding: '12px 16px' }}><span>3</span><div><strong>Publish</strong><small>Postiz drafts or schedules</small></div></Link>
      </section>

      {error && <p className="inline-error" role="alert">{error}</p>}
      <section className="console-grid">
        <form className="operation-card acquire-card" onSubmit={fetchMedia} style={{ padding: '16px' }}>
          <div className="card-heading" style={{ margin: '0 0 16px', alignItems: 'center' }}>
            <h2 style={{ fontSize: '14px', margin: 0, fontWeight: 500 }}>Fetch source videos</h2>
            <span className={`provider-pill ${canFetch ? "ready" : "setup"}`}>{canFetch ? "Douyin ready" : "Setup needed"}</span>
          </div>
          <div className="source-tabs" style={{ display: 'flex', background: '#f0f2f5', padding: '2px', borderRadius: '4px', marginBottom: '16px' }}>
            <button type="button" className={platform === "douyin" ? "selected" : ""} onClick={() => setPlatform("douyin")} style={{ flex: 1, padding: '6px 12px', minHeight: '32px', fontSize: '12px' }}>Douyin</button>
            <button type="button" className={platform === "tiktok" ? "selected" : ""} onClick={() => setPlatform("tiktok")} style={{ flex: 1, padding: '6px 12px', minHeight: '32px', fontSize: '12px' }}>TikTok <span style={{ color: 'var(--muted)', fontWeight: 400 }}>(not configured)</span></button>
          </div>
          {platform === "douyin" ? <>
            <label className="field-label">
              <span style={{ display: 'block', marginBottom: '4px' }}>Video, profile, or copied share links</span>
              <textarea value={input} onChange={(event) => setInput(event.target.value)} rows={5} placeholder={"Paste one or more Douyin links here…\nhttps://www.douyin.com/video/…"} style={{ minHeight: '100px' }} />
            </label>
            <div className="compact-fields" style={{ display: 'grid', gridTemplateColumns: '1fr 120px', gap: '12px', margin: '12px 0' }}>
              <label className="field-label">
                <span style={{ display: 'block', marginBottom: '4px' }}>Fetch type</span>
                <select value={mode} onChange={(event) => setMode(event.target.value)} style={{ height: '32px', padding: '4px 8px' }}>
                  <option value="post">Published posts</option>
                  <option value="like">Liked videos</option>
                  <option value="mix">Collections</option>
                  <option value="music">Music videos</option>
                </select>
              </label>
              <label className="field-label">
                <span style={{ display: 'block', marginBottom: '4px' }}>Limit</span>
                <input type="number" min="1" max="100" value={limit} onChange={(event) => setLimit(Number(event.target.value))} style={{ height: '32px', padding: '4px 8px' }} />
              </label>
            </div>
            {!providerReady && <div className="setup-note" style={{ margin: '12px 0', padding: '8px 12px' }}>Douyin Downloader must be installed and active. <Link href="/tools">Open Tools</Link></div>}
            {providerReady && !cookiesReady && (
              <div className="setup-note" style={{ margin: '12px 0', padding: '8px 12px' }}>
                Douyin cookies are required. In a terminal run <code>npm run douyin -- install --login-browser</code> then <code>npm run douyin -- login</code>, or set <code>DOUYIN_COOKIE</code> in <code>.env</code>.
              </div>
            )}
            <button className="primary-button" style={{ width: '100%', minHeight: '32px' }} disabled={busy || !canFetch || !input.trim()}>{busy ? "Starting…" : "Fetch videos"}</button>
            <small className="rights-note" style={{ display: 'block', textAlign: 'center', marginTop: '12px', color: 'var(--muted)' }}>Only fetch media you are authorized to retain and reuse.</small>
          </> : <div className="provider-empty" style={{ minHeight: '200px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', background: '#fafafa', border: '1px solid var(--line)', borderRadius: '4px' }}>
            <h3 style={{ fontSize: '13px', margin: '0 0 8px' }}>TikTok acquisition needs a provider</h3>
            <p style={{ margin: '0 0 16px', fontSize: '12px' }}>Add and review a TikTok downloader before enabling this source.</p>
            <Link className="secondary-button" href="/tools">Review providers</Link>
          </div>}
        </form>

        <aside className="operation-card queue-card" style={{ padding: '0', display: 'flex', flexDirection: 'column' }}>
          <div className="card-heading" style={{ margin: 0, padding: '12px 16px', borderBottom: '1px solid var(--line)', alignItems: 'center' }}>
            <h2 style={{ fontSize: '14px', margin: 0, fontWeight: 500 }}>Media queue</h2>
            <button type="button" className="icon-button" onClick={() => void refreshJobs()} aria-label="Refresh">↻</button>
          </div>
          <div className="media-queue" style={{ flex: 1, padding: 0 }}>
            {jobs.length === 0 && <div className="quiet-empty" style={{ padding: '40px 16px' }}><strong>No downloads yet</strong><span>Paste a Douyin link to begin.</span></div>}
            {jobs.map((job) => <article key={job.id} className="media-job" style={{ padding: '12px 16px', borderTop: 'none', borderBottom: '1px solid var(--line)' }}>
              <div className="job-row" style={{ marginBottom: '8px' }}>
                <span className={`job-status ${effectiveStatus(job)}`} style={{ padding: '2px 6px', borderRadius: '4px', background: effectiveStatus(job) === 'succeeded' ? '#e6f6ee' : ['failed', 'empty'].includes(effectiveStatus(job)) ? '#f8d7da' : '#fff8e1' }}>{effectiveStatus(job)}</span>
                <time>{new Date(job.created_at).toLocaleString()}</time>
              </div>
              <strong style={{ display: 'block', marginBottom: '8px' }}>{job.payload.request?.urls?.[0] ?? job.id}</strong>
              {job.result?.summary && job.status === "succeeded" && (
                <small style={{ display: 'block', marginBottom: '8px', color: 'var(--muted)' }}>{job.result.summary}</small>
              )}
              {job.error && <small className="job-error" style={{ display: 'block', marginBottom: '8px', whiteSpace: 'pre-wrap', color: '#a11' }}>{job.error}</small>}
              <div style={{ display: 'grid', gap: '8px' }}>
                {job.payload?.output_root && job.status === "succeeded" && (
                  <div style={{ padding: '8px', background: '#fafafa', border: '1px solid var(--line-strong)', borderRadius: '4px', fontSize: '11px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div><strong style={{ color: 'var(--muted)' }}>Saved to: </strong><span style={{ fontFamily: 'monospace' }}>{job.payload.output_root}</span></div>
                    <button type="button" className="secondary-button" style={{ minHeight: '24px', padding: '0 8px', fontSize: '11px' }} onClick={() => apiFetch('/api/tools/open-folder', { method: 'POST', body: JSON.stringify({ path: job.payload.output_root }) }).catch(() => alert('Could not open folder. Check if the API supports this.'))}>
                      Open folder
                    </button>
                  </div>
                )}
                {job.status === "succeeded" && (job.result?.artifacts?.length ?? 0) === 0 && (
                  <small className="job-error" style={{ color: '#a11' }}>Legacy empty result: no media files were saved. New runs fail instead of reporting success.</small>
                )}
                {job.result?.artifacts?.map((artifact: Artifact) => <div className="artifact-row" key={artifact.path} style={{ padding: '8px', background: '#fff', border: '1px solid var(--line-strong)' }}>
                  <div><strong>{artifact.name}</strong><small>{size(artifact.size_bytes)}</small></div>
                  <div><Link href={`/studio?source=${encodeURIComponent(artifact.path)}`}>Prepare</Link><Link href={`/publish?video=${encodeURIComponent(artifact.path)}`}>Publish</Link></div>
                </div>)}
              </div>
            </article>)}
          </div>
        </aside>
      </section>

      <section className="quick-actions" style={{ borderRadius: '4px', overflow: 'hidden' }}>
        <div><span>Workspace</span><strong>{selectedWorkspace?.name}</strong></div>
        <Link href="/research"><span>Find ideas</span><strong>Run trend research →</strong></Link>
        <Link href="/studio"><span>Local files</span><strong>Prepare existing media →</strong></Link>
        <Link href="/publish"><span>Ready media</span><strong>Open publishing →</strong></Link>
      </section>
    </>}
  </main>;
}
