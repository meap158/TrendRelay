"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { WorkspaceSectionNav } from "../workspace-section-nav";

type Workspace = { id: string; name: string; role: string };
type ViewMode = "gallery" | "list";
type GroupBy = "none" | "channel" | "source" | "rights";
type Facet = { value: string; label: string; count: number };
type LibraryFacets = {
  channels: Facet[];
  platforms: Facet[];
  rights: Facet[];
  media_kinds: Facet[];
};
type Version = { kind: "original" | "proxy" | "thumbnail" | "audio"; path: string; size_bytes: number };
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
  rights_status: string;
  rights_basis?: string | null;
  publishable: boolean;
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
  const videoRef = useRef<HTMLVideoElement>(null);
  const navigatingRef = useRef(false);

  useEffect(() => {
    if (asset.media_kind !== "video" || !requested) return;
    let active = true;
    let objectUrl = "";
    const controller = new AbortController();
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/preview`, {
      method: "POST",
      signal: controller.signal,
    })
      .then((response) => json<{ mime_type: string; content_base64: string }>(response))
      .then((preview) => {
        objectUrl = URL.createObjectURL(previewBlob(preview.content_base64, preview.mime_type));
        if (active) setSource(objectUrl);
      })
      .catch((reason) => {
        if (active && reason instanceof DOMException && reason.name === "AbortError") return;
        if (active) setError(reason instanceof Error ? reason.message : "Video preview unavailable");
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, asset.id, asset.media_kind, requested, workspaceId]);

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
export default function LibraryPage() {
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [query, setQuery] = useState("");
  const [rightsFilter, setRightsFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState("");
  const [platformFilter, setPlatformFilter] = useState("");
  const [mediaKind, setMediaKind] = useState<"" | Asset["media_kind"]>("");
  const [sortOrder, setSortOrder] = useState("newest");
  const [groupBy, setGroupBy] = useState<GroupBy>("none");
  const [facets, setFacets] = useState<LibraryFacets>({ channels: [], platforms: [], rights: [], media_kinds: [] });
  const [total, setTotal] = useState(0);
  const [viewMode, setViewMode] = useState<ViewMode>("gallery");
  const [continueVideoPlayback, setContinueVideoPlayback] = useState(false);
  const autoSyncedWorkspaces = useRef(new Set<string>());
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

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
  const canEnrich = ["owner", "editor", "analyst"].includes(workspace?.role ?? "");
  const canReviewRights = ["owner", "approver"].includes(workspace?.role ?? "");
  const activeFilterCount = [query.trim(), rightsFilter, channelFilter, platformFilter, mediaKind].filter(Boolean).length;
  const mediaTotal = facets.media_kinds.reduce((sum, facet) => sum + facet.count, 0);
  const mediaCount = (kind: Asset["media_kind"]) => facets.media_kinds.find((facet) => facet.value === kind)?.count ?? 0;
  const groupedAssets = groupBy === "none"
    ? []
    : Array.from(assets.reduce((groups, asset) => {
      const label = groupBy === "channel"
        ? asset.creator || "Unassigned channel"
        : groupBy === "source"
          ? asset.platform || asset.source_type || "Other sources"
          : asset.rights_status || "Unknown rights";
      const items = groups.get(label) ?? [];
      items.push(asset);
      groups.set(label, items);
      return groups;
    }, new Map<string, Asset[]>())).sort((left, right) => right[1].length - left[1].length || left[0].localeCompare(right[0]));

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (rightsFilter) params.set("rights_status", rightsFilter);
    if (channelFilter === "__unassigned__") params.set("creator_missing", "true");
    else if (channelFilter) params.set("creator", channelFilter);
    if (platformFilter === "__other__") params.set("platform_missing", "true");
    else if (platformFilter) params.set("platform", platformFilter);
    if (mediaKind) params.set("media_kind", mediaKind);
    params.set("sort", sortOrder);
    params.set("limit", "100");
    const suffix = `?${params}`;
    const [assetBody, jobBody, statusBody] = await Promise.all([
      json<{ assets: Asset[]; total?: number; facets?: LibraryFacets }>(
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
  }, [apiFetch, channelFilter, mediaKind, platformFilter, query, rightsFilter, sortOrder, workspaceId]);

  function clearFilters() {
    setQuery("");
    setRightsFilter("");
    setChannelFilter("");
    setPlatformFilter("");
    setMediaKind("");
  }

  function groupTotal(label: string, loadedCount: number) {
    const source = groupBy === "channel"
      ? facets.channels
      : groupBy === "source"
        ? facets.platforms
        : facets.rights;
    return source.find((facet) => facet.label === label)?.count ?? loadedCount;
  }

  function renderAsset(asset: Asset) {
    return (
      <button className={selectedId === asset.id ? "selected" : ""} key={asset.id} aria-label={`Open ${asset.title}`} aria-pressed={selectedId === asset.id} onClick={() => setSelectedId(asset.id)}>
        <Thumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
        <span>
          <strong>{asset.title}</strong>
          <small>{asset.creator ? `${asset.creator} · ` : ""}{asset.platform ?? asset.source_type} · {displayDuration(asset.duration_ms)} · {displaySize(asset.size_bytes)}</small>
          <em className={`rights-badge ${asset.publishable ? "publishable" : "reference"}`}>{asset.rights_status}</em>
        </span>
      </button>
    );
  }

  function chooseView(nextView: ViewMode) {
    setViewMode(nextView);
  }

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
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Workspaces unavailable."));
    return () => { cancelled = true; };
  }, [apiFetch, user]);

  useEffect(() => {
    if (!workspaceId) return;
    queueMicrotask(() => {
      void refresh(workspaceId).catch((reason) =>
        setError(reason instanceof Error ? reason.message : "Library unavailable."),
      );
    });
  }, [refresh, workspaceId]);

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
            setError(`${body.sync.errors.length} downloaded media items could not be prepared.`);
          }
          await refresh(workspaceId);
        } catch (reason) {
          autoSyncedWorkspaces.current.delete(workspaceId);
          setError(reason instanceof Error ? reason.message : "Downloaded media could not be synchronized.");
        }
      })();
    });
  }, [apiFetch, canImport, refresh, workspaceId]);

  useEffect(() => {
    if (!jobs.some((job) => ["queued", "running"].includes(job.status))) return;
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 2500);
    return () => window.clearInterval(timer);
  }, [jobs, refresh]);

  async function syncDownloads() {
    setBusy("sync");
    setError("");
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
      if (body.sync.errors.length) setError(`${body.sync.errors.length} media items could not be queued.`);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Downloaded media could not be added.");
    } finally {
      setBusy("");
    }
  }

  async function importMedia(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("import");
    setError("");
    setMessage("");
    const form = new FormData(event.currentTarget);
    const rightsStatus = String(form.get("rights_status"));
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
            rights_status: rightsStatus,
            rights_basis: form.get("rights_basis") || null,
            confirm_external_action: true,
          }),
        }),
      );
      setMessage(body.job.asset_id ? "That file is already safely stored." : "Import queued. Derivatives will appear automatically.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import failed.");
    } finally {
      setBusy("");
    }
  }

  async function enrich(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy("enrich");
    setError("");
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
      setError(reason instanceof Error ? reason.message : "Enrichment failed.");
    } finally {
      setBusy("");
    }
  }

  async function updateRights(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    const nextStatus = String(form.get("rights_status"));
    if (!window.confirm(`Record this asset as “${nextStatus}”?`)) return;
    setBusy("rights");
    setError("");
    try {
      const body = await json<{ asset: Asset }>(
        await apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${selected.id}/rights`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            rights_status: nextStatus,
            rights_basis: form.get("rights_basis"),
            confirm_external_action: true,
          }),
        }),
      );
      setAssets((current) => current.map((asset) => asset.id === body.asset.id ? body.asset : asset));
      setMessage("Rights record updated and audited.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Rights update failed.");
    } finally {
      setBusy("");
    }
  }

  if (loading) return <main className="library-page"><p>Opening media library…</p></main>;
  if (!user) return <main className="library-page"><Link className="primary-link" href="/sign-in?next=%2Flibrary">Sign in to open Library</Link></main>;

  return (
    <main className="library-page">
      <WorkspaceSectionNav area="library" />
      <div className="page-sticky-shell library-sticky-header">
        <header className="library-heading">
          <div>
            <p className="section-kicker">Creative intelligence</p>
            <h1>Media Library</h1>
            <p>Keep originals immutable, review usage rights, and turn reference clips into searchable creative recipes.</p>
          </div>
          <label>Workspace
            <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
              {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
            </select>
          </label>
        </header>
      </div>

      {error && <p className="error-banner">{error}</p>}
      {message && <p className="campaign-message">{message}</p>}

      <section className="library-status">
        <span className={status?.runtime.local_derivatives ? "ready" : "warning"}>
          Media processing: {status?.runtime.local_derivatives ? "ready" : "setup required"}
        </span>
        <span className="warning">
          Transcription: reviewed text import
        </span>
        <small>{status?.transcription.reason}</small>
      </section>

      <section className="library-layout">
        <aside className="library-browser">
          <div className="library-browser-toolbar">
          <form className="library-search" onSubmit={(event) => { event.preventDefault(); void refresh(); }}>
            <input aria-label="Search library" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search titles, hooks, transcripts, or creators…" />
            <button>Search</button>
          </form>

          <nav className="library-category-bar" aria-label="Media categories">
            <div className="library-category-tabs">
              <button type="button" className={!mediaKind ? "selected" : ""} aria-pressed={!mediaKind} onClick={() => setMediaKind("")}>All <span>{mediaTotal}</span></button>
              <button type="button" className={mediaKind === "video" ? "selected" : ""} aria-pressed={mediaKind === "video"} onClick={() => setMediaKind("video")}>Videos <span>{mediaCount("video")}</span></button>
              <button type="button" className={mediaKind === "image" ? "selected" : ""} aria-pressed={mediaKind === "image"} onClick={() => setMediaKind("image")}>Images <span>{mediaCount("image")}</span></button>
              <button type="button" className={mediaKind === "audio" ? "selected" : ""} aria-pressed={mediaKind === "audio"} onClick={() => setMediaKind("audio")}>Audio <span>{mediaCount("audio")}</span></button>
            </div>
            <label>Sort
              <select aria-label="Sort media" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)}>
                <option value="newest">Newest</option>
                <option value="oldest">Oldest</option>
                <option value="title">Title</option>
                <option value="duration">Longest</option>
              </select>
            </label>
          </nav>

          <div className="library-facet-row" aria-label="Library categories">
            <label>Channel
              <select aria-label="Filter by channel" value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
                <option value="">All channels</option>
                {facets.channels.map((facet) => <option key={facet.value || "__unassigned__"} value={facet.value || "__unassigned__"}>{facet.label} ({facet.count})</option>)}
              </select>
            </label>
            <label>Source
              <select aria-label="Filter by source" value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value)}>
                <option value="">All sources</option>
                {facets.platforms.map((facet) => <option key={facet.value || "__other__"} value={facet.value || "__other__"}>{facet.label} ({facet.count})</option>)}
              </select>
            </label>
            <label>Usage rights
              <select aria-label="Filter by usage rights" value={rightsFilter} onChange={(event) => setRightsFilter(event.target.value)}>
                <option value="">All rights</option>
                {facets.rights.map((facet) => <option key={facet.value} value={facet.value}>{facet.label} ({facet.count})</option>)}
              </select>
            </label>
            <label>Group
              <select aria-label="Group library" value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)}>
                <option value="none">No grouping</option>
                <option value="channel">Channel</option>
                <option value="source">Source</option>
                <option value="rights">Usage rights</option>
              </select>
            </label>
            {activeFilterCount > 0 && <button type="button" className="library-clear-filters" onClick={clearFilters}>Clear {activeFilterCount}</button>}
          </div>
          <div className="library-collection-toolbar">
            <strong>{total} {total === 1 ? "item" : "items"}</strong>
            <div className="library-collection-actions">
              {canImport && <button type="button" className="library-sync-button" disabled={busy === "sync"} onClick={() => void syncDownloads()}>{busy === "sync" ? "Refreshing…" : "Refresh downloads"}</button>}
              <div className="library-view-switcher" role="group" aria-label="Library view">
                <button type="button" className={viewMode === "gallery" ? "selected" : ""} aria-label="Gallery view" title="Gallery view" aria-pressed={viewMode === "gallery"} onClick={() => chooseView("gallery")}><span aria-hidden="true">▦</span></button>
                <button type="button" className={viewMode === "list" ? "selected" : ""} aria-label="List view" title="List view" aria-pressed={viewMode === "list"} onClick={() => chooseView("list")}><span aria-hidden="true">☷</span></button>
              </div>
            </div>
          </div>
          </div>

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
                <label>Usage rights
                  <select name="rights_status" defaultValue="unknown">
                    <option value="unknown">Unknown</option>
                    <option value="reference-only">Reference only</option>
                    <option value="owned">Owned</option>
                    <option value="licensed">Licensed</option>
                    <option value="public-domain">Public domain</option>
                    <option value="prohibited">Prohibited</option>
                  </select>
                </label>
                <label>Rights evidence<textarea name="rights_basis" rows={2} placeholder="Required before publishable use" /></label>
                <button className="primary-button" disabled={busy === "import"}>{busy === "import" ? "Queuing…" : "Import safely"}</button>
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
                  </p>
                  <h2>{selected.title}</h2>
                  <p>{selected.caption || "No source caption recorded."}</p>
                  <small>{selected.width && selected.height ? `${selected.width}×${selected.height} · ` : ""}{displaySize(selected.size_bytes)}</small>
                </div>
                <div className="library-actions">
                  <nav className="library-item-navigation" aria-label="Browse media">
                    <button type="button" disabled={selectedIndex <= 0} onClick={() => setSelectedId(assets[selectedIndex - 1]?.id ?? selectedId)}>← Previous</button>
                    <span>{selectedIndex + 1} of {assets.length}</span>
                    <button type="button" disabled={selectedIndex < 0 || selectedIndex >= assets.length - 1} onClick={() => setSelectedId(assets[selectedIndex + 1]?.id ?? selectedId)}>Next →</button>
                  </nav>
                  {selected.publishable && (
                    <>
                      <Link className="primary-action" href={`/studio?source=${encodeURIComponent(selected.original_path)}`}>Auto-edit in Studio</Link>
                      <Link href={`/campaigns?video=${encodeURIComponent(selected.original_path)}`}>Plan campaign</Link>
                      <Link href={`/publish?video=${encodeURIComponent(selected.original_path)}`}>Prepare to publish</Link>
                    </>
                  )}
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

                <article>
                  <h3>Rights</h3>
                  <p>{selected.rights_basis || "No evidence recorded."}</p>
                  {canReviewRights && (
                    <form className="library-compact-form" onSubmit={updateRights}>
                      <label>Status<select name="rights_status" defaultValue={selected.rights_status}>
                        <option value="unknown">Unknown</option>
                        <option value="reference-only">Reference only</option>
                        <option value="owned">Owned</option>
                        <option value="licensed">Licensed</option>
                        <option value="public-domain">Public domain</option>
                        <option value="prohibited">Prohibited</option>
                      </select></label>
                      <label>Evidence<textarea name="rights_basis" required minLength={3} defaultValue={selected.rights_basis ?? ""} rows={3} /></label>
                      <button disabled={busy === "rights"}>{busy === "rights" ? "Saving…" : "Review rights"}</button>
                    </form>
                  )}
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
                    <button className="primary-button" disabled={busy === "enrich"}>{busy === "enrich" ? "Analyzing…" : "Save and derive recipe"}</button>
                  </form>
                </article>
              )}
            </>
          ) : <article className="library-summary"><p>Select an asset or import a local file to begin.</p></article>}
        </section>
      </section>
    </main>
  );
}
