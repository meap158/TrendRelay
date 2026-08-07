"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "./auth-provider";
import { type BaseJob, useJobs } from "./jobs-provider";
import { Button } from "./ui/button";

const READ_NOTIFICATIONS_KEY = "trendrelay:read-notifications:";
const MAX_STORED_READ_KEYS = 300;

function notificationKey(job: BaseJob): string {
  return `${job.id}:${job.status}`;
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
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
  const { user, signOut, localMode } = useAuth();
  const { jobs } = useJobs();
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [readKeys, setReadKeys] = useState<Set<string>>(new Set());
  const [readStateReady, setReadStateReady] = useState(false);
  const notificationShellRef = useRef<HTMLDivElement>(null);
  const notificationButtonRef = useRef<HTMLButtonElement>(null);

  const storageKey = user ? READ_NOTIFICATIONS_KEY + user.id : null;
  const groups = useMemo(() => groupNotifications(jobs), [jobs]);
  // Counted over groups, not jobs: the badge should say how many things need
  // attention, and one failure repeated thirty times is one thing.
  const unreadCount = readStateReady
    ? groups.filter((group) => group.jobs.some((job) => !readKeys.has(notificationKey(job)))).length
    : 0;

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
  const discoverActive = pathname === "/discover" || pathname.startsWith("/discover/") || pathname === "/opportunities" || pathname.startsWith("/opportunities/");
  const libraryActive = pathname === "/library" || pathname.startsWith("/library/");
  const publishActive = pathname === "/publish" || pathname.startsWith("/publish/") || pathname === "/attribution" || pathname.startsWith("/attribution/");

  return (
    <header className="app-toolbar">
      <Link className="app-brand" href="/" aria-label="TrendRelay home">
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
        <Link className={discoverActive ? "active" : ""} href="/discover">Discover</Link>
        <Link className={pathname === "/" ? "active" : ""} href="/">Downloads</Link>
        <Link className={libraryActive ? "active" : ""} href="/library">Library</Link>
        <Link className={publishActive ? "active" : ""} href="/publish">Publish</Link>
        <Link className={pathname === "/campaigns" ? "active" : ""} href="/campaigns">Campaigns</Link>
        <Link className={pathname === "/tools" ? "active" : ""} href="/tools">Tools</Link>
      </nav>

      <div className="toolbar-actions">
        <div className="notification-shell" ref={notificationShellRef}>
          <button
            ref={notificationButtonRef}
            type="button"
            className="notification-trigger"
            aria-label={unreadCount ? `Notifications, ${unreadCount} unread` : "Notifications"}
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
            <section id="notification-panel" className="notification-panel" aria-label="Notifications">
              <header className="notification-heading">
                <div>
                  <h2>Notifications</h2>
                  <p>{unreadCount ? `${unreadCount} unread` : "You are all caught up"}</p>
                </div>
                <div className="notification-heading-actions">
                  <button type="button" className="notification-mark-all" disabled={unreadCount === 0} onClick={markAllRead}>Mark all read</button>
                  <button type="button" className="notification-close" aria-label="Close notifications" onClick={closeDrawer}>×</button>
                </div>
              </header>

              {groups.length === 0 ? (
                <div className="notification-empty"><strong>No notifications yet</strong><span>Job updates will appear here.</span></div>
              ) : (
                <ol className="notification-list">
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
                        <strong className="notification-title">{job.title}</strong>
                        {job.error && <p className="notification-error">{job.error}</p>}
                        <footer>
                          <time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time>
                          {read
                            ? <span className="notification-read-label">Read</span>
                            : <button type="button" className="notification-row-read" onClick={() => markRead(group)}>Mark read</button>}
                        </footer>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          )}
        </div>
        {localMode ? <span className="local-admin-badge" title="Development-only loopback session">Local admin</span> : <Button variant="link" size="sm" onClick={() => void signOut()}>Sign out</Button>}
      </div>
    </header>
  );
}
