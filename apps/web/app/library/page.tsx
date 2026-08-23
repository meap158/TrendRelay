"use client";

import { Check, CircleAlert, CircleCheck, CirclePause, CircleX, Layers3, LoaderCircle, Undo2 } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { effectLabel, effectTag } from "../../lib/i18n/effects";
import { LOCALES } from "../../lib/i18n/locales";
import { mediaTypeFor, opaquePreviewUrl } from "../../lib/media-preview";
import { useAuth } from "../auth-provider";
import { type BaseJob, useJobs } from "../jobs-provider";
import { useWorkspace } from "../workspace-provider";
import { useT } from "../i18n-provider";
import { blurredVersion, handoffPath, openingCut } from "../../lib/media-rules";
import type { ReadableTranscript } from "./transcript-reader";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { Button, ButtonPair, buttonClass } from "../ui/button";
import { WaitingScreen } from "../ui/waiting-screen";
import { WaitingBlock } from "../ui/waiting-block";
import { Dialog } from "../ui/dialog";
import { SegmentedControl } from "../ui/segmented";
import { ActionIcon, bulkActionIcon } from "../ui/action-icons";
import type { ActionName } from "../ui/action-icons";
import { StatusToasts, useStatus } from "../ui/status";
import { Select } from "../ui/select";
import { Badge } from "../ui/primitives";
import {
  AssetFilters,
  EMPTY_FACETS,
  activeFilterCount,
  assetFilterParams,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";
import { ActionMenu, type ActionMenuItem } from "../ui/action-menu";
import {
  LIBRARY_SELECTION_ACTIONS,
  SELECTION_ACTION_ICON,
  SELECTION_ACTION_KEY,
  selectionActionState,
  type LibrarySelectionActionId,
  type LibrarySelectionTarget,
} from "../../lib/library-selection-actions";

// The editors are heavy and only render inside their dialogs, so their code is
// loaded when one opens rather than in the Library page's first bundle. ssr:false
// because they are client-only anyway - there is nothing to render on the server.
const CaptionEditor = dynamic(() => import("./caption-editor").then((m) => m.CaptionEditor), { ssr: false });
const VoiceEditor = dynamic(() => import("./voice-editor").then((m) => m.VoiceEditor), { ssr: false });
const CampaignPicker = dynamic(() => import("./campaign-picker").then((m) => m.CampaignPicker), { ssr: false });
const BulkVoiceEditor = dynamic(() => import("./bulk-voice-editor").then((m) => m.BulkVoiceEditor), { ssr: false });
const BatchTranscribe = dynamic(() => import("./batch-transcribe").then((m) => m.BatchTranscribe), { ssr: false });
const ClipEditor = dynamic(() => import("./clip-editor").then((m) => m.ClipEditor), { ssr: false });
const EffectEditor = dynamic(() => import("./effect-editor").then((m) => m.EffectEditor), { ssr: false });
const AutoTranscribe = dynamic(() => import("./auto-transcribe").then((m) => m.AutoTranscribe), { ssr: false });
const TranscriptDraft = dynamic(() => import("./auto-transcribe").then((m) => m.TranscriptDraft), { ssr: false });
const TranscriptReader = dynamic(() => import("./transcript-reader").then((m) => m.TranscriptReader), { ssr: false });
const TranscriptionSwitch = dynamic(() => import("./transcription-setup").then((m) => m.TranscriptionSwitch), { ssr: false });

type ViewMode = "gallery" | "list";
type GroupBy = "none" | "channel" | "source";

type VersionEffect = { id: string; label: string };
type Version = {
  kind: "original" | "proxy" | "thumbnail" | "audio" | "blurred" | "edited" | "captioned"
    // The speech alone, and the clip carrying it. Two kinds because they are
    // produced at different moments - the audio always, the cut only when
    // asked for - and because "is this ready to post" is answered from this
    // list, which one kind could not do.
    | "voiceover" | "voiced";
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
 * What can be read at all: speech off a soundtrack, or text off a frame.
 *
 * The union of the two modes rather than either one, because the dialog is
 * where the choice between them is made and it disables whichever the asset
 * cannot do. Disabling the button for an image would hide OCR entirely.
 */
const TRANSCRIBABLE = ["video", "audio", "image"];

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

/** Workflow artifacts carried by an asset, separate from visual effects. */
function processingTags(t: Translate, asset: Asset): string[] {
  const speech = asset.transcripts.filter((item) => item.kind === "speech");
  const ocr = asset.transcripts.filter((item) => item.kind === "ocr");
  const kinds = new Set(asset.versions.map((version) => version.kind));
  return [
    speech.some((item) => item.status === "reviewed")
      ? t("filters.transcriptReviewed")
      : speech.some((item) => item.status === "machine")
        ? t("filters.transcriptDraft") : null,
    ocr.some((item) => item.status === "reviewed")
      ? t("filters.textReviewed")
      : ocr.some((item) => item.status === "machine")
        ? t("filters.textDraft") : null,
    kinds.has("captioned") ? t("filters.captions") : null,
    kinds.has("voiceover") || kinds.has("voiced") ? t("filters.voiceover") : null,
  ].filter((tag): tag is string => Boolean(tag));
}

function assetTags(t: Translate, asset: Asset): string[] {
  const rendered = renderedCut(asset.versions);
  const effects = rendered ? cutEffects(t, rendered) : [];
  // Captioned cuts are represented by the workflow tag below. A legacy blur
  // or edit with no recorded recipe still needs its known outcome named.
  if (rendered && !effects.length && rendered.kind !== "captioned") {
    effects.push(cutLabel(t, rendered));
  }
  return [...new Set([...effects, ...processingTags(t, asset)])];
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
  // The two halves of a voiceover, told apart. "Is this ready to post" is
  // answered from this list, and both kinds falling through to "edited cut"
  // made the speech and the clip carrying it indistinguishable - which is the
  // one thing they are kept as separate kinds to avoid.
  if (version.kind === "voiceover") return "Voiceover, on its own";
  if (version.kind === "voiced") return "Clip with the voiceover on";
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

type ThumbnailMediaActivity = {
  label: string;
  detail: string;
  progress: number | null;
  /** Nothing is working on it: hold the bar still rather than animating it. */
  stalled: boolean;
};

/** The single most useful active render state to show on an asset card. */
function thumbnailMediaActivity(
  t: Translate,
  jobs: BaseJob[],
  assetId: string,
  cancellingJobId = "",
): ThumbnailMediaActivity | null {
  const active = jobs.filter((job) =>
    job.assetId === assetId
    && !(job.category === "edit" && isEffectPreviewJob(job))
    && ["queued", "running"].includes(job.status),
  );
  const job = active.find((candidate) => candidate.status === "running") ?? active[0];
  if (!job) return null;

  const effectNames = job.category === "edit"
    ? ((job.raw?.payload?.effects ?? []) as string[]).map((id) => effectLabel(t, id, id))
    : [];
  const stopping = job.id === cancellingJobId;
  const progress = typeof job.progress === "number"
    ? Math.max(0, Math.min(1, job.progress))
    : null;

  return {
    label: job.category !== "edit"
      ? job.stalled ? "Paused" : job.status === "queued" ? `${job.activityLabel ?? "Processing"} queued` : job.activityLabel ?? "Processing"
      : stopping
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
    detail: job.category === "edit"
      ? effectNames.join(" + ") || "Effect stack"
      : job.progressStage || job.activityDetail || job.title,
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
/**
 * `provider` and `status` are what separate a machine reading from a reviewed
 * one. The API has always sent both; nothing read them until the drafts had
 * somewhere to appear.
 */
type Transcript = {
  id: string;
  kind: "speech" | "ocr";
  language: string;
  text: string;
  provider: string;
  status: string;
};
type Analysis = {
  version: number;
  spoken_hook?: string | null;
  text_hook?: string | null;
  call_to_action?: string | null;
  product_shown?: string | null;
  creative_format?: string | null;
  emotional_angle?: string | null;
  structure_tags: string[];
  scene_boundaries_ms: number[];
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

type LibrarySnapshot = {
  assets: Asset[];
  total: number;
  facets: AssetFacets;
  jobs: Job[];
  status: Status;
};

// Next keeps the shell mounted while routes change, but the page itself is
// recreated. Keep the most recent Library results at module scope so returning
// from Campaigns or Publish paints the known list immediately, then validates
// it against the API in the background. This is deliberately memory-only:
// closing the app still starts from authoritative server data.
const librarySnapshots = new Map<string, LibrarySnapshot>();
const LIBRARY_SNAPSHOT_LIMIT = 12;

function rememberLibrarySnapshot(key: string, snapshot: LibrarySnapshot) {
  librarySnapshots.delete(key);
  librarySnapshots.set(key, snapshot);
  while (librarySnapshots.size > LIBRARY_SNAPSHOT_LIMIT) {
    const oldest = librarySnapshots.keys().next().value;
    if (!oldest) break;
    librarySnapshots.delete(oldest);
  }
}

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Media library request failed.");
  return body;
}

/**
 * What a person signed off, for the field that holds exactly that.
 *
 * A machine draft used to land here too, because this only matched on `kind`.
 * That put unchecked text in the reviewed box, where saving the form once
 * promoted it to reviewed without anybody having read it — the drafts appear
 * under the field now instead, with a button that says what it is doing.
 */
function reviewedText(asset: Asset, kind: "speech" | "ocr"): string {
  return asset.transcripts.find(
    (item) => item.kind === kind && item.status === "reviewed",
  )?.text ?? "";
}

function reviewedLanguage(asset: Asset): string {
  return asset.transcripts.find((item) => item.status === "reviewed")?.language ?? "und";
}

/**
 * When a clip arrived, short enough to sit in a line of metadata.
 *
 * The date and the hour, not a relative "3d ago": this reads beside the
 * dimensions and the file size, which are facts about the asset rather than
 * news about it, and the download window in the filter strip above is already
 * the relative way to ask the question. The full moment goes in the `title`,
 * because the minute matters when two clips came from one batch.
 *
 * Rendered only in the browser - these assets arrive from a fetch - so the
 * viewer's own locale and timezone are the right ones to use.
 */
function downloadedOn(when: string | null | undefined): { short: string; exact: string } {
  if (!when) return { short: "", exact: "" };
  const at = new Date(when);
  if (!Number.isFinite(at.getTime())) return { short: "", exact: "" };
  return {
    short: at.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }),
    exact: at.toLocaleString(),
  };
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
  mediaActivity,
}: {
  asset: Asset;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  mediaActivity?: ThumbnailMediaActivity | null;
}) {
  const [source, setSource] = useState("");
  const hasThumbnail = asset.versions.some((version) => version.kind === "thumbnail");

  useEffect(() => {
    if (!hasThumbnail) return;
    let active = true;
    let objectUrl = "";
    // Asked opaque and retyped here: an honest image/* on the wire is a file
    // to a grabber configured for pictures, exactly as video/* is to one
    // configured for clips. Same contract as every other served byte.
    apiFetch(opaquePreviewUrl(`/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/content/thumbnail`))
      .then((response) => {
        if (!response.ok) throw new Error("Preview unavailable");
        return response.arrayBuffer();
      })
      .then((bytes) => {
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
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
      {mediaActivity ? (
        <span
          className={`library-effect-processing${mediaActivity.stalled ? " stalled" : ""}`}
          aria-label={`${mediaActivity.label}: ${mediaActivity.detail}`}
          title={mediaActivity.stalled
            ? `${mediaActivity.detail} — paused. Nothing is working on this; it resumes when the worker is back.`
            : `${mediaActivity.label}: ${mediaActivity.detail}`}
        >
          <span className="library-effect-processing-label">
            {/* A spinner on a job nobody is working on is the animation that
                made a ten-hour-dead render look alive. */}
            {mediaActivity.stalled
              ? <CirclePause size={15} aria-hidden="true" />
              : <LoaderCircle className="is-spinning" size={15} aria-hidden="true" />}
            <strong>{mediaActivity.label}</strong>
            {mediaActivity.progress !== null && <small>{Math.round(mediaActivity.progress * 100)}%</small>}
          </span>
          <span
            className={`library-effect-processing-progress ${
              mediaActivity.progress === null && !mediaActivity.stalled ? "indeterminate" : ""
            }`}
            aria-hidden="true"
          >
            <span style={mediaActivity.progress === null ? undefined : { width: `${Math.round(mediaActivity.progress * 100)}%` }} />
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
    // Hands a finished blob to the row through one guarded door, so both the
    // base64 path and the streamed fallback revoke correctly on cleanup.
    const adopt = (url: string) => {
      if (active) {
        objectUrl = url;
        setSource(url);
      } else {
        URL.revokeObjectURL(url);
      }
    };
    apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/preview?cut=${wanted}`,
      { method: "POST", signal: controller.signal },
    )
      .then((response) => json<{ mime_type: string; content_base64: string }>(response))
      .then((preview) => {
        adopt(URL.createObjectURL(previewBlob(preview.content_base64, preview.mime_type)));
      })
      .catch((reason) => {
        if (active && reason instanceof DOMException && reason.name === "AbortError") return;
        // Caption burns re-encode the full-length source, so long clips pass
        // the base64 preview's size cap. The stream endpoint answers the same
        // cut with range requests, which is what a long video wanted anyway -
        // but a progressive video/mp4 over plain GET is precisely the request
        // a download manager takes, and this surface had the distinction of
        // being the last one still making it. So the stream is fetched here,
        // asked for opaque and retyped into a blob like every other served
        // byte. The cost of buffering before playback is the price of the
        // player not being a download button.
        if (!(active && playable && reason instanceof Error && /too large/i.test(reason.message))) {
          if (active) setError(reason instanceof Error ? reason.message : t("library.previewUnavailable"));
          return;
        }
        apiFetch(
          // A path, not a URL: `apiFetch` puts the base in front. Passing the
          // absolute form here is what produced the origin twice over, and the
          // burned-in caption preview could not be opened at all.
          opaquePreviewUrl(
            `/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/preview/stream?cut=${wanted}`,
          ),
          { signal: controller.signal },
        )
          .then((response) => {
            if (!response.ok) throw new Error(t("library.previewUnavailable"));
            return response.arrayBuffer();
          })
          .then((bytes) => {
            adopt(URL.createObjectURL(new Blob([bytes], {
              type: mediaTypeFor(asset.original_path || asset.title, asset.media_kind === "audio" ? "audio/mpeg" : "video/mp4"),
            })));
          })
          .catch((streamReason) => {
            if (active && !(streamReason instanceof DOMException && streamReason.name === "AbortError")) {
              setError(streamReason instanceof Error ? streamReason.message : t("library.previewUnavailable"));
            }
          });
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, asset.id, asset.original_path, asset.title, asset.media_kind, rendered, cut, requested, t, workspaceId]);

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
function notificationAssetsFromQuery(value: string): string[] {
  return [...new Set(value.split(",").map((id) => id.trim()).filter(Boolean))].slice(0, 200);
}

function notificationActionTitle(value: string): string {
  return value.trim().replace(/\s*·\s*\d+\s+items?\s*$/i, "").slice(0, 140);
}

function notificationScopeProgress(
  jobs: BaseJob[],
  assetIds: string[],
  title: string,
): { settled: number; retrying: number; fraction: number } | null {
  if (!assetIds.length || !title) return null;
  const wanted = new Set(assetIds);
  const byAsset = new Map<string, BaseJob>();
  jobs.forEach((job) => {
    if (
      job.assetId
      && wanted.has(job.assetId)
      && notificationActionTitle(job.title) === title
      && !byAsset.has(job.assetId)
    ) {
      byAsset.set(job.assetId, job);
    }
  });
  if (!byAsset.size) return null;
  const related = [...byAsset.values()];
  const settled = related.filter((job) => ["succeeded", "failed", "cancelled"].includes(job.status)).length;
  const retrying = related.filter((job) =>
    job.stalled
    && Number(job.raw?.attempt_count ?? 0) < Number(job.raw?.max_attempts ?? 0)
  ).length;
  return {
    settled,
    retrying,
    fraction: Math.max(0, Math.min(1, settled / assetIds.length)),
  };
}

function LibraryContent() {
  const t = useT();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { loading, user, apiFetch } = useAuth();
  const { workspaces, workspaceId } = useWorkspace();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  // One object rather than four separate states, so the list, the select-all
  // and the Publish picker all describe a filter the same way.
  const [filters, setFilters] = useState<AssetFilterValues>({});
  const notificationAssetQuery = searchParams.get("assets") ?? "";
  const notificationTitle = notificationActionTitle(searchParams.get("notice") ?? "");
  const notificationAssetIds = useMemo(
    () => notificationAssetsFromQuery(notificationAssetQuery),
    [notificationAssetQuery],
  );
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
  // Keep "not answered yet" separate from a genuine empty library. On route
  // entry the asset array is necessarily empty; treating that as real data
  // flashes the empty collection and detail prompts before the request lands.
  const [loadedWorkspaceId, setLoadedWorkspaceId] = useState("");
  // Background syncs, filter changes and job completion can all request a
  // refresh. They must all read the latest selection, and an older response
  // must never put an unfiltered list back after a newer filtered one arrived.
  const latestFilters = useRef(filters);
  const latestSortOrder = useRef(sortOrder);
  const latestWorkspaceId = useRef(workspaceId);
  const refreshSequence = useRef(0);
  // The two reviewed-text boxes, so a machine draft can be copied into one on
  // request. The form reads its values from the DOM, so writing to the node is
  // what the submit will pick up.
  const speechField = useRef<HTMLTextAreaElement | null>(null);
  const ocrField = useRef<HTMLTextAreaElement | null>(null);
  const [viewMode, setViewMode] = usePersistedState<ViewMode>(
    "trendrelay.library.view", "gallery", isViewMode,
  );
  const [continueVideoPlayback, setContinueVideoPlayback] = useState(false);
  const [busy, setBusy] = useState("");
  // Errors are reported over the page: in flow they shifted everything below
  // them whenever an action finished. The bulk-action outcome below is not a
  // banner — it reads back inline where the run was started — so it stays put.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();
  const {
    jobs: notificationJobs,
    refresh: refreshJobs,
  } = useJobs();
  const notificationProgress = useMemo(
    () => notificationScopeProgress(notificationJobs, notificationAssetIds, notificationTitle),
    [notificationAssetIds, notificationJobs, notificationTitle],
  );
  const previousMediaJobStates = useRef<Map<string, string>>(new Map());
  const [message, setMessage] = useState("");
  const [selection, setSelection] = useState<Set<string>>(new Set());
  /** Anchor for shift-click range selection. */
  const [lastPicked, setLastPicked] = useState<string | null>(null);
  const [bulkActions, setBulkActions] = useState<BulkAction[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [transcribeOpen, setTranscribeOpen] = useState(false);
  const [captionsOpen, setCaptionsOpen] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  /** What the campaign picker is about to add. Empty closes it. */
  const [campaignPickerFor, setCampaignPickerFor] = useState<Asset[]>([]);
  /** The machine reading open in the reader, or null. */
  const [readingDraft, setReadingDraft] = useState<ReadableTranscript | null>(null);
  const [effectsOpen, setEffectsOpen] = useState(false);
  const [selectionAction, setSelectionAction] = useState<LibrarySelectionActionId | null>(null);
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
  const filterParams = useCallback(() => {
    const params = assetFilterParams(filters);
    if (notificationAssetIds.length) params.set("asset_ids", notificationAssetIds.join(","));
    return params;
  }, [filters, notificationAssetIds]);

  useEffect(() => { latestFilters.current = filters; }, [filters]);
  useEffect(() => { latestSortOrder.current = sortOrder; }, [sortOrder]);
  useEffect(() => { latestWorkspaceId.current = workspaceId; }, [workspaceId]);
  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    if (nextWorkspace !== latestWorkspaceId.current) return;
    // A delayed auto-sync may hold a callback created before the user selected
    // Images or an effect. Reading refs here makes even that delayed refresh
    // use what the controls show now, rather than silently restoring "All".
    const params = assetFilterParams(latestFilters.current);
    if (notificationAssetIds.length) params.set("asset_ids", notificationAssetIds.join(","));
    params.set("sort", latestSortOrder.current);
    params.set("limit", "100");
    const suffix = `?${params}`;
    const snapshotKey = `${nextWorkspace}${suffix}`;
    const snapshot = librarySnapshots.get(snapshotKey);
    const sequence = ++refreshSequence.current;
    if (snapshot) {
      setAssets(snapshot.assets);
      setTotal(snapshot.total);
      setFacets(snapshot.facets);
      setJobs(snapshot.jobs);
      setStatus(snapshot.status);
      setSelectedId((current) =>
        snapshot.assets.some((asset) => asset.id === current)
          ? current
          : (snapshot.assets[0]?.id ?? ""),
      );
    }
    setLoadingAssets(!snapshot);
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
      rememberLibrarySnapshot(snapshotKey, {
        assets: assetBody.assets,
        total: assetBody.total ?? assetBody.assets.length,
        facets: assetBody.facets ?? EMPTY_FACETS,
        jobs: jobBody.jobs,
        status: statusBody,
      });
      setSelectedId((current) =>
        assetBody.assets.some((asset) => asset.id === current)
          ? current
          : (assetBody.assets[0]?.id ?? ""),
      );
    } finally {
      if (sequence === refreshSequence.current) {
        setLoadingAssets(false);
        setLoadedWorkspaceId(nextWorkspace);
      }
    }
  }, [apiFetch, notificationAssetIds, workspaceId]);

  useEffect(() => {
    const mediaJobs = notificationJobs.filter((job) => Boolean(job.assetId));
    const previous = previousMediaJobStates.current;
    const settledNow = mediaJobs.some((job) =>
      ["succeeded", "failed", "cancelled"].includes(job.status)
      && ["queued", "running"].includes(previous.get(job.id) ?? ""),
    );
    previousMediaJobStates.current = new Map(
      mediaJobs.map((job) => [job.id, job.status]),
    );
    // The notification announces completion; refresh the same screen at that
    // moment so its new cut, exact tags, and effect facet appear without a
    // manual reload. Failed and cancelled jobs refresh too, clearing stale UI.
    if (settledNow) void refresh();
  }, [notificationJobs, refresh]);

  function clearFilters() {
    setFilters({});
  }

  function clearNotificationView() {
    const url = new URL(window.location.href);
    url.searchParams.delete("asset");
    url.searchParams.delete("assets");
    url.searchParams.delete("from");
    url.searchParams.delete("notice");
    router.replace(`${url.pathname}${url.search}${url.hash}`, { scroll: false });
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
  const selectionTargets: LibrarySelectionTarget[] = selectionList.map((asset) => ({
    id: asset.id,
    title: asset.title,
    mediaKind: asset.media_kind,
  }));
  const selectionActionItems: ActionMenuItem[] = LIBRARY_SELECTION_ACTIONS.map((action) => {
    const state = selectionActionState(action, selectionTargets);
    const suffix = SELECTION_ACTION_KEY[action.id];
    const label = t(`library.selectionAction${suffix}`);
    const description = t(`library.selectionAction${suffix}Help`);
    const disabledReason = state.compatible.length === 0
      ? t("library.actionNoCompatible")
      : state.overLimit && action.maxItems
        ? t("library.actionLimit", { count: action.maxItems })
        : undefined;
    return {
      id: action.id,
      label,
      description,
      disabled: !canImport || !state.enabled,
      disabledReason,
      icon: <ActionIcon name={SELECTION_ACTION_ICON[action.id]} />,
    };
  });

  function finishSelectionAction(text: string, completedIds: string[]) {
    const completed = new Set(completedIds);
    setSelection((current) => new Set([...current].filter((id) => !completed.has(id))));
    setMessage(text);
  }

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
      // Kept rather than thrown. A batch that fails used to abandon the whole
      // run, so everything the earlier batches had queued vanished from the
      // report while their jobs went on working - the screen said the action
      // had not started and the queue disagreed.
      let interrupted = "";
      let handled = 0;
      for (const [index, batch] of batches.entries()) {
        if (batches.length > 1) {
          setMessage(`${action.verb}: batch ${index + 1} of ${batches.length}…`);
        }
        try {
          const response = await apiFetch(`/api/workspaces/${workspaceId}/media/library/bulk`, {
            method: "POST",
            body: JSON.stringify({
              action: action.id, asset_ids: batch, confirm_external_action: true,
            }),
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(body.detail ?? `${action.label} could not start.`);
          }
          for (const key of ["queued", "skipped", "failed", "missing"] as const) {
            totals[key] += body.counts?.[key] ?? 0;
          }
          handled += batch.length;
        } catch (reason) {
          interrupted = reason instanceof Error
            ? reason.message : `${action.label} could not start.`;
          break;
        }
      }
      const { queued, skipped, failed, missing } = totals;
      // Every outcome is reported: a bare "queued" would hide that a third of
      // the selection was skipped for already being done.
      const parts = [`${queued} queued`];
      if (skipped) parts.push(`${skipped} skipped`);
      if (failed) parts.push(`${failed} failed`);
      if (missing) parts.push(`${missing} missing`);
      if (interrupted) {
        fail(`${interrupted} ${handled} of ${ids.length} were handled `
          + `(${parts.join(" · ")}); the rest were not.`);
      } else {
        setMessage(`${action.verb}: ${parts.join(" · ")}.`);
      }
      if (queued) {
        // Only what went through, so anything left behind stays picked and can
        // be run again without finding it in the grid a second time.
        setSelection((current) => new Set(
          [...current].filter((id) => !ids.slice(0, handled).includes(id)),
        ));
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
    const mediaActivity = thumbnailMediaActivity(
      t,
      notificationJobs,
      asset.id,
      cancellingEffectJobId,
    );
    const tags = assetTags(t, asset);
    return (
      <button className={`${selectedId === asset.id ? "selected" : ""}${renderedCut(asset.versions) ? " has-versions" : ""}${selection.has(asset.id) ? " picked" : ""}`} key={asset.id} aria-label={`Open ${asset.title}${mediaActivity ? `. ${mediaActivity.label}: ${mediaActivity.detail}` : ""}`} aria-pressed={selectedId === asset.id} onClick={() => setSelectedId(asset.id)}>
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
        <Thumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} mediaActivity={mediaActivity} />
        <span>
          <strong>{asset.title}</strong>
          <small>{asset.creator ? `${asset.creator} · ` : ""}{asset.platform ?? asset.source_type} · {displayDuration(asset.duration_ms)} · {displaySize(asset.size_bytes)}</small>
          {/* Marked for any rendered cut, not only a blurred one. An asset with
              a crop and a sticker on it has been edited just as much, and the
              row was the only place that said so at a glance. */}
          {tags.length > 0 && (
            <span className="effect-tags" aria-label={t("library.mediaTags")}>
              {tags.map((name) => (
                <em className="blurred-tag" key={name}>{name}</em>
              ))}
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
      const url = new URL(window.location.href);
      url.searchParams.delete("asset");
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    });
  }, [assets]);

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

  if (loading) return <WaitingScreen className="library-page" message={t("library.opening")} />;
  if (!user) return <main className="library-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Flibrary">{t("library.signInPrompt")}</Link></main>;

  const initialAssetsPending = Boolean(workspaceId) && loadedWorkspaceId !== workspaceId;

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
        </header>
      </div>

      <section className="library-layout" aria-busy={loadingAssets}>
        <aside className="library-browser">
          <div className="library-browser-sticky-controls">
            <div className="library-browser-toolbar">
          {notificationAssetIds.length > 0 && (
            <div className="library-notification-view" role="status">
              <span className="library-notification-copy">
                <strong>{notificationTitle || t("library.fromNotifications")}</strong>
                <small>
                  {notificationProgress
                    ? `${notificationProgress.settled}/${notificationAssetIds.length} ${t("library.itemsDone")}${notificationProgress.retrying ? ` · ${notificationProgress.retrying} ${t("library.toRetry")}` : ""}`
                    : t(
                      notificationAssetIds.length === 1
                        ? "library.notificationViewOne"
                        : "library.notificationView",
                      { count: notificationAssetIds.length },
                    )}
                </small>
                {notificationProgress && (
                  <span className="library-notification-progress" aria-hidden="true">
                    <span style={{ width: `${notificationProgress.fraction * 100}%` }} />
                  </span>
                )}
              </span>
              <Button variant="quiet" size="sm" onClick={clearNotificationView}>
                {t("library.showAllMedia")}
              </Button>
            </div>
          )}
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
              <Select aria-label={t("library.sortLabel")} value={sortOrder} onChange={(event) => { if (isSortOrder(event.target.value)) setSortOrder(event.target.value); }}>
                <option value="newest">{t("library.sortNewest")}</option>
                <option value="oldest">{t("library.sortOldest")}</option>
                <option value="title">{t("library.sortTitle")}</option>
                <option value="duration">{t("library.sortLongest")}</option>
              </Select>
            </label>
          </nav>

          <AssetFilters
            values={filters}
            facets={facets}
            fields={["channel", "platform", "effect", "processing", "downloaded"]}
            onChange={setFilters}
          >
            <label>{t("library.group")}
              <Select aria-label={t("library.groupLabel")} value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)}>
                <option value="none">{t("library.noGrouping")}</option>
                <option value="channel">{t("library.channel")}</option>
                <option value="source">{t("library.source")}</option>
              </Select>
            </label>
          </AssetFilters>
          <div className="library-collection-toolbar">
            <strong aria-live="polite">
              {loadingAssets ? "Filtering…" : `${total} ${total === 1 ? "item" : "items"}`}
            </strong>
            <div className="library-collection-actions">
              {canImport && <Button variant="quiet" size="sm" busy={busy === "sync"} onClick={() => void syncDownloads()}><ActionIcon name="refresh" />{busy === "sync" ? "Refreshing" : "Refresh downloads"}</Button>}
              {/* The control this one always was, now shared - the campaign
                  timeline had a second version of it that did not match. */}
              <SegmentedControl
                label={t("library.viewLabel")}
                value={viewMode}
                onChange={chooseView}
                options={[
                  { value: "gallery", title: t("library.galleryView"),
                    icon: <ActionIcon name="grid" /> },
                  { value: "list", title: t("library.listView"),
                    icon: <ActionIcon name="list" /> },
                ]}
              />
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
                      disabled={!canImport || selection.size === 0}
                      title="Queue the selected clips into a campaign"
                      onClick={() => setCampaignPickerFor(
                        assets.filter((asset) => selection.has(asset.id)),
                      )}
                    ><ActionIcon name="campaign" />Add to campaign</Button>
                    <ActionMenu
                      label={t("library.selectionActions")}
                      ariaLabel={t("library.selectionActionsLabel")}
                      icon={<ActionIcon name="edit" />}
                      items={selectionActionItems}
                      disabled={!canImport || selectionList.length === 0}
                      onSelect={(id) => setSelectionAction(id as LibrarySelectionActionId)}
                    />
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
          </div>
          {initialAssetsPending ? (
            <WaitingBlock className="library-collection-wait" message={t("common.loading")} />
          ) : <div className={`library-collection ${groupBy === "none" ? `library-${viewMode}` : "library-grouped"}`}>
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
          </div>}
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
          {initialAssetsPending ? (
            <WaitingBlock className="library-detail-wait" message={t("common.loading")} />
          ) : selected ? (
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
                    {assetTags(t, selected).length > 0 && (
                      <span
                        className="effect-tags"
                        aria-label={t("library.mediaTags")}
                        title={blurredVersion(selected)
                          ? `Handoffs send this cut: ${handoffPath(selected)}`
                          : undefined}
                      >
                        {assetTags(t, selected).map((name) => (
                          <em className="blurred-tag" key={name}>{name}</em>
                        ))}
                      </span>
                    )}
                  </p>
                  <h2>{selected.title}</h2>
                  <p>{selected.caption || "No source caption recorded."}</p>
                  {/* The pager shares the metadata line rather than taking a
                      row of its own; the clip and its details are what deserve
                      the vertical space. */}
                  <div className="library-meta-line">
                    <small>{selected.width && selected.height ? `${selected.width}×${selected.height} · ` : ""}{displaySize(selected.size_bytes)}
                      {/* When it arrived. Kept beside the size rather than
                          given a row: it is one more fact about the file, and
                          the reason to want it - telling this morning's batch
                          from last week's - is answered by reading it, not by
                          hunting for it. */}
                      {downloadedOn(selected.collected_at).short && (
                        <> · <span title={`Downloaded ${downloadedOn(selected.collected_at).exact}`}>
                          Downloaded {downloadedOn(selected.collected_at).short}
                        </span></>
                      )}</small>
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
                      {/* Taking the effects off is not a separate action from
                          putting them on, and it only exists while there are
                          any - so it hangs off the Effects button as an icon
                          rather than standing beside it as an equal, and the
                          row no longer changes width as the selection moves
                          between an edited asset and an untouched one. */}
                      <ButtonPair label="Effects">
                        <Button
                          variant="secondary"
                          title="Stack, preview, and apply any available effect, including face blur"
                          onClick={() => setEffectsOpen(true)}
                        ><ActionIcon name="effects" />Effects</Button>
                        {renderedCut(selected.versions) && (
                          <Button
                            variant="secondary"
                            iconOnly
                            busy={busy === "discard-effects"}
                            disabled={!canImport}
                            aria-label="Remove effects"
                            title="Remove rendered effects and the saved recipe; keep the original media"
                            onClick={() => void removeEffects(selected)}
                          ><ActionIcon name="dismiss" size={13} /></Button>
                        )}
                      </ButtonPair>
                      <Button
                        variant="secondary"
                        disabled={selected.media_kind !== "video"}
                        title={selected.media_kind === "video"
                          ? t("library.clipPlanHelp")
                          : t("library.videoOnlyClip")}
                        onClick={() => setEditorOpen(true)}
                        ><ActionIcon name="clip" />{t("library.clipPlan")}</Button>
                      {/* Ahead of Captions because that is the order the work
                          goes in: both captions and a voiceover read from a
                          transcript, and until now the only way to make one
                          from this panel was a widget buried in the reviewed-
                          text form far below - so the step they depend on was
                          the one step the row did not offer. Same dialog the
                          batch menu opens, with this asset as its single
                          target, so the two paths cannot drift apart. */}
                      <Button
                        variant="secondary"
                        disabled={!TRANSCRIBABLE.includes(selected.media_kind)}
                        title={TRANSCRIBABLE.includes(selected.media_kind)
                          ? "Read this asset's speech, or the text on screen, into a draft transcript"
                          : "Transcribing needs a clip with sound or something to read on screen"}
                        onClick={() => setTranscribeOpen(true)}
                      ><ActionIcon name="transcribe" />Transcribe</Button>
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
                      ><ActionIcon name="captions" />Captions</Button>
                      {/* Its own button for the same reason captions have one:
                          it comes from the words rather than the picture, and
                          does not stack with anything. Video or audio, because
                          a voiceover replaces a sound track and both kinds
                          have one. */}
                      <Button
                        variant="secondary"
                        disabled={!["video", "audio"].includes(selected.media_kind)}
                        title={["video", "audio"].includes(selected.media_kind)
                          ? "Speak this asset's reviewed transcript in a chosen voice"
                          : "A voiceover needs a clip to put it on"}
                        onClick={() => setVoiceOpen(true)}
                      ><ActionIcon name="voiceover" />Voiceover</Button>
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
                      {/* Asked and answered here rather than by navigating.
                          "Which campaign" is a list and a click; going to the
                          campaign workspace to answer it left somebody in
                          another tab with a picker open over a campaign they
                          had not chosen. */}
                      <Button
                        variant="secondary"
                        disabled={!canImport}
                        onClick={() => setCampaignPickerFor([selected])}
                      ><ActionIcon name="campaign" />Add to campaign</Button>
                      {/* The asset, not just its path. Publish resolves it and
                          selects it exactly as its own library picker would -
                          a path alone filled the field and left the clip
                          card, its length and its blur tag missing. */}
                      <Link href={`/publish?asset=${encodeURIComponent(selected.id)}`}><ActionIcon name="publish" />{t("library.prepareToPublish")}</Link>
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

              {/* The recipe card that sat here showed the derived analysis back
                  to the operator, and mostly showed "no recipe yet". The
                  analysis itself is kept - it is what search and campaign
                  matching read - but it earns its keep there rather than as a
                  card of dashes above the form that produces it. */}
              {canEnrich && (
                <article className="library-enrichment">
                  <header className="library-enrichment-head">
                    <h3>{t("recipe.reviewedHeading")}</h3>
                    <p>{t("recipe.reviewedIntro")}</p>
                  </header>
                  {/* Keyed to the asset. These fields are uncontrolled, so
                      without it selecting another clip left the previous one's
                      transcript sitting in the boxes — which matters far more
                      now that a draft can be poured into them. */}
                  <form key={selected.id} onSubmit={enrich}>
                    <AutoTranscribe
                      workspaceId={workspaceId}
                      assetId={selected.id}
                      hasAudio={selected.has_audio}
                      mediaKind={selected.media_kind}
                      apiFetch={apiFetch}
                      canEdit={canEnrich}
                      onFinished={() => void refresh()}
                    />
                    <div className="library-enrichment-workspace">
                      <section>
                        <div className="library-enrichment-section-head">
                          <h4>Reviewed text</h4>
                          <label className="library-language-field">
                            {t("recipe.language")}
                            <Select
                              name="language"
                              defaultValue={reviewedLanguage(selected)}
                              aria-label={t("recipe.language")}
                              title={t("recipe.languageAutomaticHelp")}
                              searchable={false}
                            >
                              <option value="und">{t("recipe.languageAutomatic")}</option>
                              {!LOCALES.some((item) => item.code === reviewedLanguage(selected))
                                && reviewedLanguage(selected) !== "und" && (
                                <option value={reviewedLanguage(selected)}>
                                  {reviewedLanguage(selected).toUpperCase()}
                                </option>
                              )}
                              {LOCALES.map((item) => (
                                <option key={item.code} value={item.code}>{item.label}</option>
                              ))}
                            </Select>
                          </label>
                        </div>
                        <label>{t("recipe.reviewedSpeech")}<textarea ref={speechField} name="speech_text" rows={5} defaultValue={reviewedText(selected, "speech")} /></label>
                        <TranscriptDraft
                          transcripts={selected.transcripts}
                          kind="speech"
                          onUse={(text) => { if (speechField.current) speechField.current.value = text; }}
                          onRead={setReadingDraft}
                        />
                        <label>{t("recipe.reviewedText")}<textarea ref={ocrField} name="ocr_text" rows={4} defaultValue={reviewedText(selected, "ocr")} /></label>
                        <TranscriptDraft
                          transcripts={selected.transcripts}
                          kind="ocr"
                          onUse={(text) => { if (ocrField.current) ocrField.current.value = text; }}
                          onRead={setReadingDraft}
                        />
                      </section>
                      <section>
                        <h4>Campaign metadata</h4>
                        <label>{t("recipe.productShown")}<input name="product_shown" defaultValue={selected.analysis?.product_shown ?? ""} placeholder="Product or offer visible in the clip" /></label>
                        <label>{t("recipe.creativeFormat")}<input name="creative_format" defaultValue={selected.analysis?.creative_format ?? ""} placeholder="Demo, testimonial, comparison…" /></label>
                        <label>{t("recipe.analystNotes")}<textarea name="analyst_notes" rows={4} defaultValue={selected.analysis?.analyst_notes ?? ""} placeholder="Context that should influence search or product matching" /></label>
                        <details className="library-analysis-advanced">
                          <summary>Timing and analysis details</summary>
                          <p>Optional metadata for deeper analysis. Most clips do not need these fields.</p>
                          <label>{t("recipe.emotionalAngle")}<input name="emotional_angle" defaultValue={selected.analysis?.emotional_angle ?? ""} /></label>
                          <label>{t("recipe.sceneCuts")}<input name="scene_boundaries_ms" defaultValue={selected.analysis?.scene_boundaries_ms.join(", ") ?? ""} placeholder="1200, 2800, 5100" /></label>
                          <label>{t("recipe.productReveal")}<input name="product_reveal_ms" type="number" min={0} defaultValue={selected.analysis?.product_reveal_ms ?? ""} /></label>
                        </details>
                      </section>
                    </div>
                    <footer className="library-enrichment-actions">
                      <Button type="submit" variant="primary" busy={busy === "enrich"}>{busy === "enrich" ? "Saving…" : "Save reviewed text & metadata"}</Button>
                      <small>Updates search, captions, voiceovers, campaign matching, and the versioned creative recipe.</small>
                    </footer>
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
          open={selectionAction === "effects"}
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
          onClose={() => setSelectionAction(null)}
          onRendered={(text) => {
            setSelection(new Set());
            setMessage(text);
          }}
        />
      )}
      {workspaceId && selectionList.length > 0 && (
        <BatchTranscribe
          key={`bulk-transcribe-${selectionList.map((asset) => asset.id).join("-")}`}
          open={selectionAction === "transcribe"}
          workspaceId={workspaceId}
          targets={selectionTargets}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setSelectionAction(null)}
          onQueued={finishSelectionAction}
        />
      )}
      {workspaceId && selectionList.length > 0 && (
        <CaptionEditor
          key={`bulk-captions-${selectionList.map((asset) => asset.id).join("-")}`}
          open={selectionAction === "captions"}
          workspaceId={workspaceId}
          targets={selectionTargets}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setSelectionAction(null)}
          onQueued={finishSelectionAction}
        />
      )}
      {workspaceId && selectionList.length > 0 && (
        <BulkVoiceEditor
          key={`bulk-voice-${selectionList.map((asset) => asset.id).join("-")}`}
          open={selectionAction === "voiceover"}
          workspaceId={workspaceId}
          targets={selectionTargets}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setSelectionAction(null)}
          onQueued={finishSelectionAction}
        />
      )}
      {workspaceId && selected && (
        // One target, but the batch dialog: it already knows which modes this
        // media supports, which providers are ready, and what to do about one
        // that is not. A second single-asset dialog beside it would be the
        // same decisions written twice, and drifting from the first.
        <BatchTranscribe
          key={`transcribe-${selected.id}`}
          open={transcribeOpen}
          workspaceId={workspaceId}
          targets={[{
            id: selected.id,
            title: selected.title,
            mediaKind: selected.media_kind,
          }]}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setTranscribeOpen(false)}
          onQueued={(text) => {
            setMessage(text);
            setTranscribeOpen(false);
          }}
        />
      )}
      {workspaceId && selected && (
        <CaptionEditor
          open={captionsOpen}
          workspaceId={workspaceId}
          assetId={selected.id}
          assetTitle={selected.title}
          hasAudio={selected.has_audio}
          canEdit={canImport}
          apiFetch={apiFetch}
          onTranscribed={() => void refresh()}
          onClose={() => setCaptionsOpen(false)}
        />
      )}
      {workspaceId && selected && (
        <VoiceEditor
          open={voiceOpen}
          workspaceId={workspaceId}
          assetId={selected.id}
          assetTitle={selected.title}
          mediaKind={selected.media_kind}
          canEdit={canImport}
          apiFetch={apiFetch}
          onClose={() => setVoiceOpen(false)}
        />
      )}
      {workspaceId && (
        <CampaignPicker
          open={campaignPickerFor.length > 0}
          workspaceId={workspaceId}
          assets={campaignPickerFor}
          apiFetch={apiFetch}
          onClose={() => setCampaignPickerFor([])}
          onAdded={(text) => { succeed(text); setSelection(new Set()); }}
        />
      )}
      {workspaceId && selected && (
        <TranscriptReader
          open={readingDraft !== null}
          transcript={readingDraft}
          workspaceId={workspaceId}
          assetId={selected.id}
          apiFetch={apiFetch}
          onClose={() => setReadingDraft(null)}
          onUse={(text) => {
            // Whichever field this reading is a candidate for. The reader does
            // not know about the form; it hands back words and closes.
            const field = readingDraft?.kind === "ocr" ? ocrField : speechField;
            if (field.current) field.current.value = text;
            setReadingDraft(null);
          }}
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

export default function LibraryPage() {
  const t = useT();
  return (
    <Suspense fallback={<WaitingScreen className="library-page" message={t("library.opening")} />}>
      <LibraryContent />
    </Suspense>
  );
}
