"use client";

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { useAuth } from "./auth-provider";
import { apiBaseUrl } from "../lib/api";
import { fetchWorkspaces } from "../lib/workspaces";
import { effectLabel } from "../lib/i18n/effects";
import { assetHref } from "../lib/job-links";
import { useT } from "./i18n-provider";

type Translate = (path: string, values?: Record<string, string | number>) => string;
type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
type JobCategory = "fetch" | "media" | "render" | "publish" | "research" | "blur" | "edit";

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
  // Specific payloads preserved for UI needs
  raw: any;
};


type JobsContextValue = {
  jobs: BaseJob[];
  busy: boolean;
  activeWorkspaceId: string | null;
  setActiveWorkspaceId: (id: string | null) => void;
  announceEffectJobs: (jobs: any[]) => void;
  refresh: () => Promise<void>;
};

const JobsContext = createContext<JobsContextValue | null>(null);

export function JobsProvider({ children }: { children: ReactNode }) {
  const { user, apiFetch } = useAuth();
  const t = useT();
  const [jobs, setJobs] = useState<BaseJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null);

  // Notifications are global, so their workspace cannot depend on first
  // visiting Library, Download, or Publish. Resolve a default as soon as the
  // signed-in shell mounts; page-level selectors can still replace it later.
  useEffect(() => {
    if (!user || activeWorkspaceId) return;
    let cancelled = false;
    void fetchWorkspaces(apiFetch)
      .then((body) => {
        const first = body.workspaces?.[0]?.id;
        if (!cancelled && first) {
          setActiveWorkspaceId((current) => current || first);
        }
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [activeWorkspaceId, apiFetch, user]);

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
    href: assetHref(job),
    raw: job,
  }), [t]);

  /**
   * Put a job returned by a mutating request on screen immediately.
   *
   * Polling remains the authority for later progress, but waiting for its next
   * four-second tick made a repeat render look as though the click did nothing:
   * there was no notification, thumbnail overlay, or detail-card activity in
   * the interval. Replacing by id also lets cancellation update the same row.
   */
  const announceEffectJobs = useCallback((incoming: any[]) => {
    const announced = incoming.filter((job) => job?.id).map(effectJob);
    if (!announced.length) return;
    const ids = new Set(announced.map((job) => job.id));
    setJobs((current) => [...announced, ...current.filter((job) => !ids.has(job.id))]
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()));
  }, [effectJob]);

  const refresh = useCallback(async () => {
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
            // The asset it produced, which it only knows once it has one.
            href: assetHref(j),
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchBlur);
        // Everything the editing suite renders. Face blur had notifications
        // because it was the first long render here; a recipe render takes just
        // as long, produces the cut that Publish will send, and used to finish
        // in silence — the editor said "it will appear as a version" and left
        // the operator to keep reopening the asset to find out whether it had.
        // A batch can contain 200 independently tracked items. Fetch enough
        // history for every selected asset to keep its inline activity visible;
        // the notification drawer still groups and presents this same stream.
        const fetchEdits = apiFetch(`/api/workspaces/${activeWorkspaceId}/media/library/effects/jobs?limit=250`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map(effectJob))
          .catch(() => []);
        fetchPromises.push(fetchEdits);
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

      setJobs(combined);
    } catch (e) {
      console.error("Failed to refresh jobs", e);
    } finally {
      setBusy(false);
    }
  }, [activeWorkspaceId, apiFetch, effectJob, user]);

  useEffect(() => {
    // Deferred, not immediate. This poll fans out to seven endpoints and lives
    // in the layout, so `queueMicrotask` fired it during hydration on every
    // page - right when first paint is already competing for the network. Let
    // the page paint, then start watching.
    let timer: ReturnType<typeof setInterval> | undefined;
    const start = window.setTimeout(() => {
      void refresh();
      timer = setInterval(() => {
        // A hidden tab is nobody watching a progress bar. Skip the fan-out while
        // it is away and pick it back up on return, below.
        if (document.visibilityState !== "hidden") void refresh();
      }, 4000);
    }, 800);
    // Returning to the tab refreshes at once rather than waiting out the
    // interval, so a job that finished while away is not stale on the way back.
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearTimeout(start);
      if (timer) clearInterval(timer);
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
      setActiveWorkspaceId,
      announceEffectJobs,
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
