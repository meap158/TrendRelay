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
export type EffectTarget = {
  id: string;
  title: string;
  path: string;
  mediaKind: string;
};
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
  targets,
  assetIds,
  onClose,
  onRendered,
  apiFetch,
  canEdit,
}: {
  open: boolean;
  workspaceId: string;
  /** Loaded targets provide a sample for preview and media compatibility. */
  targets: EffectTarget[];
  /** May include selected items beyond the loaded page. */
  assetIds?: string[];
  onClose: () => void;
  onRendered: (message: string) => void;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  canEdit: boolean;
}) {
  const t = useT();
  // The drawer polls every few seconds; a render that has just been asked for
  // should be in it before the operator has finished reading the toast.
  const { announceEffectJobs, refresh: refreshJobs } = useJobs();
  const [effects, setEffects] = useState<EffectDefinition[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  const [recipeRecovered, setRecipeRecovered] = useState(false);
  const [saved, setSaved] = useState(true);
  const [previewJob, setPreviewJob] = useState<PreviewJob | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewLabel, setPreviewLabel] = useState("Effect stack preview");
  const [previewMediaKind, setPreviewMediaKind] = useState("");
  const previewUrlRef = useRef("");
  /** Which step has its gallery open, if any. */
  const [picking, setPicking] = useState<number | null>(null);

  const base = `/api/workspaces/${workspaceId}/media/library`;
  const targetIds = assetIds?.length ? assetIds : targets.map((target) => target.id);
  const batch = targetIds.length > 1;
  const primary = targets[0]!;

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
        batch && primary
          ? Promise.resolve({ steps: [] })
          : apiFetch(`${base}/assets/${primary.id}/recipe`).then((response) => response.json()),
      ]);
      setEffects(catalogue.effects ?? []);
      setSteps(recipe.steps ?? []);
      setRecipeRecovered(Boolean(recipe.recovered));
      // A recovered historical stack has not been persisted yet. Keep Save
      // available so accepting the shown defaults needs no artificial edit.
      setSaved(!recipe.recovered);
    } catch {
      setFailure("The effects could not be loaded.");
    }
  }, [apiFetch, base, batch, primary]);

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
    queueMicrotask(() => {
      setPreviewJob(null);
      replacePreviewUrl("");
      void load();
    });
    // Reopened for a different asset, so the recipe is read again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, primary?.id, replacePreviewUrl]);

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

  // A mixed selection sees every effect that applies to at least one loaded
  // kind. The batch endpoint then reports items incompatible with the complete
  // stack as skipped, rather than hiding tools or pretending they ran.
  const usable = effects.filter((effect) =>
    targets.some((target) => effect.media_kinds.includes(target.mediaKind)),
  );
  const definitionOf = (id: string) => effects.find((effect) => effect.id === id);
  const previewTargetFor = (candidateSteps: Step[]) => targets.find((target) =>
    candidateSteps.every((step) =>
      definitionOf(step.effect)?.media_kinds.includes(target.mediaKind),
    ),
  );
  const previewTarget = previewTargetFor(steps);

  function edit(next: Step[]) {
    setSteps(next);
    setRecipeRecovered(false);
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
    if (!primary || batch) return;
    setBusy("save");
    setFailure("");
    try {
      const response = await apiFetch(`${base}/assets/${primary.id}/recipe`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ steps }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The recipe could not be saved.");
      setRecipeRecovered(false);
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
      if (batch) {
        const totals = { queued: 0, skipped: 0, failed: 0, missing: 0 };
        const queuedJobs: any[] = [];
        for (let at = 0; at < targetIds.length; at += 200) {
          const response = await apiFetch(`${base}/effects/render-batch`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              asset_ids: targetIds.slice(at, at + 200),
              steps,
              confirm_external_action: true,
            }),
          });
          const body = await response.json();
          if (!response.ok) throw new Error(body.detail ?? "The batch render could not start.");
          for (const key of Object.keys(totals) as Array<keyof typeof totals>) {
            totals[key] += body.counts?.[key] ?? 0;
          }
          queuedJobs.push(...(body.jobs ?? []));
        }
        announceEffectJobs(queuedJobs);
        const details = [`${totals.queued} queued`];
        if (totals.skipped) details.push(`${totals.skipped} skipped`);
        if (totals.failed) details.push(`${totals.failed} failed`);
        if (totals.missing) details.push(`${totals.missing} missing`);
        onRendered(`Effect stack: ${details.join(" · ")}. Track each item in notifications.`);
      } else {
        const response = await apiFetch(`${base}/effects/render`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            source_path: primary.path,
            steps,
            confirm_external_action: true,
          }),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? "The render could not start.");
        announceEffectJobs(body.job ? [body.job] : []);
        onRendered(t("effectEditor.renderStarted"));
      }
      await refreshJobs();
      onClose();
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The render could not start.");
    } finally {
      setBusy("");
    }
  }

  async function preview(
    previewSteps: Step[] = steps,
    label = "Effect stack preview",
  ) {
    const target = previewTargetFor(previewSteps);
    if (!target) return;
    setBusy("preview");
    setFailure("");
    setPreviewLabel(label);
    setPreviewMediaKind(target.mediaKind);
    replacePreviewUrl("");
    try {
      const response = await apiFetch(`${base}/effects/render`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_path: target.path,
          steps: previewSteps,
          preview_seconds: target.mediaKind === "video" ? 5 : 1,
          confirm_external_action: true,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The preview could not start.");
      setPreviewJob(body.job);
      announceEffectJobs(body.job ? [body.job] : []);
      await refreshJobs();
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
      announceEffectJobs(body.job ? [body.job] : []);
      await refreshJobs();
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
      title={gallery
        ? t("overlayPicker.heading")
        : batch ? `Apply effects to ${targetIds.length.toLocaleString()} items` : t("effectEditor.edit")}
      description={gallery
        ? "Pick one, then check it on a real frame before rendering."
        : batch
          ? "Build one stack for the selection. Every compatible item gets its own tracked job."
          : "Stack effects on this asset. The original is never changed."}
      onClose={onClose}
      footer={gallery ? (
        <Button variant="primary" onClick={() => setPicking(null)}>
          {t("overlayPicker.done")}
        </Button>
      ) : (
        <>
          <Button variant="quiet" onClick={onClose}>{t("common.close")}</Button>
          {!batch && <Button
              variant="secondary"
              busy={busy === "save"}
              disabled={!canEdit || saved}
              onClick={() => void save()}
            >{saved ? "Saved" : "Save recipe"}</Button>}
          <Button
            variant="secondary"
            busy={busy === "preview"}
            disabled={!canEdit || !steps.length || unavailable.length > 0 || !previewTarget
              || Boolean(previewJob && ["queued", "running", "loading"].includes(previewJob.status))}
            onClick={() => void preview()}
          >Preview stack</Button>
          <Button
            variant="primary"
            busy={busy === "render"}
            disabled={!canEdit || !steps.length || unavailable.length > 0}
            onClick={() => void render()}
          >{batch ? "Apply to selection" : t("effectEditor.render")}</Button>
        </>
      )}
    >
      {!gallery && <div className={`effect-editor${previewJob || previewUrl ? " effect-editor-with-preview" : ""}`}>
        <div className="effect-editor-controls">
        {failure && <p className="console-error" role="alert">{failure}</p>}
        {recipeRecovered && (
          <p className="effect-recovery-note" role="status">
            This render predates saved effect settings. Its stack was restored in
            the original order with current defaults; review the controls, then save
            or render to preserve your exact settings from now on.
          </p>
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

        <p className="effect-batch-note">
          Effects are equal steps in one stack and run from top to bottom. Select
          multiple Library items before opening this editor to apply the same stack
          as a batch. Incompatible media are skipped and reported, never silently changed.
        </p>

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
                        variant="quiet"
                        size="sm"
                        iconOnly
                        aria-label={`Preview through ${effectLabel(t, effect.id, effect.label)}`}
                        title={`Preview the result through step ${index + 1}`}
                        disabled={!canEdit || !effect.available
                          || !previewTargetFor(steps.slice(0, index + 1))
                          || Boolean(previewJob && ["queued", "running", "loading"].includes(previewJob.status))}
                        onClick={() => void preview(
                          steps.slice(0, index + 1),
                          `Preview through ${effectLabel(t, effect.id, effect.label)}`,
                        )}
                      ><ActionIcon name="play" /></Button>
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
            Applied top to bottom in exactly this order. Use the play button on
            any step to preview the result through that point in the stack.
          </p>
        )}
        </div>

        {(previewJob || previewUrl) && (
          <section className="effect-recipe-preview" aria-live="polite">
            <div className="effect-recipe-preview-head">
              <div>
                <strong>{previewUrl ? previewLabel : `Preparing ${previewLabel.toLowerCase()}`}</strong>
                {!previewUrl && (
                  <small>
                    {[previewJob?.progress_stage,
                      typeof previewJob?.progress === "number"
                        ? `${Math.round(previewJob.progress * 100)}%`
                        : previewMediaKind === "video" ? "First 5 seconds" : "Still image",
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
            {!previewUrl && previewJob && (
              <progress
                max={1}
                value={typeof previewJob.progress === "number" ? previewJob.progress : undefined}
                aria-label={previewJob.progress_stage || "Preparing effect preview"}
              />
            )}
            {previewUrl && previewMediaKind === "video" && (
              <video src={previewUrl} controls preload="metadata" />
            )}
            {previewUrl && previewMediaKind === "image" && (
              // Blob URLs are private, short-lived previews and cannot use Next's optimiser.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={previewUrl} alt="The current effect recipe preview" />
            )}
          </section>
        )}
      </div>}

      {/* One picker, opened against whichever step asked for it. Its options are
          the step's own declared ones, so this stays a renderer of the registry
          rather than a second place that knows what an overlay is. */}
      {gallery && (
        <GalleryPanel
          open
          workspaceId={workspaceId}
          assetPath={previewTarget?.path ?? primary.path}
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
