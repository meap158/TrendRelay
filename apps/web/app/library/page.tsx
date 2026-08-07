"use client";

import { Check, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
import { useAuth } from "../auth-provider";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { Button, buttonClass } from "../ui/button";
import { StatusToasts, useStatus } from "../ui/status";
import { Badge } from "../ui/primitives";
import { BlurSettings } from "./blur-settings";
import { ClipEditor } from "./clip-editor";
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

type Version = { kind: "original" | "proxy" | "thumbnail" | "audio" | "blurred"; path: string; size_bytes: number };
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
  transcription: { reviewed_import: boolean; automatic_provider: string | null; reason: string };
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
}: {
  asset: Asset;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
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
      {asset.media_kind === "video" && <span className="library-play-indicator" aria-hidden="true">▶</span>}
      {asset.media_kind === "video" && <span className="library-duration-badge">{displayDuration(asset.duration_ms)}</span>}
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
  videoPosition,
  videoTotal,
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
  videoPosition: number;
  videoTotal: number;
  hasPreviousVideo: boolean;
  hasNextVideo: boolean;
  autoStart: boolean;
  onPlaybackChange: (playing: boolean) => void;
  onPreviousVideo: () => void;
  onNextVideo: () => void;
}) {
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [requested, setRequested] = useState(autoStart);
  // A blurred cut is watched in the same player as the original, so the two are
  // compared in place rather than in a second, smaller video somewhere else.
  const [cut, setCut] = useState<"original" | "blurred">("original");
  const videoRef = useRef<HTMLVideoElement>(null);
  const navigatingRef = useRef(false);
  const blurred = asset.versions.find((version) => version.kind === "blurred") ?? null;

  useEffect(() => {
    if (asset.media_kind !== "video" || !requested) return;
    let active = true;
    let objectUrl = "";
    const controller = new AbortController();
    const wanted = cut === "blurred" && blurred ? "blurred" : "original";
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
        if (active) setError(reason instanceof Error ? reason.message : "Video preview unavailable");
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, asset.id, asset.media_kind, blurred, cut, requested, workspaceId]);

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
    if (asset.media_kind !== "video") return;
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
      if (event.code === "Space") {
        event.preventDefault();
        togglePlayback();
      }
    }
    window.addEventListener("keydown", navigateWithKeyboard);
    return () => window.removeEventListener("keydown", navigateWithKeyboard);
  });

  if (asset.media_kind !== "video") return null;
  return (
    <article className="library-preview-card">
      <div className="library-preview-stage">
        {!requested ? (
          <button type="button" className="library-preview-launch" onClick={startPlayback}>
            <Thumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
            <span className="library-preview-launch-overlay">
              <span className="library-preview-launch-icon" aria-hidden="true">▶</span>
              <strong>Play video preview</strong>
              <small>Loaded privately only when you choose to play it</small>
            </span>
          </button>
        ) : source ? (
          <video
            ref={videoRef}
            aria-label={`Preview ${asset.title}`}
            controls
            autoPlay
            playsInline
            preload="metadata"
            src={source}
            onPlay={() => onPlaybackChange(true)}
            onPause={() => { if (!navigatingRef.current) onPlaybackChange(false); }}
            onEnded={() => onPlaybackChange(false)}
          />
        ) : <p>{error || "Loading video preview…"}</p>}
      </div>
      {blurred && (
        <div className="library-cut-switch" role="group" aria-label="Which cut to play">
          {(["original", "blurred"] as const).map((option) => (
            <button
              key={option}
              type="button"
              className={cut === option ? "selected" : ""}
              aria-pressed={cut === option}
              onClick={() => { setError(""); setSource(""); setCut(option); setRequested(true); }}
            >{option === "original" ? "Original" : "Faces blurred"}</button>
          ))}
        </div>
      )}
      <nav className="library-preview-navigation" aria-label="Browse video previews">
        <button type="button" disabled={!hasPreviousVideo} onClick={() => navigateVideo(onPreviousVideo)} aria-label="Previous video" title="Previous video (Left arrow)">
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m12.5 4.5-5.5 5.5 5.5 5.5" /></svg>
        </button>
        <span>{videoPosition} of {videoTotal} videos <small>← → navigate · Space play/pause</small></span>
        <button type="button" disabled={!hasNextVideo} onClick={() => navigateVideo(onNextVideo)} aria-label="Next video" title="Next video (Right arrow)">
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
const isPadding = (value: unknown): value is number =>
  typeof value === "number" && value >= 0 && value <= 0.4;

export default function LibraryPage() {
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
  const [viewMode, setViewMode] = usePersistedState<ViewMode>(
    "trendrelay.library.view", "gallery", isViewMode,
  );
  const [continueVideoPlayback, setContinueVideoPlayback] = useState(false);
  const autoSyncedWorkspaces = useRef(new Set<string>());
  const [busy, setBusy] = useState("");
  const [blurResult, setBlurResult] = useState<{
    status: string;
    output?: string;
    coverage?: number;
    faces_tracked?: number;
    warning?: string | null;
    preview?: boolean;
    version_registered?: boolean;
    version_note?: string;
    error?: string | null;
  } | null>(null);
  // Errors are reported over the page: in flow they shifted everything below
  // them whenever an action finished. The bulk-action outcome below is not a
  // banner — it reads back inline where the run was started — so it stays put.
  const { messages: statusMessages, fail, dismiss } = useStatus();
  const [message, setMessage] = useState("");
  const [selection, setSelection] = useState<Set<string>>(new Set());
  /** Anchor for shift-click range selection. */
  const [lastPicked, setLastPicked] = useState<string | null>(null);
  const [bulkActions, setBulkActions] = useState<BulkAction[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [blurSettingsOpen, setBlurSettingsOpen] = useState(false);
  /** How far past the detected face the blur reaches, kept between sessions. */
  const [blurPadding, setBlurPadding] = usePersistedState(
    "trendrelay.library.blurPadding", 0.08, isPadding,
  );

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
  const videoAssets = assets.filter((asset) => asset.media_kind === "video");
  const selectedVideoIndex = videoAssets.findIndex((asset) => asset.id === selectedId);
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

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const params = filterParams();
    params.set("sort", sortOrder);
    params.set("limit", "100");
    const suffix = `?${params}`;
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
  }, [apiFetch, filterParams, sortOrder, workspaceId]);

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
    return (
      <button className={`${selectedId === asset.id ? "selected" : ""}${asset.versions.some((version) => version.kind === "blurred") ? " has-versions" : ""}${selection.has(asset.id) ? " picked" : ""}`} key={asset.id} aria-label={`Open ${asset.title}`} aria-pressed={selectedId === asset.id} onClick={() => setSelectedId(asset.id)}>
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
        <Thumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
        <span>
          <strong>{asset.title}</strong>
          <small>{asset.creator ? `${asset.creator} · ` : ""}{asset.platform ?? asset.source_type} · {displayDuration(asset.duration_ms)} · {displaySize(asset.size_bytes)}</small>
          {asset.versions.some((version) => version.kind === "blurred") && (
            <em className="blurred-tag" title="A blurred cut exists and is what handoffs send">
              Faces blurred
            </em>
          )}
        </span>
      </button>
    );
  }

  function chooseView(nextView: ViewMode) {
    setViewMode(nextView);
  }

  useEffect(() => {
    // Downloads links a rendered artifact here by path; select it once the
    // list has loaded so the asset opens rather than the library's default.
    queueMicrotask(() => {
      const wanted = new URLSearchParams(window.location.search).get("asset");
      if (!wanted) return;
      const match = assets.find((asset) => asset.original_path === wanted);
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
  }, [refresh, workspaceId, fail]);

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


  function blurredVersion(asset: Asset) {
    // Latest wins when a clip was re-blurred with different settings.
    const blurred = asset.versions.filter((version) => version.kind === "blurred");
    return blurred.length ? blurred[blurred.length - 1] : null;
  }

  function handoffPath(asset: Asset) {
    return blurredVersion(asset)?.path ?? asset.original_path;
  }

  async function blurFaces(asset: Asset) {
    // A preview is cheap and reversible, so it runs on one click. The full
    // render replaces what Publish sends, so that one still asks.
    if (!window.confirm(
      `Blur every detected face in "${asset.title}"?\n\n`
      + "This renders a new file. The original is not modified, but Campaigns and "
      + "Publish will use the blurred version.",
    )) return;
    setBusy("blur");
    fail("");
    setBlurResult(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/face-blur/jobs`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source_path: asset.original_path,
            padding_ratio: blurPadding,
            confirm_external_action: true,
          }),
        },
      );
      const payload = (await response.json()) as { detail?: string; job?: { id: string } };
      if (!response.ok || !payload.job) {
        throw new Error(payload.detail ?? "Face blurring could not start.");
      }
      await followBlurJob(payload.job.id);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Face blurring failed.");
    } finally {
      setBusy("");
    }
  }

  async function followBlurJob(jobId: string) {
    // The render finishes on the worker, so the panel follows it rather than
    // making the operator reload to find out what happened.
    const deadline = Date.now() + 15 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/face-blur/status`,
      );
      if (!response.ok) continue;
      const payload = (await response.json()) as {
        jobs?: Array<{ id: string; status: string; error?: string | null; result?: Record<string, unknown> | null }>;
      };
      const job = payload.jobs?.find((item) => item.id === jobId);
      if (!job || job.status === "queued" || job.status === "running") continue;
      setBlurResult({
        status: job.status,
        error: job.error,
        ...(job.result ?? {}),
      } as typeof blurResult);
      if (job.status === "succeeded" && (job.result as { version_registered?: boolean } | null)?.version_registered) {
        // The asset gained a version; reload so the detail and handoffs see it.
        await refresh();
      }
      return;
    }
    fail("Still rendering. Reopen this asset shortly to see the result.");
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

  if (loading) return <main className="library-page"><p>Opening media library…</p></main>;
  if (!user) return <main className="library-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Flibrary">Sign in to open Library</Link></main>;

  return (
    <main className="library-page">
      <WorkspaceSectionNav area="library" />
      <div className="page-sticky-shell library-sticky-header">
        <header className="library-heading">
          <div>
            <p className="section-kicker">Creative intelligence</p>
            <h1>
              Media Library
              <span className="library-status-dots">
                <span
                  className={`library-status-dot ${status?.runtime.local_derivatives ? "ready" : "setup"}`}
                  role="img"
                  tabIndex={0}
                  aria-label={`Media processing: ${status?.runtime.local_derivatives ? "ready" : "setup required"}`}
                >
                  <span className="library-status-tooltip" aria-hidden="true">
                    Media processing: {status?.runtime.local_derivatives ? "ready" : "setup required"}
                  </span>
                </span>
                <span
                  className="library-status-dot setup"
                  role="img"
                  tabIndex={0}
                  aria-label={`Transcription: reviewed text import. ${status?.transcription.reason ?? ""}`}
                >
                  <span className="library-status-tooltip" aria-hidden="true">
                    Transcription: reviewed text import
                    {status?.transcription.reason
                      ? <span className="library-status-reason">{status.transcription.reason}</span>
                      : null}
                  </span>
                </span>
              </span>
            </h1>
            <p>Keep originals immutable and turn reference clips into searchable creative recipes.</p>
          </div>
          <label>Workspace
            <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
              {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
            </select>
          </label>
        </header>
      </div>

      {blurResult?.status === "failed" && (
        <p className="error-banner" role="alert">
          Blurring failed. {blurResult.error}
        </p>
      )}
      <section className="library-layout">
        <aside className="library-browser">
          <div className="library-browser-toolbar">
          <form className="library-search" onSubmit={(event) => { event.preventDefault(); void refresh(); }}>
            <input aria-label="Search library" value={query} onChange={(event) => patchFilters({ query: event.target.value })} placeholder="Search titles, hooks, transcripts, or creators…" />
            <Button type="submit">Search</Button>
          </form>

          <nav className="library-category-bar" aria-label="Media categories">
            <div className="library-category-tabs">
              <button type="button" className={!mediaKind ? "selected" : ""} aria-pressed={!mediaKind} onClick={() => patchFilters({ mediaKind: "" })}>All <span>{mediaTotal}</span></button>
              <button type="button" className={mediaKind === "video" ? "selected" : ""} aria-pressed={mediaKind === "video"} onClick={() => patchFilters({ mediaKind: "video" })}>Videos <span>{mediaCount("video")}</span></button>
              <button type="button" className={mediaKind === "image" ? "selected" : ""} aria-pressed={mediaKind === "image"} onClick={() => patchFilters({ mediaKind: "image" })}>Images <span>{mediaCount("image")}</span></button>
              <button type="button" className={mediaKind === "audio" ? "selected" : ""} aria-pressed={mediaKind === "audio"} onClick={() => patchFilters({ mediaKind: "audio" })}>Audio <span>{mediaCount("audio")}</span></button>
            </div>
            <label>Sort
              <select aria-label="Sort media" value={sortOrder} onChange={(event) => { if (isSortOrder(event.target.value)) setSortOrder(event.target.value); }}>
                <option value="newest">Newest</option>
                <option value="oldest">Oldest</option>
                <option value="title">Title</option>
                <option value="duration">Longest</option>
              </select>
            </label>
          </nav>

          <AssetFilters
            values={filters}
            facets={facets}
            fields={["channel", "platform", "effect"]}
            onChange={setFilters}
          >
            <label>Group
              <select aria-label="Group library" value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)}>
                <option value="none">No grouping</option>
                <option value="channel">Channel</option>
                <option value="source">Source</option>
              </select>
            </label>
          </AssetFilters>
          <div className="library-collection-toolbar">
            <strong>{total} {total === 1 ? "item" : "items"}</strong>
            <div className="library-collection-actions">
              {canImport && <Button variant="quiet" size="sm" busy={busy === "sync"} onClick={() => void syncDownloads()}>{busy === "sync" ? "Refreshing" : "Refresh downloads"}</Button>}
              <div className="library-view-switcher" role="group" aria-label="Library view">
                <button type="button" className={viewMode === "gallery" ? "selected" : ""} aria-label="Gallery view" title="Gallery view" aria-pressed={viewMode === "gallery"} onClick={() => chooseView("gallery")}><span aria-hidden="true">▦</span></button>
                <button type="button" className={viewMode === "list" ? "selected" : ""} aria-label="List view" title="List view" aria-pressed={viewMode === "list"} onClick={() => chooseView("list")}><span aria-hidden="true">☷</span></button>
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
                <Badge tone="good">every match selected</Badge>
              )}
              {selection.size > 0 && (
                <>
                  <Button variant="quiet" size="sm" onClick={() => setSelection(new Set())}>
                    Clear
                  </Button>
                  <span className="library-selection-tools">
                    {bulkActions.map((action) => (
                      <Button
                        key={action.id}
                        variant={action.id === "delete" ? "danger" : "secondary"}
                        size="sm"
                        busy={busy === `bulk-${action.id}`}
                        disabled={!canImport || !action.available}
                        title={action.available ? action.description : action.reason ?? undefined}
                        onClick={() => void runBulkAction(action)}
                      >{action.label}</Button>
                    ))}
                  </span>
                  {selection.size > Math.min(...bulkActions.map((a) => a.max_batch), Infinity) && (
                    <Badge tone="neutral">runs in batches</Badge>
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
            {!assets.length && <p>No matching media yet.</p>}
          </div>
          {canImport && (
            <details className="library-import">
              <summary>Import a local file</summary>
              <form onSubmit={importMedia}>
                <label>File path<input name="path" required placeholder="S:\Media\clip.mp4" /></label>
                <label>Title<input name="title" required /></label>
                <div className="library-form-row">
                  <label>Platform<input name="platform" placeholder="douyin" /></label>
                  <label>Creator<input name="creator" /></label>
                  <label>Published at<input name="published_at" type="datetime-local" /></label>
                </div>
                <label>Source URL<input name="source_url" type="url" /></label>
                <label>Caption<textarea name="caption" rows={2} /></label>
                <label>Hashtags<input name="hashtags" placeholder="coffee, travel" /></label>
                <div className="library-form-row">
                  <label>Likes<input name="likes" type="number" min={0} /></label>
                  <label>Comments<input name="comments" type="number" min={0} /></label>
                  <label>Shares<input name="shares" type="number" min={0} /></label>
                </div>
                <Button type="submit" variant="primary" busy={busy === "import"}>{busy === "import" ? "Queuing" : "Import safely"}</Button>
              </form>
            </details>
          )}

          {!!jobs.length && (
            <div className="library-jobs">
              <strong>Recent ingestion</strong>
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
                videoPosition={selectedVideoIndex + 1}
                videoTotal={videoAssets.length}
                hasPreviousVideo={selectedVideoIndex > 0}
                hasNextVideo={selectedVideoIndex >= 0 && selectedVideoIndex < videoAssets.length - 1}
                autoStart={continueVideoPlayback}
                onPlaybackChange={setContinueVideoPlayback}
                onPreviousVideo={() => setSelectedId(videoAssets[selectedVideoIndex - 1]?.id ?? selected.id)}
                onNextVideo={() => setSelectedId(videoAssets[selectedVideoIndex + 1]?.id ?? selected.id)}
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
                    {blurredVersion(selected) && (
                      <em className="blurred-tag" title={`Handoffs send this cut: ${handoffPath(selected)}`}>
                        Faces blurred
                      </em>
                    )}
                  </p>
                  <h2>{selected.title}</h2>
                  <p>{selected.caption || "No source caption recorded."}</p>
                  {/* The pager shares the metadata line rather than taking a
                      row of its own; the clip and its details are what deserve
                      the vertical space. */}
                  <div className="library-meta-line">
                    <small>{selected.width && selected.height ? `${selected.width}×${selected.height} · ` : ""}{displaySize(selected.size_bytes)}</small>
                    <nav className="library-item-navigation" aria-label="Browse media">
                      <Button
                        variant="quiet"
                        size="sm"
                        iconOnly
                        aria-label="Previous item"
                        title="Previous item"
                        disabled={selectedIndex <= 0}
                        onClick={() => setSelectedId(assets[selectedIndex - 1]?.id ?? selectedId)}
                      >‹</Button>
                      <span>{selectedIndex + 1} of {assets.length}</span>
                      <Button
                        variant="quiet"
                        size="sm"
                        iconOnly
                        aria-label="Next item"
                        title="Next item"
                        disabled={selectedIndex < 0 || selectedIndex >= assets.length - 1}
                        onClick={() => setSelectedId(assets[selectedIndex + 1]?.id ?? selectedId)}
                      >›</Button>
                    </nav>
                  </div>
                </div>
                <div className="library-actions">
                  <Button
                    variant="secondary"
                    busy={busy === "folder"}
                    onClick={() => void openAssetFolder(selected)}
                  >{busy === "folder" ? "Opening" : "Open folder"}</Button>
                  <Button
                    variant="secondary"
                    busy={busy === "blur"}
                    disabled={busy.startsWith("blur") || selected.media_kind !== "video"}
                    title={selected.media_kind === "video"
                      ? "Detect every face and burn the blur into a new render"
                      : "Face blurring applies to video"}
                    onClick={() => void blurFaces(selected)}
                  >{busy === "blur" ? "Blurring" : "Blur faces"}</Button>
                  <Button
                    variant="secondary"
                    iconOnly
                    aria-label="Blur settings"
                    title="Check coverage on one frame and set how wide the blur sits"
                    disabled={selected.media_kind !== "video"}
                    onClick={() => setBlurSettingsOpen(true)}
                  ><SlidersHorizontal size={15} strokeWidth={2} /></Button>
                  <Button
                    variant="secondary"
                    disabled={selected.media_kind !== "video"}
                    title={selected.media_kind === "video"
                      ? "Build and render a clip plan from this video"
                      : "Clip plans apply to video"}
                    onClick={() => setEditorOpen(true)}
                  >Advanced editor</Button>
                  {deleteAction && (
                    <Button
                      variant="danger"
                      busy={busy === `bulk-${deleteAction.id}`}
                      disabled={!canImport}
                      title={deleteAction.description}
                      onClick={() => void runBulkAction(deleteAction, [selected.id])}
                    >Delete</Button>
                  )}
                  <Link href={`/campaigns?video=${encodeURIComponent(handoffPath(selected))}`}>Plan campaign</Link>
                  <Link href={`/publish?video=${encodeURIComponent(handoffPath(selected))}`}>Prepare to publish</Link>
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
                </div>
              </article>

              <div className="library-detail-grid">
                <article>
                  <h3>Creative recipe</h3>
                  {selected.analysis ? (
                    <dl className="recipe-grid">
                      <div><dt>Spoken hook</dt><dd>{selected.analysis.spoken_hook || "—"}</dd></div>
                      <div><dt>Text hook</dt><dd>{selected.analysis.text_hook || "—"}</dd></div>
                      <div><dt>CTA</dt><dd>{selected.analysis.call_to_action || "—"}</dd></div>
                      <div><dt>Product</dt><dd>{selected.analysis.product_shown || "—"}</dd></div>
                      <div><dt>Format</dt><dd>{selected.analysis.creative_format || "—"}</dd></div>
                      <div><dt>Editing</dt><dd>{selected.analysis.shot_count ? `${selected.analysis.shot_count} shots · ${selected.analysis.average_shot_ms}ms average` : "—"}</dd></div>
                      <div><dt>Structure</dt><dd>{selected.analysis.structure_tags.join(", ") || "—"}</dd></div>
                      <div><dt>Keywords</dt><dd>{selected.analysis.keywords.join(", ") || "—"}</dd></div>
                    </dl>
                  ) : <p>No recipe yet. Add reviewed speech or on-screen text below.</p>}
                </article>
              </div>

              {canEnrich && (
                <article className="library-enrichment">
                  <div>
                    <h3>Reviewed transcript and analysis</h3>
                    <p>Paste reviewed speech and on-screen text. TrendRelay derives a searchable, versioned recipe without claiming machine output was human-reviewed.</p>
                  </div>
                  <form onSubmit={enrich}>
                    <div className="library-form-row">
                      <label>Language<input name="language" defaultValue="und" /></label>
                      <label>Product shown<input name="product_shown" defaultValue={selected.analysis?.product_shown ?? ""} /></label>
                      <label>Creative format<input name="creative_format" defaultValue={selected.analysis?.creative_format ?? ""} placeholder="faceless demo" /></label>
                    </div>
                    <label>Reviewed speech<textarea name="speech_text" rows={5} defaultValue={selected.transcripts.find((item) => item.kind === "speech")?.text ?? ""} /></label>
                    <label>Reviewed on-screen text<textarea name="ocr_text" rows={4} defaultValue={selected.transcripts.find((item) => item.kind === "ocr")?.text ?? ""} /></label>
                    <div className="library-form-row">
                      <label>Scene cuts (ms)<input name="scene_boundaries_ms" placeholder="1200, 2800, 5100" /></label>
                      <label>Product reveal (ms)<input name="product_reveal_ms" type="number" min={0} defaultValue={selected.analysis?.product_reveal_ms ?? ""} /></label>
                      <label>Emotional angle<input name="emotional_angle" /></label>
                    </div>
                    <label>Analyst notes<textarea name="analyst_notes" rows={3} defaultValue={selected.analysis?.analyst_notes ?? ""} /></label>
                    <Button type="submit" variant="primary" busy={busy === "enrich"}>{busy === "enrich" ? "Analyzing" : "Save and derive recipe"}</Button>
                  </form>
                </article>
              )}
            </>
          ) : <article className="library-summary"><p>Select an asset or import a local file to begin.</p></article>}
        </section>
      </section>
      {workspaceId && selected && (
        <BlurSettings
          open={blurSettingsOpen}
          workspaceId={workspaceId}
          path={selected.original_path}
          padding={blurPadding}
          onPadding={setBlurPadding}
          busy={busy === "blur"}
          apiFetch={apiFetch}
          onClose={() => setBlurSettingsOpen(false)}
          onBlur={() => { setBlurSettingsOpen(false); void blurFaces(selected); }}
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
