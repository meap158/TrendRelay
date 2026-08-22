"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Clock, Languages, Settings } from "lucide-react";
import { notificationHref } from "../lib/job-links";

import { useAuth } from "./auth-provider";
import { type BaseJob, useJobs } from "./jobs-provider";
import { NotificationContext } from "./notification-context";
import { useT } from "./i18n-provider";
import { Button } from "./ui/button";
import { ActionIcon } from "./ui/action-icons";
import { LanguagePicker } from "./ui/language-picker";
import { TimezonePicker } from "./ui/timezone-picker";
import { useWorkspace } from "./workspace-provider";

const READ_NOTIFICATIONS_KEY = "trendrelay:read-notifications:";
const MAX_STORED_READ_KEYS = 300;
/** Pointer travel before a press becomes a drag, so a click still clicks. */
const DRAG_THRESHOLD_PX = 6;

function notificationKey(job: BaseJob): string {
  return `${job.id}:${job.status}`;
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

/**
 * The states a notification row can be filtered by.
 *
 * Coarser than the badges on the rows themselves: a row tagged Queued is
 * waiting for its turn just as much as a batch tagged Waiting, and somebody
 * looking for what has not happened yet should find both under one chip.
 */
type NotificationFilter = "all" | "running" | "waiting" | "paused" | "succeeded" | "failed";
const NOTIFICATION_FILTERS: Exclude<NotificationFilter, "all">[] = [
  "running", "waiting", "paused", "succeeded", "failed",
];

/**
 * Which chip a notification answers to - its displayed state, not its raw
 * status word. Batches read their own roll-up, exactly as their badge does;
 * a lone job reads its own.
 */
function groupFilter(group: NotificationGroup): Exclude<NotificationFilter, "all"> {
  const batch = batchProgress(group);
  if (batch) {
    if (batch.working) return "running";
    if (batch.stalled) return "paused";
    if (batch.running) return "waiting";
    return batch.short || batch.failed ? "failed" : "succeeded";
  }
  const job = group.latest;
  if (job.stalled) return "paused";
  if (["failed", "cancelled"].includes(job.status)) return "failed";
  if (["running", "in_progress"].includes(job.status)) return "running";
  // Anything holding for a turn - queued, planned, scheduled - is waiting.
  if (!["succeeded"].includes(job.status)) return "waiting";
  return "succeeded";
}

/**
 * Drag-to-scroll for a strip too narrow for its content.
 *
 * Mouse users get the gesture every touch surface has taught them; touch and
 * pen keep the browser's native pan with its momentum, because a hand-rolled
 * copy of that feels worse everywhere it differs. A press that travels less
 * than the threshold stays a click, and the click that follows a real drag is
 * swallowed - releasing a drag over a chip must not also press it.
 */
function NotificationFilterStrip({
  chips,
  selected,
  onChange,
}: {
  chips: { key: NotificationFilter; count: number }[];
  selected: NotificationFilter;
  onChange: (next: NotificationFilter) => void;
}) {
  const t = useT();
  const frameRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const drag = useRef({ pointerId: -1, startX: 0, startScrollLeft: 0, active: false });
  const suppressClick = useRef(false);

  /**
   * Which ends still hold chips, written as attributes on the frame for its
   * fade pseudo-elements. Read straight off the DOM rather than through state:
   * this runs on every scroll tick, and a render per pixel of travel is a
   * price the list behind the strip should not pay.
   */
  const applyEdges = useCallback(() => {
    const strip = stripRef.current;
    const frame = frameRef.current;
    if (!strip || !frame) return;
    const overflow = strip.scrollWidth - strip.clientWidth > 1;
    // scrollLeft runs negative in RTL, so distance from the start is |value|.
    const fromStart = Math.abs(strip.scrollLeft);
    frame.toggleAttribute("data-can-start", overflow && fromStart > 1);
    frame.toggleAttribute(
      "data-can-end",
      overflow && fromStart < strip.scrollWidth - strip.clientWidth - 1,
    );
  }, []);

  // Measured when the chips change, since that is what changes their width,
  // and again on resize. Only DOM attributes move here, not React state.
  useEffect(() => {
    applyEdges();
    window.addEventListener("resize", applyEdges);
    return () => window.removeEventListener("resize", applyEdges);
  }, [applyEdges, chips]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: stripRef.current?.scrollLeft ?? 0,
      active: false,
    };
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (state.pointerId !== event.pointerId) return;
    const delta = event.clientX - state.startX;
    if (!state.active) {
      if (Math.abs(delta) < DRAG_THRESHOLD_PX) return;
      state.active = true;
      stripRef.current?.classList.add("dragging");
      // Capturing keeps the drag alive when the cursor leaves the strip.
      try {
        stripRef.current?.setPointerCapture(event.pointerId);
      } catch {
        // The pointer was already gone; the native pan takes over instead.
      }
    }
    // Assigned, not nudged by deltas: RTL scrollLeft runs negative, and
    // `start - travelled` holds in both directions without special cases.
    if (stripRef.current) {
      stripRef.current.scrollLeft = state.startScrollLeft - delta;
      applyEdges();
    }
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (state.pointerId !== event.pointerId) return;
    if (state.active) {
      suppressClick.current = true;
      // Cleared one macrotask out: the click following pointerup fires before
      // any timer, so it is eaten, while a genuine later click never is.
      window.setTimeout(() => { suppressClick.current = false; }, 0);
      stripRef.current?.classList.remove("dragging");
      try {
        stripRef.current?.releasePointerCapture(event.pointerId);
      } catch {
        // Already released with the pointer itself; nothing to clean up.
      }
    }
    drag.current = { pointerId: -1, startX: 0, startScrollLeft: 0, active: false };
  };

  const onClickCapture = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!suppressClick.current) return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div className="notification-filters" ref={frameRef}>
      <div
        ref={stripRef}
        role="group"
        aria-label={t("notifications.filterLabel")}
        className="notification-filter-strip"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClickCapture={onClickCapture}
        onScroll={applyEdges}
      >
        {chips.map(({ key, count }) => (
          <button
            key={key}
            type="button"
            data-filter={key}
            aria-pressed={selected === key}
            className={`notification-filter${selected === key ? " selected" : ""}`}
            onClick={() => onChange(key)}
          >
            {key === "all" ? t("notifications.filterAll") : t(`notifications.filter_${key}`)}
            <span className="notification-filter-count">{count}</span>
          </button>
        ))}
      </div>
    </div>
  );
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
  // A batch of one is a job. Marking it is still worth doing - the identity is
  // what keeps it from merging with the next single render of the same kind -
  // but "0 of 1 done" is progress chrome around something that has none to
  // report, so it reads as the plain row it was before batches existed.
  if (Math.max(batch.total, group.jobs.length) <= 1) return null;
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
  const { workspaces, workspaceId, setWorkspaceId, loading: workspaceLoading, error: workspaceError } = useWorkspace();
  const pathname = usePathname();
  const t = useT();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false);
  /** Which status chip the list is narrowed to; reset when the drawer closes. */
  const [statusFilter, setStatusFilter] = useState<NotificationFilter>("all");
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
  const workspaceShellRef = useRef<HTMLDivElement>(null);
  const workspaceButtonRef = useRef<HTMLButtonElement>(null);

  const selectedWorkspace = workspaces.find((workspace) => workspace.id === workspaceId);

  const storageKey = user ? READ_NOTIFICATIONS_KEY + user.id : null;
  const groups = useMemo(() => groupNotifications(jobs), [jobs]);
  // Counted over groups, not jobs: the badge should say how many things need
  // attention, and one failure repeated thirty times is one thing.
  const unreadCount = readStateReady
    ? groups.filter((group) => group.jobs.some((job) => !readKeys.has(notificationKey(job)))).length
    : 0;
  // Only the states actually present get a chip - an empty filter is a
  // promise with nothing behind it, and dropping it keeps the strip short
  // enough to skip scrolling on a quiet day.
  const filterChips = useMemo(() => {
    const counts = new Map<Exclude<NotificationFilter, "all">, number>();
    for (const group of groups) {
      const key = groupFilter(group);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [
      { key: "all" as const, count: groups.length },
      ...NOTIFICATION_FILTERS
        .filter((key) => (counts.get(key) ?? 0) > 0)
        .map((key) => ({ key, count: counts.get(key) ?? 0 })),
    ];
  }, [groups]);
  const visibleGroups = useMemo(
    () => statusFilter === "all"
      ? groups
      : groups.filter((group) => groupFilter(group) === statusFilter),
    [groups, statusFilter],
  );

  /**
   * The filter lives only as long as the drawer it serves. Reset on the way
   * into the drawer rather than on the way out: closing has several doors -
   * the X, Escape, a click outside, toggling the bell - and every one of them
   * opens again through this single path.
   */
  function openDrawer() {
    setStatusFilter("all");
    setDrawerOpen(true);
  }

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

  useEffect(() => {
    if (!workspaceMenuOpen) return;
    function closeFromOutside(event: PointerEvent) {
      if (!workspaceShellRef.current?.contains(event.target as Node)) setWorkspaceMenuOpen(false);
    }
    function closeFromKeyboard(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        setWorkspaceMenuOpen(false);
        workspaceButtonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", closeFromOutside);
    document.addEventListener("keydown", closeFromKeyboard);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      document.removeEventListener("keydown", closeFromKeyboard);
    };
  }, [workspaceMenuOpen]);

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

  // The frame renders with or without a session: the brand and the section
  // links need only the path, so the app has structure on screen the instant it
  // mounts rather than a blank bar while auth resolves.
  const frame = (
    <>
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
    </>
  );

  // Until the session is known, the workspace chrome - pickers, notifications -
  // is not ready; the frame alone still gives the toolbar its shape.
  if (!user) return <header className="app-toolbar">{frame}</header>;

  return (
    <header className="app-toolbar">
      {frame}
      <div className="toolbar-actions">
        <div className="workspace-session-shell" ref={workspaceShellRef}>
          <button
            ref={workspaceButtonRef}
            type="button"
            className="workspace-session-trigger"
            aria-label={t("workspace.settings")}
            title={t("workspace.settingsHelp")}
            aria-expanded={workspaceMenuOpen}
            aria-controls="workspace-session-panel"
            onClick={() => {
              setDrawerOpen(false);
              setWorkspaceMenuOpen((current) => !current);
            }}
          >
            <span>
              <strong>{selectedWorkspace?.name ?? (workspaceLoading ? t("workspace.loading") : t("workspace.none"))}</strong>
              <small>{localMode ? t("session.localAdmin") : user.email ?? user.id}{selectedWorkspace?.role ? ` · ${selectedWorkspace.role}` : ""}</small>
            </span>
            <span className="workspace-session-trigger-icons" aria-hidden="true">
              <Settings />
              <svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4" /></svg>
            </span>
          </button>
          {workspaceMenuOpen && (
            <section id="workspace-session-panel" className="workspace-session-panel workspace-settings-menu" aria-label={t("workspace.settings")}>
              <header className="workspace-settings-heading">
                <span className="workspace-settings-heading-icon" aria-hidden="true"><Settings /></span>
                <span>
                  <strong>{t("workspace.settings")}</strong>
                  <small>{t("workspace.settingsHelp")}</small>
                </span>
              </header>
              <section className="workspace-settings-section" aria-labelledby="workspace-settings-workspace">
                <header>
                  <strong id="workspace-settings-workspace">{t("workspace.workspaceSection")}</strong>
                  <small>{t("workspace.workspaceHelp")}</small>
                </header>
                {workspaceError && <p className="workspace-session-error">{workspaceError}</p>}
                <div className="workspace-session-list" role="listbox" aria-label={t("workspace.select")}>
                  {workspaces.map((workspace) => (
                    <button
                      key={workspace.id}
                      type="button"
                      role="option"
                      aria-selected={workspace.id === workspaceId}
                      className={workspace.id === workspaceId ? "selected" : ""}
                      onClick={() => {
                        setWorkspaceId(workspace.id);
                        setWorkspaceMenuOpen(false);
                        workspaceButtonRef.current?.focus();
                      }}
                    >
                      <span><strong>{workspace.name}</strong><small>{workspace.role}</small></span>
                      {workspace.id === workspaceId && <span className="workspace-session-check" aria-hidden="true">✓</span>}
                    </button>
                  ))}
                </div>
              </section>
              <section className="workspace-session-preferences" aria-labelledby="workspace-settings-preferences">
                <header>
                  <strong id="workspace-settings-preferences">{t("workspace.preferences")}</strong>
                  <small>{t("workspace.preferencesHelp")}</small>
                </header>
                <div className="workspace-preference-row">
                  <span className="workspace-preference-icon" aria-hidden="true"><Clock /></span>
                  <div>
                    <TimezonePicker />
                    <small>{t("workspace.timezoneHelp")}</small>
                  </div>
                </div>
                <div className="workspace-preference-row">
                  <span className="workspace-preference-icon" aria-hidden="true"><Languages /></span>
                  <div>
                    <LanguagePicker />
                    <small>{t("workspace.languageHelp")}</small>
                  </div>
                </div>
              </section>
              {!localMode && <Button variant="link" size="sm" onClick={() => void signOut()}>{t("session.signOut")}</Button>}
            </section>
          )}
        </div>
        <div className="notification-shell" ref={notificationShellRef}>
          <button
            ref={notificationButtonRef}
            type="button"
            className="notification-trigger"
            aria-label={unreadCount ? t("notifications.unreadCount", { count: unreadCount }) : t("notifications.heading")}
            aria-expanded={drawerOpen}
            aria-controls="notification-panel"
            onClick={() => {
              setWorkspaceMenuOpen(false);
              if (drawerOpen) setDrawerOpen(false);
              else openDrawer();
            }}
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

              {/* Status chips. The strip hides its scrollbar and drags with the
                  mouse instead; on a touch screen it pans natively. */}
              {filterChips.length > 1 && (
                <NotificationFilterStrip
                  chips={filterChips}
                  selected={statusFilter}
                  onChange={setStatusFilter}
                />
              )}

              {groups.length === 0 ? (
                <div className="notification-empty"><strong>{t("notifications.empty")}</strong><span>{t("notifications.emptyHelp")}</span></div>
              ) : visibleGroups.length === 0 ? (
                <div className="notification-empty"><strong>{t("notifications.filterEmpty")}</strong><span>{t("notifications.filterEmptyHelp")}</span></div>
              ) : (
                <ol className="notification-list">
                  {cancelError && <li className="notification-error" role="alert">{cancelError}</li>}
                  {visibleGroups.slice(0, 15).map((group) => {
                    const job = group.latest;
                    const batch = batchProgress(group);
                    const destination = notificationHref(group.jobs, { title: job.title }) ?? job.href;
                    const read = group.jobs.every((item) => readKeys.has(notificationKey(item)));
                    // One face for the row only when every job in it worked
                    // on the same asset; a mixed batch gets no favourite.
                    const assetIds = [...new Set(
                      group.jobs.map((item) => item.assetId).filter(Boolean),
                    )] as string[];
                    const sharedAssetId = assetIds.length === 1 ? assetIds[0] : null;
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
                        {destination ? (
                          <Link
                            className="notification-title linked"
                            href={destination}
                            onClick={() => { markRead(group); setDrawerOpen(false); }}
                          >{job.title}</Link>
                        ) : (
                          <strong className="notification-title">{job.title}</strong>
                        )}
                        {/* What the row is about, not just what happened to
                            it: the post that went out, or the clip it worked
                            on - shown only when every job in the group agrees
                            on which asset that is. */}
                        <NotificationContext
                          job={job}
                          workspaceId={workspaceId}
                          apiFetch={apiFetch}
                          assetId={sharedAssetId}
                        />
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
      </div>
    </header>
  );
}
