"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../ui/button";
import { ProviderSwitch, providerOf, useMediaAi } from "./transcription-setup";
import type { ProviderKey } from "./transcription-setup";

/**
 * Asking the machine to read a clip, next to the box where the answer is checked.
 *
 * Everything behind this existed and nothing could reach it: the providers were
 * installable, the job could transcribe speech and read text off frames, and
 * the only way to get words onto an asset was to type them into the textarea
 * yourself. The queue had no producer and no consumer — no endpoint created the
 * job, and the worker did not drain its kind.
 *
 * Two readings, not one. Speech is what the clip says; OCR is what it shows,
 * and a price or a product name burned into the first frame is nowhere in the
 * audio. They are offered separately because a silent screen-recording wants
 * only the second and a talking head only the first.
 *
 * What comes back is a draft and is labelled one. The transcript is recorded
 * with `status: "machine"` and stays that way until a person puts it in the
 * reviewed field and saves — which is why this sits beside that field rather
 * than writing into it. A machine reading of a noisy clip is often most of the
 * way there and wrong in exactly the place that matters.
 */

type Transcript = {
  id: string;
  kind: "speech" | "ocr";
  language: string;
  text: string;
  provider?: string;
  status?: string;
};

type EnrichmentJob = {
  id: string;
  status: string;
  stalled: boolean;
  error: string | null;
  progress: number | null;
  progress_stage: string | null;
  payload: { asset_id?: string; modes?: string[] };
  result: { transcript_ids?: string[] } | null;
};

/** How often to ask again while a reading is running. */
const WATCH_MS = 2500;

/** The provider each mode needs, in the shape `useMediaAi` names them. */
const MODE_PROVIDER: Record<"speech" | "ocr", ProviderKey> = {
  speech: "speech",
  ocr: "ocr",
};

function isRunning(job: EnrichmentJob | null): boolean {
  return Boolean(job && !job.stalled && ["queued", "running"].includes(job.status));
}

export function AutoTranscribe({
  workspaceId,
  assetId,
  hasAudio,
  mediaKind,
  apiFetch,
  canEdit,
  onFinished,
}: {
  workspaceId: string;
  assetId: string;
  hasAudio: boolean;
  mediaKind: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  canEdit: boolean;
  /** A new draft landed, so the asset needs re-reading to show it. */
  onFinished: () => void;
}) {
  // What each mode can even apply to. An audio-less clip has no speech to
  // transcribe and OCR needs frames, so the control says so rather than
  // offering a choice the API will refuse.
  const speechPossible = hasAudio;
  const ocrPossible = mediaKind === "video" || mediaKind === "image";
  const [modes, setModes] = useState<Record<"speech" | "ocr", boolean>>({
    speech: speechPossible,
    ocr: false,
  });
  const [job, setJob] = useState<EnrichmentJob | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const mediaAi = useMediaAi(apiFetch, true);
  // Read by the poll without becoming a dependency of it, or every answer
  // would tear down the timer that asked for it.
  const running = useRef(false);
  const settled = useRef<string | null>(null);

  const chosen = (Object.keys(modes) as ("speech" | "ocr")[]).filter((mode) => modes[mode]);

  const read = useCallback(async () => {
    const response = await apiFetch(
      `/api/workspaces/${workspaceId}/media/library/transcription/jobs`,
    );
    if (!response.ok) return null;
    const body = await response.json();
    const mine = ((body.jobs ?? []) as EnrichmentJob[])
      .filter((item) => item.payload?.asset_id === assetId);
    // Newest first from the API, so the first is this asset's latest attempt.
    const latest = mine[0] ?? null;
    running.current = isRunning(latest);
    setJob(latest);
    return latest;
  }, [apiFetch, assetId, workspaceId]);

  // Nothing resets state when the asset changes because nothing has to: the
  // form this sits in is keyed to the asset, so selecting another clip remounts
  // this with fresh state rather than showing the previous one's progress
  // against the new one's name.
  useEffect(() => {
    let live = true;
    let timer = 0;
    const tick = async () => {
      const latest = await read().catch(() => null);
      if (!live) return;
      // Refresh the asset once, on the transition into a finished state: the
      // draft only exists after that, and re-reading on every poll would fight
      // whatever the operator is typing.
      if (latest && latest.status === "succeeded" && settled.current !== latest.id) {
        settled.current = latest.id;
        onFinished();
      }
      if (running.current) timer = window.setTimeout(() => void tick(), WATCH_MS);
    };
    void tick();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
    // `onFinished` is deliberately not a dependency: the page rebuilds it every
    // render, and depending on it would restart this poll every render with it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [read]);

  const start = useCallback(async () => {
    setBusy(true);
    setFailure("");
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/transcription`,
        { method: "POST", body: JSON.stringify({ modes: chosen }) },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail ?? "The reading could not be started.");
      running.current = true;
      settled.current = null;
      await read();
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The reading could not be started.");
    } finally {
      setBusy(false);
    }
  }, [apiFetch, assetId, chosen, read, workspaceId]);

  // A mode the operator has chosen whose provider is not ready yet. Offered as
  // the download rather than as an error, because that is the actual answer.
  const missing = chosen.filter((mode) => !providerOf(mediaAi.state, MODE_PROVIDER[mode])?.ready);
  const working = busy || isRunning(job);

  if (!speechPossible && !ocrPossible) return null;

  return (
    <div className="auto-transcribe">
      <div className="auto-transcribe-head">
        <span className="auto-transcribe-label">Read it automatically</span>
        <span className="auto-transcribe-modes">
          {speechPossible && (
            <label>
              <input
                type="checkbox"
                checked={modes.speech}
                disabled={working}
                onChange={(event) => setModes((c) => ({ ...c, speech: event.target.checked }))}
              />
              Speech
            </label>
          )}
          {ocrPossible && (
            <label>
              <input
                type="checkbox"
                checked={modes.ocr}
                disabled={working}
                onChange={(event) => setModes((c) => ({ ...c, ocr: event.target.checked }))}
              />
              On-screen text
            </label>
          )}
        </span>
        <Button
          variant="secondary"
          size="sm"
          busy={working}
          disabled={!canEdit || working || chosen.length === 0 || missing.length > 0}
          title={
            canEdit
              ? "Produces a draft transcript to check, not a saved one"
              : "You do not have permission to analyse here"
          }
          onClick={() => void start()}
        >{isRunning(job) ? "Reading…" : "Read this clip"}</Button>
      </div>

      {missing.map((mode) => (
        <ProviderSwitch
          key={mode}
          label={mode === "speech" ? "Transcribe speech" : "Read on-screen text"}
          provider={MODE_PROVIDER[mode]}
          state={mediaAi.state}
          busy={mediaAi.busy}
          onPrepare={(provider) => void mediaAi.prepare(provider)}
          onToggle={(provider, on) => void mediaAi.setActive(provider, on)}
        />
      ))}

      {isRunning(job) && (
        <span className="auto-transcribe-progress">
          <progress max={1} value={job?.progress ?? undefined} />
          <small>{job?.progress_stage ?? "Queued…"}</small>
        </span>
      )}
      {job?.stalled && (
        <p className="auto-transcribe-problem">
          The worker stopped holding this job. It resumes on its own when one is back.
        </p>
      )}
      {!isRunning(job) && job?.status === "failed" && job.error && (
        <p className="auto-transcribe-problem" role="alert">{job.error}</p>
      )}
      {(failure || mediaAi.failure) && (
        <p className="auto-transcribe-problem" role="alert">{failure || mediaAi.failure}</p>
      )}
    </div>
  );
}

/**
 * One machine reading, under the field it is a candidate for.
 *
 * Shown rather than poured into the box. The reviewed field is what a person
 * signed off; filling it silently from a machine would make the two
 * indistinguishable a week later, which is the distinction the whole
 * machine/reviewed split exists to keep.
 */
export function TranscriptDraft({
  transcripts,
  kind,
  onUse,
}: {
  transcripts: Transcript[];
  kind: "speech" | "ocr";
  onUse: (text: string) => void;
}) {
  const draft = transcripts.find((item) => item.kind === kind && item.status === "machine");
  const [open, setOpen] = useState(false);
  if (!draft || !draft.text.trim()) return null;

  return (
    <div className="transcript-draft">
      <div className="transcript-draft-head">
        <span>
          <strong>Machine draft</strong>
          <small>{draft.provider} · {draft.language}</small>
        </span>
        <span className="transcript-draft-actions">
          <Button variant="quiet" size="sm" onClick={() => setOpen((current) => !current)}>
            {open ? "Hide" : "Show"}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => onUse(draft.text)}>
            Use as reviewed
          </Button>
        </span>
      </div>
      {open && <p className="transcript-draft-text">{draft.text}</p>}
    </div>
  );
}
