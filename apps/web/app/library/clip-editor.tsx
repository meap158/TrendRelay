"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { useT } from "../i18n-provider";

type Segment = { label: string; start_seconds: number; end_seconds: number };
type Production = {
  id: string;
  title: string;
  status: string;
  source: { path: string };
  execution?: { enabled?: boolean } | null;
};
type RenderJob = {
  id: string;
  status: string;
  error?: string | null;
  result?: { artifacts?: { path: string; label: string }[] } | null;
};

const MAX_SEGMENTS = 20;
/** A plan starts with one clip, because a plan with none cannot be rendered. */
const FIRST_SEGMENT: Segment = { label: "Hook", start_seconds: 0, end_seconds: 15 };

function secondsLabel(value: number) {
  const whole = Math.max(0, Math.floor(value));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * The advanced editor: build an immutable clip plan for one asset, approve it,
 * and render it locally.
 *
 * It opens over the Library rather than living on its own page, because every
 * input except the plan itself comes from the asset already selected there -
 * the separate page had to ask for a media path by hand, which is a value
 * nobody can type from memory.
 */
export function ClipEditor({
  open,
  workspaceId,
  assetPath,
  assetTitle,
  durationMs,
  canApprove,
  apiFetch,
  onClose,
}: {
  open: boolean;
  workspaceId: string;
  assetPath: string;
  assetTitle: string;
  durationMs: number | null;
  canApprove: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
}) {
  const t = useT();
  const [segments, setSegments] = useState<Segment[]>([FIRST_SEGMENT]);
  const [productions, setProductions] = useState<Production[]>([]);
  const [renders, setRenders] = useState<RenderJob[]>([]);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const durationSeconds = durationMs ? Math.floor(durationMs / 1000) : null;

  const load = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const [status, list] = await Promise.all([
        apiFetch(`/api/workspaces/${workspaceId}/studio/status`).then((r) => r.json()),
        apiFetch(`/api/workspaces/${workspaceId}/studio/productions`).then((r) => r.json()),
      ]);
      const runtime = status?.runtime ?? {};
      setAvailable(Boolean(runtime.installed ?? runtime.available ?? true));
      setReason(typeof runtime.reason === "string" ? runtime.reason : null);
      // Only this asset's plans; the rest belong to whatever opened them.
      setProductions(
        (list?.productions ?? []).filter((item: Production) => item.source?.path === assetPath),
      );
      setRenders(list?.renders ?? []);
    } catch {
      setAvailable(null);
    }
  }, [apiFetch, assetPath, workspaceId]);

  useEffect(() => {
    // Deferred, so the state this sets lands after the render that opened the
    // dialog rather than cascading into it.
    if (open) queueMicrotask(() => void load());
  }, [open, load]);

  function updateSegment(index: number, patch: Partial<Segment>) {
    setSegments(segments.map((item, at) => (at === index ? { ...item, ...patch } : item)));
  }

  const overlong = durationSeconds
    ? segments.filter((segment) => segment.end_seconds > durationSeconds)
    : [];
  const inverted = segments.filter((s) => s.end_seconds <= s.start_seconds);
  const planProblem = inverted.length
    ? "A clip has to end after it starts."
    : overlong.length
      ? `A clip runs past the end of the video (${secondsLabel(durationSeconds ?? 0)}).`
      : null;

  async function propose(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (planProblem) return;
    const form = new FormData(event.currentTarget);
    setBusy("propose");
    setError(null);
    setNotice(null);
    try {
      const response = await apiFetch(`/api/workspaces/${workspaceId}/studio/productions`, {
        method: "POST",
        body: JSON.stringify({
          title: form.get("title"),
          source_asset: assetPath,
          pipeline: form.get("pipeline"),
          budget_usd: Number(form.get("budget_usd")),
          segments,
          confirm_external_action: true,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "The plan could not be created.");
      setNotice("Preflight created. Approve it to allow rendering.");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The plan could not be created.");
    } finally {
      setBusy(null);
    }
  }

  async function act(productionId: string, what: "approve" | "render") {
    setBusy(`${what}-${productionId}`);
    setError(null);
    try {
      const path = what === "approve"
        ? `/api/workspaces/${workspaceId}/studio/productions/${productionId}/approval`
        : `/api/workspaces/${workspaceId}/studio/renders`;
      const response = await apiFetch(path, {
        method: "POST",
        body: JSON.stringify(
          what === "approve"
            ? { decision: "approve", confirm_external_action: true }
            : { production_id: productionId, confirm_external_action: true },
        ),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? `The plan could not be ${what}d.`);
      setNotice(what === "approve" ? "Plan approved." : "Render queued.");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That action failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog
      open={open}
      title={t("clipEditor.heading")}
      description={assetTitle}
      onClose={onClose}
    >
      {available === false && (
        <p className="engine-warning" role="status">
          {reason ?? "OpenMontage is not installed. Install it from Tools to build clip plans."}
        </p>
      )}
      {error && <p className="engine-warning" role="alert">{error}</p>}
      {notice && <p className="clip-editor-note" role="status">{notice}</p>}

      <div className="clip-editor-body">
        <form className="clip-editor-form" onSubmit={propose}>
          <div className="clip-editor-grid">
            <label className="ui-field">Plan title
              <input name="title" required minLength={2} defaultValue={assetTitle.slice(0, 60)} />
            </label>
            <label className="ui-field">Pipeline
              <select name="pipeline" defaultValue="clip-factory">
                <option value="clip-factory">{t("clipEditor.clipFactory")}</option>
                <option value="podcast-repurpose">{t("clipEditor.podcastRepurpose")}</option>
              </select>
            </label>
            <label className="ui-field">Budget cap
              <input name="budget_usd" type="number" min="1" max="100" step="0.01" defaultValue="1" />
            </label>
          </div>

          <div className="clip-editor-plan">
            <div className="clip-editor-plan-head">
              <strong>{t("clipEditor.clips")}</strong>
              <span>
                {segments.length} of {MAX_SEGMENTS}
                {durationSeconds ? ` · source runs ${secondsLabel(durationSeconds)}` : ""}
              </span>
            </div>
            {segments.map((segment, index) => (
              <div className="segment-row" key={index}>
                <label className="ui-field">Label
                  <input
                    value={segment.label}
                    onChange={(event) => updateSegment(index, { label: event.target.value })}
                  />
                </label>
                <label className="ui-field">Start
                  <input
                    type="number" min="0" step="0.1" value={segment.start_seconds}
                    onChange={(event) => updateSegment(index, { start_seconds: Number(event.target.value) })}
                  />
                </label>
                <label className="ui-field">End
                  <input
                    type="number" min="0.1" step="0.1" value={segment.end_seconds}
                    onChange={(event) => updateSegment(index, { end_seconds: Number(event.target.value) })}
                  />
                </label>
                {segments.length > 1 && (
                  <button
                    type="button"
                    className="slot-remove"
                    aria-label={`Remove ${segment.label}`}
                    onClick={() => setSegments(segments.filter((_item, at) => at !== index))}
                  >×</button>
                )}
              </div>
            ))}
            {/* Stated rather than left to fail at the renderer, which reports it
                as a plan error long after the numbers were typed. */}
            {planProblem && <p className="clip-editor-problem">{planProblem}</p>}
            <Button
              variant="quiet"
              size="sm"
              disabled={segments.length >= MAX_SEGMENTS}
              onClick={() => setSegments([
                ...segments,
                {
                  label: `Clip ${segments.length + 1}`,
                  start_seconds: segments[segments.length - 1]?.end_seconds ?? 0,
                  end_seconds: (segments[segments.length - 1]?.end_seconds ?? 0) + 15,
                },
              ])}
            >{t("clipEditor.addClip")}</Button>
          </div>

          <Button
            type="submit"
            variant="primary"
            busy={busy === "propose"}
            disabled={Boolean(planProblem) || available === false}
            title={planProblem ?? undefined}
          >{t("clipEditor.createPreflight")}</Button>
        </form>

        {productions.length > 0 && (
          <div className="clip-editor-plans">
            <strong>{t("clipEditor.plansForClip")}</strong>
            {productions.map((production) => (
              <div className="clip-editor-plan-row" key={production.id}>
                <span>{production.title}</span>
                <Badge tone={production.status === "approved" ? "good" : "neutral"}>
                  {production.status.replace(/_/g, " ")}
                </Badge>
                {production.status === "awaiting_approval" && (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!canApprove}
                    busy={busy === `approve-${production.id}`}
                    title={canApprove ? undefined : "Only owners and approvers can approve a plan"}
                    onClick={() => void act(production.id, "approve")}
                  >{t("clipEditor.approve")}</Button>
                )}
                {production.execution?.enabled && (
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={!canApprove}
                    busy={busy === `render-${production.id}`}
                    onClick={() => void act(production.id, "render")}
                  >{t("clipEditor.render")}</Button>
                )}
              </div>
            ))}
          </div>
        )}

        {renders.length > 0 && (
          <div className="clip-editor-plans">
            <strong>{t("clipEditor.recentRenders")}</strong>
            {renders.slice(0, 5).map((job) => (
              <div className="clip-editor-plan-row" key={job.id}>
                <span>{job.result?.artifacts?.[0]?.label ?? job.id}</span>
                <Badge tone={job.status === "succeeded" ? "good" : job.status === "failed" ? "bad" : "neutral"}>
                  {job.status}
                </Badge>
                {job.error && <small className="clip-editor-problem">{job.error}</small>}
              </div>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}
