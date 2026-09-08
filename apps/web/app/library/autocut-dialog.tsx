"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { ActionIcon } from "../ui/action-icons";
import { AssetFilters } from "../ui/asset-filters";
import { useLibraryAssets } from "../../lib/use-library-assets";
import { AssetThumbnail } from "../publish/composer";
import type { LibraryAsset } from "../publish/composer";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

//: How this operator likes their AutoCuts presented - shape, fill and speed.
//  Remembered across opens the way an editor remembers your export settings;
//  the template and music are matched to the actual clips, so they are not.
const PREFS_KEY = "trendrelay.autocut.prefs";

//: The most clips one AutoCut takes - the API caps a plan at forty, past which
//  it is a different kind of video, so the modal never lets the set grow beyond
//  what a render would accept.
const MAX_CLIPS = 40;

function readPrefs(): { aspect?: string; fill?: string; speed?: number; captionPos?: string } {
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};  // no storage, a private window, or malformed - just use defaults
  }
}

/** Just what the timeline needs of a selected clip - a superset of what the
    Library and Publish both hold, so either can pass its own asset rows. */
export type AutoCutAsset = {
  id: string;
  title: string;
  media_kind: string;
  original_path: string;
  duration_ms?: number | null;
  width?: number | null;
  height?: number | null;
  versions: { kind: string }[];
};

type TemplateView = {
  id: string;
  name: string;
  description: string;
  mood: string;
  transition: string;
  music: string;
  designed_bpm: number;
  ideal_pictures: [number, number];
  music_available: boolean;
  match?: number;
  /** The steady cadence in beats-per-cut, for the rhythm hint. */
  cadence: number[];
};

/**
 * A small strip that shows a template's rhythm without rendering it.
 *
 * The chooser used to describe each cadence in words - "two beats a picture",
 * "one-beat doubles on the drops" - which a reader has to translate into a
 * feel. This plays it instead: one segment per beat-cut in the steady cadence,
 * sized to its length, with a playhead sweeping across at the template's own
 * tempo, so it crosses a cut boundary exactly when a cut would land. The sweep
 * is paused in CSS until the template is chosen or hovered - and by a reduced-
 * motion preference always - so the list stays calm and cheap.
 */
function RhythmHint({ cadence, bpm }: { cadence: number[]; bpm: number }) {
  const beats = cadence.length ? cadence : [2];
  // One full pass through the cadence, at the real beat period.
  const seconds = (beats.reduce((sum, b) => sum + b, 0) * 60) / (bpm > 0 ? bpm : 100);
  return (
    <span className="autocut-rhythm" aria-hidden="true">
      <span className="autocut-rhythm-track">
        {beats.map((b, index) => (
          <span key={index} className="autocut-rhythm-cell" style={{ flexGrow: b }} />
        ))}
      </span>
      <span className="autocut-rhythm-head" style={{ animationDuration: `${seconds.toFixed(2)}s` }} />
    </span>
  );
}

type PlanShot = { asset_id: string; start: number; end: number };
type PlanView = {
  template: TemplateView;
  plan: { duration: number; bpm: number; beat_synced: boolean; shots: PlanShot[] };
  bpm: number;
  beat_synced: boolean;
  music_available: boolean;
  picture_count: number;
  asset_ids: string[];
};

/**
 * Turn the selected photos and videos into a beat-synced video.
 *
 * The CapCut photo-template flow, adapted to the app and grown to fit this
 * goal: a left column carries the templates (auto-matched, overridable),
 * music (bundled with the template, swappable), a speed dial, and a
 * drag-to-reorder timeline of the chosen clips - stills and videos mixed, in
 * whatever order the operator arranges. The right column is the preview,
 * built on open and redrawn live as any of that changes, so the video is
 * always in view rather than imagined. The full render lands in the Library.
 */
export function AutoCutDialog({
  open,
  workspaceId,
  apiFetch,
  assets,
  onClose,
  onQueued,
  onError,
}: {
  open: boolean;
  workspaceId: string;
  apiFetch: Fetcher;
  /** The chosen visuals - images and videos - in their initial order. */
  assets: AutoCutAsset[];
  onClose: () => void;
  onQueued: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [templates, setTemplates] = useState<TemplateView[]>([]);
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [music, setMusic] = useState<string | null>(null);
  const [speed, setSpeed] = useState(1);
  const [order, setOrder] = useState<string[]>([]);
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [planning, setPlanning] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [previewState, setPreviewState] = useState<"idle" | "building" | "ready" | "error">("idle");
  // Two video layers, double-buffered: a new clip loads into the hidden slot
  // and is cross-faded to only once it has decoded, so the shown frame never
  // blanks to black on a redraw. `visible` is which slot is on top.
  const [slots, setSlots] = useState<[string | null, string | null]>([null, null]);
  const [visible, setVisible] = useState<0 | 1>(0);
  const hasPreview = slots[0] !== null || slots[1] !== null;
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const [aspect, setAspect] = useState<"portrait" | "square" | "landscape">("portrait");
  const [fill, setFill] = useState<"cover" | "blur">("cover");
  const [caption, setCaption] = useState("");
  const [captionPos, setCaptionPos] = useState<"top" | "bottom">("bottom");
  const [title, setTitle] = useState("");
  // Clips pulled from the Library inside the modal, on top of the set the
  // dialog opened on. Cleared whenever it opens on a different selection.
  const [added, setAdded] = useState<AutoCutAsset[]>([]);
  const [browsing, setBrowsing] = useState(false);
  // The saved draft this arrangement belongs to, once saved - so a re-save
  // updates it rather than making a second one - and the list to resume from.
  const [draftId, setDraftId] = useState<string | null>(null);
  const [savedDrafts, setSavedDrafts] = useState<
    { id: string; title: string; status: string; updated_at: string | null; summary: { clips: number } }[]
  >([]);
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);

  const base = `/api/workspaces/${workspaceId}/autocut`;
  const creationsBase = `/api/workspaces/${workspaceId}/creations`;
  // The opening selection plus anything added since, the added ones that are
  // already in the base set dropped so a picture cannot be listed twice.
  const assetById = useMemo(() => {
    const map = new Map(assets.map((asset) => [asset.id, asset]));
    for (const asset of added) if (!map.has(asset.id)) map.set(asset.id, asset);
    return map;
  }, [assets, added]);

  // The order and preview track the incoming selection - both reset when the
  // dialog opens on a different set, the order left alone otherwise so a drag
  // is not undone. Done during render, not in an effect: React's own way to
  // reset state on a prop change, and it clears the last set's preview before
  // a frame paints, so reopening on new media never flashes the old clip or
  // its "updating" badge.
  const selectionKey = assets.map((asset) => asset.id).join(",");
  const [orderKey, setOrderKey] = useState(selectionKey);
  // Bumped on every build start and on every reset. A build in flight when a
  // newer one begins, or when the selection changes, sees the number has moved
  // and drops its result rather than painting a stale clip over a fresh one -
  // which is what let a slow older redraw flash in after a newer one.
  const previewSeq = useRef(0);
  // Read by the async build to pick the hidden slot, and to free the layers on
  // unmount, without either becoming a render dependency.
  const visibleRef = useRef<0 | 1>(0);
  visibleRef.current = visible;
  const slotsRef = useRef<[string | null, string | null]>([null, null]);
  slotsRef.current = slots;
  if (orderKey !== selectionKey) {
    setOrderKey(selectionKey);
    setOrder(selectionKey ? selectionKey.split(",") : []);
    setAdded([]);
    setBrowsing(false);
    setDraftId(null);
    setDraftsOpen(false);
    setSlots([null, null]);
    setVisible(0);
    setPreviewState("idle");
    setPlan(null);
    previewSeq.current += 1;
  }

  // Restore the operator's saved presentation choices when the dialog opens.
  // Done on the open transition during render - like the selection reset
  // above - so it sets no state in an effect and never mismatches the
  // server's closed first render, which reads no storage at all.
  const [wasOpen, setWasOpen] = useState(false);
  if (open && !wasOpen) {
    setWasOpen(true);
    const prefs = readPrefs();
    if (prefs.aspect === "portrait" || prefs.aspect === "square" || prefs.aspect === "landscape") {
      setAspect(prefs.aspect);
    }
    if (prefs.fill === "cover" || prefs.fill === "blur") setFill(prefs.fill);
    if (typeof prefs.speed === "number" && prefs.speed >= 0.5 && prefs.speed <= 2) {
      setSpeed(prefs.speed);
    }
    if (prefs.captionPos === "top" || prefs.captionPos === "bottom") setCaptionPos(prefs.captionPos);
  } else if (!open && wasOpen) {
    setWasOpen(false);
  }

  // Persist those choices as they change. Only while open, so the closed
  // mount's defaults never clobber what a past session saved; no setState, so
  // it is an effect the lint rule is happy with.
  useEffect(() => {
    if (!open) return;
    try {
      window.localStorage.setItem(PREFS_KEY, JSON.stringify({ aspect, fill, speed, captionPos }));
    } catch {
      /* storage unavailable - the choices simply do not carry over */
    }
  }, [open, aspect, fill, speed, captionPos]);

  // Free a preview blob once it leaves both slots - displaced by a newer clip
  // or cleared on a reset. Compared against the previous slots so a URL still
  // shown (or waiting hidden) is never revoked, only one nothing points at any
  // more. Never touches the visible layer, so the picture never drops out.
  const prevSlots = useRef<[string | null, string | null]>([null, null]);
  useEffect(() => {
    for (const url of prevSlots.current) {
      if (url && url !== slots[0] && url !== slots[1]) URL.revokeObjectURL(url);
    }
    prevSlots.current = slots;
  }, [slots]);
  // The last blobs outstanding when the dialog unmounts.
  useEffect(() => () => { for (const url of slotsRef.current) if (url) URL.revokeObjectURL(url); }, []);

  // The shown layer carries the sound; the hidden one is muted so a cross-fade
  // never plays two tracks at once. Set on the element itself - React's `muted`
  // prop does not reliably reach the DOM property.
  const layer0 = useRef<HTMLVideoElement>(null);
  const layer1 = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (layer0.current) layer0.current.muted = visible !== 0;
    if (layer1.current) layer1.current.muted = visible !== 1;
    // Nudge the shown layer to keep playing after it unmutes, in case the
    // browser would otherwise pause a track that began muted. Best-effort.
    const front = visible === 0 ? layer0.current : layer1.current;
    front?.play?.().catch(() => {});
  }, [visible, slots]);

  // Templates, ranked for this many clips, with the best pre-selected.
  useEffect(() => {
    if (!open || !order.length) return;
    let live = true;
    void apiFetch(`${base}/templates?pictures=${order.length}`)
      .then((res) => res.json())
      .then((body: { templates: TemplateView[]; recommended: string }) => {
        if (!live) return;
        setTemplates(body.templates);
        setTemplateId((current) => current ?? body.recommended);
      })
      .catch(() => onError("Could not load AutoCut templates."));
    return () => { live = false; };
  }, [open, order.length, apiFetch, base, onError]);

  const planKey = `${templateId}:${music}:${speed}:${order.join(",")}`;

  // The plan preview, always what a render would produce right now.
  useEffect(() => {
    if (!open || !templateId || !order.length) return;
    let live = true;
    const timer = setTimeout(() => setPlanning(true), 0);
    void apiFetch(`${base}/plan`, {
      method: "POST",
      body: JSON.stringify({ asset_ids: order, template_id: templateId, music, speed }),
    })
      .then((res) => res.json().then((body) => ({ ok: res.ok, body })))
      .then(({ ok, body }) => {
        if (!live) return;
        if (!ok) throw new Error(body?.detail ?? "Could not build the plan.");
        setPlan(body as PlanView);
      })
      .catch((reason) => { if (live) onError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (live) setPlanning(false); });
    return () => { live = false; clearTimeout(timer); };
  }, [open, planKey, templateId, music, speed, order, apiFetch, base, onError]);

  const chosen = useMemo(
    () => templates.find((template) => template.id === templateId) ?? null,
    [templates, templateId],
  );

  const buildPreview = useCallback(async () => {
    if (!templateId || !order.length) return;
    const seq = ++previewSeq.current;
    const stale = () => seq !== previewSeq.current;
    setPreviewState("building");
    try {
      // The caption is not burned into the preview: it rides as a live HTML
      // overlay instead (see the pane below), so editing the hook is instant -
      // a keystroke restyles a div rather than re-rendering the whole montage.
      // The full render still burns it into the file; the preview only stands
      // in for it.
      const res = await apiFetch(`${base}/preview`, {
        method: "POST",
        body: JSON.stringify({
          asset_ids: order, template_id: templateId, music, speed, aspect, fill,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? "Could not start the preview.");
      const jobId = body.id as string;
      const deadline = Date.now() + 120_000;
      // Poll tight at first - an in-process preview is often ready inside a
      // second or two - then ease off so a slow one does not hammer the API.
      let wait = 250;
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, wait));
        wait = Math.min(wait + 150, 1000);
        if (Date.now() > deadline) throw new Error("The preview took too long. Try again, or render it.");
        if (stale()) return;
        const status = await apiFetch(`${base}/jobs/${jobId}`).then((r) => r.json());
        if (status.status === "failed") throw new Error(status.error ?? "The preview could not be drawn.");
        if (status.ready) break;
      }
      const clip = await apiFetch(`${base}/preview/${jobId}/video`);
      if (!clip.ok) throw new Error("The preview clip could not be loaded.");
      const blob = await clip.blob();
      if (stale()) return;  // a newer build took over while this drew
      const url = URL.createObjectURL(blob);
      // Into the hidden slot. Its <video> loads it and, on its first decoded
      // frame, cross-fades itself in (see onLoadedData) - so the shown clip
      // stays put until the new one is actually ready to paint. The displaced
      // blob is freed by the slots effect, never here.
      const target = (visibleRef.current ^ 1) as 0 | 1;
      setSlots((current) => {
        const next: [string | null, string | null] = [...current];
        next[target] = url;
        return next;
      });
      setPreviewState("ready");
    } catch (reason) {
      if (stale()) return;
      setPreviewState("error");
      onError(reason instanceof Error ? reason.message : String(reason));
    }
    // Caption and its position are deliberately absent: they change only the
    // HTML overlay, never the rendered montage, so they must not rebuild it.
  }, [templateId, order, music, speed, aspect, fill, apiFetch, base, onError]);

  // Preview on by default: it builds when the dialog opens and redraws
  // (debounced) whenever the template, music, speed or order changes - so the
  // right pane always shows the current arrangement without a button press.
  useEffect(() => {
    if (!open || !templateId || !order.length) return;
    const timer = setTimeout(() => void buildPreview(), 350);
    return () => clearTimeout(timer);
  }, [open, buildPreview, templateId, order.length]);

  const render = useCallback(async () => {
    if (!templateId) return;
    setRendering(true);
    try {
      const res = await apiFetch(`${base}/render`, {
        method: "POST",
        body: JSON.stringify({
          asset_ids: order, template_id: templateId, music, speed, aspect, fill,
          caption: caption.trim(), caption_position: captionPos,
          title: title.trim() || undefined,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? "Could not queue the render.");
      onQueued("AutoCut is rendering in the background - it lands in your Library, and progress is in the bell.");
      onClose();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRendering(false);
    }
  }, [templateId, order, music, speed, aspect, fill, caption, captionPos, title, apiFetch, base, onQueued, onError, onClose]);

  // The current arrangement as an AutoCut draft spec.
  const draftSpec = useCallback(() => ({
    asset_ids: order, template_id: templateId, music, speed, aspect, fill,
    caption: caption.trim(), caption_position: captionPos,
  }), [order, templateId, music, speed, aspect, fill, caption, captionPos]);

  // Save (or re-save) this arrangement as a resumable draft, so closing the
  // dialog no longer loses it. A first save creates; later saves update the
  // same draft rather than piling up copies.
  const saveDraft = useCallback(async () => {
    if (!order.length) return;
    setSavingDraft(true);
    try {
      const path = draftId ? `${creationsBase}/${draftId}` : creationsBase;
      const res = await apiFetch(path, {
        method: draftId ? "PATCH" : "POST",
        body: JSON.stringify(
          draftId
            ? { title: title.trim() || undefined, spec: draftSpec() }
            : { kind: "autocut", title: title.trim() || undefined, spec: draftSpec() },
        ),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? "Could not save the draft.");
      setDraftId(body.id as string);
      // Show it in the resume list at once, newest first, without a refetch.
      setSavedDrafts((current) => [
        { id: body.id, title: body.title, status: body.status,
          updated_at: body.updated_at, summary: body.summary },
        ...current.filter((saved) => saved.id !== body.id),
      ]);
      onQueued("Draft saved - reopen it any time from Saved drafts.");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSavingDraft(false);
    }
  }, [order.length, draftId, creationsBase, apiFetch, title, draftSpec, onQueued, onError]);

  // Reopen a saved draft: pull its spec and its media, and set the whole
  // arrangement from it. The media is fetched by id so a draft resumes even
  // when the dialog was opened on a different selection.
  const resumeDraft = useCallback(async (id: string) => {
    try {
      const res = await apiFetch(`${creationsBase}/${id}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? "Could not open the draft.");
      const spec = body.spec ?? {};
      const ids: string[] = spec.asset_ids ?? [];
      const libraryIds = ids.filter((assetId) => !assetId.startsWith("draft:"));
      let rows: AutoCutAsset[] = [];
      if (libraryIds.length) {
        const assetsRes = await apiFetch(
          `/api/workspaces/${workspaceId}/media/library/assets?asset_ids=${libraryIds.join(",")}`,
        );
        const assetsBody = await assetsRes.json();
        rows = (assetsBody.assets ?? []).map((asset: AutoCutAsset & { versions?: { kind: string }[] }) => ({
          id: asset.id, title: asset.title, media_kind: asset.media_kind,
          original_path: asset.original_path, duration_ms: asset.duration_ms ?? null,
          width: asset.width ?? null, height: asset.height ?? null,
          versions: (asset.versions ?? []).map((v) => ({ kind: v.kind })),
        }));
      }
      const present = new Set(rows.map((row) => row.id));
      setAdded(rows);
      setOrder(ids.filter((assetId) => present.has(assetId)));
      setTemplateId(spec.template_id ?? null);
      setMusic(spec.music ?? null);
      setSpeed(typeof spec.speed === "number" ? spec.speed : 1);
      setAspect(spec.aspect ?? "portrait");
      setFill(spec.fill ?? "cover");
      setCaption(spec.caption ?? "");
      setCaptionPos(spec.caption_position ?? "bottom");
      setTitle(body.title ?? "");
      setDraftId(id);
      setDraftsOpen(false);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [creationsBase, apiFetch, workspaceId, onError]);

  // Offer the saved drafts to resume whenever the dialog is open. Inline fetch
  // (setState in the async callback, guarded by `live`) like the other reads
  // here, so no state is set directly in an effect body.
  useEffect(() => {
    if (!open) return;
    let live = true;
    void apiFetch(`${creationsBase}?kind=autocut&limit=50`)
      .then((res) => res.json())
      .then((body) => { if (live && Array.isArray(body.items)) setSavedDrafts(body.items); })
      .catch(() => { /* a drafts list that will not load is not worth an error */ });
    return () => { live = false; };
  }, [open, apiFetch, creationsBase]);

  // Drag-to-reorder: the dragged clip drops before the one it is released on,
  // moving it in the order the plan and preview read from.
  const moveClip = useCallback((from: number, to: number) => {
    if (from === to) return;
    setOrder((current) => {
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }, []);

  // Drop a clip from the sequence without leaving the modal - the other half
  // of arranging by hand. Kept from emptying the timeline: one clip is the
  // floor a render still has something to draw from.
  const removeClip = useCallback((assetId: string) => {
    setOrder((current) => (current.length > 1 ? current.filter((id) => id !== assetId) : current));
  }, []);

  // Browse the workspace's photos and videos to add to the sequence, through
  // the shared library loop every other picker reads with (ADR 0025) - it owns
  // the debounce, paging, facets and stale-response guard. Audio is dropped on
  // arrival: AutoCut cuts visuals, and a sound file has nothing to show.
  const library = useLibraryAssets<LibraryAsset>({
    workspaceId,
    apiFetch,
    enabled: open && browsing,
    keep: (asset) => asset.media_kind === "image" || asset.media_kind === "video",
  });

  // Append a picked clip to the end of the sequence. Guarded against a
  // duplicate and against passing the render's clip ceiling, and it keeps its
  // own row so the timeline can draw the thumbnail the same as the rest.
  const full = order.length >= MAX_CLIPS;
  const addClip = useCallback((asset: LibraryAsset) => {
    setOrder((current) => {
      if (current.includes(asset.id) || current.length >= MAX_CLIPS) return current;
      setAdded((rows) => (rows.some((row) => row.id === asset.id) ? rows : [...rows, {
        id: asset.id,
        title: asset.title,
        media_kind: asset.media_kind,
        original_path: asset.original_path,
        duration_ms: asset.duration_ms,
        width: asset.width,
        height: asset.height,
        versions: asset.versions.map((version) => ({ kind: version.kind })),
      }]));
      return [...current, asset.id];
    });
  }, []);

  // Each clip's time on screen, from the plan, so the timeline reads like an
  // editor track - a hold in seconds under every thumbnail.
  const shotDurations = useMemo(() => {
    const map = new Map<string, number>();
    for (const shot of plan?.plan.shots ?? []) {
      map.set(shot.asset_id, Math.max(0, shot.end - shot.start));
    }
    return map;
  }, [plan]);

  return (
    <Dialog
      open={open}
      size="wide"
      title={`AutoCut ${order.length} into a video`}
      onClose={onClose}
    >
      <div className="autocut-layout">
        <div className="autocut-left">
          <div className="autocut-templates" role="radiogroup" aria-label="Template">
            {templates.map((template) => (
              <button
                key={template.id}
                type="button"
                role="radio"
                aria-checked={template.id === templateId}
                className={`autocut-template${template.id === templateId ? " selected" : ""}`}
                onClick={() => { setTemplateId(template.id); setMusic(null); }}
              >
                <strong>{template.name}
                  {template.match === 1 && <em className="autocut-best">best fit</em>}
                </strong>
                <small>{template.description}</small>
                <RhythmHint cadence={template.cadence} bpm={template.designed_bpm} />
                <span className="autocut-template-meta">
                  {template.transition} · {template.designed_bpm} BPM
                  {!template.music_available && <em title="No music file for this template yet"> · silent</em>}
                </span>
              </button>
            ))}
          </div>

          <div className="autocut-controls">
            <label>
              <span>Music</span>
              <span className="autocut-music-row">
                <span>{music ?? chosen?.music ?? "—"}</span>
                {music
                  ? <Button variant="quiet" size="sm" onClick={() => setMusic(null)}>Use template default</Button>
                  : <small>travels with the template</small>}
              </span>
            </label>
            <label>
              <span>Speed <b>{speed.toFixed(2)}×</b></span>
              <input
                type="range" min={0.5} max={2} step={0.25}
                value={speed}
                onChange={(event) => setSpeed(Number(event.target.value))}
              />
            </label>
            <label>
              <span>Shape</span>
              <span className="autocut-aspect" role="radiogroup" aria-label="Video shape">
                {([
                  ["portrait", "9:16"],
                  ["square", "1:1"],
                  ["landscape", "16:9"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={aspect === value}
                    className={aspect === value ? "selected" : ""}
                    onClick={() => setAspect(value)}
                  >{label}</button>
                ))}
              </span>
            </label>
            <label>
              <span>Off-ratio media</span>
              <span className="autocut-aspect" role="radiogroup" aria-label="How off-ratio media fills the frame">
                {([
                  ["cover", "Crop to fill"],
                  ["blur", "Fit · blur bg"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={fill === value}
                    className={fill === value ? "selected" : ""}
                    onClick={() => setFill(value)}
                  >{label}</button>
                ))}
              </span>
            </label>
            <label className="autocut-field-wide">
              <span>Caption <small>optional hook, burned on</small></span>
              <input
                type="text"
                className="autocut-title"
                value={caption}
                placeholder="Wait for the end…"
                maxLength={120}
                onChange={(event) => setCaption(event.target.value)}
              />
            </label>
            {caption.trim() && (
              <label>
                <span>Caption place</span>
                <span className="autocut-aspect" role="radiogroup" aria-label="Caption position">
                  {([
                    ["top", "Top"],
                    ["bottom", "Bottom"],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={captionPos === value}
                      className={captionPos === value ? "selected" : ""}
                      onClick={() => setCaptionPos(value)}
                    >{label}</button>
                  ))}
                </span>
              </label>
            )}
            <label>
              <span>Name <small>optional</small></span>
              <input
                type="text"
                className="autocut-title"
                value={title}
                placeholder={chosen ? `AutoCut - ${chosen.name}` : "AutoCut"}
                maxLength={200}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
          </div>

          {/* The timeline: the chosen clips in order, dragged to rearrange
              and dropped to remove. A number, a hold in seconds, and a
              video marker per clip, so the sequence reads like an editor
              track - and a line marks where a dragged clip will land. */}
          <div className="autocut-timeline" aria-label="Clip order">
            {order.map((assetId, index) => {
              const asset = assetById.get(assetId);
              if (!asset) return null;
              const hold = shotDurations.get(assetId);
              return (
                <div
                  key={assetId}
                  className={[
                    "autocut-clip",
                    dragIndex === index ? "dragging" : "",
                    dropIndex === index && dragIndex !== index ? "drop-target" : "",
                  ].filter(Boolean).join(" ")}
                  draggable
                  onDragStart={() => setDragIndex(index)}
                  onDragEnd={() => { setDragIndex(null); setDropIndex(null); }}
                  onDragOver={(event) => { event.preventDefault(); setDropIndex(index); }}
                  onDrop={(event) => {
                    event.preventDefault();
                    if (dragIndex !== null) moveClip(dragIndex, index);
                    setDragIndex(null);
                    setDropIndex(null);
                  }}
                  title={asset.title}
                >
                  <span className="autocut-clip-index">{index + 1}</span>
                  {order.length > 1 && (
                    <button
                      type="button"
                      className="autocut-clip-remove"
                      aria-label={`Remove ${asset.title}`}
                      title="Remove from this video"
                      // Draggable ancestors swallow a plain click on some
                      // browsers; pointer-down fires it reliably.
                      onPointerDown={(event) => { event.stopPropagation(); removeClip(assetId); }}
                    >×</button>
                  )}
                  <AssetThumbnail
                    asset={{
                      id: asset.id,
                      title: asset.title,
                      media_kind: asset.media_kind,
                      original_path: asset.original_path,
                      platform: null,
                      creator: null,
                      duration_ms: asset.duration_ms ?? null,
                      width: asset.width ?? null,
                      height: asset.height ?? null,
                      versions: asset.versions.map((v) => ({ id: `${asset.id}-${v.kind}`, kind: v.kind })),
                    }}
                    workspaceId={workspaceId}
                    apiFetch={apiFetch}
                  />
                  {asset.media_kind === "video" && (
                    <span className="autocut-clip-kind" aria-label="Video">▶</span>
                  )}
                  {hold !== undefined && <span className="autocut-clip-hold">{hold.toFixed(1)}s</span>}
                </div>
              );
            })}
          </div>

          {/* Add more clips without leaving the modal - the set is no longer
              fixed at what the Library selection opened it on. The shared
              library browser folds in under a toggle, so it never competes
              with the timeline above it. */}
          <div className="autocut-add">
            <div className="autocut-add-row">
              <Button
                variant="secondary"
                size="sm"
                aria-expanded={browsing}
                disabled={full && !browsing}
                onClick={() => setBrowsing((current) => !current)}
              >{browsing ? "Done adding" : "Add clips from Library"}</Button>
              {full && <small className="autocut-note">Forty clips is the most one AutoCut takes.</small>}
            </div>
            {browsing && (
              <div className="autocut-library">
                <AssetFilters
                  values={library.filters}
                  facets={library.facets}
                  fields={["query", "channel", "platform", "downloaded"]}
                  cleared={{}}
                  onChange={(next) => library.setFilters(next)}
                />
                {library.failure && <p className="console-error" role="alert">{library.failure}</p>}
                {library.loading === "list" && !library.assets.length ? (
                  <p className="autocut-note">Loading…</p>
                ) : library.assets.length ? (
                  <>
                    <div className="autocut-library-grid">
                      {library.assets.map((asset) => {
                        const inUse = order.includes(asset.id);
                        return (
                          <button
                            key={asset.id}
                            type="button"
                            className={`autocut-library-tile${inUse ? " is-chosen" : ""}`}
                            disabled={inUse || full}
                            title={inUse ? "Already in this video" : asset.title}
                            onClick={() => addClip(asset)}
                          >
                            <AssetThumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
                            <span>{asset.title}</span>
                            {inUse && <em className="autocut-in-use">in video</em>}
                          </button>
                        );
                      })}
                    </div>
                    {library.canLoadMore && (
                      <Button
                        variant="secondary"
                        size="sm"
                        busy={Boolean(library.loading)}
                        onClick={() => library.loadMore()}
                      >Load more</Button>
                    )}
                  </>
                ) : (
                  <p className="autocut-note">No photos or videos match.</p>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="autocut-right">
          {/* The preview, on by default and double-buffered: two stacked video
              layers, a redraw loading into the hidden one and cross-fading in
              only once it has decoded, so the shown clip never blanks. The
              placeholder holds until a frame is actually visible, and the
              'updating' badge marks a redraw in flight over the current clip. */}
          <div className="autocut-video" data-aspect={aspect}>
            {!slots[visible] && (
              <span className="autocut-video-building">
                {previewState === "error" ? "Preview failed - adjust and it retries." : "Drawing the preview…"}
              </span>
            )}
            <video
              ref={layer0}
              className={visible === 0 ? "is-shown" : ""}
              src={slots[0] ?? undefined}
              controls autoPlay loop playsInline
              onLoadedData={() => setVisible(0)}
            />
            <video
              ref={layer1}
              className={visible === 1 ? "is-shown" : ""}
              src={slots[1] ?? undefined}
              controls autoPlay loop playsInline
              onLoadedData={() => setVisible(1)}
            />
            {/* The caption as a live overlay, not burned into the preview -
                so editing it is instant. A stand-in for the burn the full
                render does: same weight, outline and placement, close enough
                to judge the hook by. */}
            {caption.trim() && slots[visible] && (
              <div className={`autocut-caption autocut-caption-${captionPos}`} aria-hidden="true">
                <span>{caption.trim()}</span>
              </div>
            )}
            {previewState === "building" && slots[visible] && (
              <span className="autocut-video-updating">Updating…</span>
            )}
          </div>
          <div className="autocut-preview" aria-live="polite">
            {planning && <small>Building the plan…</small>}
            {plan && !planning && (
              <>
                <span><b>{plan.plan.shots.length}</b> cuts · <b>{plan.plan.duration.toFixed(1)}s</b></span>
                <span className={plan.beat_synced ? "autocut-synced" : "autocut-unsynced"}>
                  {plan.beat_synced
                    ? `beat-synced · ${Math.round(plan.bpm)} BPM`
                    : plan.music_available ? "spaced to tempo" : "no music - even spacing"}
                </span>
              </>
            )}
          </div>
          {/* Save the arrangement as a resumable draft, and reopen a saved one.
              So closing the dialog no longer loses the work, and an AutoCut
              begun in the app or by an assistant can be carried on. */}
          <div className="autocut-drafts-bar">
            {savedDrafts.length > 0 && (
              <Button
                variant="quiet" size="sm" aria-expanded={draftsOpen}
                onClick={() => setDraftsOpen((current) => !current)}
              >{draftsOpen ? "Hide saved" : `Saved drafts · ${savedDrafts.length}`}</Button>
            )}
            <Button
              variant="secondary" size="sm" busy={savingDraft}
              disabled={savingDraft || !order.length}
              onClick={() => void saveDraft()}
            >{draftId ? "Update draft" : "Save draft"}</Button>
          </div>
          {draftsOpen && savedDrafts.length > 0 && (
            <div className="autocut-drafts-list" role="listbox" aria-label="Saved drafts">
              {savedDrafts.map((saved) => (
                <button
                  key={saved.id}
                  type="button"
                  role="option"
                  aria-selected={saved.id === draftId}
                  className={`autocut-draft-row${saved.id === draftId ? " is-current" : ""}`}
                  onClick={() => void resumeDraft(saved.id)}
                >
                  <span className="autocut-draft-title">{saved.title}</span>
                  <span className="autocut-draft-meta">
                    {saved.summary?.clips ?? 0} clips · {saved.status}
                  </span>
                </button>
              ))}
            </div>
          )}
          <div className="autocut-actions">
            <Button variant="quiet" onClick={onClose} disabled={rendering}>Cancel</Button>
            <Button
              variant="primary"
              busy={rendering}
              disabled={rendering || planning || !plan || plan.plan.shots.length === 0}
              onClick={() => void render()}
            ><ActionIcon name="play" />Make the video</Button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
