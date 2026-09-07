"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { ActionIcon } from "../ui/action-icons";
import { AssetThumbnail } from "../publish/composer";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

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
};

type PlanView = {
  template: TemplateView;
  plan: { duration: number; bpm: number; beat_synced: boolean; shots: { asset_id: string }[] };
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
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const base = `/api/workspaces/${workspaceId}/autocut`;
  const assetById = useMemo(
    () => new Map(assets.map((asset) => [asset.id, asset])),
    [assets],
  );

  // The order tracks the incoming selection - reset when the dialog opens on a
  // different set, but left alone otherwise so a drag is not undone. Done
  // during render, not in an effect: React's own way to reset state on a
  // prop change, without the extra pass an effect would cost.
  const selectionKey = assets.map((asset) => asset.id).join(",");
  const [orderKey, setOrderKey] = useState(selectionKey);
  if (orderKey !== selectionKey) {
    setOrderKey(selectionKey);
    setOrder(selectionKey ? selectionKey.split(",") : []);
  }

  const previewUrlRef = useRef<string | null>(null);
  useEffect(() => { previewUrlRef.current = previewUrl; }, [previewUrl]);
  useEffect(() => () => { if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current); }, []);

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
    setPreviewState("building");
    try {
      const res = await apiFetch(`${base}/preview`, {
        method: "POST",
        body: JSON.stringify({ asset_ids: order, template_id: templateId, music, speed }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? "Could not start the preview.");
      const jobId = body.id as string;
      const deadline = Date.now() + 120_000;
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
        if (Date.now() > deadline) throw new Error("The preview took too long. Try again, or render it.");
        const status = await apiFetch(`${base}/jobs/${jobId}`).then((r) => r.json());
        if (status.status === "failed") throw new Error(status.error ?? "The preview could not be drawn.");
        if (status.ready) break;
      }
      const clip = await apiFetch(`${base}/preview/${jobId}/video`);
      if (!clip.ok) throw new Error("The preview clip could not be loaded.");
      const url = URL.createObjectURL(await clip.blob());
      setPreviewUrl((old) => { if (old) URL.revokeObjectURL(old); return url; });
      setPreviewState("ready");
    } catch (reason) {
      setPreviewState("error");
      onError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [templateId, order, music, speed, apiFetch, base, onError]);

  // Preview on by default: it builds when the dialog opens and redraws
  // (debounced) whenever the template, music, speed or order changes - so the
  // right pane always shows the current arrangement without a button press.
  useEffect(() => {
    if (!open || !templateId || !order.length) return;
    const timer = setTimeout(() => void buildPreview(), 600);
    return () => clearTimeout(timer);
  }, [open, buildPreview, templateId, order.length]);

  const render = useCallback(async () => {
    if (!templateId) return;
    setRendering(true);
    try {
      const res = await apiFetch(`${base}/render`, {
        method: "POST",
        body: JSON.stringify({ asset_ids: order, template_id: templateId, music, speed }),
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
  }, [templateId, order, music, speed, apiFetch, base, onQueued, onError, onClose]);

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

  return (
    <Dialog
      open={open}
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
          </div>

          {/* The timeline: the chosen clips in order, dragged to rearrange.
              A number and a video/photo marker per clip, so the sequence
              reads at a glance the way a track does in an editor. */}
          <div className="autocut-timeline" aria-label="Clip order">
            {order.map((assetId, index) => {
              const asset = assetById.get(assetId);
              if (!asset) return null;
              return (
                <div
                  key={assetId}
                  className={`autocut-clip${dragIndex === index ? " dragging" : ""}`}
                  draggable
                  onDragStart={() => setDragIndex(index)}
                  onDragEnd={() => setDragIndex(null)}
                  onDragOver={(event) => { event.preventDefault(); }}
                  onDrop={(event) => {
                    event.preventDefault();
                    if (dragIndex !== null) moveClip(dragIndex, index);
                    setDragIndex(null);
                  }}
                  title={asset.title}
                >
                  <span className="autocut-clip-index">{index + 1}</span>
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
                </div>
              );
            })}
          </div>
        </div>

        <div className="autocut-right">
          {/* The preview, on by default. The old clip stays on screen while a
              redraw runs, with an 'updating' badge, so a settings change never
              flashes an empty pane. */}
          <div className="autocut-video">
            {previewUrl
              ? <video src={previewUrl} controls autoPlay loop playsInline />
              : <span className="autocut-video-building">
                  {previewState === "error" ? "Preview failed - adjust and it retries." : "Drawing the preview…"}
                </span>}
            {previewState === "building" && previewUrl && (
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
