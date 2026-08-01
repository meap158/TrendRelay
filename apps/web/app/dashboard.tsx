"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { useAuth } from "./auth-provider";
import { useJobs } from "./jobs-provider";

type Workspace = { id: string; name: string; role: string };
type Artifact = { path: string; name: string; size_bytes: number };
type DownloadJob = {
  id: string;
  status: string;
  error?: string | null;
  created_at: string;
  payload: {
    request?: { urls?: string[]; mode?: string; limit?: number };
    output_root?: string;
  };
  result?: { artifacts?: Artifact[]; summary?: string } | null;
};
type MediaStatus = {
  douyin: {
    installed: boolean;
    active: boolean;
    revision?: string;
    cookies_ready?: boolean;
    connection?: { state: string; message: string };
  };
  tiktok: { installed: boolean; active: boolean; reason: string };
};
type QueueFilter = "all" | "active" | "completed" | "attention";

const ACTIVE_STATUSES = new Set(["queued", "running", "in_progress", "pending"]);
const INITIAL_JOB_COUNT = 5;

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Request failed.");
  return body;
}

export function sourceUrls(value: string): string[] {
  return Array.from(new Set(value.match(/https?:\/\/[^\s<>"']+/g) ?? [])).map((url) =>
    url.replace(/[.,;:!?\])}]+$/, ""),
  );
}

function effectiveStatus(job: DownloadJob): string {
  return job.status === "succeeded" && (job.result?.artifacts?.length ?? 0) === 0
    ? "empty"
    : job.status;
}

function statusLabel(status: string): string {
  return ({
    queued: "Waiting",
    running: "Downloading",
    in_progress: "Downloading",
    pending: "Waiting",
    succeeded: "Downloaded",
    failed: "Needs attention",
    empty: "No files found",
    cancelled: "Cancelled",
  } as Record<string, string>)[status] ?? status.replaceAll("_", " ");
}

function isDouyinSource(url: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host === "douyin.com" || host.endsWith(".douyin.com") || host === "iesdouyin.com" || host.endsWith(".iesdouyin.com");
  } catch {
    return false;
  }
}

function modeLabel(mode: string): string {
  return ({ post: "Published posts", like: "Liked videos", mix: "Collections", music: "Music videos" } as Record<string, string>)[mode] ?? mode;
}

function sourceType(url: string): string {
  try {
    const path = new URL(url).pathname.toLowerCase();
    if (path.includes("/video/") || path.includes("/note/")) return "Video";
    if (path.includes("/user/")) return "Profile";
    if (path.includes("/mix/")) return "Collection";
    if (path.includes("/music/")) return "Music";
    return "Share link";
  } catch {
    return "Link";
  }
}

function shortSource(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "") + (parsed.pathname === "/" ? "" : parsed.pathname);
  } catch {
    return url;
  }
}

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function isVisibleForFilter(job: DownloadJob, filter: QueueFilter): boolean {
  const current = effectiveStatus(job);
  if (filter === "active") return ACTIVE_STATUSES.has(current);
  if (filter === "completed") return current === "succeeded";
  if (filter === "attention") return ["failed", "empty", "cancelled"].includes(current);
  return true;
}

export default function Dashboard() {
  const { loading, user, apiFetch } = useAuth();
  const { jobs: allJobs, busy: jobsBusy, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("post");
  const [limit, setLimit] = useState(20);
  const [status, setStatus] = useState<MediaStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [visibleJobCount, setVisibleJobCount] = useState(INITIAL_JOB_COUNT);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const jobs = useMemo(
    () => allJobs.filter((job) => job.category === "fetch").map((job) => job.raw as DownloadJob),
    [allJobs],
  );
  const extractedUrls = useMemo(() => sourceUrls(input), [input]);
  const urls = useMemo(() => extractedUrls.filter(isDouyinSource), [extractedUrls]);
  const unsupportedCount = extractedUrls.length - urls.length;
  const filteredJobs = useMemo(
    () => jobs.filter((job) => isVisibleForFilter(job, queueFilter)),
    [jobs, queueFilter],
  );
  const visibleJobs = filteredJobs.slice(0, visibleJobCount);
  const queueCounts = useMemo(() => ({
    all: jobs.length,
    active: jobs.filter((job) => ACTIVE_STATUSES.has(effectiveStatus(job))).length,
    completed: jobs.filter((job) => effectiveStatus(job) === "succeeded").length,
    attention: jobs.filter((job) => ["failed", "empty", "cancelled"].includes(effectiveStatus(job))).length,
  }), [jobs]);

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    const fetchStatus = async () => {
      try {
        const body = await json<MediaStatus>(await apiFetch("/api/workspaces/" + workspaceId + "/media/status"));
        if (!cancelled) setStatus(body);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Media service unavailable.");
      }
    };
    void fetchStatus();
    const timer = setInterval(() => void fetchStatus(), 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!user) return;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        setWorkspaces(body.workspaces);
        setWorkspaceId((current) => current || body.workspaces[0]?.id || "");
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load workspaces."));
  }, [apiFetch, user]);

  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);
  const providerReady = Boolean(status?.douyin.installed && status?.douyin.active);
  const cookiesReady = status?.douyin.cookies_ready === true;
  const canFetch = providerReady && cookiesReady;
  const connectionState = status?.douyin.connection?.state ?? "disconnected";
  const connectionActive = ["starting", "installing", "opening_browser", "waiting_for_login"].includes(connectionState);

  async function connectDouyin() {
    if (!workspaceId) return;
    setConnecting(true);
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ connection: { state: string; message: string } }>(
        await apiFetch("/api/workspaces/" + workspaceId + "/media/douyin/connection", {
          method: "POST",
          body: JSON.stringify({ confirm_external_action: true, force_refresh: cookiesReady }),
        }),
      );
      setStatus((current) => current ? {
        ...current,
        douyin: { ...current.douyin, connection: body.connection },
      } : current);
      setNotice("Douyin opened in a separate window. Finish signing in there; TrendRelay will detect it automatically.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Douyin connection could not start.");
    } finally {
      setConnecting(false);
    }
  }

  async function pasteLinks() {
    setError(null);
    try {
      const text = await navigator.clipboard.readText();
      if (!text.trim()) {
        setError("Your clipboard does not contain any text.");
        return;
      }
      setInput((current) => current.trim() ? current.trim() + "\n" + text.trim() : text.trim());
    } catch {
      setError("Clipboard access was not available. Paste into the box with Ctrl+V instead.");
    }
  }

  function removeSource(url: string) {
    setInput((current) => current.replaceAll(url, "").replace(/\n{3,}/g, "\n\n").trim());
  }

  function selectQueueFilter(filter: QueueFilter) {
    setQueueFilter(filter);
    setVisibleJobCount(INITIAL_JOB_COUNT);
  }

  async function fetchMedia(event: FormEvent) {
    event.preventDefault();
    if (!urls.length) {
      setError("Paste at least one complete Douyin URL or copied share message.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await json(await apiFetch("/api/workspaces/" + workspaceId + "/media/douyin/downloads", {
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
      setQueueFilter("active");
      setVisibleJobCount(INITIAL_JOB_COUNT);
      setNotice(urls.length === 1 ? "Download added to the queue." : urls.length + " downloads added to the queue.");
      await refreshJobs();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Download could not start.");
    } finally {
      setBusy(false);
    }
  }

  async function openFolder(path: string) {
    setError(null);
    try {
      await json(await apiFetch("/api/tools/open-folder", {
        method: "POST",
        body: JSON.stringify({ path }),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The download folder could not be opened.");
    }
  }

  if (loading) return <main className="console-page"><div className="loading-panel">Loading workspace…</div></main>;
  if (!user) return <main className="console-page"><section className="empty-console"><strong>TrendRelay</strong><h1>Sign in to manage media.</h1><p>Fetch source videos, prepare clips, and send approved posts from one workspace.</p><Link className="primary-button" href="/sign-in?next=%2F">Sign in</Link></section></main>;

  return <main className="console-page">
    <section className="console-heading downloader-heading">
      <div>
        <p className="eyebrow">MEDIA ACQUISITION</p>
        <h1>Download from Douyin</h1>
        <p>Paste videos, profiles, or collections. TrendRelay downloads them in the background and adds the files to your library.</p>
      </div>
      <label className="workspace-control">
        <span>Workspace</span>
        <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
          {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}
        </select>
      </label>
    </section>

    {!workspaceId && <section className="empty-console"><h2>Create a workspace first</h2><p>A workspace owns media, approvals, and publishing history.</p><Link className="primary-button" href="/workspaces">Create workspace</Link></section>}
    {workspaceId && <>
      <nav className="download-steps" aria-label="Media workflow">
        <a className="current" href="#add-links"><span>1</span><strong>Add links</strong></a>
        <a href="#download-queue"><span>2</span><strong>Track downloads</strong></a>
        <Link href="/library"><span>3</span><strong>Use your clips</strong></Link>
      </nav>

      <div className="console-messages" aria-live="polite">
        {error && <p className="inline-error" role="alert"><strong>Something needs attention.</strong><span>{error}</span></p>}
        {notice && <p className="inline-notice"><strong>All set.</strong><span>{notice}</span></p>}
      </div>

      <section className="downloader-layout">
        <form id="add-links" className="download-composer" onSubmit={fetchMedia}>
          <div className="download-card-heading">
            <div>
              <p className="step-kicker">STEP 1</p>
              <h2>Add Douyin links</h2>
              <p>Paste a copied share message or put one link on each line.</p>
            </div>
            <span className={"connection-badge " + (canFetch ? "ready" : connectionActive ? "working" : "setup")}>
              <i aria-hidden="true" />
              {canFetch ? "Douyin connected" : connectionActive ? "Waiting for sign-in" : "Connection needed"}
            </span>
          </div>

          <div className="link-input-field">
            <label className="link-input-label" htmlFor="douyin-links">Douyin links</label>
            <div className="link-input-shell">
              <textarea
                id="douyin-links"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                rows={5}
                placeholder={"Paste a video, profile, collection, or share message…\nhttps://www.douyin.com/video/…"}
                aria-describedby="douyin-link-help"
              />
              <div className="link-input-footer">
                <span id="douyin-link-help">{urls.length ? urls.length + " Douyin " + (urls.length === 1 ? "link" : "links") + " detected" + (unsupportedCount ? " · " + unsupportedCount + " unsupported ignored" : "") : unsupportedCount ? "No supported Douyin links found" : "Video · Profile · Collection · Music"}</span>
                <button type="button" className="paste-button" onClick={() => void pasteLinks()}>Paste from clipboard</button>
              </div>
            </div>
          </div>

          {urls.length > 0 && <section className="detected-sources" aria-label="Detected Douyin sources">
            <div className="detected-heading"><strong>Ready to download</strong><span>{urls.length} {urls.length === 1 ? "source" : "sources"}</span></div>
            <ul>
              {urls.map((url) => <li key={url}>
                <span className="source-kind">{sourceType(url)}</span>
                <span className="source-address" title={url}>{shortSource(url)}</span>
                <button type="button" onClick={() => removeSource(url)} aria-label={"Remove " + shortSource(url)}>Remove</button>
              </li>)}
            </ul>
          </section>}

          {!providerReady && <div className="connection-callout warning">
            <div><strong>Install the Douyin downloader</strong><span>Enable the managed provider once, then return here.</span></div>
            <Link className="secondary-button" href="/tools">Open Tools</Link>
          </div>}
          {providerReady && !cookiesReady && <div className="connection-callout warning">
            <div>
              <strong>{connectionActive ? "Finish signing in to Douyin" : "Connect your Douyin session"}</strong>
              <span>{status?.douyin.connection?.message ?? "TrendRelay opens a dedicated login window and stores the session only on this computer."}</span>
            </div>
            <button type="button" className="secondary-button" disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}>
              {connecting || connectionActive ? "Waiting for sign-in…" : "Connect Douyin"}
            </button>
          </div>}
          {providerReady && cookiesReady && <div className="connection-callout connected">
            <div><strong>Ready to download</strong><span>Your Douyin session is stored locally. Refresh it only if downloads stop working.</span></div>
            <button type="button" className="text-action" disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}>
              {connecting || connectionActive ? "Refreshing…" : "Refresh session"}
            </button>
          </div>}

          <details className="download-options">
            <summary>Download options <span>{modeLabel(mode)} · up to {limit} per source</span></summary>
            <div className="download-options-grid">
              <label><span>Content from profiles</span><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="post">Published posts</option><option value="like">Liked videos</option><option value="mix">Collections</option><option value="music">Music videos</option></select></label>
              <fieldset><legend>Maximum per source</legend><div className="limit-presets">{[10, 20, 50, 100].map((value) => <button key={value} type="button" className={limit === value ? "selected" : ""} aria-pressed={limit === value} onClick={() => setLimit(value)}>{value}</button>)}</div></fieldset>
            </div>
            <p>TrendRelay skips files already downloaded from the same source.</p>
          </details>

          <div className="download-submit-row">
            <button className="primary-button download-button" disabled={busy || !canFetch || urls.length === 0}>
              {busy ? "Adding to queue…" : urls.length > 1 ? "Download " + urls.length + " sources" : "Start download"}
            </button>
            <small>Only download media you are authorized to retain and reuse.</small>
          </div>
        </form>

      </section>

      <section id="download-queue" className="download-queue-card">
        <div className="queue-heading">
          <div><p className="step-kicker">STEP 2</p><h2>Downloads</h2><p>Active batches update automatically every few seconds.</p></div>
          <button type="button" className="secondary-button refresh-button" disabled={jobsBusy} onClick={() => void refreshJobs()}>{jobsBusy ? "Refreshing…" : "Refresh"}</button>
        </div>
        <div className="queue-filters" role="group" aria-label="Filter downloads">
          {([
            ["all", "All"],
            ["active", "Active"],
            ["completed", "Completed"],
            ["attention", "Needs attention"],
          ] as [QueueFilter, string][]).map(([value, label]) => <button key={value} type="button" className={queueFilter === value ? "selected" : ""} aria-pressed={queueFilter === value} onClick={() => selectQueueFilter(value)}><span>{label}</span><b>{queueCounts[value]}</b></button>)}
        </div>

        {filteredJobs.length === 0 && <div className="download-empty">
          <span className="empty-download-icon" aria-hidden="true">↓</span>
          <strong>{jobs.length ? "No " + (queueFilter === "attention" ? "downloads need attention" : queueFilter + " downloads") : "Your downloads will appear here"}</strong>
          <p>{jobs.length ? "Choose another filter to see the rest of your queue." : "Add one or more Douyin links above to start your first batch."}</p>
          {!jobs.length && <a href="#add-links">Add Douyin links</a>}
        </div>}

        <div className="download-job-list">
          {visibleJobs.map((job) => {
            const current = effectiveStatus(job);
            const sources = job.payload.request?.urls ?? [];
            const artifacts = job.result?.artifacts ?? [];
            return <details key={job.id} className={"download-job " + current} open={ACTIVE_STATUSES.has(current) || undefined}>
              <summary>
                <span className={"job-status " + current}><i aria-hidden="true" />{statusLabel(current)}</span>
                <span className="download-job-summary-title"><strong>{sources[0] ? shortSource(sources[0]) : job.id}</strong><small>{sources.length > 1 ? sources.length + " sources" : sources[0] ? sourceType(sources[0]) : "Douyin batch"} · {artifacts.length ? artifacts.length + " files" : "up to " + (job.payload.request?.limit ?? "—") + " per source"}</small></span>
                <time>{new Date(job.created_at).toLocaleString()}</time>
                <span className="job-disclosure" aria-hidden="true">⌄</span>
              </summary>
              <div className="download-job-body">
                {ACTIVE_STATUSES.has(current) && <div className={"job-progress " + current} aria-label={current === "queued" ? "Waiting to start" : "Download in progress"}><span /></div>}
                {current === "succeeded" && job.payload.output_root && <div className="download-job-actions"><button type="button" className="secondary-button" onClick={() => void openFolder(job.payload.output_root!)}>Open folder</button><Link href="/library">Open library</Link></div>}
                {job.result?.summary && current === "succeeded" && <p className="job-summary">{job.result.summary}. Files were also added to the media library.</p>}
                {job.error && <div className="job-error"><strong>Download stopped</strong><span>{job.error}</span><a href="#add-links" onClick={() => setInput(sources.join("\n"))}>Load these links again</a></div>}
                {current === "empty" && !job.error && <div className="job-error"><strong>No media files were saved</strong><span>Refresh the Douyin session, then load these links again.</span><a href="#add-links" onClick={() => setInput(sources.join("\n"))}>Load these links again</a></div>}
                {artifacts.length > 0 && <div className="artifact-list">
                  {artifacts.slice(0, 4).map((artifact) => <div className="artifact-row" key={artifact.path}>
                    <div><strong>{artifact.name}</strong><small>{size(artifact.size_bytes)}</small></div>
                    <div><Link href={"/studio?source=" + encodeURIComponent(artifact.path)}>Prepare</Link><Link href={"/campaigns?video=" + encodeURIComponent(artifact.path)}>Plan</Link><Link href={"/publish?video=" + encodeURIComponent(artifact.path)}>Publish</Link></div>
                  </div>)}
                  {artifacts.length > 4 && <p className="more-artifacts">+ {artifacts.length - 4} more files in this batch</p>}
                </div>}
              </div>
            </details>;
          })}
        </div>
        {visibleJobs.length < filteredJobs.length && <button type="button" className="queue-show-more" onClick={() => setVisibleJobCount((current) => current + INITIAL_JOB_COUNT)}>Show {Math.min(INITIAL_JOB_COUNT, filteredJobs.length - visibleJobs.length)} more downloads</button>}
      </section>
    </>}
  </main>;
}