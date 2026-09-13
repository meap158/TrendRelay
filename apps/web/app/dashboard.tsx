"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { File as FileIcon, ImageOff, RefreshCw } from "lucide-react";

import { useAuth } from "./auth-provider";
import { useT } from "./i18n-provider";
import { Button, buttonClass } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { LoadingMark } from "./ui/loading-mark";
import { WaitingBlock } from "./ui/waiting-block";
import { ActionIcon } from "./ui/action-icons";
import { StatusToasts, useStatus } from "./ui/status";
import { Select } from "./ui/select";
import { numberIn, oneOf, subsetOf, usePersistedState } from "./ui/use-persisted-state";

const isDownloadMode = oneOf("post", "like", "mix", "music");
import { useJobs } from "./jobs-provider";
import { useWorkspace } from "./workspace-provider";
import {
  FALLBACK_PROVIDERS,
  describeConflict,
  detect,
  kindOf,
  type DownloadProvider,
} from "../lib/download-providers";
import { readTabSnapshot, refreshTabSnapshot } from "../lib/tab-snapshots";
import { downloadFileLibraryHref, downloadLibraryHref } from "../lib/job-links";
import { useOpaqueMedia } from "../lib/media-preview";
import { downloadRunRollup } from "../lib/download-run-rollup";
import { DOUYIN_BOOKMARKLET } from "../lib/douyin-bookmarklet";
import { downloadGroupRequest, downloadGroupUrls, importBatchCount, isCapturedVideoUrl, parseCapturedLinks } from "../lib/douyin-import-links";

type Artifact = { path: string; name: string; size_bytes: number; sha256?: string };
type DownloadLibraryAsset = {
  id: string;
  title: string;
  creator?: string | null;
  media_kind: "video" | "audio" | "image";
  original_path: string;
  versions: Array<{ kind: string; path: string }>;
};
const CampaignPicker = dynamic(
  () => import("./library/campaign-picker").then((module) => module.CampaignPicker),
  { ssr: false },
);
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
/** One source's coverage: what the profile declares vs what we hold. */
type SourceStat = {
  url: string;
  kind: string;
  sec_uid?: string;
  nickname?: string;
  /** The profile's own 作品 count, read from Douyin at job end. */
  declared_total?: number;
  /** How many of that author's posts the downloader holds, across all runs. */
  held?: number;
  error?: string;
};
type DownloadJob = {
  id: string;
  status: string;
  error?: string | null;
  created_at: string;
  payload: {
    service?: "douyin" | "tiktok";
    request?: { urls?: string[]; mode?: string; limit?: number; media_kinds?: string[] };
    source_group_request?: { urls?: string[]; mode?: string; limit?: number; media_kinds?: string[] };
    output_root?: string;
  };
  result?: {
    artifacts?: Artifact[];
    summary?: string;
    creator_urls?: string[];
    source_stats?: SourceStat[];
    source_errors?: string[];
    incomplete_profile?: boolean;
    requires_sign_in?: boolean;
  } | null;
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

function profileRequestIncomplete(job: DownloadJob): boolean {
  if (job.result?.incomplete_profile) return true;
  if ((job.payload.request?.limit ?? 0) !== 0) return false;
  return (job.result?.source_stats ?? []).some((stat) =>
    stat.kind === "profile" && Boolean(stat.declared_total)
      && (stat.held ?? 0) < (stat.declared_total ?? 0),
  );
}

function DownloadArtifactThumbnail({
  artifact,
  workspaceId,
  apiFetch,
  resolveAsset,
}: {
  artifact: Artifact;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  resolveAsset: (artifact: Artifact) => Promise<DownloadLibraryAsset>;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const metadataLoading = useRef(false);
  const tooltipId = useId();
  const [asset, setAsset] = useState<DownloadLibraryAsset | null>(null);
  const [previewRatio, setPreviewRatio] = useState(16 / 9);
  const [previewPosition, setPreviewPosition] = useState<{
    left: number;
    top: number;
    width: number;
    mediaHeight: number;
  } | null>(null);
  const previewable = /\.(?:mp4|m4v|mov|webm|mkv|jpe?g|png|webp|gif)$/i.test(artifact.path);
  const source = previewable
    ? `/api/workspaces/${workspaceId}/publishing/media/preview?thumbnail=true&path=${encodeURIComponent(artifact.path)}${artifact.sha256 ? `&sha256=${artifact.sha256}` : ""}`
    : "";
  const { objectUrl, problem } = useOpaqueMedia(
    source,
    artifact.path,
    "image/jpeg",
    Boolean(source),
    apiFetch,
  );

  function showPreview() {
    const trigger = triggerRef.current;
    if (!trigger || !objectUrl) return;
    const rect = trigger.getBoundingClientRect();
    const compact = window.innerWidth <= 640;
    const maxWidth = compact ? Math.min(188, window.innerWidth - 16) : 260;
    const maxMediaHeight = compact
      ? Math.min(250, window.innerHeight - 104)
      : Math.min(320, window.innerHeight - 104);
    let width = maxWidth;
    let mediaHeight = width / previewRatio;
    if (mediaHeight > maxMediaHeight) {
      mediaHeight = maxMediaHeight;
      width = mediaHeight * previewRatio;
    }
    // Two compact metadata lines below the frame. The estimate is used only
    // to keep the portalled card inside the viewport; content remains auto-sized.
    const estimatedHeight = mediaHeight + 58;
    const gap = 10;
    const left = rect.right + gap + width <= window.innerWidth - 8
      ? rect.right + gap
      : Math.max(8, rect.left - gap - width);
    const top = Math.min(
      Math.max(8, rect.top + rect.height / 2 - estimatedHeight / 2),
      Math.max(8, window.innerHeight - estimatedHeight - 8),
    );
    setPreviewPosition({ left, top, width, mediaHeight });
    // The screenshot appears immediately. Metadata is one small authenticated
    // read, only on intent, and shares the cache with both row actions.
    if (!asset && artifact.sha256 && !metadataLoading.current) {
      metadataLoading.current = true;
      void resolveAsset(artifact)
        .then(setAsset)
        .catch(() => undefined)
        .finally(() => { metadataLoading.current = false; });
    }
  }

  if (objectUrl) {
    return (
      <button
        type="button"
        ref={triggerRef}
        className="download-artifact-preview-trigger"
        aria-label={`Preview ${artifact.name}`}
        aria-describedby={previewPosition ? tooltipId : undefined}
        aria-expanded={Boolean(previewPosition)}
        onMouseEnter={showPreview}
        onMouseLeave={() => setPreviewPosition(null)}
        onFocus={showPreview}
        onBlur={() => setPreviewPosition(null)}
        onClick={showPreview}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          setPreviewPosition(null);
          event.currentTarget.blur();
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL */}
        <img
          className="download-artifact-thumbnail"
          src={objectUrl}
          alt=""
          loading="lazy"
          onLoad={(event) => {
            const image = event.currentTarget;
            if (image.naturalWidth && image.naturalHeight) {
              setPreviewRatio(image.naturalWidth / image.naturalHeight);
            }
          }}
        />
        {previewPosition && createPortal(
          <span
            id={tooltipId}
            role="tooltip"
            className="download-artifact-preview-tooltip"
            style={{
              left: previewPosition.left,
              top: previewPosition.top,
              width: previewPosition.width,
            }}
          >
            <span
              className="download-artifact-preview-media"
              style={{ height: previewPosition.mediaHeight }}
            >
              {/* Reuses the same object URL as the thumbnail: no second media request. */}
              {/* eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL */}
              <img src={objectUrl} alt={`Preview of ${asset?.title ?? artifact.name}`} />
            </span>
            <span className="download-artifact-preview-meta">
              <strong>{asset?.title ?? artifact.name}</strong>
              {asset?.creator && <small>{asset.creator}</small>}
            </span>
          </span>,
          document.body,
        )}
      </button>
    );
  }

  /**
   * Three reasons a row has no picture, drawn as three different things.
   *
   * It used to be one empty box for all of them, which reads as a frame that
   * failed to load rather than as a placeholder - and a file still being
   * fetched looked exactly like one that will never have a thumbnail.
   *
   * Said in the drawing rather than in words: these sit four to a batch beside
   * a filename that already names the file, and a line of text under each one
   * would be four sentences saying what a glyph says at a glance.
   */
  if (!previewable) {
    // Nothing to preview and nothing coming - a sidecar, a text file, audio.
    return (
      <span className="download-artifact-thumbnail is-empty" aria-hidden="true">
        <FileIcon />
      </span>
    );
  }
  if (problem) {
    // A picture was expected and did not arrive.
    return (
      <span className="download-artifact-thumbnail is-empty" aria-hidden="true">
        <ImageOff />
      </span>
    );
  }
  // Still coming. A shimmer says so honestly - it is the one state where
  // something really is on its way, which is why the other two do not use it.
  return (
    <span className="download-artifact-thumbnail is-pending" aria-hidden="true" />
  );
}

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
  // Derive this for older completed jobs too; they predate the explicit flag
  // but already carry enough coverage evidence to avoid a misleading status.
  if (job.status === "succeeded" && profileRequestIncomplete(job)) return "partial";
  if (job.status === "succeeded" && (job.result?.source_errors?.length ?? 0) > 0) return "partial";
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

function friendlyDownloadError(error: string, retainedFiles = 0, service = "Douyin"): string {
  if (/datetime is not JSON serializable/i.test(error)) {
    return "The files are saved, but TrendRelay could not finish recording the batch. Resume to finalize it without redownloading retained files.";
  }
  if (/cookies|anti-bot|without saving any media|could not access this source/i.test(error)) {
    if (retainedFiles > 0 && /without saving any media|could not access this source/i.test(error)) {
      return `${retainedFiles} retained media files are safe on disk. ${service} returned no new media on the retry; resume to finish recording the saved files locally.`;
    }
    return `${service} could not access this source. ${service === "TikTok" ? "Try Import links after loading the channel in Chrome, then retry with direct video links." : "Refresh the Douyin session, then retry with a specific video or profile link."}`;
  }
  if (service === "TikTok" && /secondary user id|channel_id/i.test(error)) {
    return "TikTok did not expose this channel's ID to the downloader. Use Import links after scrolling the channel in Chrome; direct video links will download normally.";
  }
  return error;
}

function modeLabel(mode: string, service = "Douyin"): string {
  if (service === "TikTok") return ({ post: "Channel videos", mix: "Collections" } as Record<string, string>)[mode] ?? mode;
  return ({ post: "Published posts", like: "Liked videos", mix: "Collections", music: "Music videos" } as Record<string, string>)[mode] ?? mode;
}

function sourceType(url: string, service?: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname.toLowerCase();
    if (service === "TikTok" || parsed.hostname.toLowerCase().includes("tiktok")) {
      if (path.includes("/video/") || path.includes("/photo/")) return "Video";
      if (/\/@[\w.-]+\/?$/.test(path)) return "Channel";
      if (path.includes("/collection/")) return "Collection";
      return "Share link";
    }
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

/**
 * Runs of the same source list, stacked into one row.
 *
 * Fetch missing re-queues a batch's exact sources, so every re-check used to
 * add another row saying the same thing. The newest run speaks for the group
 * - its status, its files, its coverage - and the older runs become one
 * compact history line each inside the row. Order-insensitive on purpose:
 * the same sources pasted in a different order are still the same batch.
 */
type JobGroup = { primary: DownloadJob; earlier: DownloadJob[] };

/**
 * Which service a finished batch was fetched from.
 *
 * Read off the job rather than guessed from its links: the links are what was
 * pasted, and a job records the service it actually ran against. Batches from
 * before there was a second service carry no `service` at all - they are
 * Douyin by construction, which is what they are labelled.
 */
function jobServiceId(job: DownloadJob): string {
  const service = (job.payload as { service?: string } | undefined)?.service;
  return service === "tiktok" ? "tiktok" : "douyin";
}

function jobServiceLabel(job: DownloadJob): string {
  return jobServiceId(job) === "tiktok" ? "TikTok" : "Douyin";
}

function sourceSignature(job: DownloadJob): string {
  const urls = downloadGroupRequest(job.payload).urls ?? [];
  return urls.length ? [...urls].sort().join("\n") : job.id;
}

/** Whole-number share where 100% means complete and 0% means empty - the
    in-between is clamped to 1-99 so rounding never overstates either end. */
function coveragePct(held: number, total: number): string {
  if (total <= 0 || held <= 0) return "0%";
  if (held >= total) return "100%";
  return `${Math.min(99, Math.max(1, Math.round((held * 100) / total)))}%`;
}

export default function Dashboard() {
  const t = useT();
  const router = useRouter();
  const { loading, user, apiFetch, retryAuth, probeError } = useAuth();
  const {
    jobs: allJobs, busy: jobsBusy, refresh: refreshJobs,
    initialLoadPending: jobsLoading,
  } = useJobs();
  const { workspaces, workspaceId } = useWorkspace();
  const [input, setInput] = useState("");
  // Download options are a standing preference, not a per-visit choice. The
  // same guard checks what is restored and what the select hands back.
  const [mode, setMode] = usePersistedState(
    "trendrelay.downloads.mode", "post", isDownloadMode,
  );
  /** Video is always fetched; image posts/covers and audio are opt-in extras. */
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
  const [refetchingJobId, setRefetchingJobId] = useState<string | null>(null);
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [visibleJobCount, setVisibleJobCount] = useState(INITIAL_JOB_COUNT);
  const [campaignPickerAsset, setCampaignPickerAsset] = useState<DownloadLibraryAsset | null>(null);
  const [artifactAction, setArtifactAction] = useState("");
  const [bookmarkletJobId, setBookmarkletJobId] = useState<string | null>(null);
  const [bookmarkletCopied, setBookmarkletCopied] = useState(false);
  const [capturedLinks, setCapturedLinks] = useState("");
  const [importingLinks, setImportingLinks] = useState(false);
  const [importLinksError, setImportLinksError] = useState<string | null>(null);
  const importLinksLock = useRef(false);
  const captured = useMemo(() => parseCapturedLinks(capturedLinks), [capturedLinks]);
  /**
   * How many of a batch's files the Library actually holds, per group.
   *
   * The truthful number: artifact records and folder scans both count files
   * that a failed import never finished or a cleanup later removed, and the
   * handover's own investigation began with a batch whose records said 40
   * while the Library held 42. Asked of the Library itself - each run's
   * asset ids, unioned so a file two runs both touched counts once. Keyed by
   * primary id plus run count, so a finished re-run recounts by itself.
   */
  const [libraryCounts, setLibraryCounts] = useState<Record<string, number>>({});
  const libraryCountsAsked = useRef(new Set<string>());
  // Only the four visible rows can ask for one. Resolve on intent rather than
  // issuing a Library request for every file in every expanded download.
  const artifactAssets = useRef(new Map<string, DownloadLibraryAsset>());
  // Announced over the page rather than inside it. Rendering these in flow
  // pushed everything below them down by 57px and pulled it back up again, so
  // an action reporting itself moved the row holding the button that ran it.
  const { messages: statusMessages, succeed, fail, dismiss, clear: clearStatus } = useStatus();

  async function copyBookmarklet() {
    try {
      await navigator.clipboard.writeText(DOUYIN_BOOKMARKLET);
      setBookmarkletCopied(true);
    } catch {
      setImportLinksError("Copy was blocked. Select the bookmarklet text and copy it manually.");
    }
  }
  const linkInputRef = useRef<HTMLTextAreaElement>(null);

  const jobs = useMemo(
    () => allJobs.filter((job) => job.category === "fetch").map((job) => job.raw as DownloadJob),
    [allJobs],
  );
  const bookmarkletJob = bookmarkletJobId
    ? jobs.find((job) => job.id === bookmarkletJobId) ?? null
    : null;

  async function importLinks() {
    if (!bookmarkletJob || importLinksLock.current || captured.error || !captured.urls.length) return;
    importLinksLock.current = true;
    setImportingLinks(true);
    setImportLinksError(null);
    try {
      await json(await apiFetch(`/api/workspaces/${workspaceId}/media/downloads/${bookmarkletJob.id}/import-links`, {
        method: "POST",
        body: JSON.stringify({ urls: captured.urls, confirm_external_action: true }),
      }));
      setBookmarkletJobId(null);
      setCapturedLinks("");
      setQueueFilter("active");
      setVisibleJobCount(INITIAL_JOB_COUNT);
      const batches = importBatchCount(captured.urls.length);
      succeed(batches > 1
        ? `${captured.urls.length} links queued as ${batches} download batches. Existing downloads are skipped.`
        : `${captured.urls.length} links queued in this batch. Existing downloads are skipped.`);
      await refreshJobs();
    } catch (reason) {
      setImportLinksError(reason instanceof Error ? reason.message : "Links could not be imported.");
    } finally {
      importLinksLock.current = false;
      setImportingLinks(false);
    }
  }
  const extractedUrls = useMemo(() => sourceUrls(input), [input]);
  /**
   * The services this workspace can download from, read from the API.
   *
   * The same table the server detects and refuses with. Kept as one copy on
   * purpose: two would let the box say "3 links ready" about a batch the
   * endpoint then rejects, and the person reading it would be right either way.
   */
  const [providers, setProviders] = useState<DownloadProvider[]>(FALLBACK_PROVIDERS);
  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/media/download-providers`)
      .then((response) => (response.ok ? response.json() : null))
      .then((body: { providers?: DownloadProvider[] } | null) => {
        if (cancelled || !body?.providers?.length) return;
        setProviders(body.providers);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  /** Which service this paste is for, or which two it spans. */
  const detected = useMemo(
    () => detect(providers, extractedUrls),
    [providers, extractedUrls],
  );
  const provider = detected.provider;
  const urls = detected.urls;
  const unsupportedCount = detected.ignored.length;
  const conflict = detected.conflict;
  const jobGroups = useMemo(() => {
    const bySignature = new Map<string, DownloadJob[]>();
    for (const job of jobs) {
      const signature = sourceSignature(job);
      const members = bySignature.get(signature);
      if (members) members.push(job);
      else bySignature.set(signature, [job]);
    }
    // Jobs arrive newest-first, so each group's first member is its newest
    // run and the groups themselves sit in newest-first order too.
    return [...bySignature.values()].map((members): JobGroup => ({
      primary: members[0], earlier: members.slice(1),
    }));
  }, [jobs]);
  // Filters and counts read the group through its newest run: a batch whose
  // re-check succeeded is a succeeded batch, and a superseded cancelled run
  // is history, not something still needing attention.
  const filteredGroups = useMemo(
    () => jobGroups.filter((group) => isVisibleForFilter(group.primary, queueFilter)),
    [jobGroups, queueFilter],
  );
  const visibleGroups = filteredGroups.slice(0, visibleJobCount);

  // One count per finished group on screen, asked once per key: the guard set
  // keeps the 12-second job poll from re-asking, and a re-run finishing grows
  // the run count into a fresh key, which is the recount.
  useEffect(() => {
    for (const group of visibleGroups) {
      if (group.primary.status !== "succeeded") continue;
      const key = `${group.primary.id}:${group.earlier.length}`;
      if (libraryCountsAsked.current.has(key)) continue;
      libraryCountsAsked.current.add(key);
      const runs = [group.primary, ...group.earlier];
      void (async () => {
        const assetIds = new Set<string>();
        for (const run of runs) {
          try {
            const response = await apiFetch(
              `/api/workspaces/${workspaceId}/media/library/assets/ids?download_job_id=${run.id}`,
            );
            if (!response.ok) throw new Error(String(response.status));
            const body = await response.json();
            for (const assetId of body.asset_ids ?? []) assetIds.add(assetId);
          } catch {
            // Unknown beats wrong: drop the guard so a later poll retries,
            // and the row simply shows no Library figure meanwhile.
            libraryCountsAsked.current.delete(key);
            return;
          }
        }
        setLibraryCounts((current) => ({ ...current, [key]: assetIds.size }));
      })();
    }
  }, [visibleGroups, apiFetch, workspaceId]);

  const queueCounts = useMemo(() => ({
    all: jobGroups.length,
    active: jobGroups.filter((group) => ACTIVE_STATUSES.has(effectiveStatus(group.primary))).length,
    completed: jobGroups.filter((group) => effectiveStatus(group.primary) === "succeeded").length,
    attention: jobGroups.filter((group) => ["failed", "partial", "empty", "cancelled"].includes(effectiveStatus(group.primary))).length,
  }), [jobGroups]);

  async function libraryAssetFor(artifact: Artifact): Promise<DownloadLibraryAsset> {
    if (!artifact.sha256) {
      throw new Error("This file is still being added to the Library. Try again in a moment.");
    }
    const cached = artifactAssets.current.get(artifact.sha256);
    if (cached) return cached;
    const response = await apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets?sha256=${artifact.sha256}&limit=1`,
    );
    const payload = (await response.json()) as {
      assets?: DownloadLibraryAsset[];
      detail?: string;
    };
    if (!response.ok) throw new Error(payload.detail ?? "The Library item could not be read.");
    const asset = payload.assets?.[0];
    if (!asset) {
      throw new Error("This file is still being added to the Library. Try again in a moment.");
    }
    artifactAssets.current.set(artifact.sha256, asset);
    return asset;
  }

  async function addArtifactToCampaign(artifact: Artifact) {
    setArtifactAction(`campaign:${artifact.path}`);
    try {
      setCampaignPickerAsset(await libraryAssetFor(artifact));
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The campaign picker could not be opened.");
    } finally {
      setArtifactAction("");
    }
  }

  async function prepareArtifactToPublish(artifact: Artifact) {
    setArtifactAction(`publish:${artifact.path}`);
    try {
      const asset = await libraryAssetFor(artifact);
      router.push(`/publish?asset=${encodeURIComponent(asset.id)}`);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Publish could not be prepared.");
      setArtifactAction("");
    }
  }

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
    const snapshotKey = `download-status:${workspaceId}`;
    const cached = readTabSnapshot<MediaStatus>(snapshotKey);
    if (cached) queueMicrotask(() => { if (!cancelled) setStatus(cached); });
    const fetchStatus = async () => {
      try {
        const body = await refreshTabSnapshot<MediaStatus>(snapshotKey, async () =>
          json<MediaStatus>(await apiFetch("/api/workspaces/" + workspaceId + "/media/status")),
        );
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

  const selectedWorkspace = workspaces.find((item) => item.id === workspaceId);
  const providerReady = Boolean(status?.douyin.installed && status?.douyin.active);
  const cookiesReady = status?.douyin.cookies_ready === true;
  // Only an explicit false warns: an older API without the field says nothing
  // about the session, and warning on silence would nag every setup.
  const anonymousSession = status?.douyin.cookies?.signed_in === false;
  const connectionState = status?.douyin.connection?.state ?? "disconnected";
  const refreshRequired = connectionState === "refresh_required";
  /** Douyin's own readiness: installed, activated, and signed in. */
  const douyinReady = providerReady && cookiesReady && !refreshRequired;
  /**
   * Whether the service this paste is for can run right now.
   *
   * Asked of the detected service rather than of Douyin. TikTok has no sign-in
   * and no cookies; what it needs is yt-dlp able to present a browser
   * fingerprint, which the catalogue reports as `ready` with the remedy in
   * `reason`. Blocking a TikTok paste on a Douyin session it will never use
   * was the shape of the old check.
   */
  const catalogued = provider
    ? providers.find((item) => item.id === provider.id) ?? provider
    : null;
  const serviceReady = !provider
    ? douyinReady
    : provider.id === "douyin"
      ? douyinReady
      : catalogued?.ready !== false;
  const serviceBlocker = provider && provider.id !== "douyin" && catalogued?.ready === false
    ? catalogued.reason ?? `${provider.label} is not ready.`
    : "";
  /**
   * Fall back to a fetch this service can do when the remembered one is not.
   *
   * The mode persists between visits, so somebody who last fetched liked
   * videos from Douyin and then pastes a TikTok link would be holding a
   * setting TikTok refuses - and would find out from a 422 after pressing the
   * button rather than from the control.
   */
  useEffect(() => {
    if (!provider || provider.modes.includes(mode)) return;
    const next = provider.modes[0];
    if (next && isDownloadMode(next)) setMode(next);
  }, [provider, mode, setMode]);

  /** Whether Douyin's own setup is what this paste depends on. */
  const douyinTab = !provider || provider.id === "douyin";
  const canFetch = serviceReady && !conflict;
  const detectedService = provider?.label ?? "Douyin";
  const connectionActive = ["starting", "installing", "opening_browser", "waiting_for_login"].includes(connectionState);

  const [installingTool, setInstallingTool] = useState("");

  /**
   * Install the downloader this service needs, from here.
   *
   * The same endpoint the Tools tab calls. Offered here because this is where
   * somebody finds out they need it: being told to go to another tab, find the
   * right row among twenty and come back is three navigations to answer a
   * question that was asked here.
   */
  async function installProviderTool(toolId: string, label: string) {
    if (!toolId) return;
    setInstallingTool(toolId);
    clearStatus();
    try {
      await json(await apiFetch(`/api/tools/${toolId}/install`, {
        method: "POST",
        body: JSON.stringify({ confirm_external_action: true }),
      }));
      succeed(`${label} is ready to download.`);
      // Re-read the catalogue rather than assume: the install reports what it
      // put on disk, and whether that is enough to fetch with is the
      // provider's own answer.
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/download-providers`,
      );
      if (response.ok) {
        const body = (await response.json()) as { providers?: DownloadProvider[] };
        if (body.providers?.length) setProviders(body.providers);
      }
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : `${label} could not be installed.`);
    } finally {
      setInstallingTool("");
    }
  }

  async function connectDouyin(requireLogin = false) {
    if (!workspaceId) return;
    setConnecting(true);
    clearStatus();
    try {
      const body = await json<{ connection: { state: string; message: string } }>(
        await apiFetch("/api/workspaces/" + workspaceId + "/media/douyin/connection", {
          method: "POST",
          body: JSON.stringify({ confirm_external_action: true, force_refresh: cookiesReady, require_login: requireLogin }),
        }),
      );
      setStatus((current) => current ? {
        ...current,
        douyin: { ...current.douyin, connection: body.connection },
      } : current);
      succeed(requireLogin ? "Finish signing in in the Douyin window." : "Saving the Douyin session automatically. No login needed.");
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
    if (conflict) {
      // Named and counted, so the fix is visible: take one service's links out.
      fail(
        `These links are from ${describeConflict(conflict)}. A download runs `
        + "against one service at a time - keep the links from one and start a "
        + "second download for the rest.",
      );
      requestAnimationFrame(() => linkInputRef.current?.focus());
      return;
    }
    if (!urls.length) {
      fail(
        "Paste a link to a specific video, channel or collection from "
        + providers.map((item) => item.label).join(" or ")
        + ".",
      );
      requestAnimationFrame(() => linkInputRef.current?.focus());
      return;
    }
    if (!canFetch) {
      fail(
        serviceBlocker
          || (refreshRequired
            ? `Refresh the ${detectedService} session before starting this download.`
            : providerReady
              ? `Connect your ${detectedService} session before starting this download.`
              : `Enable the ${detectedService} downloader in Tools before starting this download.`),
      );
      return;
    }
    setBusy(true);
    clearStatus();
    try {
      await json(await apiFetch("/api/workspaces/" + workspaceId + "/media/downloads", {
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

  /**
   * Queue the same sources again, downloading only what is missing.
   *
   * The re-run lists each source afresh and skips everything already saved -
   * the downloader dedupes by post id and by file - so a batch marked
   * downloaded is exactly as re-runnable as a fresh one. This is how a capped
   * profile is topped up after new posts land, and how it completes itself
   * the moment Douyin serves a deeper listing again.
   */
  async function refetchMissing(job: DownloadJob) {
    const request = downloadGroupRequest(job.payload);
    const jobUrls = downloadGroupUrls(job.payload);
    if (!jobUrls.length) return;
    if (!canFetch) {
      fail(`Connect the ${jobServiceLabel(job)} session before re-checking this batch.`);
      return;
    }
    setRefetchingJobId(job.id);
    clearStatus();
    try {
      const capturedUrls = jobUrls.filter(isCapturedVideoUrl);
      const path = capturedUrls.length
        ? `/api/workspaces/${workspaceId}/media/downloads/${job.id}/import-links`
        : `/api/workspaces/${workspaceId}/media/downloads`;
      const body = capturedUrls.length
        ? { urls: capturedUrls, confirm_external_action: true }
        : {
          workspace_id: workspaceId,
          urls: jobUrls,
          mode: request?.mode ?? "post",
          media_kinds: request?.media_kinds ?? ["video"],
          limit: request?.limit ?? 0,
          incremental: true,
          confirm_external_action: true,
        };
      await json(await apiFetch(path, {
        method: "POST",
        body: JSON.stringify(body),
      }));
      setQueueFilter("active");
      setVisibleJobCount(INITIAL_JOB_COUNT);
      succeed(`Re-checking ${jobUrls.length === 1 ? "the source" : jobUrls.length + " sources"} for missing media. Anything already saved is skipped.`);
      await refreshJobs();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The re-check could not start.");
    } finally {
      setRefetchingJobId(null);
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
        ? "Finishing the files already saved. No new source request is needed."
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
    <LoadingMark />
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
            {/* The badge reports the service in the box, not Douyin's session
                unconditionally. A TikTok paste is not blocked by a Douyin
                sign-in it will never use, so saying "connection needed" there
                described a problem that did not exist. */}
            {/* One group, so the row stays two things wide. `space-between`
                across three children would have left the badge floating in
                the middle with the link stranded at the edge. */}
            <span className="download-heading-state">
            <span className={"connection-badge " + (!status ? "working" : canFetch ? "ready" : connectionActive ? "working" : "setup")}>
              <i aria-hidden="true" />
              {!status
                ? "Checking connection…"
                : conflict
                  ? "One service per download"
                  : provider && provider.id !== "douyin"
                    ? canFetch ? `${provider.label} ready` : `${provider.label} needs setup`
                    : canFetch ? "Douyin connected" : connectionActive ? "Waiting for sign-in" : "Connection needed"}
            </span>
            {/* The action the retired "ready" callout carried, kept where the
                state it acts on is reported. Only while Douyin is the service
                in the box and the session is healthy - when it is not, one of
                the callouts below is already offering this with a reason. */}
            {douyinTab && providerReady && cookiesReady && !refreshRequired && !anonymousSession && (
              <button type="button" className={buttonClass({ variant: "link", size: "sm" })}
                title={t("downloads.refreshSessionHelp")}
                disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"}
                onClick={() => void connectDouyin()}>
                {connecting || connectionActive ? "Refreshing…" : "Refresh session"}
              </button>
            )}
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
                placeholder={"Paste a video, profile, collection, or share message…\n"
                  + providers.map((item) => item.example).filter(Boolean).join("\n")}
                aria-describedby="douyin-link-help"
              />
              <div className="link-input-footer">
                {/* One line, and it names the service once there is one to
                    name. Which service was a fact nobody had to establish
                    while this was the Douyin tab; now it is the first thing
                    worth confirming, so it reads back from the links rather
                    than sitting in a dropdown somebody has to set. */}
                <span id="douyin-link-help">{conflict
                  ? `${describeConflict(conflict)} — one service per download`
                  : urls.length
                    ? `${urls.length} ${provider?.label ?? ""} ${urls.length === 1 ? "link" : "links"}`
                      + (unsupportedCount ? ` · ${unsupportedCount} ignored` : "")
                    : unsupportedCount
                      ? "No downloadable link yet — paste a video, channel or collection"
                      : providers.map((item) => item.label).join(" · ")}</span>
                <Button variant="link" size="sm" onClick={() => void pasteLinks()}><ActionIcon name="copy" />{t("downloads.pasteFromClipboard")}</Button>
              </div>
            </div>
          </div>

          {/* A batch is one service, and the way out is to remove one group -
              so each group is offered as the thing to keep. Acting on it is a
              click rather than an instruction to go and edit the box. */}
          {conflict && <div className="connection-callout warning download-conflict">
            <div>
              <strong>These links are from {describeConflict(conflict)}</strong>
              <span>
                A download runs against one service at a time — each has its own
                sign-in, its own limits and its own downloader. Keep one and
                start a second download for the rest.
              </span>
            </div>
            <div className="download-conflict-actions">
              {conflict.map((entry) => (
                <Button key={entry.provider.id} variant="secondary" size="sm"
                  onClick={() => setInput(entry.urls.join("\n"))}>
                  Keep {entry.provider.label} ({entry.urls.length})
                </Button>
              ))}
            </div>
          </div>}

          {/* A service that cannot run yet, said once, where the links are.
              TikTok reads a link happily and then refuses the fetch, so this
              has to be visible before the button is pressed. */}
          {!conflict && serviceBlocker && <div className="connection-callout warning">
            <div>
              <strong>{provider?.label} is not ready</strong>
              <span>{serviceBlocker}</span>
            </div>
            <div className="download-conflict-actions">
              {/* Fixed from here, because here is where it was noticed. The
                  Tools tab still lists it; it is no longer the only way. */}
              {catalogued?.tool_id && (
                <Button variant="primary" size="sm"
                  busy={installingTool === catalogued.tool_id}
                  disabled={Boolean(installingTool)}
                  onClick={() => void installProviderTool(
                    catalogued.tool_id ?? "", provider?.label ?? "This service",
                  )}>
                  Install {catalogued.tool_id}
                </Button>
              )}
              <Link className={buttonClass({ variant: "secondary", size: "sm" })} href="/tools">{t("downloads.openTools")}</Link>
            </div>
          </div>}

          {urls.length > 0 && <section className="detected-sources" aria-label={t("downloads.detectedSources")}>
            <div className="detected-heading"><strong>{t("downloads.readyToDownload")}</strong><span>{urls.length} {urls.length === 1 ? "source" : "sources"}</span></div>
            <ul>
              {urls.map((url) => <li key={url}>
                <span className="source-kind">{
                  (provider && kindOf(provider, url)?.label) || sourceType(url, detectedService)
                }</span>
                <span className="source-address" title={url}>{shortSource(url)}</span>
                <button type="button" onClick={() => removeSource(url)} aria-label={"Remove " + shortSource(url)}>{t("downloads.remove")}</button>
              </li>)}
            </ul>
          </section>}

          {/* Only once the status is known: while it is still null on first
              load, providerReady is false for want of an answer, not because a
              provider is missing, and this warning would flash then vanish. */}
          {douyinTab && status && !providerReady && <div className="connection-callout warning">
            <div><strong>{t("downloads.installProvider")}</strong><span>{t("downloads.installProviderHelp")}</span></div>
            <Link className={buttonClass({ variant: "secondary" })} href="/tools">{t("downloads.openTools")}</Link>
          </div>}
          {douyinTab && providerReady && !cookiesReady && <div className="connection-callout warning">
            <div>
              <strong>{connectionActive ? "Connecting to Douyin" : "Connect your Douyin session"}</strong>
              <span>{status?.douyin.connection?.message ?? "TrendRelay opens a dedicated login window and stores the session only on this computer."}</span>
            </div>
            <Button variant="secondary" busy={connecting} disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}>
              {connecting || connectionActive ? "Connecting…" : "Connect Douyin"}
            </Button>
          </div>}
          {douyinTab && providerReady && cookiesReady && refreshRequired && <div className="connection-callout warning">
            <div><strong>{t("downloads.refreshSession")}</strong><span>{status?.douyin.connection?.message}</span></div>
            <Button variant="secondary" busy={connecting} disabled={selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}><ActionIcon name="refresh" />{connecting ? "Opening" : "Refresh session"}</Button>
          </div>}
          {douyinTab && providerReady && cookiesReady && !refreshRequired && anonymousSession && <div className="connection-callout connected">
            {/* Anonymous works: single links reliably, and a profile fetches
                its most recent posts. Not a warning; signing in is the path
                to a profile's full history and to topic search. */}
            <div><strong>Douyin connected (signed out)</strong><span>{status?.douyin.connection?.message}</span></div>
            <button type="button" className={buttonClass({ variant: "link" })} disabled={connecting || connectionActive || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin(true)}>
              {connecting || connectionActive ? "Opening…" : "Log in for full profiles"}
            </button>
          </div>}
          {/* Nothing here when nothing is wrong. This was a full-width row
              saying the session works, directly under a badge already saying
              "Douyin connected" - 69px, on every visit, to repeat the good
              news. The one thing it carried that the badge does not is the
              refresh action, which now sits beside the badge as a quiet link:
              reachable, and not a row of its own. */}

          <details className="download-options">
            {/* The label is its own element so the chevron has something to sit
                beside. Left as a bare text node it shared the row with the
                summary text under `space-between`, which would have pushed the
                two to opposite ends of the row. */}
            <summary><strong>{t("downloads.options")}</strong> <span>{modeLabel(mode, detectedService)} · {limit === 0 ? (detectedService === "TikTok" ? "all channel videos" : "all posts") : `up to ${limit} per source`} · {mediaKinds.length === 3 ? "video, images and audio" : mediaKinds.length === 1 ? "video only" : `video and ${mediaKinds.includes("image") ? "images" : "audio"}`}</span></summary>
            <div className="download-options-grid">
              {/* Only the fetches this service can actually deliver. TikTok keeps
                  likes private and its sound extractor is broken upstream, so
                  offering those here would be controls that always fail. */}
              <label><span>{t("downloads.fromProfiles")}</span><Select value={mode} onChange={(event) => { if (isDownloadMode(event.target.value)) setMode(event.target.value); }}>
                {[
                  { id: "post", label: t("downloads.publishedPosts") },
                  { id: "like", label: t("downloads.likedVideos") },
                  { id: "mix", label: t("downloads.collections") },
                  { id: "music", label: t("downloads.musicVideos") },
                ].filter((option) => !provider || provider.modes.includes(option.id))
                  .map((option) => (
                    <option key={option.id} value={option.id}>{option.label}</option>
                  ))}
              </Select></label>
              <fieldset><legend>{t("downloads.perSource")}</legend><div className="limit-presets">{[0, 10, 20, 50, 100].map((value) => <button key={value} type="button" className={limit === value ? "selected" : ""} aria-pressed={limit === value} onClick={() => setLimit(value)}>{value === 0 ? "All" : value}</button>)}</div></fieldset>
              <fieldset>
                <legend>{t("downloads.whatToFetch")}</legend>
                <div className="limit-presets">
                  {([
                    ["video", "Video", "The post itself; always fetched"],
                    ["image", "Images", "Photo posts plus each video's cover"],
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
            {/* Off while the box holds two services. The callout above already
                says which, and offers the two ways out; a button that can only
                answer with the same sentence is a button that should not be
                pressable. It stays live for every other not-ready state, where
                pressing it is how somebody learns what is missing. */}
            <button className={`${buttonClass({ variant: "primary" })} download-button`}
              disabled={busy || Boolean(conflict)}>
              {busy
                ? "Adding to queue…"
                : conflict
                  ? "One service per download"
                  : urls.length > 1
                    ? `Download ${urls.length} ${provider?.label ?? ""} sources`.replace("  ", " ")
                    : "Start download"}
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
            ["all", "All", ""],
            ["active", "Active", "running"],
            ["completed", "Completed", "succeeded"],
            ["attention", "Needs attention", "failed"],
          ] as [QueueFilter, string, string][]).map(([value, label, tone]) => <button
            key={value}
            type="button"
            className={queueFilter === value ? "selected" : ""}
            aria-pressed={queueFilter === value}
            onClick={() => selectQueueFilter(value)}
          ><span>{label}</span>{/* The count wears the colour its own rows wear:
              green for what finished, red for what needs somebody, and the
              blue of the progress bar for what is moving. Only while there is
              something to count - a red nought is a warning about nothing, and
              a row of permanently lit colours stops meaning anything at all.
              The words carry the state on their own; the colour is a second
              way of saying it, never the only one. */}
            <b data-tone={tone && queueCounts[value] > 0 ? tone : undefined}>
              {queueCounts[value]}
            </b>
          </button>)}
        </div>

        {/* A wait, not an empty shelf. Until the first listing answers there
            is nothing to be empty about, and "Your downloads will appear here"
            told somebody with a full queue that they had none - the one moment
            the sentence is guaranteed to be wrong. */}
        {filteredGroups.length === 0 && jobsLoading && (
          <WaitingBlock className="waiting-block-compact download-wait" message={t("common.loading")} />
        )}

        {filteredGroups.length === 0 && !jobsLoading && <div className="download-empty">
          <span className="empty-download-icon" aria-hidden="true">↓</span>
          <strong>{jobs.length ? "No " + (queueFilter === "attention" ? "downloads need attention" : queueFilter + " downloads") : "Your downloads will appear here"}</strong>
          <p>{jobs.length ? "Choose another filter to see the rest of your queue." : "Add one or more Douyin or TikTok links above to start your first batch."}</p>
          {!jobs.length && <a href="#add-links">{t("downloads.addLinks")}</a>}
        </div>}

        <div className="download-job-list">
          {visibleGroups.map(({ primary: job, earlier }) => {
            const current = effectiveStatus(job);
            const sources = downloadGroupUrls(job.payload);
            const artifacts = job.result?.artifacts ?? [];
            const progress = job.progress;
            const groupedProgress = downloadRunRollup([job, ...earlier]);
            const libraryProgress = job.library_progress;
            const preparingLibrary = current === "processing";
            const downloadingAndPreparing = current === "downloading_preparing";
            const libraryPercent = libraryProgress?.total ? Math.round((libraryProgress.succeeded / libraryProgress.total) * 100) : 0;
            const downloadCount = progressSummary(groupedProgress, current, artifacts, job.payload.request?.limit);
            const creatorProfiles = (job.result?.creator_urls ?? []).filter(
              (profile) => !sources.includes(profile),
            );
            const canOpenFolder = Boolean(job.payload.output_root && (progress?.folder_exists || (!progress && job.status === "succeeded")));
            // The newest run that measured the profiles. While a re-run is
            // still downloading it has no result yet, and the artist badges
            // vanishing mid-download read as data lost rather than data
            // pending - so the previous run's badges stand until the new
            // ones land.
            const statsRun = [job, ...earlier].find((run) =>
              (run.result?.source_stats ?? []).some((stat) => stat.declared_total));
            const coverageStats = (statsRun?.result?.source_stats ?? []).filter((stat) => stat.declared_total);
            const missingPosts = coverageStats.some((stat) => (stat.held ?? 0) < (stat.declared_total ?? 0));
            const inLibrary = libraryCounts[`${job.id}:${earlier.length}`];
            return <details key={job.id} className={"download-job " + current} open={ACTIVE_STATUSES.has(current) || undefined}>
              <summary>
                <span className={"job-status " + current}><i aria-hidden="true" />{job.error && current === "queued" ? "Waiting to retry" : statusLabel(current)}</span>
                {/* The service leads the meta line now that there is more than
                    one. Older batches carry no `service`, and predate the
                    question - they are Douyin by construction, so they say so
                    rather than showing a gap. */}
                <span className="download-job-summary-title"><strong>{sources[0] ? shortSource(sources[0]) : job.id}</strong><small>{jobServiceLabel(job)} · {sources.length > 1 ? sources.length + " sources" : sources[0] ? sourceType(sources[0], jobServiceLabel(job)) : "batch"} · {downloadCount}{earlier.length > 0 ? ` · ${earlier.length + 1} runs` : ""}</small></span>
                <time>{new Date(job.created_at).toLocaleString()}</time>
                <span className="job-disclosure" aria-hidden="true">
                  <svg viewBox="0 0 16 16" focusable="false"><path d="m4 6 4 4 4-4" /></svg>
                </span>
              </summary>
              <div className="download-job-body">
                {ACTIVE_STATUSES.has(current) && <div className={"job-progress " + current} aria-label={current === "queued" ? "Waiting to start" : preparingLibrary ? "Preparing downloaded media for Library" : downloadingAndPreparing ? "Downloading while preparing earlier files for Library" : "Download in progress"}><span style={preparingLibrary ? { width: `${libraryPercent}%` } : undefined} /></div>}
                {progress?.folder_exists && <div className="download-live-status"><strong>{job.error && current === "queued" ? "Ready to resume" : preparingLibrary ? "Preparing Library" : downloadingAndPreparing ? "Downloading now · preparing Library" : ACTIVE_STATUSES.has(current) ? "Downloading now" : earlier.length ? `Across ${earlier.length + 1} runs` : "Files on disk"}</strong><span>{libraryProgress && (preparingLibrary || downloadingAndPreparing) ? `${progressBreakdown(progress)} · ${libraryProgressBreakdown(libraryProgress, t)}` : progressBreakdown(groupedProgress ?? progress)}</span></div>}
                {ACTIVE_STATUSES.has(current) && !job.error && <div className="download-job-actions">
                  {/* Stop keeps whatever already downloaded - a running job
                      halts at its next source, a queued one right away. */}
                  <Button variant="secondary" size="sm" disabled={cancellingJobId === job.id} onClick={() => void cancelDownload(job.id)}><ActionIcon name="dismiss" />{cancellingJobId === job.id ? "Stopping…" : "Stop download"}</Button>
                </div>}
                {(sources.length > 0 || canOpenFolder || job.status === "succeeded") && <div className="download-job-actions">
                  {/* Every finished batch can be re-checked, downloaded ones
                      included: the run lists each source afresh and skips
                      what is already saved, so it costs one listing when
                      nothing is missing - and tops the batch up when a capped
                      profile has new posts or Douyin serves a deeper list. */}
                  {sources.length > 0 && !ACTIVE_STATUSES.has(current) && <Button variant="secondary" size="sm"
                    title="Re-check these sources and download anything missing. Files already saved are skipped."
                    disabled={refetchingJobId === job.id}
                    onClick={() => void refetchMissing(job)}>
                    <ActionIcon name="refresh" />{refetchingJobId === job.id ? "Queuing…" : "Fetch missing"}
                  </Button>}
                  {sources.length > 0 && <Button variant="secondary" size="sm"
                    title={`Collect additional video links from a manually scrolled ${jobServiceLabel(job)} channel`}
                    onClick={() => { setBookmarkletCopied(false); setCapturedLinks(""); setImportLinksError(null); setBookmarkletJobId(job.id); }}>
                    <ActionIcon name="link" />Import links
                  </Button>}
                  {sources.length > 0 && <Button variant="secondary" size="sm" onClick={() => reuseLinks(sources)}><ActionIcon name="link" />Reuse {sources.length === 1 ? "link" : "links"}</Button>}
                  {creatorProfiles.length > 0 && <Button variant="secondary" size="sm" title={t("downloads.addCreatorProfile")} onClick={() => addCreatorProfiles(creatorProfiles)}>Add creator {creatorProfiles.length === 1 ? "profile" : `profiles (${creatorProfiles.length})`}</Button>}
                  {sources[0] && <a href={sources[0]} target="_blank" rel="noreferrer">{t("downloads.openSource")}</a>}
                  {canOpenFolder && <Button variant="secondary" size="sm" title={earlier.length ? "Open the newest run's download folder" : undefined} onClick={() => void openFolder(job.payload.output_root!)}><ActionIcon name="openFolder" />{earlier.length ? "Open latest folder" : t("downloads.openFolder")}</Button>}
                  {job.status === "succeeded" && (
                    <Link href={downloadLibraryHref(
                      [job.id, ...earlier.map((run) => run.id)],
                      sources[0] ? `Downloaded from ${shortSource(sources[0])}` : "Downloaded batch",
                    )}>{t("downloads.openLibrary")}</Link>
                  )}
                </div>}
                {/* Coverage, where a count can be honest about its ceiling:
                    what we hold of each profile against the total its page
                    declares. Held is cumulative across every run, so a batch
                    completed over several fetches still reads whole. A batch
                    of several profiles leads with its whole-batch share, so
                    "how much of this is here" is one glance, not arithmetic. */}
                {coverageStats.length > 0 && (() => {
                  const heldSum = coverageStats.reduce((sum, stat) => sum + (stat.held ?? 0), 0);
                  const totalSum = coverageStats.reduce((sum, stat) => sum + (stat.declared_total ?? 0), 0);
                  /*
                   * When this coverage was counted, and what it counts.
                   *
                   * These badges sit beside "179 files downloaded" and were
                   * read as the same quantity disagreeing with itself. They
                   * are not: this counts a creator's *posts* held, taken once
                   * when that run finished, while the line below counts the
                   * *files* the whole group downloaded. Both were true and
                   * neither said which it was, so a profile at 165/197 beside
                   * 179 files looked like a bug in one of them.
                   */
                  const measured = statsRun
                    ? new Date(statsRun.created_at).toLocaleString()
                    : null;
                  const asOf = measured ? ` Counted when that run finished, ${measured}.` : "";
                  return (
                    <div className="download-coverage" aria-label="Profile coverage">
                      {coverageStats.length > 1 && (
                        <span className={"coverage-total " + (heldSum >= totalSum ? "coverage-complete" : "coverage-partial")}
                          title={`${heldSum} of the ${totalSum} posts these ${coverageStats.length} profiles have published.`
                            + `${asOf} Posts, not files: one post can save more than one file, and this batch's file count is measured separately.`}>
                          All profiles <b>{heldSum}/{totalSum}</b>
                          <span className="coverage-unit">posts</span>
                          <span className="coverage-pct">{coveragePct(heldSum, totalSum)}</span>
                        </span>
                      )}
                      {coverageStats.map((stat) => {
                        const held = stat.held ?? 0;
                        const total = stat.declared_total ?? 0;
                        const complete = total > 0 && held >= total;
                        const who = stat.nickname ?? shortSource(stat.url);
                        const explains = (complete
                          ? `All ${total} of ${who}'s posts.${asOf}`
                          : `${held} of ${who}'s ${total} posts.${asOf}`
                            + " Fetch missing checks for more."
                            + " Posts, not files - the file count below is a separate measure.");
                        const badge = <>
                          {/* The unit, on the badge rather than only in its
                              tooltip: this sits beside a file count, and a
                              bare fraction next to a bare total is what made
                              the two look like the same number disagreeing. */}
                          {who} <b>{held}/{total}</b>
                          <span className="coverage-unit">posts</span>
                          <span className="coverage-pct">{coveragePct(held, total)}</span>
                        </>;
                        // The badge names a profile, so it takes you there:
                        // the source url is the profile page (a short link
                        // resolves to it), and checking a creator's page is
                        // the natural next step from reading their count.
                        return stat.url ? (
                          <a key={stat.url} className={complete ? "coverage-complete" : "coverage-partial"}
                            href={stat.url} target="_blank" rel="noreferrer"
                            title={`Open ${who}'s ${jobServiceLabel(job)} profile. ${explains}`}>
                            {badge}
                          </a>
                        ) : (
                          <span key={who} className={complete ? "coverage-complete" : "coverage-partial"}
                            title={explains}>
                            {badge}
                          </span>
                        );
                      })}
                    </div>
                  );
                })()}
                {/* Said from live data instead of replaying the stored run
                    summary: what got saved, how much of it the Library
                    actually holds, and - plainly - why a profile can be
                    short. The Library figure is the Library's own answer,
                    not the run's record of itself. */}
                {job.status === "succeeded" && <p className="job-summary">
                  {`${earlier.length ? "Across " + (earlier.length + 1) + " runs: " : ""}${(groupedProgress ?? progress)?.files_downloaded ?? artifacts.length} files downloaded.`}
                  {typeof inLibrary === "number" && ` ${inLibrary} ${inLibrary === 1 ? "is" : "are"} in your Library.`}
                  {missingPosts && ` ${jobServiceLabel(job)} may show signed-out visitors only a channel's newest posts. Fetch missing checks for more; use Import links after scrolling the channel when the list is truncated.`}
                </p>}
                {(job.result?.source_errors?.length ?? 0) > 0 && <details className="download-run-history">
                  <summary>{job.result!.source_errors!.length} sources need attention</summary>
                  <ul>{job.result!.source_errors!.map((message, index) => <li key={index}>{message}</li>)}</ul>
                </details>}
                {/* The same sources, run before. One compact line per run:
                    the newest run above already tells the batch's current
                    story, so history needs a date and a count, not another
                    full row in the list. */}
                {earlier.length > 0 && (
                  <details className="download-run-history">
                    <summary>{earlier.length} earlier {earlier.length === 1 ? "run" : "runs"} of these sources</summary>
                    <ul>
                      {earlier.map((run) => {
                        const runStatus = effectiveStatus(run);
                        return (
                          <li key={run.id} className={runStatus}>
                            <span className={"job-status " + runStatus}><i aria-hidden="true" />{statusLabel(runStatus)}</span>
                            <span>{progressSummary(run.progress, runStatus, run.result?.artifacts ?? [], run.payload.request?.limit)}</span>
                            <time>{new Date(run.created_at).toLocaleString()}</time>
                          </li>
                        );
                      })}
                    </ul>
                  </details>
                )}
                {job.error && <div className="job-error"><strong>{current === "queued" ? "Download ready to resume" : "Download stopped"}</strong><span>{friendlyDownloadError(job.error, groupedProgress?.files_downloaded ?? 0, jobServiceLabel(job))}</span><div className="download-recovery-actions">{(groupedProgress?.files_downloaded ?? 0) > 0 && <button type="button" className={buttonClass({ variant: "link" })} disabled={resumingJobId === job.id || current === "running"} onClick={() => void resumeDownload(job.id, true)}><ActionIcon name="confirm" />{resumingJobId === job.id ? "Working…" : "Finish saved media"}</button>}<button type="button" className={buttonClass({ variant: "link" })} disabled={resumingJobId === job.id || current === "running"} onClick={() => void resumeDownload(job.id)}><ActionIcon name="play" />{resumingJobId === job.id ? "Working…" : "Resume download"}</button>{jobServiceId(job) === "douyin" && <button type="button" className={buttonClass({ variant: "link" })} disabled={connecting || selectedWorkspace?.role !== "owner"} onClick={() => void connectDouyin()}><ActionIcon name="refresh" />{connecting ? "Opening…" : "Refresh session"}</button>}<button type="button" className={buttonClass({ variant: "link" })} onClick={() => reuseLinks(sources)}><ActionIcon name="link" />Reuse {sources.length === 1 ? "link" : "links"}</button></div></div>}
                {current === "empty" && !job.error && <div className="job-error"><strong>{t("downloads.noneSaved")}</strong><span>{t("downloads.reuseLinks")}</span><button type="button" className={buttonClass({ variant: "link" })} onClick={() => reuseLinks(sources)}><ActionIcon name="link" />Reuse {sources.length === 1 ? "link" : "links"}</button></div>}
                {artifacts.length > 0 && <div className="artifact-list">
                  {artifacts.slice(0, 4).map((artifact) => <div className="artifact-row" key={artifact.path}>
                    <DownloadArtifactThumbnail
                      artifact={artifact}
                      workspaceId={workspaceId}
                      apiFetch={apiFetch}
                      resolveAsset={libraryAssetFor}
                    />
                    <div><strong>{artifact.name}</strong><small>{size(artifact.size_bytes)}</small></div>
                    <div><Link href={artifact.sha256
                      ? downloadFileLibraryHref(artifact.sha256, artifact.name)
                      // Nothing the downloader writes today lacks a hash. One
                      // written before it did still opens the batch it belongs
                      // to, which is where it will be, rather than a Library
                      // that would select nothing.
                      : downloadLibraryHref(job.id, artifact.name)}>{t("downloads.openInLibrary")}</Link>
                      <Button
                        size="sm"
                        busy={artifactAction === `campaign:${artifact.path}`}
                        disabled={!artifact.sha256 || Boolean(artifactAction)}
                        title={artifact.sha256
                          ? "Choose a campaign for this Library item"
                          : "Available after this file is added to the Library"}
                        onClick={() => void addArtifactToCampaign(artifact)}
                      ><ActionIcon name="campaign" />Add to campaign</Button>
                      <Button
                        size="sm"
                        busy={artifactAction === `publish:${artifact.path}`}
                        disabled={!artifact.sha256 || Boolean(artifactAction)}
                        title={artifact.sha256
                          ? "Open this Library item in Publish"
                          : "Available after this file is added to the Library"}
                        onClick={() => void prepareArtifactToPublish(artifact)}
                      ><ActionIcon name="publish" />Prepare to publish</Button>
                    </div>
                  </div>)}
                  {artifacts.length > 4 && <p className="more-artifacts">+ {artifacts.length - 4} more files in {earlier.length ? "the latest run" : "this batch"}</p>}
                </div>}
              </div>
            </details>;
          })}
        </div>
        {visibleGroups.length < filteredGroups.length && <button type="button" className="queue-show-more" onClick={() => setVisibleJobCount((current) => current + INITIAL_JOB_COUNT)}>Show {Math.min(INITIAL_JOB_COUNT, filteredGroups.length - visibleGroups.length)} more downloads</button>}
      </section>
      </section>
    </>}
    {workspaceId && (
      <CampaignPicker
        open={campaignPickerAsset !== null}
        workspaceId={workspaceId}
        assets={campaignPickerAsset ? [campaignPickerAsset] : []}
        assetIds={campaignPickerAsset ? [campaignPickerAsset.id] : []}
        apiFetch={apiFetch}
        onClose={() => setCampaignPickerAsset(null)}
        onAdded={(message) => { succeed(message); setCampaignPickerAsset(null); }}
      />
    )}
    <Dialog
      open={bookmarkletJob !== null}
      title="Import links"
      description={bookmarkletJob ? `Add to this batch: ${shortSource(downloadGroupRequest(bookmarkletJob.payload).urls?.[0] ?? bookmarkletJob.id)}` : undefined}
      onClose={() => { if (!importLinksLock.current) setBookmarkletJobId(null); }}
      footer={<>
        <Button variant="secondary" disabled={importingLinks} onClick={() => setBookmarkletJobId(null)}>Cancel</Button>
        <Button variant="primary" disabled={importingLinks || !bookmarkletJob || (bookmarkletJob ? jobServiceId(bookmarkletJob) === "douyin" && !douyinReady : false) || Boolean(captured.error) || !captured.urls.length} onClick={() => void importLinks()}>
          {importingLinks ? "Queuing…" : `Import${captured.urls.length && !captured.error ? ` ${captured.urls.length}` : ""} links`}
        </Button>
      </>}
    >
      <div className="bookmarklet-help">
        <label htmlFor="douyin-captured-links">Captured post links</label>
        <textarea id="douyin-captured-links" className="bookmarklet-code bookmarklet-links" rows={5}
          placeholder="Paste captured links here, one per line"
          value={capturedLinks} disabled={importingLinks} aria-describedby="douyin-import-status"
          aria-invalid={Boolean(captured.error)}
          onChange={(event) => { setCapturedLinks(event.target.value); setImportLinksError(null); }} />
        <p id="douyin-import-status" aria-live="polite" className="bookmarklet-note">
          {captured.error ?? `${captured.urls.length} unique links${importBatchCount(captured.urls.length) > 1 ? ` · imports as ${importBatchCount(captured.urls.length)} batches of up to 400` : ""}. Existing downloads are skipped.`}
        </p>
        {importLinksError && <p role="alert">{importLinksError}</p>}
        {bookmarkletJob && jobServiceId(bookmarkletJob) === "douyin" && !douyinReady && <p role="status">Connect Douyin in the left panel before importing. You can still collect links.</p>}
        <details>
        <summary>Capture loaded links from Chrome</summary>
        <div className="bookmarklet-help">
        <p>Open the same {bookmarkletJob && jobServiceId(bookmarkletJob) === "tiktok" ? "TikTok channel" : "Douyin profile"} in Chrome. The bookmark collects loaded posts as you scroll; it does not bypass login prompts.</p>
        <ol>
          <li>Copy the bookmarklet text, then create a browser bookmark whose URL is that text.</li>
          <li>Scroll the channel until more posts load and click the bookmark.</li>
          <li>Paste the copied links into this dialog and choose Import links.</li>
        </ol>
        <label htmlFor="douyin-bookmarklet">Bookmarklet URL</label>
        <textarea id="douyin-bookmarklet" className="bookmarklet-code" value={DOUYIN_BOOKMARKLET} readOnly onFocus={(event) => event.currentTarget.select()} />
        <Button variant="primary" size="sm" onClick={() => void copyBookmarklet()}>
          {bookmarkletCopied ? "Copied" : "Copy bookmarklet"}
        </Button>
        <p className="bookmarklet-note">Only loaded profile posts are collected. Review the pasted list before downloading; no cookies or links are sent automatically.</p>
        </div>
        </details>
      </div>
    </Dialog>
    <StatusToasts messages={statusMessages} onDismiss={dismiss} />
  </main>;
}
