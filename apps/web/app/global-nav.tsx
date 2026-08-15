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
  return [job.category, job.status, job.error || job.title].join("");
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

  async function cancelEditJob(job: BaseJob) {
    const workspaceId = job.raw?.workspace_id;
    if (!workspaceId) return;
    setCancellingJobId(job.id);
    setCancelError("");
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/effects/jobs/${job.id}/cancel`,
        { method: "POST" },
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The effect job could not be cancelled.");
      await refreshJobs();
    } catch (reason) {
      setCancelError(reason instanceof Error ? reason.message : "The effect job could not be cancelled.");
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
                    const read = group.jobs.every((item) => readKeys.has(notificationKey(item)));
                    return (
                      <li className={read ? "notification-item read" : "notification-item unread"} key={group.key}>
                        <div className="notification-item-topline">
                          <span className="notification-category">{job.category}</span>
                          {/* How many jobs this one message stands for. Shown
                              rather than repeated, so the count is information
                              instead of noise. */}
                          {group.jobs.length > 1 && (
                            <span className="notification-repeat">×{group.jobs.length}</span>
                          )}
                          <span className={`notification-status status-${job.status.replace(/[^a-z0-9_-]/gi, "-")}`}>{statusLabel(job.status)}</span>
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
                        {typeof job.progress === "number"
                          && ["running", "in_progress"].includes(job.status) && (
                          <div className="notification-progress">
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
                          </div>
                        )}
                        {job.error && <p className="notification-error">{job.error}</p>}
                        <footer>
                          <time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time>
                          {job.category === "edit" && ["queued", "running"].includes(job.status) && (
                            <Button
                              variant="quiet"
                              size="sm"
                              busy={cancellingJobId === job.id}
                              onClick={() => void cancelEditJob(job)}
                            >{t("common.cancel")}</Button>
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
