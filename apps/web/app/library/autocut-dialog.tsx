"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

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

  const base = `/api/workspaces/${workspaceId}/autocut`;

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
    <Dialog open={open} title={`AutoCut ${assetIds.length} pictures into a video`} onClose={onClose}>
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
    </Dialog>
  );
}
