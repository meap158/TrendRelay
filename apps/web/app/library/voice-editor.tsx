"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { SegmentedControl } from "../ui/segmented";

/**
 * A spoken take of a clip's words.
 *
 * Every decision here exists because this one costs money per character, which
 * makes it a different kind of control from the rest of the Library.
 *
 * **The count is shown before the button is pressed.** The API refuses at the
 * queue rather than in the worker precisely so the refusal can be actionable -
 * "this needs 4,200 characters and 900 are left" while somebody is still
 * looking at it, rather than a failed row twenty minutes later. This screen
 * finishes that thought: the number is on screen next to what is left, so the
 * refusal is usually never reached.
 *
 * **The script defaults to the reviewed transcript, and says so.** Not the
 * machine draft: an unread draft voiced in a creator's voice is unchecked words
 * put in somebody's mouth, and the Library keeps the two apart so this
 * distinction can be made. Typed text overrides, and the screen says which one
 * is about to be spoken rather than leaving it to be inferred.
 *
 * **There is no retry.** Every generation is billed again, so a failure is
 * something to read, not something to run twice. The button says "Generate"
 * once and a failed job offers no second press.
 */

type Voice = {
  voice_id: string;
  name: string;
  category?: string | null;
  labels?: Record<string, string>;
};

type VoiceStatus = {
  configured: boolean;
  reachable: boolean;
  reason?: string | null;
  tier?: string | null;
  characters_remaining?: number | null;
};

type Transcript = {
  id: string;
  kind: string;
  status: string;
  language?: string | null;
  text?: string | null;
};

type Job = {
  id: string;
  status: string;
  error?: string | null;
  progress_stage?: string | null;
  payload?: { asset_id?: string; characters?: number } | null;
  result?: { version_ids?: string[] } | null;
};

/** How the sound comes back. The same word the caption job uses for the same choice. */
type Deliver = "audio" | "video" | "both";

const DELIVERY: { value: Deliver; label: string; title: string }[] = [
  { value: "audio", label: "Audio", title: "The speech on its own, to hear before committing" },
  { value: "video", label: "On the clip", title: "The clip with the speech on it" },
  { value: "both", label: "Both", title: "The speech, and the clip with it on" },
];

export function VoiceEditor({
  open,
  workspaceId,
  assetId,
  assetTitle,
  mediaKind,
  canEdit,
  apiFetch,
  onClose,
}: {
  open: boolean;
  workspaceId: string;
  assetId: string;
  assetTitle: string;
  /** Only a video has a picture to put a voiceover on. */
  mediaKind: string;
  canEdit: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
}) {
  /**
   * Everything the form is drawn from, in one piece.
   *
   * One object rather than three states so a half-loaded form cannot exist:
   * the voices, what is left to spend and the transcript are read together and
   * are only useful together. Loading is derived from its absence rather than
   * tracked, which also keeps this effect free of a synchronous `setState`.
   */
  const [data, setData] = useState<{
    voices: Voice[];
    status: VoiceStatus | null;
    transcript: Transcript | null;
  } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [voiceId, setVoiceId] = useState("");
  /** The override. Empty means "use the transcript", which is the common case. */
  const [script, setScript] = useState("");
  const [deliver, setDeliver] = useState<Deliver>("audio");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);
  const polling = useRef<number | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();

    Promise.all([
      apiFetch(`/api/workspaces/${workspaceId}/media/library/voice/voices`,
        { signal: controller.signal }),
      apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${assetId}`,
        { signal: controller.signal }),
    ])
      .then(async ([voiceResponse, assetResponse]) => {
        const voicePayload = (await voiceResponse.json()) as {
          voices?: Voice[]; status?: VoiceStatus; detail?: string;
        };
        if (!voiceResponse.ok) {
          throw new Error(voicePayload.detail ?? "The voices could not be read.");
        }
        const assetPayload = (await assetResponse.json()) as { transcripts?: Transcript[] };
        // Reviewed only. A machine draft is deliberately not offered here -
        // see the note at the top of this file.
        const reviewed = (assetPayload.transcripts ?? []).find(
          (item) => item.kind === "speech" && item.status === "reviewed"
            && (item.text ?? "").trim(),
        );
        setData({
          voices: voicePayload.voices ?? [],
          status: voicePayload.status ?? null,
          transcript: reviewed ?? null,
        });
        setLoadError(null);
        setVoiceId((current) => current || voicePayload.voices?.[0]?.voice_id || "");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setLoadError(
          reason instanceof Error ? reason.message : "Voice generation is unavailable.",
        );
      });

    return () => controller.abort();
  }, [open, reload, apiFetch, workspaceId, assetId]);

  // A job left running must not keep a timer alive behind a closed screen.
  useEffect(() => () => {
    if (polling.current) window.clearInterval(polling.current);
  }, []);

  const refreshJob = useCallback(async () => {
    try {
      const response = await apiFetch(`/api/workspaces/${workspaceId}/media/library/voice/jobs`);
      const payload = (await response.json()) as { jobs?: Job[] };
      const mine = (payload.jobs ?? []).find((item) => item.payload?.asset_id === assetId);
      if (!mine) return;
      setJob(mine);
      if (["succeeded", "failed", "cancelled"].includes(mine.status) && polling.current) {
        window.clearInterval(polling.current);
        polling.current = null;
        // The allowance moved, so the number on screen is stale.
        if (mine.status === "succeeded") setReload((count) => count + 1);
      }
    } catch {
      // A missed poll is not worth reporting; the next one answers.
    }
  }, [apiFetch, workspaceId, assetId]);

  // A recording has no picture, so the mux cannot run and the API refuses it
  // at the queue. Not offering it here means that refusal is never reached -
  // the same reason the character count is on screen beside the button.
  const canMux = mediaKind === "video";
  const delivery = canMux ? DELIVERY : DELIVERY.slice(0, 1);

  const voices = data?.voices ?? [];
  const status = data?.status ?? null;
  const transcript = data?.transcript ?? null;
  const loading = open && !data && !loadError;

  const typed = script.trim();
  const spoken = typed || (transcript?.text ?? "").trim();
  const characters = spoken.length;
  const remaining = status?.characters_remaining ?? null;
  // Known and short is the only state worth blocking on. An unknown allowance
  // must not read as an empty account: a flaky status call is not a refusal.
  const tooLong = remaining !== null && characters > remaining;
  const ready = Boolean(voiceId) && characters > 0 && !tooLong && canEdit;

  async function generate() {
    setQueueing(true);
    setError(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/voiceover`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            voice_id: voiceId,
            deliver,
            // Sent only when it is an override. Left out, the API reads the
            // reviewed transcript itself - which keeps one rule in one place
            // rather than two that can disagree.
            ...(typed ? { text: typed } : {}),
            ...(!typed && transcript ? { transcript_id: transcript.id } : {}),
          }),
        },
      );
      const payload = (await response.json()) as { job?: Job; detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "The voiceover could not be queued.");
      setJob(payload.job ?? null);
      if (polling.current) window.clearInterval(polling.current);
      polling.current = window.setInterval(() => void refreshJob(), 2000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The voiceover could not be queued.");
    } finally {
      setQueueing(false);
    }
  }

  const unavailable = status && !status.reachable;

  return (
    <Dialog
      open={open}
      title="Voiceover"
      description={assetTitle}
      onClose={onClose}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            busy={queueing}
            disabled={!ready || queueing || Boolean(unavailable)}
            onClick={() => void generate()}
          >Generate</Button>
        </>
      }
    >
      {loading && <p className="voice-note">Reading the voices…</p>}

      {unavailable && (
        <p className="voice-note problem" role="alert">
          {status?.reason ?? "ElevenLabs cannot be reached."}
        </p>
      )}

      {(error ?? loadError) && <p className="voice-note problem" role="alert">{error ?? loadError}</p>}

      {!loading && !unavailable && (
        <>
          <label className="voice-field">
            <span>Voice</span>
            <select value={voiceId} onChange={(event) => setVoiceId(event.target.value)}>
              {voices.length === 0 && <option value="">No voices on this key</option>}
              {voices.map((voice) => (
                <option key={voice.voice_id} value={voice.voice_id}>
                  {voice.name}{voice.category ? ` · ${voice.category}` : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="voice-field">
            <span>
              Script
              {/* Which of the two is about to be spoken, said rather than
                  inferred. The default is the reviewed transcript and the
                  common case is leaving this empty. */}
              <em>{typed
                ? "your own words"
                : transcript
                  ? `the reviewed transcript${transcript.language ? ` · ${transcript.language}` : ""}`
                  : "no reviewed transcript on this asset"}</em>
            </span>
            <textarea
              rows={5}
              value={script}
              maxLength={20_000}
              placeholder={transcript?.text
                ? transcript.text.slice(0, 400)
                : "Type what should be said. This asset has no reviewed transcript to fall back on."}
              onChange={(event) => setScript(event.target.value)}
            />
            <small>
              {typed
                ? "Typed words override the transcript."
                : "Leave empty to speak the reviewed transcript. A machine draft is never voiced."}
            </small>
          </label>

          <div className="voice-field">
            <span>Deliver</span>
            <SegmentedControl
              value={deliver}
              options={delivery}
              onChange={(next) => setDeliver(next)}
              label="What comes back"
            />
            <small>
              {!canMux
                ? "This is a recording, so there is no picture to put the speech on."
                : deliver === "audio"
                ? "The speech on its own. The clip is untouched."
                : deliver === "video"
                  ? "The clip with this speech in place of its own sound."
                  : "Both: the speech to check, and the clip with it on."}
            </small>
          </div>

          {/* The cost, beside what is left to spend. This is the whole reason
              the refusal lives at the queue rather than in the worker: at this
              moment it is still a number somebody can act on. */}
          <p className={`voice-cost${tooLong ? " problem" : ""}`}>
            <strong>{characters.toLocaleString()} characters</strong>
            {remaining === null
              ? " · allowance unknown"
              : ` · ${remaining.toLocaleString()} left on this key`}
            {tooLong && (
              <>
                {" "}— {(characters - remaining!).toLocaleString()} more than the plan has.
                Shorten the script, or top up the plan.
              </>
            )}
          </p>

          {job && (
            <p className="voice-note" aria-live="polite">
              <Badge tone={job.status === "succeeded" ? "good"
                : job.status === "failed" ? "warn" : "neutral"}>
                {job.status}
              </Badge>{" "}
              {job.status === "succeeded"
                ? "Filed on this asset. Close and reopen the asset to see the new version."
                : job.status === "failed"
                  // Named rather than offered a retry: a second attempt is a
                  // second charge, so this is something to read.
                  ? job.error ?? "The generation failed."
                  : job.progress_stage ?? "Generating…"}
            </p>
          )}
        </>
      )}
    </Dialog>
  );
}
