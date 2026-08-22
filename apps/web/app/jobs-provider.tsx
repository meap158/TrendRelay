"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from "react";
import { useAuth } from "./auth-provider";
import { apiBaseUrl } from "../lib/api";
import { effectLabel } from "../lib/i18n/effects";
import { assetHref } from "../lib/job-links";
import { useT } from "./i18n-provider";
import { useWorkspace } from "./workspace-provider";

type Translate = (path: string, values?: Record<string, string | number>) => string;
type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
type JobCategory = "fetch" | "media" | "render" | "publish" | "research" | "blur"
  | "edit" | "captions" | "transcription" | "voice" | "processing";

/**
 * What an editing-suite render is called while it runs, and once it is done.
 *
 * Named by what it is applying rather than by its job id, because a queue of
 * three renders over the same clip is otherwise three identical lines. When it
 * finishes the count of covered frames is worth surfacing: a render that found
 * a face in half the clip succeeded and still needs looking at, and that is
 * exactly the case somebody would otherwise publish without noticing.
 */
function editTitle(t: Translate, job: any): string {
  // Undoing a render is the same kind of event as making one, and the log is
  // unreadable as a history if it shows every application and no removal.
  if (job?.payload?.action === "discard") {
    const removed = Number(job?.result?.removed_versions ?? 0);
    return removed === 1
      ? "Removed effects — 1 cut deleted"
      : `Removed effects — ${removed} cuts deleted`;
  }
  const steps: string[] = job?.payload?.effects ?? [];
  // The job stores effect ids; the drawer should say what the editor says. The
  // id doubles as the dictionary key, so an unknown one falls back to itself
  // rather than to a blank.
  const named = steps.map((id) => effectLabel(t, id, id));
  const applied = named.length ? named.join(" + ") : "effects";
  // A job that belongs to a batch is titled for the batch, not for itself.
  // Every title below turns on this one job's status - so the row standing for
  // seventy-seven of them read "Could not apply Face cover" whenever the most
  // recent of the seventy-seven had failed, whatever the other seventy-six
  // did. What the batch is doing is on the row already, in its own status and
  // its own count.
  const batchTotal = Number(job?.payload?.batch?.total ?? 0);
  if (batchTotal > 1) return `${applied} · ${batchTotal.toLocaleString()} items`;
  if (job?.cancellation_requested && ["queued", "running"].includes(job?.status)) {
    return `Cancelling ${applied}`;
  }
  if (job?.payload?.request?.preview_seconds) {
    if (job?.status === "succeeded") return `Preview ready: ${applied}`;
    if (job?.status === "cancelled") return `Preview cancelled: ${applied}`;
    if (job?.status === "failed") return `Preview failed: ${applied}`;
    return `Previewing ${applied}`;
  }
  if (job?.status === "cancelled") return `Cancelled: ${applied}`;
  if (job?.status === "failed") return `Could not apply ${applied}`;
  if (job?.status !== "succeeded") {
    return `${Number(job?.attempt_count ?? 0) > 1 ? "Resuming" : "Applying"} ${applied}`;
  }
  const frames = job?.result?.frame_effects?.[0];
  const coverage = typeof frames?.coverage === "number"
    ? ` — ${Math.round(frames.coverage * 100)}% of frames`
    : "";
  return `Applied ${applied}${coverage}`;
}

export type BaseJob = {
  id: string;
  category: JobCategory;
  status: JobStatus | string; // allowing string so we map specific statuses easily
  created_at: string;
  title: string;
  error?: string | null;
  /**
   * Where this notification goes when opened, if anywhere.
   *
   * A job that produced something the app can show links to it; one that did
   * not stays plain rather than becoming a link to somewhere unrelated. A blur
   * only knows its asset once it has finished, so a running one has nothing to
   * open yet - which is correct, there is nothing there to look at.
   */
  href?: string;
  /**
   * How far a long job has got, 0 to 1, and which pass it is on.
   *
   * Undefined means no estimate rather than no progress — a job too short to
   * bother reporting looks the same as one that has just started, and a bar
   * stuck at zero reads as stuck.
   */
  progress?: number | null;
  progressStage?: string | null;
  /** When the worker picked it up, which is what an estimate is measured from. */
  startedAt?: string | null;
  /**
   * Says `running` but no worker holds its lease, so nothing is happening.
   *
   * The server derives this from the lease rather than storing it, because the
   * process that would have written a status is the one that disappeared.
   * Treat it as paused, not as progressing: the last percentage is where it got
   * to, not where it is. It resumes on its own once a worker is back.
   */
  stalled?: boolean;
  /** Library asset this work belongs to, independent of the feature that queued it. */
  assetId?: string | null;
  /** Compact text for the progress layer drawn over that asset's thumbnail. */
  activityLabel?: string;
  activityDetail?: string;
  // Specific payloads preserved for UI needs
  raw: any;
};


type JobsContextValue = {
  jobs: BaseJob[];
  busy: boolean;
  activeWorkspaceId: string | null;
  announceMediaJobs: (jobs: any[]) => void;
  refresh: () => Promise<void>;
};

const JobsContext = createContext<JobsContextValue | null>(null);

export function JobsProvider({ children }: { children: ReactNode }) {
  const { user, apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const t = useT();
  const [jobs, setJobs] = useState<BaseJob[]>([]);
  const [busy, setBusy] = useState(false);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const hasActiveJobs = useRef(false);
  const activeWorkspaceId = workspaceId || null;

  const effectJob = useCallback((job: any): BaseJob => ({
    id: job.id,
    category: "edit",
    status: job.status,
    created_at: job.created_at,
    title: editTitle(t, job),
    error: job.error,
    progress: job.progress,
    progressStage: job.cancellation_requested && ["queued", "running"].includes(job.status)
      ? "Stopping safely"
      : job.progress_stage,
    startedAt: job.started_at,
    stalled: Boolean(job.stalled),
    assetId: job?.payload?.asset_id ?? job?.result?.asset_id ?? null,
    href: assetHref(job),
    raw: job,
  }), [t]);

  const mediaJob = useCallback((job: any): BaseJob => {
    if (job?.kind === "media_effect_render") return effectJob(job);
    const active = ["queued", "running"].includes(job?.status);
    const definitions: Record<string, {
      category: JobCategory; working: string; done: string; detail: string;
    }> = {
      caption_render: {
        category: "captions",
        working: job?.payload?.delivery === "sidecar" ? "Writing subtitles" : "Adding captions",
        done: job?.payload?.delivery === "sidecar" ? "Subtitle files ready" : "Captions ready",
        detail: job?.payload?.translate_to ? `Translating to ${job.payload.translate_to}` : "Caption track",
      },
      media_enrichment: {
        category: "transcription",
        working: "Reading media",
        done: "Transcript ready to review",
        detail: Array.isArray(job?.payload?.modes)
          ? job.payload.modes.map((mode: string) => mode === "ocr" ? "On-screen text" : "Speech").join(" + ")
          : "Speech and text",
      },
      voice_render: {
        category: "voice",
        working: "Generating voiceover",
        done: "Voiceover ready",
        detail: "Voiceover",
      },
    };
    const definition = definitions[job?.kind] ?? {
      category: "processing" as JobCategory,
      working: "Processing media",
      done: "Media processing finished",
      detail: String(job?.kind ?? "Media processing").replaceAll("_", " "),
    };
    const failed = job?.status === "failed";
    const cancelled = job?.status === "cancelled";
    return {
      id: job.id,
      category: definition.category,
      status: job.status,
      created_at: job.created_at,
      title: failed ? `${definition.working} failed`
        : cancelled ? `${definition.working} cancelled`
          : active ? definition.working : definition.done,
      error: job.error,
      progress: job.progress,
      progressStage: job.progress_stage,
      startedAt: job.started_at,
      stalled: Boolean(job.stalled),
      assetId: job?.payload?.asset_id ?? job?.result?.asset_id ?? null,
      activityLabel: definition.working,
      activityDetail: definition.detail,
      href: assetHref(job) ?? "/library",
      raw: job,
    };
  }, [effectJob]);

  /**
   * Put a job returned by a mutating request on screen immediately.
   *
   * Polling remains the authority for later progress, but waiting for its next
   * four-second tick made a repeat render look as though the click did nothing:
   * there was no notification, thumbnail overlay, or detail-card activity in
   * the interval. Replacing by id also lets cancellation update the same row.
   */
  const announceMediaJobs = useCallback((incoming: any[]) => {
    const announced = incoming.filter((job) => job?.id).map(mediaJob);
    if (!announced.length) return;
    hasActiveJobs.current = announced.some((job) =>
      ["queued", "running", "in_progress", "pending"].includes(job.status));
    const ids = new Set(announced.map((job) => job.id));
    setJobs((current) => [...announced, ...current.filter((job) => !ids.has(job.id))]
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()));
  }, [mediaJob]);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const request = (async () => {
      if (!user) {
        setJobs([]);
        return;
      }
      setBusy(true);
      try {
      const fetchPromises: Promise<BaseJob[]>[] = [];

      const researchWorkspace = activeWorkspaceId ?? "local";
      const fetchResearch = fetch(`${apiBaseUrl()}/api/research/jobs?workspace_id=${encodeURIComponent(researchWorkspace)}`, { cache: 'no-store' })
        .then(res => res.json())
        .then(data => (data.jobs || []).map((j: any) => ({
          id: j.id,
          category: "research" as JobCategory,
          status: j.status,
          created_at: j.created_at,
          title: `Research: ${j.topic}`,
          error: j.error,
          href: "/discover",
          raw: j,
        })))
        .catch(() => []);

      fetchPromises.push(fetchResearch);

      if (activeWorkspaceId) {
        const fetchMedia = apiFetch(`/api/workspaces/${activeWorkspaceId}/media/downloads`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map((j: any) => ({
            id: j.id,
            category: "fetch" as JobCategory,
            status: j.status,
            created_at: j.created_at,
            title: `Fetch: ${j.payload?.request?.urls?.[0] ?? j.id}`,
            error: j.error,
            // The batch downloader is the root screen, and a finished fetch is
            // read there next to the queue it came from.
            href: "/",
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchMedia);

        const fetchLibrary = apiFetch(`/api/workspaces/${activeWorkspaceId}/media/library/jobs`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map((j: any) => ({
            id: j.id,
            category: "media" as JobCategory,
            status: j.status,
            created_at: j.created_at ?? j.payload?.created_at,
            title: `Library: ${j.payload?.title ?? j.id}`,
            error: j.error,
            href: assetHref(j) ?? "/library",
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchLibrary);
        // Face blurring: a long render whose outcome belongs in notifications
        // rather than pinned to the asset that started it.
        const fetchBlur = apiFetch(`/api/workspaces/${activeWorkspaceId}/media/library/face-blur/status`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map((j: any) => ({
            id: j.id,
            category: "blur" as JobCategory,
            status: j.status,
            created_at: j.created_at,
            title: j.status === "succeeded" && j.result
              ? `Faces blurred: ${Math.round((j.result.coverage ?? 0) * 100)}% of frames, ${j.result.faces_tracked ?? 0} face(s)`
              : "Blurring faces",
            error: j.error,
            progress: j.progress,
            progressStage: j.progress_stage,
            startedAt: j.started_at,
            stalled: Boolean(j.stalled),
            assetId: j?.payload?.asset_id ?? j?.result?.asset_id ?? null,
            activityLabel: "Blurring faces",
            activityDetail: "Face privacy",
            // The asset it produced, which it only knows once it has one.
            href: assetHref(j),
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchBlur);
        // One asset-processing stream drives both notifications and thumbnail
        // overlays. Effects, captions, transcription and voice therefore share
        // the same lifecycle, and a new server-registered media kind needs no
        // new poll or UI state. Keep enough history for a 200-item batch.
        // The endpoint's maximum, because unfinished work is what this list is
      // for and there can legitimately be a lot of it: four batches going at
      // once is over three hundred jobs, and at 250 two of those batches were
      // absent from the answer entirely. The endpoint puts unfinished work
      // first, so this ceiling now bites on history rather than on anything
      // still to run.
      const fetchProcessing = apiFetch(`/api/workspaces/${activeWorkspaceId}/media/library/processing/jobs?limit=500`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map(mediaJob))
          .catch(() => []);
        fetchPromises.push(fetchProcessing);
        // Studio renders
        const fetchRenders = apiFetch(`/api/workspaces/${activeWorkspaceId}/studio/productions`)
          .then(res => res.json())
          .then(data => (data.renders || []).map((j: any) => ({
            id: j.id,
            category: "render" as JobCategory,
            status: j.status,
            created_at: j.created_at,
            title: `Render: ${j.id}`,
            error: j.error,
            href: assetHref(j) ?? "/library",
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchRenders);

        // Publish jobs
        const fetchPublish = apiFetch(`/api/workspaces/${activeWorkspaceId}/publishing/jobs`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map((j: any) => ({
            id: j.id,
            category: "publish" as JobCategory,
            status: j.status,
            created_at: j.created_at,
            title: `Publish: ${j.payload?.title ?? j.id}`,
            error: j.error,
            href: "/publish",
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchPublish);
      }

      const results = await Promise.all(fetchPromises);
      const combined = results.flat().sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        hasActiveJobs.current = combined.some((job) =>
          ["queued", "running", "in_progress", "pending"].includes(job.status));
        setJobs(combined);
      } catch (e) {
        console.error("Failed to refresh jobs", e);
      } finally {
        setBusy(false);
      }
    })();
    refreshInFlight.current = request;
    try {
      await request;
    } finally {
      if (refreshInFlight.current === request) refreshInFlight.current = null;
    }
  }, [activeWorkspaceId, apiFetch, mediaJob, user]);

  useEffect(() => {
    // Deferred, not immediate. This poll fans out to seven endpoints and lives
    // in the layout, so `queueMicrotask` fired it during hydration on every
    // page - right when first paint is already competing for the network. Let
    // the page paint, then start watching.
    let timer: number | undefined;
    let stopped = false;
    const poll = async () => {
      if (document.visibilityState !== "hidden") await refresh();
      if (stopped) return;
      // Progress deserves a responsive poll; a settled queue does not deserve
      // seven API calls every four seconds forever. The provider lives above
      // every route, so its idle traffic otherwise competes with whichever tab
      // the operator is trying to open.
      timer = window.setTimeout(poll, hasActiveJobs.current ? 2500 : 12000);
    };
    const start = window.setTimeout(() => {
      void poll();
    }, 1500);
    // Returning to the tab refreshes at once rather than waiting out the
    // interval, so a job that finished while away is not stale on the way back.
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(start);
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  useEffect(() => {
    if (typeof document !== 'undefined') {
      const activeJobsCount = jobs.filter((j) => ["queued", "running", "in_progress", "pending"].includes(j.status)).length;
      let link = document.querySelector("link[rel~='icon']") as HTMLLinkElement;
      if (!link) {
        link = document.createElement('link');
        link.rel = 'icon';
        document.head.appendChild(link);
      }

      if (activeJobsCount > 0) {
        // Red dot favicon for active jobs
        link.href = 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><circle cx=%2250%22 cy=%2250%22 r=%2250%22 fill=%22%23e13333%22/></svg>';
      } else {
        // Default favicon
        link.href = '/favicon.ico';
      }
    }
  }, [jobs]);

  return (
    <JobsContext.Provider value={{
      jobs,
      busy,
      activeWorkspaceId,
      announceMediaJobs,
      refresh,
    }}>
      {children}
    </JobsContext.Provider>
  );
}

export function useJobs() {
  const context = useContext(JobsContext);
  if (!context) throw new Error("useJobs must be used within a JobsProvider");
  return context;
}
