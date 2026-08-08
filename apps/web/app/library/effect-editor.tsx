"use client";

import { useCallback, useEffect, useState } from "react";

import { ActionIcon } from "../ui/action-icons";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { useT } from "../i18n-provider";
import {
  effectLabel,
  effectSummary,
  optionLabel,
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

type ParamOption = { value: string; label: string };
type EffectParam = {
  id: string;
  label: string;
  kind: "number" | "choice" | "toggle";
  default: unknown;
  help: string;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  unit: string;
  options: ParamOption[];
};
export type EffectDefinition = {
  id: string;
  label: string;
  summary: string;
  stage: "stream" | "frame";
  retimes: boolean;
  media_kinds: string[];
  available: boolean;
  unavailable_reason: string | null;
  params: EffectParam[];
};
type Step = { effect: string; values: Record<string, unknown> };

function defaultsFor(effect: EffectDefinition): Record<string, unknown> {
  return Object.fromEntries(effect.params.map((param) => [param.id, param.default]));
}

/** One control, chosen by what the parameter says it is. */
function ParamControl({
  param,
  effectId,
  value,
  onChange,
}: {
  param: EffectParam;
  effectId: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const t = useT();
  const label = paramLabel(t, effectId, param.id, param.label);
  const help = param.help ? paramHelp(t, effectId, param.id, param.help) : "";
  if (param.kind === "choice") {
    return (
      <label className="effect-param">
        <span>{label}</span>
        <select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
          {param.options.map((option) => (
            <option key={option.value} value={option.value}>
              {optionLabel(t, effectId, option.value, option.label)}
            </option>
          ))}
        </select>
        {help && <small>{help}</small>}
      </label>
    );
  }
  if (param.kind === "toggle") {
    return (
      <label className="effect-param effect-param-toggle">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>{label}</span>
        {help && <small>{help}</small>}
      </label>
    );
  }
  const numeric = typeof value === "number" ? value : Number(param.default ?? 0);
  return (
    <label className="effect-param">
      <span>
        {label}
        <b>{numeric}{param.unit}</b>
      </span>
      <input
        type="range"
        min={param.minimum ?? 0}
        max={param.maximum ?? 1}
        step={param.step ?? 0.01}
        value={numeric}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {help && <small>{help}</small>}
    </label>
  );
}

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
  const [effects, setEffects] = useState<EffectDefinition[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  const [saved, setSaved] = useState(true);

  const base = `/api/workspaces/${workspaceId}/media/library`;

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

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => void load());
    // Reopened for a different asset, so the recipe is read again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, assetId]);

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
      onRendered("Rendering started. It will appear as a version of this asset when it finishes.");
      onClose();
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The render could not start.");
    } finally {
      setBusy("");
    }
  }

  const unavailable = steps
    .map((step) => definitionOf(step.effect))
    .filter((effect): effect is EffectDefinition => Boolean(effect) && !effect!.available);

  return (
    <Dialog
      open={open}
      title={t("effectEditor.edit")}
      description="Stack effects on this asset. The original is never changed."
      onClose={onClose}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>{t("common.close")}</Button>
          <Button
            variant="secondary"
            busy={busy === "save"}
            disabled={!canEdit || saved}
            onClick={() => void save()}
          >{saved ? "Saved" : "Save recipe"}</Button>
          <Button
            variant="primary"
            busy={busy === "render"}
            disabled={!canEdit || !steps.length || unavailable.length > 0}
            onClick={() => void render()}
          >{t("effectEditor.render")}</Button>
        </>
      }
    >
      <div className="effect-editor">
        {failure && <p className="console-error" role="alert">{failure}</p>}

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
                      {effect.params.map((param) => (
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
      </div>
    </Dialog>
  );
}
