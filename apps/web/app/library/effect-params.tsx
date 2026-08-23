"use client";

import { optionLabel, paramHelp, paramLabel } from "../../lib/i18n/effects";
import { useT } from "../i18n-provider";
import { Button } from "../ui/button";
import { Select } from "../ui/select";

/**
 * The shape of the effect registry as the API serves it, and the control that
 * renders one setting from it.
 *
 * Shared rather than owned by the editor because the gallery dialog renders the
 * same settings beside its preview — the size of a sticker is judged while
 * looking at it, not in a panel behind the dialog. Two implementations of the
 * same control would be two places for a range or a unit to go stale.
 */

/**
 * An option can carry more than a name. A catalogue of picture-shaped choices
 * needs a group to sit in, something to say for itself, and somewhere to get a
 * thumbnail — all of which arrive from the API alongside the value.
 */
export type ParamOption = {
  value: string;
  label: string;
  group?: string;
  /**
   * A stable id for the group, when it is one this build ships. Absent for a
   * folder an operator named, which keeps their heading.
   */
  group_id?: string | null;
  /** Whether choosing this actually hides a face. Overlay objects set it. */
  occludes?: boolean;
  note?: string;
  /**
   * Where to fetch a thumbnail, relative to the workspace media-library base.
   * Carried on the option so one gallery can show overlay sprites and
   * face-swap portraits without knowing which it has.
   */
  preview?: string;
  /** Supplied by the operator rather than shipped, so never cached. */
  custom?: boolean;
};

export type EffectParam = {
  id: string;
  label: string;
  /**
   * `regions` is not a knob. It holds rectangles read off the clip itself, so
   * it is filled from the asset rather than dragged — and it is listed here
   * because a kind this file does not know falls through to the number branch,
   * which would render a slider over an array and replace it with a number on
   * the first drag.
   */
  kind: "number" | "choice" | "toggle" | "regions";
  default: unknown;
  help: string;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  unit: string;
  options: ParamOption[];
  /** How the choice is offered. A gallery gets its own dialog, not a dropdown. */
  presentation?: "control" | "gallery";
  /**
   * Where an operator adds their own options, for a choice fed by a folder —
   * and which files there did not become options. A choice that is not fed by
   * a folder simply has none.
   */
  folder?: {
    directory: string;
    skipped: { file: string; reason: string }[];
    /**
     * Where a library picture can be sent to become an option, relative to the
     * media-library base. Absent when this folder does not take one.
     */
    import_from_library?: string;
    accepts?: string[];
  } | null;
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
  /** Whether one frame can show what this effect does. */
  previewable?: boolean;
  /** Why it cannot, when it cannot. */
  unpreviewable_reason?: string;
  params: EffectParam[];
};

export type Step = { effect: string; values: Record<string, unknown> };

export function defaultsFor(effect: EffectDefinition): Record<string, unknown> {
  return Object.fromEntries(effect.params.map((param) => [param.id, param.default]));
}

/** The parameter offered as a gallery, if this effect has one. */
export function galleryParam(effect: EffectDefinition): EffectParam | undefined {
  return effect.params.find((param) => param.presentation === "gallery");
}

/** One control, chosen by what the parameter says it is. */
export function ParamControl({
  param,
  effectId,
  value,
  onChange,
  onCommit,
  disabled,
  fill,
  filling,
}: {
  param: EffectParam;
  effectId: string;
  value: unknown;
  onChange: (next: unknown) => void;
  /**
   * Called when a drag ends. Where a change is expensive to act on — a preview
   * is a decode — the caller wants the value on every frame of the drag and the
   * work only once, at the end of it.
   */
  onCommit?: (next: unknown) => void;
  disabled?: boolean;
  /**
   * How to fill a parameter that is read off the clip rather than chosen.
   *
   * Passed in rather than done here: this file renders controls from a
   * description and knows nothing about assets or endpoints, which is what
   * lets a new effect arrive without frontend. `canFill` is false with a
   * reason when the answer would be wrong — covering text is per-clip, and one
   * clip's rectangles are in the wrong places on another.
   */
  fill?: { onFill: () => void | Promise<void>; canFill: boolean; reason?: string };
  filling?: boolean;
}) {
  const t = useT();
  const label = paramLabel(t, effectId, param.id, param.label);
  const help = param.help ? paramHelp(t, effectId, param.id, param.help) : "";
  if (param.kind === "choice") {
    return (
      <label className="effect-param">
        <span>{label}</span>
        <Select
          value={String(value ?? "")}
          disabled={disabled}
          onChange={(event) => {
            onChange(event.target.value);
            onCommit?.(event.target.value);
          }}
        >
          {param.options.map((option) => (
            <option key={option.value} value={option.value}>
              {optionLabel(t, effectId, option.value, option.label)}
            </option>
          ))}
        </Select>
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
          disabled={disabled}
          onChange={(event) => {
            onChange(event.target.checked);
            onCommit?.(event.target.checked);
          }}
        />
        <span>{label}</span>
        {help && <small>{help}</small>}
      </label>
    );
  }
  if (param.kind === "regions") {
    const regions = Array.isArray(value) ? value : [];
    return (
      <div className="effect-param effect-param-regions">
        <span>
          {label}
          <b>{regions.length ? `${regions.length}` : "—"}</b>
        </span>
        {/* What is there, in the terms somebody chose it in. A count alone
            does not say whether it is this clip's reading or a stale one
            carried in from the step it was copied from. */}
        <small>
          {regions.length
            ? `Taken from this clip's on-screen text reading.`
            : `Nothing to cover yet.`}
          {help ? ` ${help}` : ""}
        </small>
        {fill && (
          <Button
            variant="secondary"
            size="sm"
            busy={filling}
            disabled={disabled || filling || !fill.canFill}
            title={fill.reason}
            onClick={() => void fill.onFill()}
          >{regions.length ? "Read it again" : "Read this clip"}</Button>
        )}
      </div>
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
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        // On release rather than on every pixel of travel, which would be a
        // request per frame of the drag.
        onPointerUp={(event) => onCommit?.(Number(event.currentTarget.value))}
        onKeyUp={(event) => onCommit?.(Number(event.currentTarget.value))}
      />
      {help && <small>{help}</small>}
    </label>
  );
}
