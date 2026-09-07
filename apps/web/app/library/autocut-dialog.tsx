"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { ActionIcon } from "../ui/action-icons";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

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

type PlanShot = { asset_id: string; start: number; end: number; transition: string };
type PlanView = {
  template: TemplateView;
  plan: { duration: number; bpm: number; beat_synced: boolean; shots: PlanShot[] };
  bpm: number;
  beat_synced: boolean;
  music_available: boolean;
  picture_count: number;
};

/**
 * Turn the selected pictures into a beat-synced video.
 *
 * The dialog mirrors CapCut's photo-template flow, adapted to this app: the
 * selected images are the material, a template is auto-matched (and
 * overridable), its music travels with it (and is swappable), and a speed
 * dial scales the cadence. A live plan preview shows the shot count and
 * length before anything renders, so the choice is made looking at its
 * result rather than blind. The render itself is queued to the worker and
 * lands in the Library like any other clip.
 */
export function AutoCutDialog({
  open,
  workspaceId,
  apiFetch,
  assetIds,
  onClose,
  onQueued,
  onError,
}: {
  open: boolean;
  workspaceId: string;
  apiFetch: Fetcher;
  assetIds: string[];
  onClose: () => void;
  onQueued: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [templates, setTemplates] = useState<TemplateView[]>([]);
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [music, setMusic] = useState<string | null>(null);
  const [speed, setSpeed] = useState(1);
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [planning, setPlanning] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [previewState, setPreviewState] = useState<"idle" | "building" | "ready" | "error">("idle");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  // Once the operator has asked for a preview, it stays live: changing the
  // template, music or speed redraws it for the new settings rather than
  // leaving a stale clip or a blank panel.
  const previewLive = useRef(false);

  const base = `/api/workspaces/${workspaceId}/autocut`;

  // Cleanup without reading state inside the effect: the ref mirrors the URL,
  // so unmount revokes whatever blob is current without a dependency that
  // would re-run and revoke a live one.
  const previewUrlRef = useRef<string | null>(null);
  useEffect(() => { previewUrlRef.current = previewUrl; }, [previewUrl]);
  useEffect(() => () => { if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current); }, []);

  // The catalogue, ranked for this many pictures, with the best pre-selected -
  // the auto-match the operator can override.
  useEffect(() => {
    if (!open || !assetIds.length) return;
    let live = true;
    void apiFetch(`${base}/templates?pictures=${assetIds.length}`)
      .then((res) => res.json())
      .then((body: { templates: TemplateView[]; recommended: string }) => {
        if (!live) return;
        setTemplates(body.templates);
        setTemplateId((current) => current ?? body.recommended);
      })
      .catch(() => onError("Could not load AutoCut templates."));
    return () => { live = false; };
  }, [open, assetIds.length, apiFetch, base, onError]);

  // A fresh plan whenever the material or the knobs change, so the preview is
  // always what a render would produce right now.
  const planKey = `${templateId}:${music}:${speed}:${assetIds.join(",")}`;
  useEffect(() => {
    if (!open || !templateId || !assetIds.length) return;
    let live = true;
    const timer = setTimeout(() => setPlanning(true), 0);
    void apiFetch(`${base}/plan`, {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds, template_id: templateId, music, speed }),
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
  }, [open, planKey, templateId, music, speed, assetIds, apiFetch, base, onError]);

  const chosen = useMemo(
    () => templates.find((template) => template.id === templateId) ?? null,
    [templates, templateId],
  );

  const buildPreview = useCallback(async () => {
    if (!templateId) return;
    previewLive.current = true;
    setPreviewState("building");
    try {
      const res = await apiFetch(`${base}/preview`, {
        method: "POST",
        body: JSON.stringify({ asset_ids: assetIds, template_id: templateId, music, speed }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? "Could not start the preview.");
      const jobId = body.id as string;
      // Poll until the worker finishes drawing the half-size clip, then fetch
      // its bytes opaquely and play them from a blob - the same private-media
      // path every other preview here uses, never a plain download URL.
      const deadline = Date.now() + 90_000;
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (Date.now() > deadline) throw new Error("The preview took too long. Try again, or render it.");
        const status = await apiFetch(`${base}/jobs/${jobId}`).then((r) => r.json());
        if (status.status === "failed") throw new Error(status.error ?? "The preview could not be drawn.");
        if (status.ready) break;
      }
      const clip = await apiFetch(`${base}/preview/${jobId}/video`);
      if (!clip.ok) throw new Error("The preview clip could not be loaded.");
      const url = URL.createObjectURL(await clip.blob());
      // Swap the blob only when the new one is ready, so the old clip stays
      // on screen while the redraw runs - no flash of empty panel.
      setPreviewUrl((old) => { if (old) URL.revokeObjectURL(old); return url; });
      setPreviewState("ready");
    } catch (reason) {
      setPreviewState("error");
      onError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [templateId, assetIds, music, speed, apiFetch, base, onError]);

  // The live-adjust loop: while a preview is engaged, any settings change
  // (which recreates buildPreview) redraws it - debounced so dragging the
  // speed slider does not fire a render per step.
  useEffect(() => {
    if (!previewLive.current) return;
    const timer = setTimeout(() => void buildPreview(), 500);
    return () => clearTimeout(timer);
  }, [buildPreview]);

  const render = useCallback(async () => {
    if (!templateId) return;
    setRendering(true);
    try {
      const res = await apiFetch(`${base}/render`, {
        method: "POST",
        body: JSON.stringify({ asset_ids: assetIds, template_id: templateId, music, speed }),
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
  }, [templateId, assetIds, music, speed, apiFetch, base, onQueued, onError, onClose]);

  return (
    <Dialog
      open={open}
      title={`AutoCut ${assetIds.length} pictures into a video`}
      onClose={() => { previewLive.current = false; onClose(); }}
    >
      <div className="autocut-dialog">
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

        {/* The result, watched before it is committed. A half-size render of
            the exact plan - so what plays is what a full render draws, only
            fewer pixels - refreshed whenever a setting changes. */}
        {(previewState !== "idle" || previewUrl) && (
          <div className="autocut-video">
            {previewState === "building" && <span className="autocut-video-building">Drawing the preview…</span>}
            {previewUrl && (
              <video src={previewUrl} controls autoPlay loop playsInline muted={false} />
            )}
            {previewState === "building" && previewUrl && (
              <span className="autocut-video-updating">Updating…</span>
            )}
          </div>
        )}

        <div className="autocut-actions">
          <Button variant="quiet" onClick={onClose} disabled={rendering}>Cancel</Button>
          <Button
            variant="secondary"
            busy={previewState === "building"}
            disabled={rendering || planning || !plan || plan.plan.shots.length === 0 || previewState === "building"}
            onClick={() => void buildPreview()}
          ><ActionIcon name="play" />{previewState === "ready" ? "Preview again" : "Preview"}</Button>
          <Button
            variant="primary"
            busy={rendering}
            disabled={rendering || planning || !plan || plan.plan.shots.length === 0}
            onClick={() => void render()}
          ><ActionIcon name="play" />Make the video</Button>
        </div>
      </div>
    </Dialog>
  );
}
