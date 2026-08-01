"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "./auth-provider";
import { type BaseJob, useJobs } from "./jobs-provider";

const READ_NOTIFICATIONS_KEY = "trendrelay:read-notifications:";
const MAX_STORED_READ_KEYS = 300;

function notificationKey(job: BaseJob): string {
  return `${job.id}:${job.status}`;
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
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
  const unreadCount = readStateReady
    ? jobs.filter((job) => !readKeys.has(notificationKey(job))).length
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

  function markRead(job: BaseJob) {
    const next = new Set(readKeys);
    next.add(notificationKey(job));
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

  return (
    <header className="app-toolbar">
      <Link className="app-brand" href="/"><span>TR</span><strong>TrendRelay</strong></Link>
      <nav className="app-nav">
        <Link className={pathname === "/" ? "active" : ""} href="/">Pipeline</Link>
        <Link className={pathname === "/research" ? "active" : ""} href="/research">Research</Link>
        <Link className={pathname === "/opportunities" ? "active" : ""} href="/opportunities">Opportunities</Link>
        <Link className={pathname === "/library" ? "active" : ""} href="/library">Library</Link>
        <Link className={pathname === "/studio" ? "active" : ""} href="/studio">Studio</Link>
        <Link className={pathname === "/campaigns" ? "active" : ""} href="/campaigns">Campaigns</Link>
        <Link className={pathname === "/attribution" ? "active" : ""} href="/attribution">Attribution</Link>
        <Link className={pathname === "/publish" ? "active" : ""} href="/publish">Publish</Link>
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

              {jobs.length === 0 ? (
                <div className="notification-empty"><strong>No notifications yet</strong><span>Job updates will appear here.</span></div>
              ) : (
                <ol className="notification-list">
                  {jobs.slice(0, 15).map((job) => {
                    const read = readKeys.has(notificationKey(job));
                    return (
                      <li className={read ? "notification-item read" : "notification-item unread"} key={notificationKey(job)}>
                        <div className="notification-item-topline">
                          <span className="notification-category">{job.category}</span>
                          <span className={`notification-status status-${job.status.replace(/[^a-z0-9_-]/gi, "-")}`}>{statusLabel(job.status)}</span>
                        </div>
                        <strong className="notification-title">{job.title}</strong>
                        {job.error && <p className="notification-error">{job.error}</p>}
                        <footer>
                          <time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time>
                          {read
                            ? <span className="notification-read-label">Read</span>
                            : <button type="button" className="notification-row-read" onClick={() => markRead(job)}>Mark read</button>}
                        </footer>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          )}
        </div>
        {localMode ? <span className="local-admin-badge" title="Development-only loopback session">Local admin</span> : <button className="text-button" onClick={() => void signOut()}>Sign out</button>}
      </div>
    </header>
  );
}
