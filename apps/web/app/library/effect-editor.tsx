"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ActionIcon } from "../ui/action-icons";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { GalleryChoiceButton, GalleryPanel } from "./gallery-picker";
import { ParamControl, defaultsFor, galleryParam } from "./effect-params";
import type { EffectDefinition } from "./effect-params";
import { useT } from "../i18n-provider";
import { useJobs } from "../jobs-provider";
import {
  effectLabel,
  effectSummary,
  paramHelp,
  paramLabel,
} from "../../lib/i18n/effects";


/**
 * The editing suite: a stack of effects applied to one asset.
 *
 * Every control here is generated from what the API declares — the effects, their
 * settings, the range each one accepts. Nothing about flip or speed or blur is
 * written into this file, so an effect added to the registry appears here with a
 * working form and needs no frontend change at all. That was the point of
 * declaring effects rather than building them.
 *
 * The edit is a recipe rather than a render. Saving keeps the steps and costs
 * nothing; rendering is the expensive part and stays a separate, deliberate act.
 */

export type { EffectDefinition };

type Step = { effect: string; values: Record<string, unknown> };
type PreviewJob = {
  id: string;
  status: string;
  progress?: number | null;
  progress_stage?: string | null;
  error?: string | null;
};

export function EffectEditor({
  open,
  workspaceId,
  assetId,
  assetPath,
  mediaKind,
  onClose,
  onRendered,
  apiFetch,
  canEdit,
}: {
  open: boolean;
  workspaceId: string;
  assetId: string;
  assetPath: string;
  mediaKind: string;
  onClose: () => void;
  onRendered: (message: string) => void;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  canEdit: boolean;
}) {
  const t = useT();
  // The drawer polls every few seconds; a render that has just been asked for
  // should be in it before the operator has finished reading the toast.
  const { refresh: refreshJobs } = useJobs();
  const [effects, setEffects] = useState<EffectDefinition[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  const [saved, setSaved] = useState(true);
  const [previewJob, setPreviewJob] = useState<PreviewJob | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const previewUrlRef = useRef("");
  /** Which step has its gallery open, if any. */
  const [picking, setPicking] = useState<number | null>(null);

  const base = `/api/workspaces/${workspaceId}/media/library`;

  const replacePreviewUrl = useCallback((next: string) => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = next;
    setPreviewUrl(next);
  }, []);

  useEffect(() => () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);

  const load = useCallback(async () => {
    setFailure("");
    try {
      const [catalogue, recipe] = await Promise.all([
        apiFetch(`${base}/effects`).then((response) => response.json()),
        apiFetch(`${base}/assets/${assetId}/recipe`).then((response) => response.json()),
      ]);
      setEffects(catalogue.effects ?? []);
      setSteps(recipe.steps ?? []);
      setSaved(true);
    } catch {
      setFailure("The effects could not be loaded.");
    }
  }, [apiFetch, assetId, base]);

  /**
   * Re-read the catalogue without touching the recipe being edited.
   *
   * Used when a folder an effect draws its options from has gained one. The
   * full load would also refetch the stored recipe and throw away unsaved
   * changes, which is not what adding a picture asked for.
   */
  const reloadEffects = useCallback(async () => {
    try {
      const catalogue = await apiFetch(`${base}/effects`).then((response) => response.json());
      setEffects(catalogue.effects ?? []);
    } catch {
      setFailure("The effects could not be reloaded.");
    }
  }, [apiFetch, base]);

  useEffect(() => {
    if (!open) return;
    setPreviewJob(null);
    replacePreviewUrl("");
    queueMicrotask(() => void load());
    // Reopened for a different asset, so the recipe is read again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, assetId, replacePreviewUrl]);

  useEffect(() => {
    if (!open || !previewJob || !["queued", "running"].includes(previewJob.status)) return;
    let active = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const response = await apiFetch(`${base}/effects/jobs`);
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? "The preview status could not be read.");
        const current = (body.jobs ?? []).find((job: PreviewJob) => job.id === previewJob.id);
        if (!active || !current) return;
        if (current.status === "succeeded") {
          const previewResponse = await apiFetch(`${base}/effects/jobs/${current.id}/preview`, {
            method: "POST",
          });
          const previewBody = await previewResponse.json();
          if (!previewResponse.ok) {
            throw new Error(previewBody.detail ?? "The preview could not be opened.");
          }
          const binary = window.atob(previewBody.content_base64);
          const bytes = new Uint8Array(binary.length);
          for (let index = 0; index < binary.length; index += 1) {
            bytes[index] = binary.charCodeAt(index);
          }
          if (!active) return;
          replacePreviewUrl(URL.createObjectURL(new Blob([bytes], { type: previewBody.mime_type })));
          setPreviewJob({ ...current, status: "ready" });
        } else {
          setPreviewJob(current);
          if (["failed", "cancelled"].includes(current.status) && current.error) {
            setFailure(current.error);
          }
        }
      } catch (reason) {
        if (active) {
          setPreviewJob((current) => current ? { ...current, status: "failed" } : current);
          setFailure(reason instanceof Error ? reason.message : "The preview could not be opened.");
        }
      } finally {
        polling = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [apiFetch, base, open, previewJob, replacePreviewUrl]);

  const usable = effects.filter((effect) => effect.media_kinds.includes(mediaKind));
  const definitionOf = (id: string) => effects.find((effect) => effect.id === id);

  function edit(next: Step[]) {
    setSteps(next);
    setSaved(false);
  }

  function add(effect: EffectDefinition) {
    edit([...steps, { effect: effect.id, values: defaultsFor(effect) }]);
  }

  function move(index: number, by: number) {
    const next = [...steps];
    const to = index + by;
    if (to < 0 || to >= next.length) return;
    [next[index], next[to]] = [next[to], next[index]];
    edit(next);
  }

  async function save() {
    setBusy("save");
    setFailure("");
    try {
      const response = await apiFetch(`${base}/assets/${assetId}/recipe`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ steps }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The recipe could not be saved.");
      setSaved(true);
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The recipe could not be saved.");
    } finally {
      setBusy("");
    }
  }

  async function render() {
    setBusy("render");
    setFailure("");
    try {
      const response = await apiFetch(`${base}/effects/render`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_path: assetPath,
          steps,
          confirm_external_action: true,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The render could not start.");
      void refreshJobs();
      onRendered(t("effectEditor.renderStarted"));
      onClose();
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The render could not start.");
    } finally {
      setBusy("");
    }
  }

  async function preview() {
    setBusy("preview");
    setFailure("");
    replacePreviewUrl("");
    try {
      const response = await apiFetch(`${base}/effects/render`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_path: assetPath,
          steps,
          preview_seconds: mediaKind === "video" ? 5 : 1,
          confirm_external_action: true,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The preview could not start.");
      setPreviewJob(body.job);
      void refreshJobs();
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The preview could not start.");
    } finally {
      setBusy("");
    }
  }

  async function cancelPreview() {
    if (!previewJob) return;
    setBusy("cancel-preview");
    try {
      const response = await apiFetch(`${base}/effects/jobs/${previewJob.id}/cancel`, {
        method: "POST",
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The preview could not be cancelled.");
      setPreviewJob(body.job);
      void refreshJobs();
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The preview could not be cancelled.");
    } finally {
      setBusy("");
    }
  }

  const unavailable = steps
    .map((step) => definitionOf(step.effect))
    .filter((effect): effect is EffectDefinition => Boolean(effect) && !effect!.available);

  /** The step whose gallery is open, paired with the parameter that opened it. */
  const gallery = (() => {
    if (picking === null) return null;
    const step = steps[picking];
    const effect = step && definitionOf(step.effect);
    const param = effect && galleryParam(effect);
    // A step deleted while its gallery was open leaves nothing to pick for.
    return step && effect && param ? { step, effect, param } : null;
  })();

  return (
    /* One panel, two views. The gallery takes this one over rather than opening
       a second modal on top of it: stacked, it was a dialog inside a dialog and
       confined to the standard width, which left the grid of objects a
       scrollbar with two thumbnails behind it. */
    <Dialog
      open={open}
      size="wide"
      title={gallery ? t("overlayPicker.heading") : t("effectEditor.edit")}
      description={gallery
        ? "Pick one, then check it on a real frame before rendering."
        : "Stack effects on this asset. The original is never changed."}
      onClose={onClose}
      footer={gallery ? (
        <Button variant="primary" onClick={() => setPicking(null)}>
          {t("overlayPicker.done")}
        </Button>
      ) : (
        <>
          <Button variant="quiet" onClick={onClose}>{t("common.close")}</Button>
          <Button
            variant="secondary"
            busy={busy === "save"}
            disabled={!canEdit || saved}
            onClick={() => void save()}
          >{saved ? "Saved" : "Save recipe"}</Button>
          <Button
            variant="secondary"
            busy={busy === "preview"}
            disabled={!canEdit || !steps.length || unavailable.length > 0
              || Boolean(previewJob && ["queued", "running", "loading"].includes(previewJob.status))}
            onClick={() => void preview()}
          >Preview</Button>
          <Button
            variant="primary"
            busy={busy === "render"}
            disabled={!canEdit || !steps.length || unavailable.length > 0}
            onClick={() => void render()}
          >{t("effectEditor.render")}</Button>
        </>
      )}
    >
      {!gallery && <div className="effect-editor">
        {failure && <p className="console-error" role="alert">{failure}</p>}

        {(previewJob || previewUrl) && (
          <section className="effect-recipe-preview" aria-live="polite">
            <div className="effect-recipe-preview-head">
              <div>
                <strong>{previewUrl ? "Effect preview" : "Preparing preview"}</strong>
                {!previewUrl && (
                  <small>
                    {[previewJob?.progress_stage,
                      typeof previewJob?.progress === "number"
                        ? `${Math.round(previewJob.progress * 100)}%`
                        : mediaKind === "video" ? "First 5 seconds" : "Still image",
                    ].filter(Boolean).join(" · ")}
                  </small>
                )}
              </div>
              {previewJob && ["queued", "running"].includes(previewJob.status) && (
                <Button
                  variant="quiet"
                  size="sm"
                  busy={busy === "cancel-preview"}
                  onClick={() => void cancelPreview()}
                >Cancel</Button>
              )}
            </div>
            {!previewUrl && typeof previewJob?.progress === "number" && (
              <progress max={1} value={previewJob.progress} />
            )}
            {previewUrl && mediaKind === "video" && (
              <video src={previewUrl} controls preload="metadata" />
            )}
            {previewUrl && mediaKind === "image" && (
              // Blob URLs are private, short-lived previews and cannot use Next's optimiser.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={previewUrl} alt="The current effect recipe preview" />
            )}
          </section>
        )}

        <div className="effect-add" role="group" aria-label={t("effectEditor.heading")}>
          {usable.map((effect) => (
            <Button
              key={effect.id}
              variant="secondary"
              size="sm"
              disabled={!canEdit || !effect.available}
              title={effect.available ? effectSummary(t, effect.id, effect.summary) : effect.unavailable_reason ?? undefined}
              onClick={() => add(effect)}
            ><ActionIcon name="add" />{effectLabel(t, effect.id, effect.label)}</Button>
          ))}
        </div>

        {!steps.length ? (
          <p className="effect-empty">
            No effects yet. The cheap transforms compose into a single pass; the
            ones that need a model to look at each frame cost more and say so.
          </p>
        ) : (
          <ol className="effect-stack">
            {steps.map((step, index) => {
              const effect = definitionOf(step.effect);
              if (!effect) {
                return (
                  <li key={`${step.effect}-${index}`} className="effect-step">
                    <div className="effect-step-head">
                      <strong>{step.effect}</strong>
                      <Badge tone="bad">{t("effectEditor.notAvailable")}</Badge>
                    </div>
                  </li>
                );
              }
              return (
                <li key={`${step.effect}-${index}`} className="effect-step">
                  <div className="effect-step-head">
                    <span className="effect-step-order">{index + 1}</span>
                    <div>
                      <strong>{effectLabel(t, effect.id, effect.label)}</strong>
                      <small>{effectSummary(t, effect.id, effect.summary)}</small>
                    </div>
                    {/* Which stage an effect runs in decides what it costs, so it
                        is stated rather than left to be discovered at render. */}
                    <Badge tone={effect.stage === "frame" ? "warn" : "neutral"}>
                      {effect.stage === "frame" ? "per frame" : "one pass"}
                    </Badge>
                    {effect.retimes && <Badge tone="accent">{t("effectEditor.changesLength")}</Badge>}
                    <div className="effect-step-actions">
                      <Button
                        variant="quiet" size="sm" iconOnly aria-label={t("effectEditor.moveEarlier")}
                        disabled={!canEdit || index === 0}
                        onClick={() => move(index, -1)}
                      >↑</Button>
                      <Button
                        variant="quiet" size="sm" iconOnly aria-label={t("effectEditor.moveLater")}
                        disabled={!canEdit || index === steps.length - 1}
                        onClick={() => move(index, 1)}
                      >↓</Button>
                      <Button
                        variant="quiet" size="sm" iconOnly aria-label={`${t("common.delete")} ${effectLabel(t, effect.id, effect.label)}`}
                        disabled={!canEdit}
                        onClick={() => edit(steps.filter((_, at) => at !== index))}
                      ><ActionIcon name="dismiss" /></Button>
                    </div>
                  </div>
                  {!effect.available && (
                    <p className="effect-unavailable">{effect.unavailable_reason}</p>
                  )}
                  {effect.params.length > 0 && (
                    <div className="effect-params">
                      {effect.params.map((param) => param.presentation === "gallery" ? (
                        /* A choice between pictures opens the gallery instead of
                           filling a select with their names. */
                        <div key={param.id} className="effect-param effect-param-gallery">
                          <span>{paramLabel(t, effect.id, param.id, param.label)}</span>
                          <GalleryChoiceButton
                            workspaceId={workspaceId}
                            option={param.options.find(
                              (option) => option.value === String(step.values?.[param.id] ?? ""),
                            )}
                            apiFetch={apiFetch}
                            disabled={!canEdit || !effect.available}
                            onOpen={() => setPicking(index)}
                          />
                          {param.help && (
                            <small>{paramHelp(t, effect.id, param.id, param.help)}</small>
                          )}
                        </div>
                      ) : (
                        <ParamControl
                          key={param.id}
                          param={param}
                          effectId={effect.id}
                          value={step.values?.[param.id] ?? param.default}
                          onChange={(next) => edit(steps.map((item, at) => at === index
                            ? { ...item, values: { ...item.values, [param.id]: next } }
                            : item))}
                        />
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}

        {steps.length > 0 && (
          /* Order is not a presentation detail here: rotating then flipping is
             not flipping then rotating, and the difference is visible. */
          <p className="effect-note">
            Applied top to bottom. Effects that look at the picture run before the
            ones that move it, whatever order they sit in here.
          </p>
        )}
      </div>}

      {/* One picker, opened against whichever step asked for it. Its options are
          the step's own declared ones, so this stays a renderer of the registry
          rather than a second place that knows what an overlay is. */}
      {gallery && (
        <GalleryPanel
          open
          workspaceId={workspaceId}
          assetPath={assetPath}
          effect={gallery.effect}
          param={gallery.param}
          values={gallery.step.values ?? {}}
          canEdit={canEdit}
          apiFetch={apiFetch}
          onChange={(next) => edit(steps.map((item, at) => at === picking
            ? { ...item, values: next }
            : item))}
          onCatalogueChanged={(chosen) => {
            // The registry is the one source of the options, so the catalogue
            // is re-read rather than patched locally — a locally added option
            // would be the version validation does not know about.
            void reloadEffects();
            if (chosen) {
              edit(steps.map((item, at) => at === picking
                ? { ...item, values: { ...item.values, [gallery.param.id]: chosen } }
                : item));
            }
          }}
        />
      )}
    </Dialog>
  );
}
