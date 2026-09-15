"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

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
  presetLabel,
  presetSummary,
} from "../../lib/i18n/effects";
import {
  DEFAULT_BOUNDS,
  frameLayout,
  hotspotBox,
  offsetsAfterDrag,
  readPlacedObjects,
  type PlacedReport,
} from "../../lib/placed-objects";


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
type PreviewSpec = { steps: Step[]; label: string };

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
  const { announceMediaJobs, refresh: refreshJobs } = useJobs();
  const [effects, setEffects] = useState<EffectDefinition[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  /** Which part of a long batch is in flight, so the wait is not a blank. */
  const [busyDetail, setBusyDetail] = useState("");
  const [recipeRecovered, setRecipeRecovered] = useState(false);
  const [saved, setSaved] = useState(true);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewLabel, setPreviewLabel] = useState("Effect stack preview");
  const [previewMediaKind, setPreviewMediaKind] = useState("");
  const [previewPosition, setPreviewPosition] = useState<number | null>(null);
  const [previewDuration, setPreviewDuration] = useState<number | null>(null);
  const [previewNote, setPreviewNote] = useState("");
  /**
   * What the render drew on the previewed frame, and where.
   *
   * The preview is a picture, so until the render said where each object
   * landed there was nothing in the page to click: repositioning meant
   * finding the step in the list beside it and moving a slider, which is a
   * long way round for "that hat is too low".
   */
  const [placed, setPlaced] = useState<PlacedReport>({ frame: null, objects: [] });
  /** The object whose actions are open, by its place in the report. */
  const [chosen, setChosen] = useState<number | null>(null);
  /** The object being dragged, and how far it has come, in the box's pixels. */
  const [moving, setMoving] = useState<number | null>(null);
  const [dragged, setDragged] = useState({ x: 0, y: 0 });
  const dragFrom = useRef<{ id: number; x: number; y: number } | null>(null);
  /** The box the picture is shown in, which the handles are laid out against. */
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });
  const frameRef = useRef<HTMLDivElement | null>(null);
  const frameWatcher = useRef<ResizeObserver | null>(null);
  const previewUrlRef = useRef("");
  const previewSpecRef = useRef<PreviewSpec | null>(null);
  /** Which step is having its regions read off the clip, if any. */
  const [reading, setReading] = useState<number | null>(null);
  /** Which step has its gallery open, if any. */
  const [picking, setPicking] = useState<number | null>(null);
  /** Resolve the face object from each item's own content in a batch. */
  const [autoFaceObject, setAutoFaceObject] = useState(false);

  const base = `/api/workspaces/${workspaceId}/media/library`;
  const targetIds = assetIds?.length ? assetIds : targets.map((target) => target.id);
  /**
   * Read a clip's on-screen text into a step that covers it.
   *
   * Resolved into the step rather than looked up when it renders, because a
   * step is a self-contained set of values everywhere else in the recipe and
   * the preview is only honest if it holds what the render will get.
   *
   * One clip only. Rectangles are shares of *this* frame, and the same numbers
   * over another clip cover whatever happens to be in those places - so a
   * multi-asset edit is refused with the reason rather than filled with the
   * first one's answer.
   */
  const fillRegions = useCallback(async (index: number, paramId: string) => {
    setReading(index);
    setFailure("");
    try {
      const response = await apiFetch(`${base}/assets/${targetIds[0]}/text-regions`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail ?? "That clip's text could not be read.");
      const regions = body.regions ?? [];
      setSteps((current) => current.map((item, at) => at === index
        ? { ...item, values: { ...item.values, [paramId]: regions } }
        : item));
      setSaved(false);
      if (!regions.length) {
        setFailure("Nothing was read off this clip, so there is nothing to cover.");
      } else if (body.dropped) {
        // Said rather than left to be noticed: the count in the control would
        // otherwise quietly disagree with the reading it came from.
        setFailure(
          `${regions.length} regions taken. ${body.dropped} less certain `
          + `${body.dropped === 1 ? "line was" : "lines were"} left out.`,
        );
      }
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "That clip's text could not be read.");
    } finally {
      setReading(null);
    }
  }, [apiFetch, base, targetIds]);

  /**
   * How many assets one request queues.
   *
   * The server's own limit, so a selection under it goes in one request and
   * one transaction. Chunking smaller was a workaround for queueing being
   * slow, and it was slow because each asset opened its own connection and
   * waited out the busy timeout behind this request's uncommitted writes -
   * measured at fifteen seconds an asset, then "database is locked". Queueing
   * writes a row; it should take a moment, and now does.
   */
  const CHUNK = 200;
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
      replacePreviewUrl("");
      setPreviewPosition(null);
      setPreviewDuration(null);
      setPreviewNote("");
      previewSpecRef.current = null;
      setAutoFaceObject(batch);
      void load();
    });
    // Reopened for a different asset, so the recipe is read again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, primary?.id, replacePreviewUrl]);

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
  const framePreviewBlockerFor = (candidateSteps: Step[]) => candidateSteps
    .map((step) => definitionOf(step.effect))
    .find((effect) => effect?.stage === "frame" && !effect.previewable);
  const previewBlocker = framePreviewBlockerFor(steps);
  const usesAutomaticFaceObject = (candidateSteps: Step[]) => batch
    && autoFaceObject
    && candidateSteps.some((step) => step.effect === "face_overlay");
  const usesAutomaticRegions = (candidateSteps: Step[]) => batch
    && candidateSteps.some((step) => step.effect === "cover_text" && !(step.values?.regions as any[])?.length);
  const automaticPreviewReason = usesAutomaticFaceObject(steps)
    ? "Each item gets its own matched object when the batch is queued. Turn off automatic selection to preview one shared object."
    : usesAutomaticRegions(steps)
      ? "Each item's text regions are resolved from its own OCR reading when the batch is queued."
      : "";

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
        const automaticObjects = { content_match: 0, safe_fallback: 0 };
        const queuedJobs: any[] = [];
        // What went wrong on the way, kept rather than thrown. A chunk that
        // fails used to abandon the whole call, so the items already queued by
        // earlier chunks vanished from the summary while their jobs ran on -
        // the screen said nothing had happened and the queue disagreed.
        let interrupted = "";
        for (let at = 0; at < targetIds.length; at += CHUNK) {
          setBusyDetail(targetIds.length > CHUNK
            ? `Queueing ${Math.min(at + CHUNK, targetIds.length)} of ${targetIds.length}…`
            : `Queueing ${targetIds.length}…`);
          try {
            const response = await apiFetch(`${base}/effects/render-batch`, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                asset_ids: targetIds.slice(at, at + CHUNK),
                steps,
                auto_face_object: autoFaceObject,
                confirm_external_action: true,
              }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
              throw new Error(body.detail ?? `The server refused the batch (${response.status}).`);
            }
            for (const key of Object.keys(totals) as Array<keyof typeof totals>) {
              totals[key] += body.counts?.[key] ?? 0;
            }
            automaticObjects.content_match += body.automatic_object_counts?.content_match ?? 0;
            automaticObjects.safe_fallback += body.automatic_object_counts?.safe_fallback ?? 0;
            queuedJobs.push(...(body.jobs ?? []));
          } catch (reason) {
            interrupted = reason instanceof Error ? reason.message : "The batch stopped early.";
            break;
          }
        }
        setBusyDetail("");
        announceMediaJobs(queuedJobs);
        const details = [`${totals.queued} queued`];
        if (totals.skipped) details.push(`${totals.skipped} skipped`);
        if (totals.failed) details.push(`${totals.failed} failed`);
        if (totals.missing) details.push(`${totals.missing} missing`);
        if (automaticObjects.content_match) {
          details.push(`${automaticObjects.content_match} content-matched objects`);
        }
        if (automaticObjects.safe_fallback) {
          details.push(`${automaticObjects.safe_fallback} safe fallbacks`);
        }
        const done = totals.queued + totals.skipped + totals.failed + totals.missing;
        if (interrupted) {
          // Said in the dialog and left open, because there is something to do
          // about it: the rest of the selection has not been queued.
          setFailure(
            `${interrupted} ${done} of ${targetIds.length} were handled `
            + `(${details.join(" · ")}); the rest were not queued.`,
          );
          return;
        }
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
        announceMediaJobs(body.job ? [body.job] : []);
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
    at?: number | null,
  ) {
    const target = previewTargetFor(previewSteps);
    if (!target) return;
    setBusy("preview");
    setFailure("");
    setPreviewLabel(label);
    setPreviewMediaKind(target.mediaKind);
    setPreviewNote("");
    previewSpecRef.current = { steps: previewSteps, label };
    try {
      const response = await apiFetch(`${base}/effects/frame`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_path: target.path,
          steps: previewSteps,
          ...(at === null || at === undefined ? {} : { at }),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "The preview frame could not be rendered.");
      }
      const landed = Number(response.headers.get("X-Frame-Position") ?? "");
      setPreviewPosition(Number.isFinite(landed) ? landed : null);
      const duration = Number(response.headers.get("X-Clip-Duration") ?? "");
      setPreviewDuration(Number.isFinite(duration) && duration > 0 ? duration : null);
      const note = response.headers.get("X-Preview-Note") ?? "";
      setPreviewNote(note ? decodeURIComponent(note) : "");
      // What is on this frame and where. Closing whatever was open with it:
      // the handles belong to the frame that was just drawn, and one left
      // open would be pointing at a picture that no longer exists.
      setPlaced(readPlacedObjects(response.headers.get("X-Preview-Objects")));
      setChosen(null);
      setMoving(null);
      setDragged({ x: 0, y: 0 });
      replacePreviewUrl(URL.createObjectURL(await response.blob()));
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The preview frame could not be rendered.");
    } finally {
      setBusy("");
    }
  }

  /**
   * Keep the handles laid out against the box the picture is actually in.
   *
   * The same arrangement the caption editor's overlay uses, for the same
   * reason: the frame changes size when the dialog does and when the
   * picture's own proportions arrive, and a handle sized against the old box
   * sits beside the thing it is meant to be on.
   */
  const measureFrame = useCallback(() => {
    const node = frameRef.current;
    if (!node) return;
    const width = node.clientWidth;
    const height = node.clientHeight;
    setFrameSize((current) => (
      current.width === width && current.height === height ? current : { width, height }
    ));
  }, []);
  useLayoutEffect(measureFrame);
  const attachFrame = useCallback((node: HTMLDivElement | null) => {
    frameRef.current = node;
    frameWatcher.current?.disconnect();
    frameWatcher.current = null;
    if (!node || typeof ResizeObserver === "undefined") return;
    frameWatcher.current = new ResizeObserver(() => measureFrame());
    frameWatcher.current.observe(node);
    measureFrame();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => () => frameWatcher.current?.disconnect(), []);

  const layout = frameLayout(placed.frame, frameSize);

  /** The two offsets one placed object is positioned by, as they stand now. */
  function offsetsOf(step: number | null) {
    const values = step === null ? undefined : steps[step]?.values;
    return {
      horizontal: Number(values?.horizontal_offset ?? 0) || 0,
      vertical: Number(values?.offset ?? 0) || 0,
    };
  }

  /** What the effect itself says those two may be, rather than what we assume. */
  function boundsOf(step: number | null) {
    const effect = step === null ? undefined : definitionOf(steps[step]?.effect ?? "");
    const range = (id: string, fallback: { minimum: number; maximum: number }) => {
      const param = effect?.params.find((item) => item.id === id);
      return param && param.minimum !== null && param.maximum !== null
        ? { minimum: param.minimum, maximum: param.maximum }
        : fallback;
    };
    return {
      horizontal: range("horizontal_offset", DEFAULT_BOUNDS.horizontal),
      vertical: range("offset", DEFAULT_BOUNDS.vertical),
    };
  }

  /** Write one step's values, from an action taken on the picture. */
  function setOn(step: number | null, values: Record<string, unknown>) {
    if (step === null || !steps[step]) return;
    const next = [...steps];
    next[step] = { ...next[step], values: { ...next[step].values, ...values } };
    edit(next);
    return next;
  }

  /** Size, changed from the object rather than from the slider beside it. */
  function resize(index: number, by: number) {
    const object = placed.objects[index];
    if (!object) return;
    const effect = definitionOf(steps[object.step ?? -1]?.effect ?? "");
    const param = effect?.params.find((item) => item.id === "scale");
    const current = Number(steps[object.step ?? -1]?.values.scale ?? 1) || 1;
    const scale = Math.min(
      param?.maximum ?? 2.5,
      Math.max(param?.minimum ?? 0.4, Math.round((current + by) * 100) / 100),
    );
    const next = setOn(object.step, { scale });
    setChosen(null);
    if (next) void preview(next, "Effect stack preview", previewPosition);
  }

  /** Take the object off, from the object. */
  function removeObject(index: number) {
    const object = placed.objects[index];
    if (!object || object.step === null) return;
    const next = steps.filter((_step, at) => at !== object.step);
    edit(next);
    setChosen(null);
    setPlaced({ frame: null, objects: [] });
    if (next.length) void preview(next, "Effect stack preview", previewPosition);
    else replacePreviewUrl("");
  }

  const unavailable = steps
    .map((step) => definitionOf(step.effect))
    .filter((effect): effect is EffectDefinition => Boolean(effect) && !effect!.available);
  /** What stops Apply, in the words of the thing that stops it. */
  const applyBlocker = !canEdit
    ? "You do not have permission to render in this workspace."
    : !steps.length
      ? "Add an effect to the stack first."
      : unavailable.length > 0
        ? `${unavailable.map((effect) => effect.label).join(", ")} ${
            unavailable.length === 1 ? "is" : "are"} not available: ${
            unavailable[0]!.unavailable_reason ?? "its runtime is not installed."}`
        : null;

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
          ? "Build one stack for the selection. Face objects can be matched separately; every compatible item gets its own tracked job."
          : "Stack effects on this asset. The original is never changed."}
      onClose={onClose}
      footer={gallery ? (
        <Button variant="primary" onClick={() => setPicking(null)}>
          {t("overlayPicker.done")}
        </Button>
      ) : (
        <>
          <Button variant="quiet" onClick={onClose}>{t("common.cancel")}</Button>
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
              || Boolean(previewBlocker) || Boolean(automaticPreviewReason)
              || busy === "preview"}
            title={automaticPreviewReason
              || previewBlocker?.unpreviewable_reason
              || undefined}
            onClick={() => void preview()}
          >Preview stack</Button>
          {/* Why it cannot be pressed, beside it. A disabled primary action
              with no reason is a dead end: the click does nothing, the dialog
              stays open, and nothing on screen accounts for either. */}
          {applyBlocker && <small className="effect-apply-blocker">{applyBlocker}</small>}
          <Button
            variant="primary"
            busy={busy === "render"}
            disabled={Boolean(applyBlocker)}
            title={applyBlocker ?? undefined}
            onClick={() => void render()}
          >{busy === "render" && busyDetail
            ? busyDetail
            : batch ? `Apply to ${targetIds.length.toLocaleString()} items` : t("effectEditor.render")}</Button>
        </>
      )}
    >
      {!gallery && <div className={`effect-editor${previewUrl || busy === "preview" ? " effect-editor-with-preview" : ""}`}>
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
          as a batch. Face objects can be chosen from each item&apos;s own content;
          incompatible media are skipped and reported, never silently changed.
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
                        disabled={!canEdit || !effect.available
                          || !previewTargetFor(steps.slice(0, index + 1))
                          || Boolean(framePreviewBlockerFor(steps.slice(0, index + 1)))
                          || usesAutomaticFaceObject(steps.slice(0, index + 1))
                          || busy === "preview"}
                        title={usesAutomaticFaceObject(steps.slice(0, index + 1))
                          ? "This batch chooses a different object per item at queue time."
                          : `Preview the result through step ${index + 1}`}
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
                  {effect.available && effect.stage === "frame" && !effect.previewable && (
                    <p className="effect-unavailable">{effect.unpreviewable_reason}</p>
                  )}
                  {batch && effect.id === "face_overlay" && (
                    <button
                      type="button"
                      className={`effect-auto-object${autoFaceObject ? " active" : ""}`}
                      role="switch"
                      aria-checked={autoFaceObject}
                      disabled={!canEdit || !effect.available}
                      onClick={() => setAutoFaceObject((current) => !current)}
                    >
                      <span className="effect-auto-object-switch" aria-hidden="true"><span /></span>
                      <span>
                        <strong>Choose the best object separately for each item</strong>
                        <small>
                          Reads that item&apos;s hashtags, caption, visual description,
                          on-screen text, and transcript. Uses Smiley only when no
                          trustworthy match exists.
                        </small>
                      </span>
                    </button>
                  )}
                  {/* Starting points, above the controls they fill. Chips
                      rather than a dropdown: there is nothing to choose
                      between here - a preset is a shortcut to a set of numbers
                      that stay visible and editable underneath, and hiding
                      them in a list would make it look like a mode. */}
                  {!!effect.presets?.length && (
                    <div className="effect-presets" role="group" aria-label={t("effects.startingPoints")}>
                      {effect.presets.map((preset) => {
                        // Lit when the controls still say what it set. Compared
                        // across every param because a preset names them all,
                        // so "on" means the whole grade, not a lucky slider.
                        const on = effect.params.every((param) =>
                          (step.values?.[param.id] ?? param.default) === preset.values[param.id]);
                        return (
                          <button
                            key={preset.id}
                            type="button"
                            className={on ? "selected" : ""}
                            aria-pressed={on}
                            title={presetSummary(t, effect.id, preset.id, preset.summary)}
                            onClick={() => edit(steps.map((item, at) => at === index
                              ? { ...item, values: { ...item.values, ...preset.values } }
                              : item))}
                          >{presetLabel(t, effect.id, preset.id, preset.label)}</button>
                        );
                      })}
                    </div>
                  )}
                  {effect.params.length > 0 && (
                    <div className="effect-params">
                      {effect.params.map((param) => (
                        autoFaceObject
                        && batch
                        && effect.id === "face_overlay"
                        && param.id === "object"
                      ) ? (
                        <div key={param.id} className="effect-param effect-param-gallery">
                          <span>{paramLabel(t, effect.id, param.id, param.label)}</span>
                          <div className="effect-auto-object-value">
                            <ActionIcon name="effects" />
                            <span>
                              <strong>Automatic per item</strong>
                              <small>The resolved object is recorded on each tracked job.</small>
                            </span>
                          </div>
                        </div>
                      ) : (
                        param.kind === "regions"
                        && batch
                        && effect.id === "cover_text"
                      ) ? (
                        <div key={param.id} className="effect-param effect-param-gallery">
                          <span>{paramLabel(t, effect.id, param.id, param.label)}</span>
                          <div className="effect-auto-object-value">
                            <ActionIcon name="effects" />
                            <span>
                              <strong>Automatic per item</strong>
                              <small>Regions are resolved from each clip&apos;s on-screen text reading.</small>
                            </span>
                          </div>
                        </div>
                      ) : param.presentation === "gallery" ? (
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
                          filling={reading === index}
                          fill={param.kind === "regions" ? {
                            onFill: () => fillRegions(index, param.id),
                            canFill: targetIds.length === 1,
                            reason: targetIds.length === 1
                              ? "Reads this clip's on-screen text"
                              : "Text sits in different places on each clip, so "
                                + "this can only be read for one at a time.",
                          } : undefined}
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

        {(busy === "preview" || previewUrl) && (
          <section className="effect-recipe-preview" aria-live="polite">
            <div className="effect-recipe-preview-head">
              <div>
                <strong>{previewLabel}</strong>
                <small>
                  {previewPosition === null
                    ? busy === "preview" ? "Rendering one frame…" : "One-frame preview"
                    : previewDuration
                      ? `${(previewPosition * previewDuration).toFixed(1)}s of ${previewDuration.toFixed(1)}s`
                      : previewMediaKind === "video"
                        ? `${Math.round(previewPosition * 100)}% through the clip`
                        : "Still image"}
                </small>
              </div>
            </div>
            <div className="effect-preview-frame" ref={attachFrame}>
              {previewUrl ? (
                // Blob URLs are private, short-lived previews and cannot use Next's optimiser.
                // eslint-disable-next-line @next/next/no-img-element
                <img src={previewUrl} alt="The current effect recipe preview frame" />
              ) : (
                <p>Rendering a frame…</p>
              )}
              {/* A handle on each object the render drew, where it drew it.
                  Repositioning used to mean finding the step in the list and
                  moving a slider; this is the object itself, which is where
                  somebody looking at it already is. */}
              {previewUrl && layout && placed.objects.map((object, index) => {
                const box = hotspotBox(object, layout);
                const shift = moving === index ? dragged : { x: 0, y: 0 };
                const name = effectLabel(t, object.effect, object.effect);
                const nudge = (across: number, down: number) => {
                  const bounds = boundsOf(object.step);
                  const current = offsetsOf(object.step);
                  const moved = {
                    horizontal: Math.min(bounds.horizontal.maximum, Math.max(
                      bounds.horizontal.minimum,
                      Math.round((current.horizontal + across) * 100) / 100,
                    )),
                    vertical: Math.min(bounds.vertical.maximum, Math.max(
                      bounds.vertical.minimum,
                      Math.round((current.vertical + down) * 100) / 100,
                    )),
                  };
                  const next = setOn(object.step, {
                    horizontal_offset: moved.horizontal, offset: moved.vertical,
                  });
                  if (next) void preview(next, "Effect stack preview", previewPosition);
                };
                return (
                  <button
                    key={`${object.step}-${index}`}
                    type="button"
                    className={[
                      "effect-object-handle",
                      moving === index ? "effect-object-moving" : "",
                      chosen === index ? "effect-object-chosen" : "",
                    ].filter(Boolean).join(" ")}
                    style={{
                      left: `${box.left + shift.x}px`,
                      top: `${box.top + shift.y}px`,
                      width: `${box.size}px`,
                      height: `${box.size}px`,
                    }}
                    aria-label={moving === index
                      ? `Moving ${name}. Drag it, or use the arrow keys. Enter puts it down.`
                      : `${name} on this frame. Open what can be done with it.`}
                    onPointerDown={(event) => {
                      if (moving !== index) return;
                      dragFrom.current = {
                        id: event.pointerId, x: event.clientX, y: event.clientY,
                      };
                      event.currentTarget.setPointerCapture(event.pointerId);
                    }}
                    onPointerMove={(event) => {
                      const from = dragFrom.current;
                      if (!from || from.id !== event.pointerId) return;
                      setDragged({ x: event.clientX - from.x, y: event.clientY - from.y });
                    }}
                    onPointerUp={(event) => {
                      const from = dragFrom.current;
                      if (!from || from.id !== event.pointerId) return;
                      dragFrom.current = null;
                      const delta = { x: event.clientX - from.x, y: event.clientY - from.y };
                      setDragged({ x: 0, y: 0 });
                      setMoving(null);
                      if (!layout || (!delta.x && !delta.y)) return;
                      const moved = offsetsAfterDrag(
                        object, delta, layout,
                        offsetsOf(object.step), boundsOf(object.step),
                      );
                      const next = setOn(object.step, {
                        horizontal_offset: moved.horizontal, offset: moved.vertical,
                      });
                      if (next) void preview(next, "Effect stack preview", previewPosition);
                    }}
                    onPointerCancel={() => {
                      dragFrom.current = null;
                      setDragged({ x: 0, y: 0 });
                    }}
                    onKeyDown={(event) => {
                      if (moving !== index) return;
                      // A step of the effect's own, so a nudge here and a
                      // press on the slider beside it move the same amount.
                      const step = 0.02;
                      const by: Record<string, [number, number]> = {
                        ArrowLeft: [-step, 0], ArrowRight: [step, 0],
                        ArrowUp: [0, -step], ArrowDown: [0, step],
                      };
                      if (event.key === "Escape" || event.key === "Enter") {
                        event.preventDefault();
                        setMoving(null);
                        return;
                      }
                      const delta = by[event.key];
                      if (!delta) return;
                      event.preventDefault();
                      nudge(delta[0], delta[1]);
                    }}
                    onClick={() => {
                      if (moving === index) return;
                      setChosen(chosen === index ? null : index);
                    }}
                  />
                );
              })}
              {/* What can be done with it, where it is. Small on purpose:
                  everything here is also in the step's own form, and this is
                  the handful worth doing without looking away from the
                  picture. */}
              {chosen !== null && layout && placed.objects[chosen] && (() => {
                const object = placed.objects[chosen];
                const box = hotspotBox(object, layout);
                const left = Math.max(4, Math.min(
                  frameSize.width - 188, box.left + box.size / 2 - 92,
                ));
                const below = box.top + box.size + 8;
                const top = below + 92 > frameSize.height
                  ? Math.max(4, box.top - 100)
                  : below;
                return (
                  <div
                    className="effect-object-actions"
                    style={{ left: `${left}px`, top: `${top}px` }}
                    role="group"
                    aria-label={`${effectLabel(t, object.effect, object.effect)} on this frame`}
                  >
                    <button
                      type="button"
                      onClick={() => { setMoving(chosen); setChosen(null); }}
                    >Move</button>
                    <button type="button" onClick={() => resize(chosen, 0.1)}>Bigger</button>
                    <button type="button" onClick={() => resize(chosen, -0.1)}>Smaller</button>
                    <button
                      type="button"
                      className="effect-object-remove"
                      onClick={() => removeObject(chosen)}
                    >Remove</button>
                  </div>
                );
              })()}
              {moving !== null && (
                <span className="effect-object-hint">
                  Drag it where it should be, or use the arrow keys.
                </span>
              )}
              {busy === "preview" && previewUrl && <span>Rendering this position…</span>}
            </div>
            {previewMediaKind === "video" && previewPosition !== null && (
              <label className="effect-preview-seek">
                <span>
                  Position
                  <b>{previewDuration
                    ? `${(previewPosition * previewDuration).toFixed(1)}s`
                    : `${Math.round(previewPosition * 100)}%`}</b>
                </span>
                <input
                  type="range"
                  min={0}
                  max={1000}
                  step={5}
                  value={Math.round(previewPosition * 1000)}
                  disabled={busy === "preview"}
                  onChange={(event) => setPreviewPosition(Number(event.target.value) / 1000)}
                  onPointerUp={(event) => {
                    const spec = previewSpecRef.current;
                    if (spec) void preview(
                      spec.steps,
                      spec.label,
                      Number(event.currentTarget.value) / 1000,
                    );
                  }}
                  onKeyUp={(event) => {
                    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                    const spec = previewSpecRef.current;
                    if (spec) void preview(
                      spec.steps,
                      spec.label,
                      Number(event.currentTarget.value) / 1000,
                    );
                  }}
                />
                <small>Drag anywhere in the clip. A new still is rendered only when you release.</small>
              </label>
            )}
            {previewNote && <p className="effect-preview-note">{previewNote}</p>}
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
