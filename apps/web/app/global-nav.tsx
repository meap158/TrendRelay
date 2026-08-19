"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "./auth-provider";
import { type BaseJob, useJobs } from "./jobs-provider";
import { useT } from "./i18n-provider";
import { Button } from "./ui/button";
import { ActionIcon } from "./ui/action-icons";
import { LanguagePicker } from "./ui/language-picker";
import { TimezonePicker } from "./ui/timezone-picker";

const READ_NOTIFICATIONS_KEY = "trendrelay:read-notifications:";
const MAX_STORED_READ_KEYS = 300;

function notificationKey(job: BaseJob): string {
  return `${job.id}:${job.status}`;
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

/** Progress needs a few percent behind it before an estimate means anything. */
const ESTIMATE_AFTER = 0.04;

/**
 * Roughly how much longer, from how long it has taken to get this far.
 *
 * Measured rather than predicted: nothing here knows how long a clip is or how
 * fast this machine encodes, and the one thing that does know is the work
 * already done. Withheld until a few percent are in, because dividing by a
 * fraction near zero produces a confident-looking number that is nonsense — and
 * "4 hours left" on a job that finishes in thirty seconds is worse than saying
 * nothing.
 *
 * The passes are weighted so the fraction tracks time rather than frames, which
 * is what keeps this from lurching when a render moves from reading a clip to
 * writing it.
 */
function timeRemaining(job: BaseJob, now: number): string {
  // Nothing is working on it, so elapsed keeps growing while progress does not:
  // the estimate would climb for as long as the drawer stayed open.
  if (job.stalled) return "";
  if (typeof job.progress !== "number" || job.progress < ESTIMATE_AFTER) return "";
  if (!job.startedAt || !now) return "";
  const elapsed = now - new Date(job.startedAt).getTime();
  if (!Number.isFinite(elapsed) || elapsed <= 0) return "";
  const left = Math.round((elapsed * (1 - job.progress)) / job.progress / 1000);
  if (left <= 0) return "almost done";
  if (left < 60) return `about ${left}s left`;
  const minutes = Math.round(left / 60);
  if (minutes < 60) return `about ${minutes} min left`;
  return `about ${Math.round(minutes / 60)} h left`;
}

type NotificationGroup = {
  key: string;
  latest: BaseJob;
  jobs: BaseJob[];
};

/**
 * What makes two notifications the same event rather than two events.
 *
 * When jobs carry an error, the error is the identity: one upstream failure
 * that hits thirty ingests is one thing that went wrong, not thirty. Without an
 * error the title is the identity instead, so two separate downloads stay two
 * rows rather than collapsing into a meaningless "2 jobs".
 */
function groupKey(job: BaseJob): string {
  // A batch is one thing somebody started, however many jobs carry it out.
  // Keyed on the batch alone - not on the status - because the point is to
  // watch it move from queued to done, and a key including the status would
  // split it into three rows that each claim to be the batch.
  const batch = batchOf(job);
  if (batch) return `${job.category}batch${batch.id}`;
  return [job.category, job.status, job.error || job.title].join("");
}

/** The batch marker the API puts on every job it queues together. */
function batchOf(job: BaseJob): { id: string; total: number } | null {
  const batch = job.raw?.payload?.batch;
  return batch?.id ? { id: String(batch.id), total: Number(batch.total) || 0 } : null;
}

/**
 * What a batch of jobs adds up to.
 *
 * Its own state rather than its newest job's: "succeeded" on the latest of
 * seventy-seven says nothing about the seventy-six behind it. Failures are
 * counted rather than folded away, because a batch that finished with three
 * casualties is not a batch that worked.
 */
function batchProgress(group: NotificationGroup): {
  total: number; settled: number; failed: number; stalled: number;
  retrying: number; spent: number; working: number;
  running: boolean; short: boolean; label: string;
} | null {
  const batch = batchOf(group.latest);
  if (!batch) return null;
  const settled = group.jobs.filter(
    (job) => ["succeeded", "failed", "cancelled"].includes(job.status),
  ).length;
  const failed = group.jobs.filter(
    (job) => ["failed", "cancelled"].includes(job.status),
  ).length;
  // The total the batch was queued with, not how many of its jobs this
  // drawer happens to hold: the list is capped, and counting rows would
  // report a batch of seventy-seven as a batch of fifteen.
  const total = Math.max(batch.total, group.jobs.length);
  // Held rather than progressing: nothing holds their lease. Counted here
  // because "running" over a batch where three items are stuck is the report
  // somebody watches for twenty minutes before working out that it is wrong.
  const stalled = group.jobs.filter((job) => job.stalled).length;
  // Actually being worked on: running, and with a worker still holding it.
  // A batch of seventy-one is a queue with a few in flight, so some of its
  // jobs having lost their worker says nothing about whether the batch is
  // moving - and it was moving that mattered to whoever queued it.
  const working = group.jobs.filter(
    (job) => ["running", "in_progress"].includes(job.status) && !job.stalled,
  ).length;
  // A job that lost its worker gets picked up again - until it has used its
  // attempts, after which the queue gives up on it and records a failure.
  // Counting both as "to retry" promises a recovery that is not coming.
  const spent = group.jobs.filter(
    (job) => job.stalled
      && Number(job.raw?.attempt_count ?? 0) >= Number(job.raw?.max_attempts ?? 0),
  ).length;
  const retrying = stalled - spent;
  // Running means something is still to happen, not that the arithmetic has
  // not reached the total. A batch whose jobs were never all created - the
  // rest refused at queueing time - can never reach it, and called itself
  // running for ever while nothing on the machine was doing anything.
  const live = group.jobs.filter(
    (job) => ["queued", "running", "in_progress"].includes(job.status),
  ).length;
  const running = live > 0;
  // Fewer jobs than the batch set out to make. Said rather than hidden: the
  // difference is work that was asked for and never started.
  const short = !running && settled < total;
  const label = [
    running
      ? `${settled} of ${total} done`
      : short
        ? `${settled} of ${total} ran · the rest were never queued`
        : failed
          ? `${total - failed} of ${total} done`
          : `All ${total} done`,
    failed ? `${failed} failed` : "",
    // Named for what will happen to them. "Paused" beside a batch that is
    // visibly working reads as a fault; these are picked up again as the
    // batch reaches them, and only mean nothing-is-happening when nothing
    // else is running either.
    retrying ? (working ? `${retrying} to retry` : `${retrying} paused`) : "",
    spent ? `${spent} giving up` : "",
  ].filter(Boolean).join(" · ");
  return {
    total, settled, failed, stalled, retrying, spent, working, running, short, label,
  };
}

/**
 * Collapse repeats, keeping the most recent of each.
 *
 * The drawer previously rendered one row per job, so an upstream provider that
 * reported the same diagnostic on every job filled the panel with identical
 * paragraphs and pushed everything else out of the fifteen it shows.
 */
function groupNotifications(jobs: BaseJob[]): NotificationGroup[] {
  const groups = new Map<string, NotificationGroup>();
  for (const job of jobs) {
    const key = groupKey(job);
    const found = groups.get(key);
    if (found) found.jobs.push(job);
    // `jobs` arrives newest first, so the first of each group is the latest.
    else groups.set(key, { key, latest: job, jobs: [job] });
  }
  return Array.from(groups.values());
}

export function GlobalNav() {
  const { user, signOut, localMode, apiFetch } = useAuth();
  const { jobs, refresh: refreshJobs } = useJobs();
  const pathname = usePathname();
  const t = useT();
  const [drawerOpen, setDrawerOpen] = useState(false);
  /**
   * A clock, so an estimate counts down between polls rather than sitting still
   * for four seconds at a time. Zero until the drawer is open: reading the real
   * time during a render would differ between the server and the browser, and
   * nothing needs it while nobody is looking.
   */
  const [now, setNow] = useState(0);
  const [readKeys, setReadKeys] = useState<Set<string>>(new Set());
  const [readStateReady, setReadStateReady] = useState(false);
  const [cancellingJobId, setCancellingJobId] = useState("");
  const [cancelError, setCancelError] = useState("");
  const notificationShellRef = useRef<HTMLDivElement>(null);
  const notificationButtonRef = useRef<HTMLButtonElement>(null);

  const storageKey = user ? READ_NOTIFICATIONS_KEY + user.id : null;
  const groups = useMemo(() => groupNotifications(jobs), [jobs]);
  // Counted over groups, not jobs: the badge should say how many things need
  // attention, and one failure repeated thirty times is one thing.
  const unreadCount = readStateReady
    ? groups.filter((group) => group.jobs.some((job) => !readKeys.has(notificationKey(job)))).length
    : 0;

  /**
   * Stop what is left of a batch.
   *
   * One row stands for every job in it, so its Cancel has to mean the same
   * thing the row does. Cancelling the newest job of seventy-one and leaving
   * seventy running is not what anybody pressing it is asking for.
   *
   * Only the unfinished ones: a finished render has nothing to stop, and
   * asking the API to cancel it would report an error about something that
   * went right.
   */
  async function cancelEditBatch(group: NotificationGroup) {
    const live = group.jobs.filter(
      (job) => ["queued", "running", "in_progress"].includes(job.status),
    );
    if (!live.length) return;
    const workspaceId = group.latest.raw?.workspace_id;
    if (!workspaceId) return;
    if (live.length > 1 && !window.confirm(
      `Stop the ${live.length} items of this batch that have not finished?`
    )) return;
    setCancellingJobId(group.latest.id);
    setCancelError("");
    const failures: string[] = [];
    try {
      for (const job of live) {
        try {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/media/library/effects/jobs/${job.id}/cancel`,
            { method: "POST" },
          );
          if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            failures.push(body.detail ?? `${job.id} could not be stopped.`);
          }
        } catch {
          failures.push(`${job.id} could not be stopped.`);
        }
      }
      // Reported as a count, because a batch that could not stop three of
      // seventy is one message, not three.
      if (failures.length) {
        setCancelError(
          `${live.length - failures.length} of ${live.length} stopped. `
          + `${failures[0]}`,
        );
      }
      await refreshJobs();
    } finally {
      setCancellingJobId("");
    }
  }

  // Ticks only while the drawer is open and something is actually running, so
  // a closed drawer costs nothing and a finished queue stops the clock.
  const anyRunning = jobs.some((job) =>
    ["running", "in_progress"].includes(job.status) && typeof job.progress === "number");
  useEffect(() => {
    if (!drawerOpen || !anyRunning) return;
    // First reading on the next tick rather than in the effect body: setting
    // state synchronously here would cascade a render for a clock nobody has
    // waited a second for yet.
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [anyRunning, drawerOpen]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      if (!storageKey) {
        setReadKeys(new Set());
        setReadStateReady(false);
        return;
      }
      try {
        const stored = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]") as unknown;
        setReadKeys(new Set(Array.isArray(stored) ? stored.filter((value): value is string => typeof value === "string") : []));
      } catch {
        setReadKeys(new Set());
      }
      setReadStateReady(true);
    });
  return () => { cancelled = true; };
  }, [storageKey]);

  useEffect(() => {
    if (!drawerOpen) return;
    function closeFromOutside(event: PointerEvent) {
      if (!notificationShellRef.current?.contains(event.target as Node)) setDrawerOpen(false);
    }
    function closeFromKeyboard(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setDrawerOpen(false);
        notificationButtonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", closeFromOutside);
    document.addEventListener("keydown", closeFromKeyboard);
  return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      document.removeEventListener("keydown", closeFromKeyboard);
    };
  }, [drawerOpen]);

  if (!user) return null;

  function saveReadKeys(next: Set<string>) {
    const bounded = new Set(Array.from(next).slice(-MAX_STORED_READ_KEYS));
    setReadKeys(bounded);
    if (storageKey) {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(Array.from(bounded)));
      } catch {
        // Read state remains available for this session when storage is unavailable.
      }
    }
  }

  /** Reading a group reads every job in it; they are one event to the reader. */
  function markRead(group: NotificationGroup) {
    const next = new Set(readKeys);
    group.jobs.forEach((job) => next.add(notificationKey(job)));
    saveReadKeys(next);
  }

  function markAllRead() {
    const next = new Set(readKeys);
    jobs.forEach((job) => next.add(notificationKey(job)));
    saveReadKeys(next);
  }

  function closeDrawer() {
    setDrawerOpen(false);
    notificationButtonRef.current?.focus();
  }
  // /opportunities is a redirect into Discover now, so it lights the same
  // entry rather than looking like a destination of its own.
  const discoverActive = pathname === "/discover" || pathname.startsWith("/discover/")
    || pathname === "/opportunities";
  const libraryActive = pathname === "/library" || pathname.startsWith("/library/");
  // Its own destination rather than a child of Publish. Attribution now carries
  // products, links, revenue and book economics - three former pages - and
  // reaching it through Publish made the biggest surface here the hardest to
  // find. /catalog redirects into it, so that path lights it up too.
  const attributionActive = pathname === "/attribution"
    || pathname.startsWith("/attribution/")
    || pathname === "/catalog";
  const publishActive = pathname === "/publish" || pathname.startsWith("/publish/");

  return (
    <header className="app-toolbar">
      <Link className="app-brand" href="/" aria-label={t("session.home")}>
        <span className="app-brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" focusable="false">
            <path d="M7.5 21.5 13 16l5 3 6.5-8.5" />
            <circle cx="7.5" cy="21.5" r="2" />
            <circle cx="13" cy="16" r="2" />
            <circle cx="18" cy="19" r="2" />
            <circle cx="24.5" cy="10.5" r="2" />
          </svg>
        </span>
        <strong>TrendRelay</strong>
      </Link>
      <nav className="app-nav">
        <Link className={discoverActive ? "active" : ""} href="/discover"><ActionIcon name="search" /><span>{t("nav.discover")}</span></Link>
        <Link className={pathname === "/" ? "active" : ""} href="/"><ActionIcon name="download" /><span>{t("common.download")}</span></Link>
        <Link className={libraryActive ? "active" : ""} href="/library"><ActionIcon name="grid" /><span>{t("nav.library")}</span></Link>
        <Link className={attributionActive ? "active" : ""} href="/attribution"><ActionIcon name="link" /><span>{t("nav.attribution")}</span></Link>
        <Link className={publishActive ? "active" : ""} href="/publish"><ActionIcon name="publish" /><span>{t("nav.publish")}</span></Link>
        <Link className={pathname === "/campaigns" ? "active" : ""} href="/campaigns"><ActionIcon name="campaign" /><span>{t("nav.campaigns")}</span></Link>
        <Link className={pathname === "/tools" ? "active" : ""} href="/tools"><ActionIcon name="setup" /><span>{t("nav.tools")}</span></Link>
      </nav>

      <div className="toolbar-actions">
        {/* Beside the language, because they are the same kind of setting:
            one says what the workspace reads in, the other what clock it
            keeps. Both decide how everything else is presented. */}
        <TimezonePicker compact />
        <LanguagePicker compact />
        <div className="notification-shell" ref={notificationShellRef}>
          <button
            ref={notificationButtonRef}
            type="button"
            className="notification-trigger"
            aria-label={unreadCount ? t("notifications.unreadCount", { count: unreadCount }) : t("notifications.heading")}
            aria-expanded={drawerOpen}
            aria-controls="notification-panel"
            onClick={() => setDrawerOpen((current) => !current)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
            </svg>
            {unreadCount > 0 && <span className="notification-count" aria-hidden="true">{unreadCount > 99 ? "99+" : unreadCount}</span>}
          </button>

          {drawerOpen && (
            <section id="notification-panel" className="notification-panel" aria-label={t("notifications.heading")}>
              <header className="notification-heading">
                <div>
                  <h2>{t("notifications.heading")}</h2>
                  <p>{unreadCount ? `${unreadCount} unread` : "You are all caught up"}</p>
                </div>
                <div className="notification-heading-actions">
                  <button type="button" className="notification-mark-all" disabled={unreadCount === 0} onClick={markAllRead}>{t("notifications.markAllRead")}</button>
                  <button type="button" className="notification-close" aria-label={t("notifications.close")} onClick={closeDrawer}><ActionIcon name="dismiss" /></button>
                </div>
              </header>

              {groups.length === 0 ? (
                <div className="notification-empty"><strong>{t("notifications.empty")}</strong><span>{t("notifications.emptyHelp")}</span></div>
              ) : (
                <ol className="notification-list">
                  {cancelError && <li className="notification-error" role="alert">{cancelError}</li>}
                  {groups.slice(0, 15).map((group) => {
                    const job = group.latest;
                    const batch = batchProgress(group);
                    const read = group.jobs.every((item) => readKeys.has(notificationKey(item)));
                    return (
                      <li className={read ? "notification-item read" : "notification-item unread"} key={group.key}>
                        <div className="notification-item-topline">
                          <span className="notification-category">{job.category}</span>
                          {/* How many jobs this one message stands for. Shown
                              rather than repeated, so the count is information
                              instead of noise. */}
                          {group.jobs.length > 1 && !batch && (
                            <span className="notification-repeat">×{group.jobs.length}</span>
                          )}
                          {batch && (
                            <span className="notification-repeat">{batch.total} items</span>
                          )}
                          {/* "running" is what the row says; "paused" is what
                              is true when no worker holds its lease. */}
                          {/* A batch's own state, not its newest job's. */}
                          {/* Working beats waiting. A batch with one clip
                              being rendered and three to retry is running, and
                              calling it paused sent somebody to look for a
                              worker that was already there. */}
                          {batch
                            ? <span className={`notification-status status-${
                                batch.working ? "running"
                                  : batch.stalled ? "paused"
                                    : batch.running ? "running"
                                      : batch.short || batch.failed ? "failed" : "succeeded"}`}>
                                {batch.working ? "running"
                                  : batch.stalled ? "paused"
                                    : batch.running ? "waiting"
                                      : batch.short ? "stopped short"
                                        : batch.failed ? "finished with failures" : "succeeded"}
                              </span>
                            : job.stalled
                              ? <span className="notification-status status-paused">paused</span>
                              : <span className={`notification-status status-${job.status.replace(/[^a-z0-9_-]/gi, "-")}`}>{statusLabel(job.status)}</span>}
                        </div>
                        {/* Opened rather than merely read. A notification says
                            something finished, and the next thing anyone wants
                            is to look at it - so the title is the way there
                            when the job produced something to see, and stays
                            plain text when it did not rather than becoming a
                            link to somewhere unrelated. */}
                        {job.href ? (
                          <Link
                            className="notification-title linked"
                            href={job.href}
                            onClick={() => { markRead(group); setDrawerOpen(false); }}
                          >{job.title}</Link>
                        ) : (
                          <strong className="notification-title">{job.title}</strong>
                        )}
                        {/* A render is minutes of work, and between "running"
                            and "succeeded" there was nothing to distinguish it
                            from a job that had hung. Only while it is running:
                            a finished bar is a bar nobody needs. */}
                        {/* How far through the selection it is. One clip's
                            own percentage is not what somebody who queued
                            seventy-seven of them wants to know. */}
                        {batch && (
                          <div className="notification-progress">
                            <div
                              className="notification-progress-track"
                              role="progressbar"
                              aria-valuemin={0}
                              aria-valuemax={batch.total}
                              aria-valuenow={batch.settled}
                              aria-label={`${job.title}: ${batch.label}`}
                            >
                              <span style={{ width: `${Math.round((batch.settled / batch.total) * 100)}%` }} />
                            </div>
                            <small>{batch.label}</small>
                            {batch.stalled > 0 && (
                              <small className="notification-stalled">
                                {[
                                  batch.retrying && batch.working
                                    ? `${batch.retrying === 1 ? "One item" : `${batch.retrying} items`} lost `
                                      + "a worker and go back in the queue; the batch is still running."
                                    : batch.retrying
                                      ? `${batch.retrying === 1 ? "One item is" : `${batch.retrying} items are`} `
                                        + "waiting on a worker. They resume on their own once one runs."
                                      : "",
                                  batch.spent
                                    ? `${batch.spent === 1 ? "One item has" : `${batch.spent} items have`} `
                                      + "used every attempt and will be recorded as failed."
                                    : "",
                                ].filter(Boolean).join(" ")}
                              </small>
                            )}
                          </div>
                        )}
                        {!batch && typeof job.progress === "number"
                          && ["running", "in_progress"].includes(job.status) && (
                          <div className={`notification-progress${job.stalled ? " stalled" : ""}`}>
                            <div
                              className="notification-progress-track"
                              role="progressbar"
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={Math.round(job.progress * 100)}
                              aria-label={job.progressStage || job.title}
                            >
                              <span style={{ width: `${Math.round(job.progress * 100)}%` }} />
                            </div>
                            <small>
                              {[
                                job.progressStage,
                                `${Math.round(job.progress * 100)}%`,
                                timeRemaining(job, now),
                              ].filter(Boolean).join(" · ")}
                            </small>
                            {/* The percentage above is where it stopped, not
                                where it is. Say so, or it reads as progress. */}
                            {job.stalled && (
                              <small className="notification-stalled">
                                Nothing is working on this. It resumes on its own once a
                                worker is running.
                              </small>
                            )}
                          </div>
                        )}
                        {job.error && <p className="notification-error">{job.error}</p>}
                        <footer>
                          <time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time>
                          {/* Offered while the batch has something to stop,
                              not while its newest job happens to be unfinished.
                              A batch row said "running" with no way to stop it
                              whenever the latest of its jobs had already
                              succeeded - and no way to stop it is the correct
                              answer only when there is nothing left running. */}
                          {job.category === "edit" && (batch
                            ? batch.running
                            : ["queued", "running"].includes(job.status)) && (
                            <Button
                              variant="quiet"
                              size="sm"
                              busy={cancellingJobId === job.id}
                              onClick={() => void cancelEditBatch(group)}
                            >{batch && batch.running
                              ? `Cancel the rest`
                              : t("common.cancel")}</Button>
                          )}
                          {read
                            ? <span className="notification-read-label">{t("notifications.read")}</span>
                            : <button type="button" className="notification-row-read" onClick={() => markRead(group)}>{t("notifications.markRead")}</button>}
                        </footer>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          )}
        </div>
        {localMode ? <span className="local-admin-badge" title={t("session.loopbackOnly")}>{t("session.localAdmin")}</span> : <Button variant="link" size="sm" onClick={() => void signOut()}>{t("session.signOut")}</Button>}
      </div>
    </header>
  );
}
