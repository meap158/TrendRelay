"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { useAuth } from "./auth-provider";
import { useT } from "./i18n-provider";
import { fetchWorkspaces } from "../lib/workspaces";
import { Button, buttonClass } from "./ui/button";
import { ActionIcon } from "./ui/action-icons";
import { StatusToasts, useStatus } from "./ui/status";
import { numberIn, oneOf, subsetOf, usePersistedState } from "./ui/use-persisted-state";

const isDownloadMode = oneOf("post", "like", "mix", "music");
import { useJobs } from "./jobs-provider";

type Workspace = { id: string; name: string; role: string };
type Artifact = { path: string; name: string; size_bytes: number };
type DownloadProgress = {
  folder_exists: boolean;
  files_downloaded: number;
  videos_downloaded: number;
  images_downloaded: number;
  audio_downloaded: number;
  bytes_downloaded: number;
  has_files_on_disk: boolean;
};
type LibraryProgress = {
  total: number;
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  active: number;
};
type DownloadJob = {
  id: string;
  status: string;
  error?: string | null;
  created_at: string;
  payload: {
    request?: { urls?: string[]; mode?: string; limit?: number };
    output_root?: string;
  };
  result?: { artifacts?: Artifact[]; summary?: string; creator_urls?: string[] } | null;
  progress?: DownloadProgress;
  library_progress?: LibraryProgress;
};
type MediaStatus = {
  douyin: {
    installed: boolean;
    active: boolean;
    revision?: string;
    cookies_ready?: boolean;
    /** signed_in distinguishes a logged-in session from a merely-visited one. */
    cookies?: { signed_in?: boolean };
    connection?: { state: string; message: string };
  };
  tiktok: { installed: boolean; active: boolean; reason: string };
};
type QueueFilter = "all" | "active" | "completed" | "attention";

const ACTIVE_STATUSES = new Set([
  "queued", "running", "in_progress", "pending", "processing", "downloading_preparing",
]);
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
  // The worker hands each finished source to the Library while it downloads the
  // next one, so a running job can legitimately be doing both at once.
  const preparing = (job.library_progress?.active ?? 0) > 0;
  if (preparing && (job.status === "running" || job.status === "in_progress")) {
    return "downloading_preparing";
  }
  if (job.status === "succeeded" && preparing) return "processing";
  if (job.status === "succeeded" && ((job.library_progress?.failed ?? 0) + (job.library_progress?.cancelled ?? 0)) > 0) return "partial";
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
    processing: "Preparing Library",
    downloading_preparing: "Downloading + preparing",
    succeeded: "Downloaded",
    failed: "Needs attention",
    partial: "Needs attention",
    empty: "No files found",
    cancelled: "Cancelled",
  } as Record<string, string>)[status] ?? status.replaceAll("_", " ");
}

export function isDouyinSource(url: string): boolean {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    if (host === "v.douyin.com") return parsed.pathname !== "/";
    const douyinHost = host === "douyin.com" || host.endsWith(".douyin.com") || host === "iesdouyin.com" || host.endsWith(".iesdouyin.com");
    return douyinHost && ["/video/", "/note/", "/user/", "/mix/", "/music/"].some((part) => parsed.pathname.toLowerCase().includes(part));
  } catch {
    return false;
  }
}

function friendlyDownloadError(error: string): string {
  if (/datetime is not JSON serializable/i.test(error)) {
    return "The files are saved, but TrendRelay could not finish recording the batch. Resume to finalize it without redownloading retained files.";
  }
  if (/cookies|anti-bot|without saving any media|could not access this source/i.test(error)) {
    return "Douyin could not access this source. Refresh the Douyin session, then retry with a specific video or profile link.";
  }
  return error;
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

function progressSummary(
  progress: DownloadProgress | undefined,
  status: string,
  artifacts: Artifact[],
  limit: number | undefined,
): string {
  if (progress) {
    if (progress.videos_downloaded > 0) {
      const videos = `${progress.videos_downloaded} ${progress.videos_downloaded === 1 ? "video" : "videos"}`;
      const files = `${progress.files_downloaded} ${progress.files_downloaded === 1 ? "file" : "files"}`;
      return progress.files_downloaded === progress.videos_downloaded ? `${videos} downloaded` : `${videos} · ${files} on disk`;
    }
    if (progress.files_downloaded > 0) return `${progress.files_downloaded} media ${progress.files_downloaded === 1 ? "file" : "files"} on disk`;
    if (progress.folder_exists && ACTIVE_STATUSES.has(status)) return "0 videos downloaded so far";
    if (status === "succeeded") return "no files on disk";
  }
  if (artifacts.length) return `${artifacts.length} files`;
  return limit === 0 ? "all videos" : `up to ${limit ?? "—"} per source`;
}

function progressBreakdown(progress: DownloadProgress): string {
  const parts = [];
  if (progress.videos_downloaded) parts.push(`${progress.videos_downloaded} ${progress.videos_downloaded === 1 ? "video" : "videos"}`);
  if (progress.images_downloaded) parts.push(`${progress.images_downloaded} ${progress.images_downloaded === 1 ? "image" : "images"}`);
  if (progress.audio_downloaded) parts.push(`${progress.audio_downloaded} audio`);
  parts.push(`${progress.files_downloaded} total ${progress.files_downloaded === 1 ? "file" : "files"}`);
  if (progress.bytes_downloaded) parts.push(size(progress.bytes_downloaded));
  return parts.join(" · ");
}

/**
 * What the Library step is doing, in words that separate waiting from stuck.
 *
 * This said "0 of 4 prepared · 4 remaining", which reads as a stalled job. It
 * was accurate - ingestion had not started yet - but nothing distinguished
 * "queued and it will happen" from "queued and nothing is coming", and the
 * difference is the whole question a reader has. It cost a bug report against a
 * pipeline that was working.
 *
 * `queued` and `running` were already in the payload; only the sentence
 * collapsed them.
 */
function libraryProgressBreakdown(
  progress: LibraryProgress,
  t: (path: string, values?: Record<string, string | number>) => string,
): string {
  const parts: string[] = [];
  if (progress.succeeded === progress.total && progress.total > 0) {
    parts.push(t("downloads.addedToLibrary", { count: progress.succeeded }));
  } else {
    parts.push(t("downloads.addedOf", { done: progress.succeeded, total: progress.total }));
    if (progress.running) parts.push(t("downloads.addingNow", { count: progress.running }));
    if (progress.queued) parts.push(t("downloads.waitingTurn", { count: progress.queued }));
  }
  if (progress.failed) parts.push(t("downloads.addFailed", { count: progress.failed }));
  if (progress.cancelled) parts.push(t("downloads.addCancelled", { count: progress.cancelled }));
  return parts.join(" · ");
}
function isVisibleForFilter(job: DownloadJob, filter: QueueFilter): boolean {
  const current = effectiveStatus(job);
  if (filter === "active") return ACTIVE_STATUSES.has(current);
  if (filter === "completed") return current === "succeeded";
  if (filter === "attention") return ["failed", "partial", "empty", "cancelled"].includes(current);
  return true;
}

export default function Dashboard() {
  const t = useT();
  const { loading, user, apiFetch, retryAuth, probeError } = useAuth();
  const { jobs: allJobs, busy: jobsBusy, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [input, setInput] = useState("");
  // Download options are a standing preference, not a per-visit choice. The
  // same guard checks what is restored and what the select hands back.
  const [mode, setMode] = usePersistedState(
    "trendrelay.downloads.mode", "post", isDownloadMode,
  );
  /** Video is always fetched; cover images and audio are opt-in extras. */
  const [mediaKinds, setMediaKinds] = usePersistedState<string[]>(
    "trendrelay.downloads.mediaKinds", ["video"], subsetOf("video", "image", "audio"),
  );
  const [limit, setLimit] = usePersistedState(
    "trendrelay.downloads.limit", 0, numberIn(0, 10, 20, 50, 100),
  );
  const [status, setStatus] = useState<MediaStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [clearingHistory, setClearingHistory] = useState(false);
  const [resumingJobId, setResumingJobId] = useState("");
  const [cancellingJobId, setCancellingJobId] = useState("");
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [visibleJobCount, setVisibleJobCount] = useState(INITIAL_JOB_COUNT);
  // Announced over the page rather than inside it. Rendering these in flow
  // pushed everything below them down by 57px and pulled it back up again, so
  // an action reporting itself moved the row holding the button that ran it.
  const { messages: statusMessages, succeed, fail, dismiss, clear: clearStatus } = useStatus();
  const linkInputRef = useRef<HTMLTextAreaElement>(null);

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
    attention: jobs.filter((job) => ["failed", "partial", "empty", "cancelled"].includes(effectiveStatus(job))).length,
  }), [jobs]);

  function addCreatorProfiles(profiles: string[]) {
    const staged = input.split(/\s+/).filter(Boolean);
    const fresh = profiles.filter((profile) => !staged.includes(profile));
    if (!fresh.length) {
      succeed(
        profiles.length === 1
          ? "That creator's profile is already in the list."
          : "Those creator profiles are already in the list.",
      );
      return;
    }
    setInput([...staged, ...fresh].join("\n"));
    succeed(
      `Added ${fresh.length} creator ${fresh.length === 1 ? "profile" : "profiles"}. ` +
      "Downloading a profile fetches its whole catalogue, not just this clip.",
    );
    requestAnimationFrame(() => {
      linkInputRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      linkInputRef.current?.focus();
    });
  }

  function reuseLinks(sources: string[]) {
    if (!sources.length) return;
    setInput(sources.join("\n"));
    succeed(`${sources.length} ${sources.length === 1 ? "link is" : "links are"} ready to download again.`);
    requestAnimationFrame(() => {
      linkInputRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      linkInputRef.current?.focus();
    });
  }

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    // Discover hands a trending video over as ?add=, so arriving here means
    // the link box should already hold it rather than asking for a paste.
    queueMicrotask(() => {
      const handoff = new URLSearchParams(window.location.search).get("add");
      if (!handoff) return;
      setInput(handoff);
      succeed("Link brought over from Discover — review it, then start the download.");
      window.history.replaceState({}, "", window.location.pathname);
    });
    // The handoff is read once, on arrival; succeed is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    const fetchStatus = async () => {
      try {
        const body = await json<MediaStatus>(await apiFetch("/api/workspaces/" + workspaceId + "/media/status"));
        if (!cancelled) setStatus(body);
      } catch (reason) {
        if (!cancelled) fail(reason instanceof Error ? reason.message : "Media service unavailable.");
      }
    };
    void fetchStatus();
    // Don't poll a tab nobody is looking at; pick it back up on return.
    const timer = setInterval(() => {
      if (document.visibilityState !== "hidden") void fetchStatus();
    }, 4000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void fetchStatus();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [apiFetch, workspaceId, fail]);

  useEffect(() => {
    if (!user) return;
    fetchWorkspaces(apiFetch)
      .then((body) => {
        setWorkspaces(body.workspaces);
        setWorkspaceId((current) => current || body.workspaces[0]?.id || "");
      })
      .catch((reason: unknown) => fail(reason instanceof Error ? reason.message : "Could not load workspaces."));
  }, [apiFetch, user, fail]);

  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);
  const providerReady = Boolean(status?.douyin.installed && status?.douyin.active);
  const cookiesReady = status?.douyin.cookies_ready === true;
  // Only an explicit false warns: an older API without the field says nothing
  // about the session, and warning on silence would nag every setup.
  const anonymousSession = status?.douyin.cookies?.signed_in === false;
  const connectionState = status?.douyin.connection?.state ?? "disconnected";
  const refreshRequired = connectionState === "refresh_required";
  const canFetch = providerReady && cookiesReady && !refreshRequired;
  const connectionActive = ["starting", "installing", "opening_browser", "waiting_for_login"].includes(connectionState);

  async function connectDouyin() {
    if (!workspaceId) return;
    setConnecting(true);
    clearStatus();
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
      succeed("Douyin opened in a separate window. Finish signing in there; TrendRelay will detect it automatically.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Douyin connection could not start.");
    } finally {
      setConnecting(false);
    }
  }

  async function pasteLinks() {
    clearStatus();
    try {
      const text = await navigator.clipboard.readText();
      if (!text.trim()) {
        fail("Your clipboard does not contain any text.");
        return;
      }
      setInput((current) => current.trim() ? current.trim() + "\n" + text.trim() : text.trim());
    } catch {
      fail("Clipboard access was not available. Paste into the box with Ctrl+V instead.");
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
      fail("Paste a specific Douyin video, profile, collection, music, or v.douyin.com share link.");
      requestAnimationFrame(() => linkInputRef.current?.focus());
      return;
    }
    if (!canFetch) {
      fail(
        refreshRequired
          ? "Refresh the Douyin session before starting this download."
          : providerReady
            ? "Connect your Douyin session before starting this download."
            : "Enable the Douyin downloader in Tools before starting this download.",
      );
      return;
    }
    setBusy(true);
    clearStatus();
    try {
      await json(await apiFetch("/api/workspaces/" + workspaceId + "/media/douyin/downloads", {
        method: "POST",
        body: JSON.stringify({
          workspace_id: workspaceId,
          urls,
          mode,
          media_kinds: mediaKinds,
          limit,
          incremental: true,
          confirm_external_action: true,
        }),
      }));
      setInput("");
      setQueueFilter("active");
      setVisibleJobCount(INITIAL_JOB_COUNT);
      succeed(urls.length === 1 ? "Download added to the queue." : urls.length + " downloads added to the queue.");
      await refreshJobs();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Download could not start.");
    } finally {
      setBusy(false);
    }
  }

  async function openFolder(path: string) {
    clearStatus();
    try {
      await json(await apiFetch("/api/tools/open-folder", {
        method: "POST",
        body: JSON.stringify({ path }),
      }));
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The download folder could not be opened.");
    }
  }

  async function clearUnavailableDownloads() {
    if (!window.confirm("Clear download history with no files on disk? Active downloads and jobs with retained files will stay.")) return;
    setClearingHistory(true);
    clearStatus();
    try {
      const body = await json<{
        cleanup: {
          removed_job_ids: string[];
          preserved_active_job_ids: string[];
          preserved_on_disk_job_ids: string[];
        };
      }>(await apiFetch(`/api/workspaces/${workspaceId}/media/downloads/clear`, {
        method: "POST",
        body: JSON.stringify({ confirm_external_action: true }),
      }));
      const removed = body.cleanup.removed_job_ids.length;
      const active = body.cleanup.preserved_active_job_ids.length;
      const retained = body.cleanup.preserved_on_disk_job_ids.length;
      succeed(`${removed} ${removed === 1 ? "download" : "downloads"} cleared. ${retained} with files and ${active} active kept.`);
      setVisibleJobCount(INITIAL_JOB_COUNT);
      await refreshJobs();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Download history could not be cleared.");
    } finally {
      setClearingHistory(false);
    }
  }

  async function cancelDownload(jobId: string) {
    setCancellingJobId(jobId);
    clearStatus();
    try {
      await json(await apiFetch(`/api/workspaces/${workspaceId}/media/downloads/${jobId}/cancel`, {
        method: "POST",
        body: JSON.stringify({}),
      }));
      succeed("Stopping the download. Anything already saved is kept.");
      await refreshJobs();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The download could not be stopped.");
    } finally {
      setCancellingJobId("");
    }
  }

  async function resumeDownload(jobId: string, fromSavedFiles = false) {
    setResumingJobId(jobId);
    clearStatus();
    try {
      await json(await apiFetch(`/api/workspaces/${workspaceId}/media/downloads/${jobId}/resume`, {
        method: "POST",
        body: JSON.stringify({ confirm_external_action: true, from_saved_files: fromSavedFiles }),
      }));
      succeed(fromSavedFiles
        ? "Finishing the files already saved. No new Douyin request is needed."
        : "Download resumed. Existing files will be kept while TrendRelay checks for anything missing.");
      await refreshJobs();
      // Only once the refreshed jobs agree it is active. Switching first
      // filtered the old list, which still had this job as failed, so the row
      // under the cursor disappeared and came back.
      selectQueueFilter("active");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The download could not be resumed.");
    } finally {
      setResumingJobId("");
    }
  }

  // The retry is offered from the first frame rather than after a delay. Every
  // way of measuring that delay is a timer, and a hidden tab freezes the page's
  // timers - which is what made this hang in the first place. A button that is
  // briefly redundant beats an escape hatch that shares the fault it escapes.
  if (loading) return <main className="console-page"><div className="loading-panel">
    <strong>{t("workspace.loading")}</strong>
    <span>Waiting on TrendRelay&apos;s local API. If it is restarting this can hang.</span>
    {/* What actually went wrong, rather than leaving a refused connection and
        an API that has not started looking identical. */}
    {probeError && <code className="loading-panel-error">{probeError}</code>}
    <div className="loading-panel-actions">
      <Button variant="secondary" size="sm" onClick={retryAuth}>{t("downloads.tryAgain")}</Button>
      {/* A plain anchor on purpose. next/link navigates on the client, which
          needs the React that may be the thing that died; a real navigation
          does not. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a className={buttonClass({ variant: "quiet", size: "sm" })} href="/">{t("common.reload")}</a>
    </div>
  </div></main>;
  if (!user) return <main className="console-page"><section className="empty-console"><strong>TrendRelay</strong><h1>{t("downloads.signInPrompt")}</h1><p>{t("downloads.signInIntro")}</p><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2F">{t("nav.signIn")}</Link></section></main>;

  return <main className="console-page">
    <section className="console-heading downloader-heading">
      <div>
        <p className="eyebrow">{t("downloads.eyebrowAcquisition")}</p>
        <h1>{t("downloads.heading")}</h1>
        <p>{t("downloads.intro")}</p>
      </div>
      <label className="workspace-control">
        <span>{t("workspace.select")}</span>
        <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
          {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}
        </select>
      </label>
    </section>

    {!workspaceId && <section className="empty-console"><h2>{t("downloads.workspaceFirst")}</h2><p>{t("downloads.workspaceOwns")}</p><Link className={buttonClass({ variant: "primary" })} href="/workspaces">{t("downloads.createWorkspace")}</Link></section>}
    {workspaceId && <>


      <section className="downloader-layout">
        <form id="add-links" className="download-composer" onSubmit={fetchMedia}>
          <div className="download-card-heading">
            <div>
              <p className="step-kicker">{t("downloads.step", { number: 1 })}</p>
              <h2>{t("downloads.addLinks")}</h2>
              <p>{t("downloads.addLinksHelp")}</p>
            </div>
            <span className={"connection-badge " + (canFetch ? "ready" : connectionActive ? "working" : "setup")}>
              <i aria-hidden="true" />
              {canFetch ? "Douyin connected" : connectionActive ? "Waiting for sign-in" : "Connection needed"}
            </span>
          </div>

          <div className="link-input-field">
            <label className="link-input-label" htmlFor="douyin-links">{t("downloads.linksLabel")}</label>
            <div className="link-input-shell">
              <textarea
                ref={linkInputRef}
                id="douyin-links"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                rows={5}
                placeholder={"Paste a video, profile, collection, or share message…\nhttps://www.douyin.com/video/…"}
                aria-describedby="douyin-link-help"
              />
              <div className="link-input-footer">
                <span id="douyin-link-help">{urls.length ? urls.length + " Douyin " + (urls.length === 1 ? "link" : "links") + " detected" + (unsupportedCount ? " · " + unsupportedCount + " unsupported ignored" : "") : unsupportedCount ? "Use a specific video, profile, collection, music, or v.douyin.com share link" : "Video · Profile · Collection · Music"}</span>
                <Button variant="link" size="sm" onClick={() => void pasteLinks()}><ActionIcon name="copy" />{t("downloads.pasteFromClipboard")}</Button>
              </div>
            </div>
          </div>

          {urls.length > 0 && <section className="detected-sources" aria-label={t("downloads.detectedSources")}>
            <div className="detected-heading"><strong>{t("downloads.readyToDownload")}</strong><span>{urls.length} {urls.length === 1 ? "source" : "sources"}</span></div>
            <ul>
              {urls.map((url) => <li key={url}>
                <span className="source-kind">{sourceType(url)}</span>
                <span className="source-address" title={url}>{shortSource(url)}</span>
                <button type="button" onClick={() => removeSource(url)} aria-label={"Remove " + shortSource(url)}>{t("downloads.remove")}</button>
              </li>)}
            </ul>
          </section>}

          {!providerReady && <div className="connection-callout warning">
            <div><strong>{t("downloads.installProvider")}</strong><span>{t("downloads.installProviderHelp")}</span></div>
            <Link className={buttonClass({ variant: "secondary" })} href="/tools">{t("downloads.openTools")}</Link>
          </div>}
          {providerReady && !cookiesReady && <div className="connection-callout warning">
            <div>
              <strong>{connectionActive ? "Finish signing in to Douyin" : "Connect your Douyin session"}</strong>
              <span>{status?.douyin.connection?.message ?? "TrendRelay opens a dedicated login window and stores the session only on this computer."}</span>
            </div>
            <Button variant="secondary" busy={connecting} disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}>
              {connecting || connectionActive ? "Waiting for sign-in" : "Connect Douyin"}
            </Button>
          </div>}
          {providerReady && cookiesReady && refreshRequired && <div className="connection-callout warning">
            <div><strong>{t("downloads.refreshSession")}</strong><span>{status?.douyin.connection?.message}</span></div>
            <Button variant="secondary" busy={connecting} disabled={selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}><ActionIcon name="refresh" />{connecting ? "Opening" : "Refresh session"}</Button>
          </div>}
          {providerReady && cookiesReady && !refreshRequired && anonymousSession && <div className="connection-callout connected">
            {/* Anonymous works: single links reliably, and a profile is read
                in a browser that recovers more than the first page when Douyin
                allows it. Not a warning; signing in is the dependable path for
                whole profiles and the only path for topic search. */}
            <div><strong>Douyin connected (signed out)</strong><span>{status?.douyin.connection?.message}</span></div>
            <button type="button" className={buttonClass({ variant: "link" })} disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}>
              {connecting || connectionActive ? "Opening…" : "Log in for full profiles"}
            </button>
          </div>}
          {providerReady && cookiesReady && !refreshRequired && !anonymousSession && <div className="connection-callout connected">
            <div><strong>{t("downloads.readyToDownload")}</strong><span>{t("downloads.refreshSessionHelp")}</span></div>
            <button type="button" className={buttonClass({ variant: "link" })} disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}>
              {connecting || connectionActive ? "Refreshing…" : "Refresh session"}
            </button>
          </div>}

          <details className="download-options">
            <summary>{t("downloads.options")} <span>{modeLabel(mode)} · {limit === 0 ? "all videos" : `up to ${limit} per source`} · {mediaKinds.length === 3 ? "video, images and audio" : mediaKinds.length === 1 ? "video only" : `video and ${mediaKinds.includes("image") ? "images" : "audio"}`}</span></summary>
            <div className="download-options-grid">
              <label><span>{t("downloads.fromProfiles")}</span><select value={mode} onChange={(event) => { if (isDownloadMode(event.target.value)) setMode(event.target.value); }}><option value="post">{t("downloads.publishedPosts")}</option><option value="like">{t("downloads.likedVideos")}</option><option value="mix">{t("downloads.collections")}</option><option value="music">{t("downloads.musicVideos")}</option></select></label>
              <fieldset><legend>{t("downloads.perSource")}</legend><div className="limit-presets">{[0, 10, 20, 50, 100].map((value) => <button key={value} type="button" className={limit === value ? "selected" : ""} aria-pressed={limit === value} onClick={() => setLimit(value)}>{value === 0 ? "All" : value}</button>)}</div></fieldset>
              <fieldset>
                <legend>{t("downloads.whatToFetch")}</legend>
                <div className="limit-presets">
                  {([
                    ["video", "Video", "The post itself; always fetched"],
                    ["image", "Cover images", "One still per post"],
                    ["audio", "Audio track", "The sound a post uses"],
                  ] as const).map(([kind, label, hint]) => (
                    <button
                      key={kind}
                      type="button"
                      title={hint}
                      className={mediaKinds.includes(kind) ? "selected" : ""}
                      aria-pressed={mediaKinds.includes(kind)}
                      disabled={kind === "video"}
                      onClick={() => setMediaKinds(mediaKinds.includes(kind)
                        ? mediaKinds.filter((item) => item !== kind)
                        : [...mediaKinds, kind])}
                    >{label}</button>
                  ))}
                </div>
              </fieldset>
            </div>
            <p>{t("downloads.whatToFetchHelp")}</p>
          </details>

          <div className="download-submit-row">
            <button className={`${buttonClass({ variant: "primary" })} download-button`} disabled={busy}>
              {busy ? "Adding to queue…" : urls.length > 1 ? "Download " + urls.length + " sources" : "Start download"}
            </button>
            <small>{t("downloads.authorisedOnly")}</small>
          </div>
        </form>

      <section id="download-queue" className="download-queue-card">
        <div className="queue-heading">
          <div><p className="step-kicker">{t("downloads.step", { number: 2 })}</p><h2>{t("downloads.heading2")}</h2><p>{t("downloads.autoUpdate")}</p></div>
          <div className="queue-heading-actions">
            {jobs.length > 0 && <button type="button" className={`${buttonClass({ variant: "link" })} clear-downloads-button`} disabled={clearingHistory || jobsBusy} onClick={() => void clearUnavailableDownloads()}><ActionIcon name="dismiss" />{clearingHistory ? "Clearing…" : "Clear missing files"}</button>}
            <Button
              variant="secondary"
              iconOnly
              aria-label={t("downloads.refreshList")}
              title={t("downloads.refreshList")}
              disabled={jobsBusy}
              onClick={() => void refreshJobs()}
            ><RefreshCw className={jobsBusy ? "spinning" : ""} size={16} strokeWidth={2} /></Button>
          </div>
        </div>
        <div className="queue-filters" role="group" aria-label={t("downloads.filter")}>
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
          {!jobs.length && <a href="#add-links">{t("downloads.addLinks")}</a>}
        </div>}

        <div className="download-job-list">
          {visibleJobs.map((job) => {
            const current = effectiveStatus(job);
            const sources = job.payload.request?.urls ?? [];
            const artifacts = job.result?.artifacts ?? [];
            const progress = job.progress;
            const libraryProgress = job.library_progress;
            const preparingLibrary = current === "processing";
            const downloadingAndPreparing = current === "downloading_preparing";
            const libraryPercent = libraryProgress?.total ? Math.round((libraryProgress.succeeded / libraryProgress.total) * 100) : 0;
            const downloadCount = progressSummary(progress, current, artifacts, job.payload.request?.limit);
            const creatorProfiles = (job.result?.creator_urls ?? []).filter(
              (profile) => !sources.includes(profile),
            );
            const canOpenFolder = Boolean(job.payload.output_root && (progress?.folder_exists || (!progress && job.status === "succeeded")));
            return <details key={job.id} className={"download-job " + current} open={ACTIVE_STATUSES.has(current) || undefined}>
              <summary>
                <span className={"job-status " + current}><i aria-hidden="true" />{job.error && current === "queued" ? "Waiting to retry" : statusLabel(current)}</span>
                <span className="download-job-summary-title"><strong>{sources[0] ? shortSource(sources[0]) : job.id}</strong><small>{sources.length > 1 ? sources.length + " sources" : sources[0] ? sourceType(sources[0]) : "Douyin batch"} · {downloadCount}</small></span>
                <time>{new Date(job.created_at).toLocaleString()}</time>
                <span className="job-disclosure" aria-hidden="true">
                  <svg viewBox="0 0 16 16" focusable="false"><path d="m4 6 4 4 4-4" /></svg>
                </span>
              </summary>
              <div className="download-job-body">
                {ACTIVE_STATUSES.has(current) && <div className={"job-progress " + current} aria-label={current === "queued" ? "Waiting to start" : preparingLibrary ? "Preparing downloaded media for Library" : downloadingAndPreparing ? "Downloading while preparing earlier files for Library" : "Download in progress"}><span style={preparingLibrary ? { width: `${libraryPercent}%` } : undefined} /></div>}
                {progress?.folder_exists && <div className="download-live-status"><strong>{job.error && current === "queued" ? "Ready to resume" : preparingLibrary ? "Preparing Library" : downloadingAndPreparing ? "Downloading now · preparing Library" : ACTIVE_STATUSES.has(current) ? "Downloading now" : "Files on disk"}</strong><span>{libraryProgress && (preparingLibrary || downloadingAndPreparing) ? `${progressBreakdown(progress)} · ${libraryProgressBreakdown(libraryProgress, t)}` : progressBreakdown(progress)}</span></div>}
                {ACTIVE_STATUSES.has(current) && !job.error && <div className="download-job-actions">
                  {/* Stop keeps whatever already downloaded - a running job
                      halts at its next source, a queued one right away. */}
                  <Button variant="secondary" size="sm" disabled={cancellingJobId === job.id} onClick={() => void cancelDownload(job.id)}><ActionIcon name="dismiss" />{cancellingJobId === job.id ? "Stopping…" : "Stop download"}</Button>
                </div>}
                {(sources.length > 0 || canOpenFolder || job.status === "succeeded") && <div className="download-job-actions">
                  {sources.length > 0 && <Button variant="secondary" size="sm" onClick={() => reuseLinks(sources)}><ActionIcon name="link" />Reuse {sources.length === 1 ? "link" : "links"}</Button>}
                  {creatorProfiles.length > 0 && <Button variant="secondary" size="sm" title={t("downloads.addCreatorProfile")} onClick={() => addCreatorProfiles(creatorProfiles)}>Add creator {creatorProfiles.length === 1 ? "profile" : `profiles (${creatorProfiles.length})`}</Button>}
                  {sources[0] && <a href={sources[0]} target="_blank" rel="noreferrer">{t("downloads.openSource")}</a>}
                  {canOpenFolder && <Button variant="secondary" size="sm" onClick={() => void openFolder(job.payload.output_root!)}><ActionIcon name="openFolder" />{t("downloads.openFolder")}</Button>}
                  {job.status === "succeeded" && <Link href="/library">{t("downloads.openLibrary")}</Link>}
                </div>}
                {job.result?.summary && current === "succeeded" && <p className="job-summary">{job.result.summary}. Files were also added to the media library.</p>}
                {job.error && <div className="job-error"><strong>{current === "queued" ? "Download ready to resume" : "Download stopped"}</strong><span>{friendlyDownloadError(job.error)}</span><div className="download-recovery-actions">{(progress?.files_downloaded ?? 0) > 0 && <button type="button" className={buttonClass({ variant: "link" })} disabled={resumingJobId === job.id || current === "running"} onClick={() => void resumeDownload(job.id, true)}><ActionIcon name="confirm" />{resumingJobId === job.id ? "Working…" : "Finish saved files"}</button>}<button type="button" className={buttonClass({ variant: "link" })} disabled={resumingJobId === job.id || current === "running"} onClick={() => void resumeDownload(job.id)}><ActionIcon name="play" />{resumingJobId === job.id ? "Working…" : "Resume download"}</button><button type="button" className={buttonClass({ variant: "link" })} disabled={connecting || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}><ActionIcon name="refresh" />{connecting ? "Opening…" : "Refresh session"}</button><button type="button" className={buttonClass({ variant: "link" })} onClick={() => reuseLinks(sources)}><ActionIcon name="link" />Reuse {sources.length === 1 ? "link" : "links"}</button></div></div>}
                {current === "empty" && !job.error && <div className="job-error"><strong>{t("downloads.noneSaved")}</strong><span>{t("downloads.reuseLinks")}</span><button type="button" className={buttonClass({ variant: "link" })} onClick={() => reuseLinks(sources)}><ActionIcon name="link" />Reuse {sources.length === 1 ? "link" : "links"}</button></div>}
                {artifacts.length > 0 && <div className="artifact-list">
                  {artifacts.slice(0, 4).map((artifact) => <div className="artifact-row" key={artifact.path}>
                    <div><strong>{artifact.name}</strong><small>{size(artifact.size_bytes)}</small></div>
                    <div><Link href={"/library?asset=" + encodeURIComponent(artifact.path)}>{t("downloads.openInLibrary")}</Link><Link href={"/campaigns?video=" + encodeURIComponent(artifact.path)}>{t("downloads.plan")}</Link><Link href={"/publish?video=" + encodeURIComponent(artifact.path)}>{t("nav.publish")}</Link></div>
                  </div>)}
                  {artifacts.length > 4 && <p className="more-artifacts">+ {artifacts.length - 4} more files in this batch</p>}
                </div>}
              </div>
            </details>;
          })}
        </div>
        {visibleJobs.length < filteredJobs.length && <button type="button" className="queue-show-more" onClick={() => setVisibleJobCount((current) => current + INITIAL_JOB_COUNT)}>Show {Math.min(INITIAL_JOB_COUNT, filteredJobs.length - visibleJobs.length)} more downloads</button>}
      </section>
      </section>
    </>}
    <StatusToasts messages={statusMessages} onDismiss={dismiss} />
  </main>;
}