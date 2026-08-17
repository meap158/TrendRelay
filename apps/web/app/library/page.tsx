"use client";

import { Check, CircleAlert, CircleCheck, CirclePause, CircleX, Layers3, LoaderCircle, Undo2 } from "lucide-react";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
import { effectLabel, effectTag } from "../../lib/i18n/effects";
import { useAuth } from "../auth-provider";
import { type BaseJob, useJobs } from "../jobs-provider";
import { useT } from "../i18n-provider";
import { blurredVersion, handoffPath, openingCut } from "../../lib/media-rules";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { Button, buttonClass } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { ActionIcon, bulkActionIcon } from "../ui/action-icons";
import { StatusToasts, useStatus } from "../ui/status";
import { Badge } from "../ui/primitives";
import { CaptionEditor } from "./caption-editor";
import { ClipEditor } from "./clip-editor";
import { EffectEditor } from "./effect-editor";
import { TranscriptionSwitch } from "./transcription-setup";
import {
  AssetFilters,
  EMPTY_FACETS,
  activeFilterCount,
  assetFilterParams,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";

type Workspace = { id: string; name: string; role: string };
type ViewMode = "gallery" | "list";
type GroupBy = "none" | "channel" | "source";

type VersionEffect = { id: string; label: string };
type Version = {
  kind: "original" | "proxy" | "thumbnail" | "audio" | "blurred" | "edited" | "captioned";
  path: string;
  size_bytes: number;
  /** What produced this cut, in the order it was applied. */
  effects?: VersionEffect[];
  created_at?: string;
};

/**
 * The kinds that are a render of the asset rather than the asset.
 *
 * They are told apart elsewhere because Publish asks for `blurred` by name to
 * know a face was dealt with, and "Remove effects" deletes `edited` — which is
 * exactly why a captioned cut is its own kind and not filed as that one. For
 * *watching* the result none of that matters: each is a render of this asset,
 * so the previewer takes them together and names the result by what made it.
 */
const RENDERED_KINDS = new Set(["blurred", "edited", "captioned"]);

/**
 * The newest render of an asset, whatever effects made it.
 *
 * Newest rather than ranked by kind: somebody who has just re-rendered wants
 * to watch what they just made, and putting last week's blur ahead of this
 * morning's edit would show them the wrong file with no way to say so.
 */
type Translate = (path: string, values?: Record<string, string | number>) => string;

/** Every effect in a cut, as tags, in the order applied and said once each. */
function cutEffects(t: Translate, version: Version): string[] {
  const names = (version.effects ?? []).map((effect) =>
    effectTag(t, effect.id, effect.label),
  );
  // Every effect keeps its own name. Privacy remains a separate filter facet,
  // while cards describe the actual recipe rather than collapsing overlays and
  // blur into one privileged tag. The chip name rather than the editor's
  // imperative label, because a card has about twenty characters of room.
  return [...new Set(names)];
}

/**
 * What to call the rendered cut on a two-option switch.
 *
 * Named after what is actually in it, because "Edited" tells somebody nothing
 * about the file they are about to watch, and this used to be able to say only
 * "Faces blurred" — which was true when blurring was the only thing that could
 * produce a cut, and became a lie the moment a stack could hold a crop and a
 * sticker too.
 *
 * Beyond two effects it counts rather than lists: the button sits under the
 * player and a full recipe would be wider than the video. The whole stack is
 * one hover away, and the editor shows it in order.
 */
function cutLabel(t: Translate, version: Version): string {
  const names = cutEffects(t, version);
  if (names.length) {
    return names.length <= 2
      ? names.join(" + ")
      : t("library.cutEffectCount", { count: names.length });
  }
  // Rendered before the recipe was recorded on the version, so what made it is
  // genuinely unknown. Its *kind* is not: everything that has ever produced a
  // `blurred` cut covered a face, so saying that much is accurate where naming
  // an effect would be a guess.
  if (version.kind === "captioned") return "Captions burned in";
  return version.kind === "blurred"
    ? t("library.cutFacesCovered")
    : t("library.cutEdited");
}

function renderedCut(versions: Version[]): Version | null {
  const rendered = versions.filter((version) => RENDERED_KINDS.has(version.kind));
  if (!rendered.length) return null;
  return rendered.reduce((newest, version) =>
    (version.created_at ?? "") >= (newest.created_at ?? "") ? version : newest);
}

function assetIdForEffectJob(job: BaseJob): string | null {
  return job.raw?.payload?.asset_id ?? job.raw?.result?.asset_id ?? null;
}

function isEffectPreviewJob(job: BaseJob): boolean {
  return Boolean(job.raw?.payload?.request?.preview_seconds);
}

/** An entry that records a removal rather than a render. */
function isEffectRemovalJob(job: BaseJob): boolean {
  return job.raw?.payload?.action === "discard";
}

/**
 * How long ago, in the coarsest unit that is still true.
 *
 * A wall-clock time answers "when" and the question here is "how recently" —
 * three entries reading 14:02, 14:03 and 14:31 take a subtraction to tell you
 * what "3m ago, 4m ago, 32m ago" says at a glance. Past a day it flips to a
 * date, because "9d ago" is the point where counting stops helping.
 */
function timeAgo(when: string | null | undefined, now: number): string {
  if (!when) return "";
  const then = new Date(when).getTime();
  if (!Number.isFinite(then)) return "";
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 45) return "just now";
  if (seconds < 90) return "1m ago";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days <= 7) return `${days}d ago`;
  return new Date(when).toLocaleDateString();
}

type ThumbnailEffectActivity = {
  label: string;
  detail: string;
  progress: number | null;
  /** Nothing is working on it: hold the bar still rather than animating it. */
  stalled: boolean;
};

/** The single most useful active render state to show on an asset card. */
function thumbnailEffectActivity(
  t: Translate,
  jobs: BaseJob[],
  assetId: string,
  cancellingJobId = "",
): ThumbnailEffectActivity | null {
  const active = jobs.filter((job) =>
    job.category === "edit"
    && !isEffectPreviewJob(job)
    && assetIdForEffectJob(job) === assetId
    && ["queued", "running"].includes(job.status),
  );
  const job = active.find((candidate) => candidate.status === "running") ?? active[0];
  if (!job) return null;

  const effectNames = ((job.raw?.payload?.effects ?? []) as string[])
    .map((id) => effectLabel(t, id, id));
  const stopping = job.id === cancellingJobId;
  const progress = typeof job.progress === "number"
    ? Math.max(0, Math.min(1, job.progress))
    : null;

  return {
    label: stopping
      ? "Stopping"
      : job.status === "queued"
        ? "Queued"
        // Before the attempt count, because a stalled second attempt is paused
        // rather than resuming — "Resuming" on a job nobody is working on is
        // the exact reading that had an operator waiting on a frozen bar.
        : job.stalled
          ? "Paused"
          : Number(job.raw?.attempt_count ?? 0) > 1
            ? "Resuming"
            : "Applying",
    detail: effectNames.join(" + ") || "Effect stack",
    progress,
    stalled: Boolean(job.stalled),
  };
}

/** How many entries the inline panel keeps. The rest are one click away. */
const ACTIVITY_INLINE_LIMIT = 3;

function EffectActivityItem({
  job,
  now,
  cancellingJobId,
  onCancel,
}: {
  job: BaseJob;
  now: number;
  cancellingJobId: string;
  onCancel: (job: BaseJob) => void;
}) {
  const t = useT();
  const removal = isEffectRemovalJob(job);
  const working = ["queued", "running"].includes(job.status);

  const status = (() => {
    if (job.status === "queued") return { label: "Waiting", icon: LoaderCircle, tone: "working" };
    // Checked before "running": the row still says running because the worker
    // that would have said otherwise is the one that went away.
    if (job.stalled) return { label: "Paused", icon: CirclePause, tone: "muted" };
    if (job.status === "running") return {
      label: Number(job.raw?.attempt_count ?? 0) > 1 ? "Resuming" : "Applying",
      icon: LoaderCircle,
      tone: "working",
    };
    if (removal) return { label: "Removed", icon: Undo2, tone: "muted" };
    if (job.status === "succeeded") return { label: "Applied", icon: CircleCheck, tone: "done" };
    if (job.status === "cancelled") return { label: "Cancelled", icon: CircleX, tone: "muted" };
    return { label: "Needs attention", icon: CircleAlert, tone: "failed" };
  })();
  const StatusIcon = status.icon;

  const effectNames = ((job.raw?.payload?.effects ?? []) as string[])
    .map((id) => effectLabel(t, id, id));
  const cuts = Number(job.raw?.result?.removed_versions ?? 0);
  const heading = removal
    ? `Effects removed${cuts ? ` · ${cuts} cut${cuts === 1 ? "" : "s"}` : ""}`
    : effectNames.join(" + ") || "Effect stack";
  const batch = job.raw?.payload?.batch;
  const progress = typeof job.progress === "number"
    ? Math.max(0, Math.min(1, job.progress))
    : null;
  const when = timeAgo(job.raw?.completed_at ?? job.created_at, now);

  return (
    <article className={`effect-activity-item ${status.tone}`}>
      <div className="effect-activity-item-main">
        <StatusIcon
          className={job.status === "running" && !job.stalled ? "is-spinning" : ""}
          size={16}
          aria-hidden="true"
        />
        <div>
          <strong>{heading}</strong>
          <small>
            {status.label}
            {!removal && job.progressStage ? ` · ${job.progressStage}` : ""}
            {batch?.total > 1 ? ` · Batch item ${batch.position} of ${batch.total}` : ""}
          </small>
          {/* Where it stopped and what happens next, because "Paused" alone
              leaves somebody watching a bar that will not move. */}
          {job.stalled && (
            <small className="effect-activity-note">
              Stopped at {progress === null ? "an unknown point" : `${Math.round(progress * 100)}%`}.
              It resumes on its own once a worker is running.
            </small>
          )}
        </div>
        {/* Its own slot rather than the end of the status line, which is
            clamped to one line: appended there, the time was the first thing
            an ellipsis ate. Coarse and relative, because the question a log
            answers is how recently rather than at what o'clock — the exact
            time stays on the element for anyone who wants it. */}
        {when && (
          <time
            className="effect-activity-when"
            dateTime={job.raw?.completed_at ?? job.created_at}
            title={new Date(job.raw?.completed_at ?? job.created_at).toLocaleString()}
          >{when}</time>
        )}
        {working && (
          <Button
            variant="quiet"
            size="sm"
            busy={cancellingJobId === job.id}
            onClick={() => onCancel(job)}
          >Cancel</Button>
        )}
      </div>
      {working && (
        <div
          className={`effect-activity-progress${
            progress === null && !job.stalled ? " indeterminate" : ""
          }${job.stalled ? " stalled" : ""}`}
          role="progressbar"
          aria-label={`${heading} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress === null ? undefined : Math.round(progress * 100)}
        >
          <span style={progress === null ? undefined : { width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}
      {job.error && <p role="alert">{job.error}</p>}
    </article>
  );
}

function EffectActivity({
  assetId,
  jobs,
  cancellingJobId,
  onCancel,
  onClearHistory,
  clearing,
}: {
  assetId: string;
  jobs: BaseJob[];
  cancellingJobId: string;
  onCancel: (job: BaseJob) => void;
  onClearHistory: () => void;
  clearing: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  // Ticks only while there is a panel to update, and once a minute because
  // that is the finest unit the wording uses.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  const matching = jobs.filter((job) =>
    job.category === "edit"
    && !isEffectPreviewJob(job)
    && assetIdForEffectJob(job) === assetId,
  );
  const active = matching.filter((job) => ["queued", "running"].includes(job.status));
  const settled = matching.filter((job) => !["queued", "running"].includes(job.status));
  // Everything in flight, always: that is operational state and carries the
  // Cancel button. History fills whatever room is left, so a quiet asset shows
  // three entries and a busy one is not truncated to hide its own renders.
  const visible = [
    ...active,
    ...settled.slice(0, Math.max(0, ACTIVITY_INLINE_LIMIT - active.length)),
  ];
  if (!matching.length) return null;
  const hidden = matching.length - visible.length;

  return (
    <section className="effect-activity" aria-labelledby={`effect-activity-${assetId}`}>
      <header>
        <span className="effect-activity-heading">
          <Layers3 size={15} aria-hidden="true" />
          <strong id={`effect-activity-${assetId}`}>Effect activity</strong>
        </span>
        <small>{active.length ? `${active.length} active` : "Recent"}</small>
      </header>
      <div className="effect-activity-list" aria-live="polite">
        {visible.map((job) => (
          <EffectActivityItem
            key={job.id}
            job={job}
            now={now}
            cancellingJobId={cancellingJobId}
            onCancel={onCancel}
          />
        ))}
      </div>
      {/* Offered whenever there is a history to act on, not only when it
          overflows: clearing three old entries is the same wish as clearing
          thirty, and hiding the control until the fourth is arbitrary. */}
      {Boolean(settled.length) && (
        <footer className="effect-activity-foot">
          <Button variant="link" size="sm" onClick={() => setShowAll(true)}>
            {hidden > 0 ? `View all ${matching.length}` : "View history"}
          </Button>
        </footer>
      )}

      <Dialog
        open={showAll}
        title="Effect activity"
        description="Everything this clip's effects have done, newest first."
        onClose={() => setShowAll(false)}
        footer={
          <Button
            variant="quiet"
            size="sm"
            busy={clearing}
            disabled={!settled.length}
            onClick={onClearHistory}
          >Clear history</Button>
        }
      >
        <div className="effect-activity-list effect-activity-history">
          {matching.map((job) => (
            <EffectActivityItem
              key={job.id}
              job={job}
              now={now}
              cancellingJobId={cancellingJobId}
              onCancel={onCancel}
            />
          ))}
        </div>
        {/* Said in the dialog where the button is, rather than in a confirm
            nobody reads: the distinction that matters is that this deletes the
            log and not the renders. */}
        <p className="effect-activity-note">
          Clearing removes these entries only. The rendered cuts, their recipes and
          the files on disk are untouched, and anything still running keeps going.
        </p>
      </Dialog>
    </section>
  );
}
type Transcript = { id: string; kind: "speech" | "ocr"; language: string; text: string };
type Analysis = {
  version: number;
  spoken_hook?: string | null;
  text_hook?: string | null;
  call_to_action?: string | null;
  product_shown?: string | null;
  creative_format?: string | null;
  structure_tags: string[];
  shot_count?: number | null;
  average_shot_ms?: number | null;
  product_reveal_ms?: number | null;
  keywords: string[];
  analyst_notes?: string | null;
};
type Asset = {
  id: string;
  title: string;
  media_kind: "video" | "audio" | "image";
  source_type: string;
  source_url?: string | null;
  source_urls?: string[];
  platform?: string | null;
  creator?: string | null;
  published_at?: string | null;
  caption?: string | null;
  hashtags: string[];
  original_path: string;
  size_bytes: number;
  duration_ms?: number | null;
  width?: number | null;
  height?: number | null;
  has_audio: boolean;
  collected_at: string;
  versions: Version[];
  transcripts: Transcript[];
  analysis?: Analysis | null;
};
type Job = {
  id?: string | null;
  status: string;
  asset_id?: string;
  payload?: { title?: string; source_path?: string };
  error?: string | null;
};
type Status = {
  runtime: { ffmpeg: boolean; ffprobe: boolean; local_derivatives: boolean };
  transcription: {
    reviewed_import: boolean;
    automatic_provider: string | null;
    prepared: boolean;
    active: boolean;
    reason: string;
  };
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Media library request failed.");
  return body;
}

function displaySize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function displayDuration(milliseconds?: number | null): string {
  if (!milliseconds) return "Still image";
  const totalSeconds = Math.round(milliseconds / 1000);
  return `${Math.floor(totalSeconds / 60)}:${String(totalSeconds % 60).padStart(2, "0")}`;
}

function douyinChannelUrl(asset: Asset): string | null {
  if (asset.platform !== "douyin") return null;
  return (asset.source_urls ?? []).find((url) => {
    try {
      const parsed = new URL(url);
      return (parsed.hostname === "douyin.com" || parsed.hostname.endsWith(".douyin.com"))
        && parsed.pathname.startsWith("/user/");
    } catch {
      return false;
    }
  }) ?? null;
}

function DouyinMark() {
  const path = "M14.2 3v10.1a4.4 4.4 0 1 1-3.3-4.26v3.06a1.75 1.75 0 1 0 .75 1.44V3h2.55Zm0 0c.38 2.62 1.95 4.2 4.8 4.68v2.77a7.4 7.4 0 0 1-4.8-1.72V3Z";
  return (
    <svg className="douyin-mark" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="douyin-mark-cyan" d={path} />
      <path className="douyin-mark-pink" d={path} />
      <path className="douyin-mark-core" d={path} />
    </svg>
  );
}

function Thumbnail({
  asset,
  workspaceId,
  apiFetch,
  effectActivity,
}: {
  asset: Asset;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  effectActivity?: ThumbnailEffectActivity | null;
}) {
  const [source, setSource] = useState("");
  const hasThumbnail = asset.versions.some((version) => version.kind === "thumbnail");

  useEffect(() => {
    if (!hasThumbnail) return;
    let active = true;
    let objectUrl = "";
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/content/thumbnail`)
      .then((response) => {
        if (!response.ok) throw new Error("Preview unavailable");
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (active) setSource(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, asset.id, hasThumbnail, workspaceId]);

  return (
    <div className="library-thumbnail-frame">
      {source ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL */}
          <img className="library-thumbnail" src={source} alt={`${asset.title} thumbnail`} loading="lazy" />
        </>
      ) : <div className="library-thumbnail library-thumbnail-empty">{asset.media_kind}</div>}
      {effectActivity ? (
        <span
          className={`library-effect-processing${effectActivity.stalled ? " stalled" : ""}`}
          aria-label={`${effectActivity.label}: ${effectActivity.detail}`}
          title={effectActivity.stalled
            ? `${effectActivity.detail} — paused. Nothing is working on this; it resumes when the worker is back.`
            : `${effectActivity.label}: ${effectActivity.detail}`}
        >
          <span className="library-effect-processing-label">
            {/* A spinner on a job nobody is working on is the animation that
                made a ten-hour-dead render look alive. */}
            {effectActivity.stalled
              ? <CirclePause size={15} aria-hidden="true" />
              : <LoaderCircle className="is-spinning" size={15} aria-hidden="true" />}
            <strong>{effectActivity.label}</strong>
            {effectActivity.progress !== null && <small>{Math.round(effectActivity.progress * 100)}%</small>}
          </span>
          <span
            className={`library-effect-processing-progress ${
              effectActivity.progress === null && !effectActivity.stalled ? "indeterminate" : ""
            }`}
            aria-hidden="true"
          >
            <span style={effectActivity.progress === null ? undefined : { width: `${Math.round(effectActivity.progress * 100)}%` }} />
          </span>
        </span>
      ) : (
        <>
          {asset.media_kind === "video" && <span className="library-play-indicator" aria-hidden="true">▶</span>}
          {asset.media_kind === "video" && <span className="library-duration-badge">{displayDuration(asset.duration_ms)}</span>}
        </>
      )}
    </div>
  );
}

function previewBlob(contentBase64: string, mimeType: string): Blob {
  const binary = window.atob(contentBase64);
  const chunks: ArrayBuffer[] = [];
  for (let offset = 0; offset < binary.length; offset += 8192) {
    const slice = binary.slice(offset, offset + 8192);
    const bytes = Uint8Array.from(slice, (character) => character.charCodeAt(0));
    chunks.push(bytes.buffer as ArrayBuffer);
  }
  return new Blob(chunks, { type: mimeType });
}

function MediaPreview({
  asset,
  workspaceId,
  apiFetch,
  previewPosition,
  previewTotal,
  hasPreviousVideo,
  hasNextVideo,
  autoStart,
  onPlaybackChange,
  onPreviousVideo,
  onNextVideo,
}: {
  asset: Asset;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  previewPosition: number;
  previewTotal: number;
  hasPreviousVideo: boolean;
  hasNextVideo: boolean;
  autoStart: boolean;
  onPlaybackChange: (playing: boolean) => void;
  onPreviousVideo: () => void;
  onNextVideo: () => void;
}) {
  const t = useT();
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  /**
   * Whether the media has been asked for.
   *
   * The gate exists so a video is not fetched until somebody wants it: they are
   * large, and one starts playing the moment it arrives. An image is neither -
   * it is small, it does nothing on arrival, and it is the thing being reviewed,
   * so asking for a click before showing it is a step that buys nothing.
   *
   * Safe to read at mount because the preview is keyed by asset, so selecting a
   * different one remounts this and asks the question again.
   */
  const [requested, setRequested] = useState(autoStart || asset.media_kind === "image");
  // The rendered cut is watched in the same player as the original, so the two
  // are compared in place rather than in a second, smaller video somewhere else.
  //
  // It also opens on the rendered cut when there is one - see `openingCut`,
  // which is the same rule Publish uses to pick the file it sends. Safe at
  // mount for the same reason `requested` is: the preview is keyed by asset,
  // so selecting another one asks the question again.
  const [cut, setCut] = useState<"original" | "edited">(() => openingCut(asset));
  // Both <video> and <audio> are HTMLMediaElement, which is the whole
  // transport surface used here: play, pause and paused.
  const videoRef = useRef<HTMLMediaElement>(null);
  const navigatingRef = useRef(false);
  const rendered = renderedCut(asset.versions);

  // Audio and video both have a transport; an image has nothing to play.
  const playable = asset.media_kind === "video" || asset.media_kind === "audio";

  useEffect(() => {
    if (!requested) return;
    let active = true;
    let objectUrl = "";
    const controller = new AbortController();
    const wanted = cut === "edited" && rendered ? "edited" : "original";
    apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/preview?cut=${wanted}`,
      { method: "POST", signal: controller.signal },
    )
      .then((response) => json<{ mime_type: string; content_base64: string }>(response))
      .then((preview) => {
        objectUrl = URL.createObjectURL(
          previewBlob(preview.content_base64, preview.mime_type),
        );
        return objectUrl;
      })
      .then((url) => { if (active) setSource(url); })
      .catch((reason) => {
        if (active && reason instanceof DOMException && reason.name === "AbortError") return;
        if (active) setError(reason instanceof Error ? reason.message : t("library.previewUnavailable"));
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, asset.id, rendered, cut, requested, t, workspaceId]);

  function startPlayback() {
    setError("");
    onPlaybackChange(true);
    setRequested(true);
  }

  function navigateVideo(action: () => void) {
    const video = videoRef.current;
    const shouldContinue = requested && (!video || !video.paused);
    navigatingRef.current = true;
    onPlaybackChange(shouldContinue);
    action();
  }

  function togglePlayback() {
    const video = videoRef.current;
    if (!requested || !video) {
      startPlayback();
      return;
    }
    if (video.paused) {
      onPlaybackChange(true);
      void video.play();
    } else {
      onPlaybackChange(false);
      video.pause();
    }
  }

  useEffect(() => {
    function navigateWithKeyboard(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.key === "ArrowLeft" && hasPreviousVideo) {
        event.preventDefault();
        navigateVideo(onPreviousVideo);
      }
      if (event.key === "ArrowRight" && hasNextVideo) {
        event.preventDefault();
        navigateVideo(onNextVideo);
      }
      if (event.code === "Space" && playable) {
        event.preventDefault();
        togglePlayback();
      }
    }
    window.addEventListener("keydown", navigateWithKeyboard);
    return () => window.removeEventListener("keydown", navigateWithKeyboard);
  });

  return (
    <article className="library-preview-card">
      <div className="library-preview-stage">
        {!requested ? (
          <button type="button" className="library-preview-launch" onClick={startPlayback}>
            <Thumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
            {/* Only video and audio reach this now: an image is shown on
                arrival, and those are the only three kinds there are. */}
            <span className="library-preview-launch-overlay">
              <span className="library-preview-launch-icon" aria-hidden="true">&#9654;</span>
              <strong>{t("library.playPreview")}</strong>
              <small>{t("library.privatePreview")}</small>
            </span>
          </button>
        ) : source ? (
          asset.media_kind === "image" ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img className="library-preview-image" src={source} alt={asset.title} />
          ) : asset.media_kind === "audio" ? (
            // Written out rather than built with createElement so the ref is a
            // real JSX ref: React forbids reading one out of a props object
            // during render, and the two tags share nothing but their props.
            <audio
              ref={videoRef as React.RefObject<HTMLAudioElement>}
              className="library-preview-audio"
              aria-label={asset.title}
              controls
              autoPlay
              preload="metadata"
              src={source}
              onPlay={() => onPlaybackChange(true)}
              onPause={() => { if (!navigatingRef.current) onPlaybackChange(false); }}
              onEnded={() => onPlaybackChange(false)}
            />
          ) : (
            <video
              ref={videoRef as React.RefObject<HTMLVideoElement>}
              aria-label={asset.title}
              controls
              autoPlay
              playsInline
              preload="metadata"
              src={source}
              onPlay={() => onPlaybackChange(true)}
              onPause={() => { if (!navigatingRef.current) onPlaybackChange(false); }}
              onEnded={() => onPlaybackChange(false)}
            />
          )
        ) : <p>{error || t("library.loadingPreview")}</p>}
      </div>
      {rendered && (
        /* Two cuts, because there are two things worth comparing: what came in
           and what the effects made of it. A render is the whole stack in one
           file, so a third option per effect would be offering cuts that do not
           exist. */
        <div className="library-cut-switch" role="group" aria-label={t("library.whichCut")}>
          {/* The render leads, because it is what the player opens on and what
              the operator came to look at. The original is the comparison, and
              a comparison reads better as the thing you switch back to. */}
          <button
            type="button"
            className={cut === "edited" ? "selected" : ""}
            aria-pressed={cut === "edited"}
            /* The full stack in the tooltip, the short form on the button:
               four effects would otherwise make a control wider than the
               player it sits under. */
            title={cutEffects(t, rendered).join(" → ") || undefined}
            onClick={() => { setError(""); setSource(""); setCut("edited"); setRequested(true); }}
          >{cutLabel(t, rendered)}</button>
          <button
            type="button"
            className={cut === "original" ? "selected" : ""}
            aria-pressed={cut === "original"}
            onClick={() => { setError(""); setSource(""); setCut("original"); setRequested(true); }}
          >{t("library.cutOriginal")}</button>
        </div>
      )}
      <nav className="library-preview-navigation" aria-label={t("library.browsePreviews")}>
        <button type="button" disabled={!hasPreviousVideo} onClick={() => navigateVideo(onPreviousVideo)} aria-label={t("library.previousVideo")} title={t("library.previousVideoKey")}>
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m12.5 4.5-5.5 5.5 5.5 5.5" /></svg>
        </button>
        <span>
          {t("library.previewPosition", { position: previewPosition, total: previewTotal })}
          <small>{playable ? t("library.keyboardHint") : t("library.arrowHint")}</small>
        </span>
        <button type="button" disabled={!hasNextVideo} onClick={() => navigateVideo(onNextVideo)} aria-label={t("library.nextVideo")} title={t("library.nextVideoKey")}>
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7.5 4.5 5.5 5.5-5.5 5.5" /></svg>
        </button>
      </nav>
    </article>
  );
}
type BulkAction = {
  id: string;
  label: string;
  verb: string;
  description: string;
  media_kinds: string[];
  max_batch: number;
  available: boolean;
  reason: string | null;
};

const isSortOrder = oneOf("newest", "oldest", "title", "duration");
const isGroupBy = oneOf("none", "channel", "source");
const isViewMode = oneOf("gallery", "list");
export default function LibraryPage() {
  const t = useT();
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  // One object rather than four separate states, so the list, the select-all
  // and the Publish picker all describe a filter the same way.
  const [filters, setFilters] = useState<AssetFilterValues>({});
  const query = filters.query ?? "";
  const mediaKind = filters.mediaKind ?? "";
  const patchFilters = (next: Partial<AssetFilterValues>) =>
    setFilters((current) => ({ ...current, ...next }));
  const [sortOrder, setSortOrder] = usePersistedState(
    "trendrelay.library.sort", "newest", isSortOrder,
  );
  const [groupBy, setGroupBy] = usePersistedState<GroupBy>(
    "trendrelay.library.groupBy", "none", isGroupBy,
  );
  const [facets, setFacets] = useState<AssetFacets>(EMPTY_FACETS);
  const [total, setTotal] = useState(0);
  const [loadingAssets, setLoadingAssets] = useState(false);
  // Background syncs, filter changes and job completion can all request a
  // refresh. They must all read the latest selection, and an older response
  // must never put an unfiltered list back after a newer filtered one arrived.
  const latestFilters = useRef(filters);
  const latestSortOrder = useRef(sortOrder);
  const latestWorkspaceId = useRef(workspaceId);
  const refreshSequence = useRef(0);
  const [viewMode, setViewMode] = usePersistedState<ViewMode>(
    "trendrelay.library.view", "gallery", isViewMode,
  );
  const [continueVideoPlayback, setContinueVideoPlayback] = useState(false);
  const autoSyncedWorkspaces = useRef(new Set<string>());
  const [busy, setBusy] = useState("");
  // Errors are reported over the page: in flow they shifted everything below
  // them whenever an action finished. The bulk-action outcome below is not a
  // banner — it reads back inline where the run was started — so it stays put.
  const { messages: statusMessages, fail, dismiss } = useStatus();
  const {
    jobs: notificationJobs,
    refresh: refreshJobs,
    setActiveWorkspaceId,
  } = useJobs();
  const previousEffectJobStates = useRef<Map<string, string>>(new Map());
  const [message, setMessage] = useState("");
  const [selection, setSelection] = useState<Set<string>>(new Set());
  /** Anchor for shift-click range selection. */
  const [lastPicked, setLastPicked] = useState<string | null>(null);
  const [bulkActions, setBulkActions] = useState<BulkAction[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [captionsOpen, setCaptionsOpen] = useState(false);
  const [effectsOpen, setEffectsOpen] = useState(false);
  const [batchEffectsOpen, setBatchEffectsOpen] = useState(false);
  const [cancellingEffectJobId, setCancellingEffectJobId] = useState("");
  const [clearingHistory, setClearingHistory] = useState(false);

  const selected = assets.find((asset) => asset.id === selectedId);
  const selectedSourceLinks = selected
    ? selected.platform === "douyin" && selected.source_url
      ? [selected.source_url]
      : selected.source_urls?.length
        ? selected.source_urls
        : selected.source_url
          ? [selected.source_url]
          : []
    : [];
  const selectedChannelUrl = selected ? douyinChannelUrl(selected) : null;
  const selectedIndex = assets.findIndex((asset) => asset.id === selectedId);
  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canImport = ["owner", "editor", "approver"].includes(workspace?.role ?? "");
  // Approving a plan commits render spend, so it is the narrower pair.
  const canApprove = ["owner", "approver"].includes(workspace?.role ?? "");
  const canEnrich = ["owner", "editor", "analyst"].includes(workspace?.role ?? "");
  const filterCount = activeFilterCount(filters);
  const mediaTotal = facets.media_kinds.reduce((sum, facet) => sum + facet.count, 0);
  const mediaCount = (kind: Asset["media_kind"]) => facets.media_kinds.find((facet) => facet.value === kind)?.count ?? 0;
  const groupedAssets = groupBy === "none"
    ? []
    : Array.from(assets.reduce((groups, asset) => {
      const label = groupBy === "channel"
        ? asset.creator || "Unassigned channel"
        : asset.platform || asset.source_type || "Other sources";
      const items = groups.get(label) ?? [];
      items.push(asset);
      groups.set(label, items);
      return groups;
    }, new Map<string, Asset[]>())).sort((left, right) => right[1].length - left[1].length || left[0].localeCompare(right[0]));

  /** The filter the list is showing, so a select-all can ask for the same set. */
  const filterParams = useCallback(() => assetFilterParams(filters), [filters]);

  useEffect(() => { latestFilters.current = filters; }, [filters]);
  useEffect(() => { latestSortOrder.current = sortOrder; }, [sortOrder]);
  useEffect(() => { latestWorkspaceId.current = workspaceId; }, [workspaceId]);
  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [setActiveWorkspaceId, workspaceId]);

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    if (nextWorkspace !== latestWorkspaceId.current) return;
    // A delayed auto-sync may hold a callback created before the user selected
    // Images or an effect. Reading refs here makes even that delayed refresh
    // use what the controls show now, rather than silently restoring "All".
    const params = assetFilterParams(latestFilters.current);
    params.set("sort", latestSortOrder.current);
    params.set("limit", "100");
    const suffix = `?${params}`;
    const sequence = ++refreshSequence.current;
    setLoadingAssets(true);
    try {
      const [assetBody, jobBody, statusBody] = await Promise.all([
        json<{ assets: Asset[]; total?: number; facets?: AssetFacets }>(
          await apiFetch(`/api/workspaces/${nextWorkspace}/media/library/assets${suffix}`),
        ),
        json<{ jobs: Job[] }>(
          await apiFetch(`/api/workspaces/${nextWorkspace}/media/library/jobs`),
        ),
        json<Status>(
          await apiFetch(`/api/workspaces/${nextWorkspace}/media/library/status`),
        ),
      ]);
      if (
        sequence !== refreshSequence.current
        || nextWorkspace !== latestWorkspaceId.current
      ) return;
      setAssets(assetBody.assets);
      setTotal(assetBody.total ?? assetBody.assets.length);
      if (assetBody.facets) setFacets(assetBody.facets);
      setJobs(jobBody.jobs);
      setStatus(statusBody);
      setSelectedId((current) =>
        assetBody.assets.some((asset) => asset.id === current)
          ? current
          : (assetBody.assets[0]?.id ?? ""),
      );
    } finally {
      if (sequence === refreshSequence.current) setLoadingAssets(false);
    }
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    const effectJobs = notificationJobs.filter((job) => job.category === "edit");
    const previous = previousEffectJobStates.current;
    const settledNow = effectJobs.some((job) =>
      ["succeeded", "failed", "cancelled"].includes(job.status)
      && ["queued", "running"].includes(previous.get(job.id) ?? ""),
    );
    previousEffectJobStates.current = new Map(
      effectJobs.map((job) => [job.id, job.status]),
    );
    // The notification announces completion; refresh the same screen at that
    // moment so its new cut, exact tags, and effect facet appear without a
    // manual reload. Failed and cancelled jobs refresh too, clearing stale UI.
    if (settledNow) void refresh();
  }, [notificationJobs, refresh]);

  function clearFilters() {
    setFilters({});
  }

  function groupTotal(label: string, loadedCount: number) {
    const source = groupBy === "channel" ? facets.channels : facets.platforms;
    return source.find((facet) => facet.label === label)?.count ?? loadedCount;
  }

  /** Ids the batch tools will act on. Kept apart from `selectedId`, which is
      the one asset being previewed - browsing and selecting are different jobs. */
  // Scoped to what is on screen, so an id left over from another filter can
  // never be acted on - and comes back if that filter is restored.
  const selectionList = assets.filter((asset) => selection.has(asset.id));
  const allLoadedSelected = assets.length > 0 && selectionList.length === assets.length;
  const deleteAction = bulkActions.find((action) => action.id === "delete");
  // Face blur used to be a separate bulk tool. It now lives in the same
  // stackable editor as every other effect; keeping both buttons would restore
  // the special tier this workflow removes.
  const visibleBulkActions = bulkActions.filter((action) => action.id !== "face_blur");

  function toggleSelection(assetId: string, extend: boolean) {
    const next = new Set(selection);
    if (extend && lastPicked) {
      // Shift-click fills the run between the anchor and here, which is the
      // only bearable way to choose forty items out of two thousand.
      const from = assets.findIndex((asset) => asset.id === lastPicked);
      const to = assets.findIndex((asset) => asset.id === assetId);
      if (from >= 0 && to >= 0) {
        const [start, end] = from < to ? [from, to] : [to, from];
        for (const asset of assets.slice(start, end + 1)) next.add(asset.id);
        setSelection(next);
        return;
      }
    }
    if (next.has(assetId)) next.delete(assetId);
    else next.add(assetId);
    setSelection(next);
    setLastPicked(assetId);
  }

  /** Select everything the filter matches, which is usually more than is loaded. */
  async function selectAllMatching() {
    if (!workspaceId) return;
    setBusy("select-all");
    fail("");
    try {
      const body = await json<{ asset_ids: string[]; matched: number; truncated: boolean }>(
        await apiFetch(
          `/api/workspaces/${workspaceId}/media/library/assets/ids?${filterParams()}`,
        ),
      );
      setSelection(new Set(body.asset_ids));
      setMessage(
        body.truncated
          ? `Selected the first ${body.asset_ids.length.toLocaleString()} of ${body.matched.toLocaleString()} matches. Narrow the filter to reach the rest.`
          : `Selected all ${body.asset_ids.length.toLocaleString()} matching items.`,
      );
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The selection could not be built.");
    } finally {
      setBusy("");
    }
  }

  async function runBulkAction(action: BulkAction, only?: string[]) {
    const ids = only ?? Array.from(selection);
    if (!ids.length) return;
    // A selection can be larger than one request allows, so it is sent in
    // batches rather than refused - the cap is the server's, not the operator's.
    const batches: string[][] = [];
    for (let at = 0; at < ids.length; at += action.max_batch) {
      batches.push(ids.slice(at, at + action.max_batch));
    }
    const subject = `${ids.length} item${ids.length === 1 ? "" : "s"}`;
    const prompt = action.id === "delete"
      ? `Delete ${subject} from the library? ${action.description}`
      : `${action.label} on ${subject}?`;
    if (!window.confirm(prompt)) return;
    setBusy(`bulk-${action.id}`);
    fail("");
    setMessage("");
    try {
      const totals = { queued: 0, skipped: 0, failed: 0, missing: 0 };
      for (const [index, batch] of batches.entries()) {
        if (batches.length > 1) {
          setMessage(`${action.verb}: batch ${index + 1} of ${batches.length}…`);
        }
        const response = await apiFetch(`/api/workspaces/${workspaceId}/media/library/bulk`, {
          method: "POST",
          body: JSON.stringify({
            action: action.id, asset_ids: batch, confirm_external_action: true,
          }),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? `${action.label} could not start.`);
        for (const key of ["queued", "skipped", "failed", "missing"] as const) {
          totals[key] += body.counts[key] ?? 0;
        }
      }
      const { queued, skipped, failed, missing } = totals;
      // Every outcome is reported: a bare "queued" would hide that a third of
      // the selection was skipped for already being done.
      const parts = [`${queued} queued`];
      if (skipped) parts.push(`${skipped} skipped`);
      if (failed) parts.push(`${failed} failed`);
      if (missing) parts.push(`${missing} missing`);
      setMessage(`${action.verb}: ${parts.join(" · ")}.`);
      if (queued) {
        setSelection(new Set());
        // Deleting changes the list itself, so it has to be read again.
        if (action.id === "delete") await refresh(workspaceId);
      }
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : `${action.label} could not start.`);
    } finally {
      setBusy("");
    }
  }

  function renderAsset(asset: Asset) {
    const effectActivity = thumbnailEffectActivity(
      t,
      notificationJobs,
      asset.id,
      cancellingEffectJobId,
    );
    return (
      <button className={`${selectedId === asset.id ? "selected" : ""}${renderedCut(asset.versions) ? " has-versions" : ""}${selection.has(asset.id) ? " picked" : ""}`} key={asset.id} aria-label={`Open ${asset.title}${effectActivity ? `. ${effectActivity.label}: ${effectActivity.detail}` : ""}`} aria-pressed={selectedId === asset.id} onClick={() => setSelectedId(asset.id)}>
        {/* A separate control, so selecting never hijacks opening a clip. */}
        <span
          className="library-pick"
          role="checkbox"
          tabIndex={0}
          aria-checked={selection.has(asset.id)}
          aria-label={`Select ${asset.title}`}
          onClick={(event) => { event.stopPropagation(); toggleSelection(asset.id, event.shiftKey); }}
          onKeyDown={(event) => {
            if (event.key !== " " && event.key !== "Enter") return;
            event.preventDefault();
            event.stopPropagation();
            toggleSelection(asset.id, event.shiftKey);
          }}
        >{selection.has(asset.id) && <Check size={12} strokeWidth={3.5} aria-hidden="true" />}</span>
        <Thumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} effectActivity={effectActivity} />
        <span>
          <strong>{asset.title}</strong>
          <small>{asset.creator ? `${asset.creator} · ` : ""}{asset.platform ?? asset.source_type} · {displayDuration(asset.duration_ms)} · {displaySize(asset.size_bytes)}</small>
          {/* Marked for any rendered cut, not only a blurred one. An asset with
              a crop and a sticker on it has been edited just as much, and the
              row was the only place that said so at a glance. */}
          {renderedCut(asset.versions) && (
            <span className="effect-tags" aria-label="Applied effects">
              {cutEffects(t, renderedCut(asset.versions)!).length
                ? cutEffects(t, renderedCut(asset.versions)!).map((name) => (
                    <em className="blurred-tag" key={name}>{name}</em>
                  ))
                : <em className="blurred-tag">{cutLabel(t, renderedCut(asset.versions)!)}</em>}
            </span>
          )}
        </span>
      </button>
    );
  }

  function chooseView(nextView: ViewMode) {
    setViewMode(nextView);
  }

  useEffect(() => {
    // Downloads links a rendered artifact here by path, and a notification
    // links one by id - a finished blur knows the asset it produced, not where
    // it came from. Either is accepted, so both callers can use one parameter.
    queueMicrotask(() => {
      const wanted = new URLSearchParams(window.location.search).get("asset");
      if (!wanted) return;
      const match = assets.find(
        (asset) => asset.original_path === wanted || asset.id === wanted,
      );
      if (!match) return;
      setSelectedId(match.id);
      window.history.replaceState({}, "", window.location.pathname);
    });
  }, [assets]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        if (cancelled) return;
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason) => fail(reason instanceof Error ? reason.message : "Workspaces unavailable."));
    return () => { cancelled = true; };
  }, [apiFetch, user, fail]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    // The tools come from the server registry rather than being listed here,
    // so adding one does not mean editing this page.
    apiFetch(`/api/workspaces/${workspaceId}/media/library/bulk-actions`)
      .then((response) => json<{ actions: BulkAction[] }>(response))
      .then((body) => { if (!cancelled) setBulkActions(body.actions); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    queueMicrotask(() => {
      void refresh(workspaceId).catch((reason) =>
        fail(reason instanceof Error ? reason.message : "Library unavailable."),
      );
    });
  }, [filters, refresh, sortOrder, workspaceId, fail]);

  useEffect(() => {
    if (!workspaceId || !canImport || autoSyncedWorkspaces.current.has(workspaceId)) return;
    autoSyncedWorkspaces.current.add(workspaceId);
    queueMicrotask(() => {
      void (async () => {
        try {
          const body = await json<{ sync: { queued: Job[]; errors: string[]; removed_asset_ids: string[] } }>(
            await apiFetch(`/api/workspaces/${workspaceId}/media/downloads/library-sync`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ confirm_external_action: true }),
            }),
          );
          const pendingCount = body.sync.queued.filter((job) => ["queued", "running"].includes(job.status)).length;
          if (pendingCount) {
            setMessage(`${pendingCount} downloaded media items are being prepared automatically.`);
          } else if (body.sync.removed_asset_ids.length) {
            setMessage(`${body.sync.removed_asset_ids.length} removed media items were cleared from Library.`);
          }
          if (body.sync.errors.length) {
            fail(`${body.sync.errors.length} downloaded media items could not be prepared.`);
          }
          await refresh(workspaceId);
        } catch (reason) {
          autoSyncedWorkspaces.current.delete(workspaceId);
          fail(reason instanceof Error ? reason.message : "Downloaded media could not be synchronized.");
        }
      })();
    });
  }, [apiFetch, canImport, refresh, workspaceId, fail]);

  useEffect(() => {
    if (!jobs.some((job) => ["queued", "running"].includes(job.status))) return;
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 2500);
    return () => window.clearInterval(timer);
  }, [jobs, refresh]);

  async function syncDownloads() {
    setBusy("sync");
    fail("");
    setMessage("");
    try {
      const body = await json<{ sync: { queued: Job[]; errors: string[]; removed_asset_ids: string[] } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/media/downloads/library-sync`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm_external_action: true }),
        }),
      );
      const queuedCount = body.sync.queued.filter((job) => ["queued", "running"].includes(job.status)).length;
      const removedCount = body.sync.removed_asset_ids.length;
      setMessage(queuedCount
        ? `${queuedCount} downloaded media items are being prepared for Library.`
        : removedCount
          ? `${removedCount} removed media items were cleared from Library.`
          : "Downloaded media is already up to date.");
      if (body.sync.errors.length) fail(`${body.sync.errors.length} media items could not be queued.`);
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Downloaded media could not be added.");
    } finally {
      setBusy("");
    }
  }

  async function importMedia(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("import");
    fail("");
    setMessage("");
    const form = new FormData(event.currentTarget);
    try {
      const body = await json<{ job: Job }>(
        await apiFetch(`/api/workspaces/${workspaceId}/media/library/imports`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: form.get("path"),
            title: form.get("title"),
            source_url: form.get("source_url") || null,
            platform: form.get("platform") || null,
            creator: form.get("creator") || null,
            published_at: form.get("published_at") || null,
            caption: form.get("caption") || null,
            engagement: Object.fromEntries(
              ["likes", "comments", "shares"]
                .map((name) => [name, Number(form.get(name))])
                .filter(([, value]) => Number.isFinite(value) && Number(value) >= 0),
            ),
            hashtags: String(form.get("hashtags") ?? "").split(",").map((item) => item.trim()).filter(Boolean),
            confirm_external_action: true,
          }),
        }),
      );
      setMessage(body.job.asset_id ? "That file is already safely stored." : "Import queued. Derivatives will appear automatically.");
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Import failed.");
    } finally {
      setBusy("");
    }
  }

  async function enrich(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy("enrich");
    fail("");
    const form = new FormData(event.currentTarget);
    try {
      const body = await json<{ asset: Asset }>(
        await apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${selected.id}/enrichment`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            language: form.get("language") || "und",
            speech_text: form.get("speech_text") || null,
            ocr_text: form.get("ocr_text") || null,
            product_shown: form.get("product_shown") || null,
            creative_format: form.get("creative_format") || null,
            emotional_angle: form.get("emotional_angle") || null,
            scene_boundaries_ms: String(form.get("scene_boundaries_ms") ?? "").split(",")
              .map((item) => Number(item.trim())).filter((item) => Number.isInteger(item) && item >= 0).sort((a, b) => a - b),
            product_reveal_ms: form.get("product_reveal_ms") ? Number(form.get("product_reveal_ms")) : null,
            analyst_notes: form.get("analyst_notes") || null,
          }),
        }),
      );
      setAssets((current) => current.map((asset) => asset.id === body.asset.id ? body.asset : asset));
      setMessage("Reviewed transcript and creative recipe saved.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Enrichment failed.");
    } finally {
      setBusy("");
    }
  }




  async function openAssetFolder(asset: Asset) {
    setBusy("folder");
    fail("");
    try {
      await json(await apiFetch("/api/tools/open-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: asset.original_path }),
      }));
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The media folder could not be opened.");
    } finally {
      setBusy("");
    }
  }

  async function cancelEffectJob(job: BaseJob) {
    setCancellingEffectJobId(job.id);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/effects/jobs/${job.id}/cancel`,
        { method: "POST" },
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The effect job could not be cancelled.");
      await refreshJobs();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The effect job could not be cancelled.");
    } finally {
      setCancellingEffectJobId("");
    }
  }

  async function clearEffectHistory(assetId: string) {
    setClearingHistory(true);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/effects/jobs/clear`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset_id: assetId }),
        },
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The activity could not be cleared.");
      await refreshJobs();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The activity could not be cleared.");
    } finally {
      setClearingHistory(false);
    }
  }

  async function removeEffects(asset: Asset) {
    if (!window.confirm(
      `Remove every applied effect from "${asset.title}"?\n\n`
      + "Rendered effect files and the saved recipe will be removed. The original media is preserved.",
    )) return;
    setBusy("discard-effects");
    fail("");
    try {
      const body = await json<{ removed_versions: number; cancelled_jobs?: number }>(
        await apiFetch(
          `/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/effects/discard`,
          { method: "POST" },
        ),
      );
      setEffectsOpen(false);
      await refresh();
      const cancelled = body.cancelled_jobs
        ? ` ${body.cancelled_jobs} active effect ${body.cancelled_jobs === 1 ? "job was" : "jobs were"} cancelled.`
        : "";
      setMessage(
        `${body.removed_versions} rendered ${body.removed_versions === 1 ? "cut was" : "cuts were"} removed. The original is unchanged.${cancelled}`,
      );
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "The applied effects could not be removed.");
    } finally {
      setBusy("");
    }
  }

  if (loading) return <main className="library-page"><p>{t("library.opening")}</p></main>;
  if (!user) return <main className="library-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Flibrary">{t("library.signInPrompt")}</Link></main>;

  return (
    <main className="library-page">
      <WorkspaceSectionNav area="library" />
      <div className="page-sticky-shell library-sticky-header">
        <header className="library-heading">
          <div>
            <p className="section-kicker">{t("library.eyebrow")}</p>
            <h1>
              Media Library
              <span className="library-status-dots">
                <span
                  className={`library-status-dot ${status?.runtime.local_derivatives ? "ready" : "setup"}`}
                  role="img"
                  tabIndex={0}
                  aria-label={`Media processing: ${status?.runtime.local_derivatives ? "ready" : "setup required"}`}
                >
                  {status?.runtime.local_derivatives
                    ? <CircleCheck size={14} aria-hidden="true" />
                    : <CircleAlert size={14} aria-hidden="true" />}
                  <span className="library-status-tooltip" aria-hidden="true">
                    Media processing: {status?.runtime.local_derivatives ? "ready" : "setup required"}
                  </span>
                </span>
                {/* Not a tooltip. This one has an answer the operator can act
                    on — download it, or switch it on — so it opens rather than
                    explaining why they cannot. Compact by design: a popover off
                    the heading, not a row of its own. */}
                <TranscriptionSwitch apiFetch={apiFetch} />
              </span>
            </h1>
            <p>{t("library.intro")}</p>
          </div>
          <label>{t("workspace.select")}
            <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
              {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
            </select>
          </label>
        </header>
      </div>

      <section className="library-layout" aria-busy={loadingAssets}>
        <aside className="library-browser">
          <div className="library-browser-toolbar">
          <form className="library-search" onSubmit={(event) => { event.preventDefault(); void refresh(); }}>
            <input aria-label={t("library.searchLabel")} value={query} onChange={(event) => patchFilters({ query: event.target.value })} placeholder={t("library.searchPlaceholder")} />
            <Button type="submit"><ActionIcon name="search" />{t("common.search")}</Button>
          </form>

          <nav className="library-category-bar" aria-label={t("library.categories")}>
            <div className="library-category-tabs">
              <button type="button" className={!mediaKind ? "selected" : ""} aria-pressed={!mediaKind} onClick={() => patchFilters({ mediaKind: "" })}>{t("common.all")} <span>{mediaTotal}</span></button>
              <button type="button" className={mediaKind === "video" ? "selected" : ""} aria-pressed={mediaKind === "video"} onClick={() => patchFilters({ mediaKind: "video" })}>{t("library.videos")} <span>{mediaCount("video")}</span></button>
              <button type="button" className={mediaKind === "image" ? "selected" : ""} aria-pressed={mediaKind === "image"} onClick={() => patchFilters({ mediaKind: "image" })}>{t("library.images")} <span>{mediaCount("image")}</span></button>
              <button type="button" className={mediaKind === "audio" ? "selected" : ""} aria-pressed={mediaKind === "audio"} onClick={() => patchFilters({ mediaKind: "audio" })}>{t("library.audio")} <span>{mediaCount("audio")}</span></button>
            </div>
            <label>{t("library.sortLabel")}
              <select aria-label={t("library.sortLabel")} value={sortOrder} onChange={(event) => { if (isSortOrder(event.target.value)) setSortOrder(event.target.value); }}>
                <option value="newest">{t("library.sortNewest")}</option>
                <option value="oldest">{t("library.sortOldest")}</option>
                <option value="title">{t("library.sortTitle")}</option>
                <option value="duration">{t("library.sortLongest")}</option>
              </select>
            </label>
          </nav>

          <AssetFilters
            values={filters}
            facets={facets}
            fields={["channel", "platform", "effect"]}
            onChange={setFilters}
          >
            <label>{t("library.group")}
              <select aria-label={t("library.groupLabel")} value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)}>
                <option value="none">{t("library.noGrouping")}</option>
                <option value="channel">{t("library.channel")}</option>
                <option value="source">{t("library.source")}</option>
              </select>
            </label>
          </AssetFilters>
          <div className="library-collection-toolbar">
            <strong aria-live="polite">
              {loadingAssets ? "Filtering…" : `${total} ${total === 1 ? "item" : "items"}`}
            </strong>
            <div className="library-collection-actions">
              {canImport && <Button variant="quiet" size="sm" busy={busy === "sync"} onClick={() => void syncDownloads()}><ActionIcon name="refresh" />{busy === "sync" ? "Refreshing" : "Refresh downloads"}</Button>}
              <div className="library-view-switcher" role="group" aria-label={t("library.viewLabel")}>
                <button type="button" className={viewMode === "gallery" ? "selected" : ""} aria-label={t("library.galleryView")} title={t("library.galleryView")} aria-pressed={viewMode === "gallery"} onClick={() => chooseView("gallery")}><ActionIcon name="grid" /></button>
                <button type="button" className={viewMode === "list" ? "selected" : ""} aria-label={t("library.listView")} title={t("library.listView")} aria-pressed={viewMode === "list"} onClick={() => chooseView("list")}><ActionIcon name="list" /></button>
              </div>
            </div>
          </div>
          </div>

          {assets.length > 0 && (
            <div className={`library-selection-bar${selection.size ? " active" : ""}`}>
              <span
                className="library-pick"
                role="checkbox"
                tabIndex={0}
                aria-checked={allLoadedSelected}
                aria-label={allLoadedSelected ? "Clear selection" : "Select all loaded"}
                onClick={() => setSelection(allLoadedSelected
                  ? new Set()
                  : new Set(assets.map((asset) => asset.id)))}
                onKeyDown={(event) => {
                  if (event.key !== " " && event.key !== "Enter") return;
                  event.preventDefault();
                  setSelection(allLoadedSelected
                    ? new Set()
                    : new Set(assets.map((asset) => asset.id)));
                }}
              >{allLoadedSelected && <Check size={12} strokeWidth={3.5} aria-hidden="true" />}</span>
              {/* "Loaded" is stated rather than implied: the grid holds the
                  current page, not every asset the filter matches. */}
              {/* The count is of everything picked, which after "all matching"
                  is more than the page can show - so it counts the selection,
                  not the ticks visible on screen. */}
              <strong>
                {selection.size
                  ? `${selection.size.toLocaleString()} selected`
                  : `Select from ${assets.length} loaded`}
              </strong>
              {total > assets.length && selection.size < total && (
                <Button
                  variant="quiet"
                  size="sm"
                  busy={busy === "select-all"}
                  onClick={() => void selectAllMatching()}
                >Select all {total.toLocaleString()} matching</Button>
              )}
              {total > assets.length && selection.size >= total && (
                <Badge tone="good">{t("library.everyMatchSelected")}</Badge>
              )}
              {selection.size > 0 && (
                <>
                  <Button variant="quiet" size="sm" onClick={() => setSelection(new Set())}>
                    Clear
                  </Button>
                  <span className="library-selection-tools">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!canImport || selectionList.length === 0}
                      title="Build one stack of effects and apply it to every compatible selected item"
                      onClick={() => setBatchEffectsOpen(true)}
                    >
                      <ActionIcon name="edit" />
                      Apply effect stack
                    </Button>
                    {visibleBulkActions.map((action) => (
                      <Button
                        key={action.id}
                        variant={action.id === "delete" ? "danger" : "secondary"}
                        size="sm"
                        busy={busy === `bulk-${action.id}`}
                        disabled={!canImport || !action.available}
                        title={action.available ? action.description : action.reason ?? undefined}
                        onClick={() => void runBulkAction(action)}
                      >
                        {bulkActionIcon(action.id) && (
                          <ActionIcon name={bulkActionIcon(action.id)!} />
                        )}
                        {action.label}
                      </Button>
                    ))}
                  </span>
                  {selection.size > Math.min(...visibleBulkActions.map((a) => a.max_batch), Infinity) && (
                    <Badge tone="neutral">{t("library.runsInBatches")}</Badge>
                  )}
                </>
              )}
              {/* The outcome reads back where the run was started rather than as
                  a banner elsewhere; per-asset progress is in notifications. */}
              {message && <span className="library-selection-note" role="status">{message}</span>}
            </div>
          )}
          <div className={`library-collection ${groupBy === "none" ? `library-${viewMode}` : "library-grouped"}`}>
            {groupBy === "none"
              ? assets.map(renderAsset)
              : groupedAssets.map(([label, groupAssets]) => (
                <section className="library-group" key={label}>
                  <header><strong>{label}</strong><span>{groupTotal(label, groupAssets.length)}</span></header>
                  <div className={`library-group-items library-${viewMode}`}>
                    {groupAssets.map(renderAsset)}
                  </div>
                </section>
              ))}
            {!assets.length && <p>{t("library.empty")}</p>}
          </div>
          {canImport && (
            <details className="library-import">
              <summary>{t("library.importLocal")}</summary>
              <form onSubmit={importMedia}>
                <label>{t("library.filePath")}<input name="path" required placeholder="S:\Media\clip.mp4" /></label>
                <label>{t("library.sortTitle")}<input name="title" required /></label>
                <div className="library-form-row">
                  <label>{t("library.platform")}<input name="platform" placeholder="douyin" /></label>
                  <label>{t("library.creator")}<input name="creator" /></label>
                  <label>{t("library.publishedAt")}<input name="published_at" type="datetime-local" /></label>
                </div>
                <label>{t("library.sourceUrl")}<input name="source_url" type="url" /></label>
                <label>{t("library.caption")}<textarea name="caption" rows={2} /></label>
                <label>{t("library.hashtags")}<input name="hashtags" placeholder="coffee, travel" /></label>
                <div className="library-form-row">
                  <label>{t("library.likes")}<input name="likes" type="number" min={0} /></label>
                  <label>{t("library.comments")}<input name="comments" type="number" min={0} /></label>
                  <label>{t("library.shares")}<input name="shares" type="number" min={0} /></label>
                </div>
                <Button type="submit" variant="primary" busy={busy === "import"}>{busy === "import" ? "Queuing" : "Import safely"}</Button>
              </form>
            </details>
          )}

          {!!jobs.length && (
            <div className="library-jobs">
              <strong>{t("library.recentIngestion")}</strong>
              {jobs.slice(0, 5).map((job, index) => (
                <div key={job.id ?? `${job.asset_id}-${index}`}><span>{job.payload?.title ?? "Media import"}</span><em>{job.status}</em></div>
              ))}
            </div>
          )}
        </aside>

        <section className="library-detail">
          {selected ? (
            <>
              <MediaPreview
                key={selected.id}
                asset={selected}
                workspaceId={workspaceId}
                apiFetch={apiFetch}
                previewPosition={selectedIndex + 1}
                previewTotal={assets.length}
                hasPreviousVideo={selectedIndex > 0}
                hasNextVideo={selectedIndex >= 0 && selectedIndex < assets.length - 1}
                autoStart={continueVideoPlayback}
                onPlaybackChange={setContinueVideoPlayback}
                onPreviousVideo={() => setSelectedId(assets[selectedIndex - 1]?.id ?? selected.id)}
                onNextVideo={() => setSelectedId(assets[selectedIndex + 1]?.id ?? selected.id)}
              />
              <article className="library-summary">
                <div>
                  <p className="section-kicker library-source-meta">
                    <span>{selected.media_kind}</span>
                    <span aria-hidden="true">·</span>
                    <span>{selected.platform ?? selected.source_type}</span>
                    {selected.creator && <>
                      <span aria-hidden="true">·</span>
                      {selectedChannelUrl ? (
                        <a className="library-channel-name library-channel-link" href={selectedChannelUrl} target="_blank" rel="noreferrer" aria-label={`Open ${selected.creator}'s Douyin channel`}>
                          Channel: {selected.creator}
                        </a>
                      ) : <span className="library-channel-name">Channel: {selected.creator}</span>}
                    </>}
                    {/* Named after what is actually in the cut. Any render
                        earns the tag, not only a blurred one — a clip that has
                        been cropped and had an object put on a face has been
                        edited just as much, and said nothing here before. */}
                    {renderedCut(selected.versions) && (
                      <span
                        className="effect-tags"
                        aria-label="Applied effects"
                        title={blurredVersion(selected)
                          ? `Handoffs send this cut: ${handoffPath(selected)}`
                          : undefined}
                      >
                        {cutEffects(t, renderedCut(selected.versions)!).length
                          ? cutEffects(t, renderedCut(selected.versions)!).map((name) => (
                              <em className="blurred-tag" key={name}>{name}</em>
                            ))
                          : <em className="blurred-tag">{cutLabel(t, renderedCut(selected.versions)!)}</em>}
                      </span>
                    )}
                  </p>
                  <h2>{selected.title}</h2>
                  <p>{selected.caption || "No source caption recorded."}</p>
                  {/* The pager shares the metadata line rather than taking a
                      row of its own; the clip and its details are what deserve
                      the vertical space. */}
                  <div className="library-meta-line">
                    <small>{selected.width && selected.height ? `${selected.width}×${selected.height} · ` : ""}{displaySize(selected.size_bytes)}</small>
                    <nav className="library-item-navigation" aria-label={t("library.browseMedia")}>
                      <Button
                        variant="quiet"
                        size="sm"
                        iconOnly
                        aria-label={t("library.previousItem")}
                        title={t("library.previousItem")}
                        disabled={selectedIndex <= 0}
                        onClick={() => setSelectedId(assets[selectedIndex - 1]?.id ?? selectedId)}
                      >‹</Button>
                      <span>{selectedIndex + 1} of {assets.length}</span>
                      <Button
                        variant="quiet"
                        size="sm"
                        iconOnly
                        aria-label={t("library.nextItem")}
                        title={t("library.nextItem")}
                        disabled={selectedIndex < 0 || selectedIndex >= assets.length - 1}
                        onClick={() => setSelectedId(assets[selectedIndex + 1]?.id ?? selectedId)}
                      >›</Button>
                    </nav>
                  </div>
                </div>
                <div className="library-actions">
                  {/* Editing first, because it is the reason this panel is open,
                      and grouped so the three edits read as one set of choices
                      rather than as neighbours of Delete. */}
                  <section className="library-action-group library-editing-actions" aria-label={t("library.editingActions")}>
                    <h4>{t("library.editingActions")}</h4>
                    <div className="library-action-row">
                      <Button
                        variant="secondary"
                        title="Stack, preview, and apply any available effect, including face blur"
                        onClick={() => setEffectsOpen(true)}
                      ><ActionIcon name="edit" />Effects</Button>
                      {renderedCut(selected.versions) && (
                        <Button
                          variant="secondary"
                          busy={busy === "discard-effects"}
                          disabled={!canImport}
                          title="Remove rendered effects and the saved recipe; keep the original media"
                          onClick={() => void removeEffects(selected)}
                        ><ActionIcon name="dismiss" />Remove effects</Button>
                      )}
                      <Button
                        variant="secondary"
                        disabled={selected.media_kind !== "video"}
                        title={selected.media_kind === "video"
                          ? t("library.clipPlanHelp")
                          : t("library.videoOnlyClip")}
                        onClick={() => setEditorOpen(true)}
                      ><ActionIcon name="clip" />{t("library.clipPlan")}</Button>
                      {/* Captions are not an effect: they come from the audio,
                          need not touch the picture, and do not stack. So they
                          get their own button rather than a row in the stack. */}
                      <Button
                        variant="secondary"
                        disabled={!["video", "audio"].includes(selected.media_kind)}
                        title={["video", "audio"].includes(selected.media_kind)
                          ? "Build subtitles from this asset's transcript, styled and timed to the speech"
                          : "Captions need an asset with audio"}
                        onClick={() => setCaptionsOpen(true)}
                      ><ActionIcon name="edit" />Captions</Button>
                    </div>
                    <EffectActivity
                      assetId={selected.id}
                      jobs={notificationJobs}
                      cancellingJobId={cancellingEffectJobId}
                      onCancel={(job) => void cancelEffectJob(job)}
                      onClearHistory={() => void clearEffectHistory(selected.id)}
                      clearing={clearingHistory}
                    />
                  </section>

                  <section className="library-action-group" aria-label={t("library.handoffActions")}>
                    <h4>{t("library.handoffActions")}</h4>
                    <div className="library-action-row">
                      <Link href={`/campaigns?video=${encodeURIComponent(handoffPath(selected))}`}><ActionIcon name="campaign" />{t("library.planCampaign")}</Link>
                      <Link href={`/publish?video=${encodeURIComponent(handoffPath(selected))}`}><ActionIcon name="publish" />{t("library.prepareToPublish")}</Link>
                    </div>
                  </section>

                  <section className="library-action-group" aria-label={t("library.fileActions")}>
                    <h4>{t("library.fileActions")}</h4>
                    <div className="library-action-row">
                      <Button
                        variant="secondary"
                        busy={busy === "folder"}
                        onClick={() => void openAssetFolder(selected)}
                      ><ActionIcon name="openFolder" />{busy === "folder" ? t("library.opening") : t("library.openFolder")}</Button>
                      {selectedSourceLinks.map((url, index, links) => {
                        const label = selected.platform === "douyin"
                          ? `${selected.creator ? `${selected.creator}'s ` : ""}original Douyin video`
                          : `Original source${links.length > 1 ? ` ${index + 1}` : ""}`;
                        return selected.platform === "douyin" ? (
                          <a className="douyin-source-link" key={url} href={url} target="_blank" rel="noreferrer" aria-label={`Open ${label}`} title={`Open ${label}`}>
                            <DouyinMark />
                          </a>
                        ) : (
                          <a key={url} href={url} target="_blank" rel="noreferrer">{label}</a>
                        );
                      })}
                      {deleteAction && (
                        // Last, and the only red thing here. Sitting between
                        // Effects and Plan campaign, it was one slip from a
                        // destructive click.
                        <span className="library-action-danger">
                        <Button
                          variant="danger"
                          busy={busy === `bulk-${deleteAction.id}`}
                          disabled={!canImport}
                          title={deleteAction.description}
                          onClick={() => void runBulkAction(deleteAction, [selected.id])}
                        ><ActionIcon name="delete" />{t("common.delete")}</Button>
                        </span>
                      )}
                    </div>
                  </section>
                </div>
              </article>

              <div className="library-detail-grid">
                <article>
                  <h3>{t("recipe.heading")}</h3>
                  {selected.analysis ? (
                    <dl className="recipe-grid">
                      <div><dt>{t("recipe.spokenHook")}</dt><dd>{selected.analysis.spoken_hook || "—"}</dd></div>
                      <div><dt>{t("recipe.textHook")}</dt><dd>{selected.analysis.text_hook || "—"}</dd></div>
                      <div><dt>{t("recipe.cta")}</dt><dd>{selected.analysis.call_to_action || "—"}</dd></div>
                      <div><dt>{t("recipe.product")}</dt><dd>{selected.analysis.product_shown || "—"}</dd></div>
                      <div><dt>{t("recipe.format")}</dt><dd>{selected.analysis.creative_format || "—"}</dd></div>
                      <div><dt>{t("recipe.editing")}</dt><dd>{selected.analysis.shot_count ? `${selected.analysis.shot_count} shots · ${selected.analysis.average_shot_ms}ms average` : "—"}</dd></div>
                      <div><dt>{t("recipe.structure")}</dt><dd>{selected.analysis.structure_tags.join(", ") || "—"}</dd></div>
                      <div><dt>{t("recipe.keywords")}</dt><dd>{selected.analysis.keywords.join(", ") || "—"}</dd></div>
                    </dl>
                  ) : <p>{t("recipe.empty")}</p>}
                </article>
              </div>

              {canEnrich && (
                <article className="library-enrichment">
                  <div>
                    <h3>{t("recipe.reviewedHeading")}</h3>
                    <p>{t("recipe.reviewedIntro")}</p>
                  </div>
                  <form onSubmit={enrich}>
                    <div className="library-form-row">
                      <label>{t("recipe.language")}<input name="language" defaultValue="und" /></label>
                      <label>{t("recipe.productShown")}<input name="product_shown" defaultValue={selected.analysis?.product_shown ?? ""} /></label>
                      <label>{t("recipe.creativeFormat")}<input name="creative_format" defaultValue={selected.analysis?.creative_format ?? ""} placeholder="faceless demo" /></label>
                    </div>
                    <label>{t("recipe.reviewedSpeech")}<textarea name="speech_text" rows={5} defaultValue={selected.transcripts.find((item) => item.kind === "speech")?.text ?? ""} /></label>
                    <label>{t("recipe.reviewedText")}<textarea name="ocr_text" rows={4} defaultValue={selected.transcripts.find((item) => item.kind === "ocr")?.text ?? ""} /></label>
                    <div className="library-form-row">
                      <label>{t("recipe.sceneCuts")}<input name="scene_boundaries_ms" placeholder="1200, 2800, 5100" /></label>
                      <label>{t("recipe.productReveal")}<input name="product_reveal_ms" type="number" min={0} defaultValue={selected.analysis?.product_reveal_ms ?? ""} /></label>
                      <label>{t("recipe.emotionalAngle")}<input name="emotional_angle" /></label>
                    </div>
                    <label>{t("recipe.analystNotes")}<textarea name="analyst_notes" rows={3} defaultValue={selected.analysis?.analyst_notes ?? ""} /></label>
                    <Button type="submit" variant="primary" busy={busy === "enrich"}>{busy === "enrich" ? "Analyzing" : "Save and derive recipe"}</Button>
                  </form>
                </article>
              )}
            </>
          ) : <article className="library-summary"><p>{t("library.selectToBegin")}</p></article>}
        </section>
      </section>
      {workspaceId && selected && (
        <EffectEditor
          open={effectsOpen}
          workspaceId={workspaceId}
          targets={[{
            id: selected.id,
            title: selected.title,
            path: selected.original_path,
            mediaKind: selected.media_kind,
          }]}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setEffectsOpen(false)}
          onRendered={(text) => setMessage(text)}
        />
      )}
      {workspaceId && selectionList.length > 0 && (
        <EffectEditor
          open={batchEffectsOpen}
          workspaceId={workspaceId}
          targets={selectionList.map((asset) => ({
            id: asset.id,
            title: asset.title,
            path: asset.original_path,
            mediaKind: asset.media_kind,
          }))}
          assetIds={Array.from(selection)}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setBatchEffectsOpen(false)}
          onRendered={(text) => {
            setSelection(new Set());
            setMessage(text);
          }}
        />
      )}
      {workspaceId && selected && (
        <CaptionEditor
          open={captionsOpen}
          workspaceId={workspaceId}
          assetId={selected.id}
          assetTitle={selected.title}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setCaptionsOpen(false)}
        />
      )}
      {workspaceId && selected && (
        <ClipEditor
          open={editorOpen}
          workspaceId={workspaceId}
          assetPath={selected.original_path}
          assetTitle={selected.title}
          durationMs={selected.duration_ms ?? null}
          canApprove={canApprove}
          apiFetch={apiFetch}
          onClose={() => setEditorOpen(false)}
        />
      )}
      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
