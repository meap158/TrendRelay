"use client";

import { useMemo, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { useJobs } from "../jobs-provider";
import { ProviderSwitch, providerOf, useMediaAi } from "./transcription-setup";
import type { ProviderKey } from "./transcription-setup";

/**
 * Reading a selection, rather than one clip at a time.
 *
 * Transcription is the step the other two depend on - a caption is built from a
 * transcript and so is a voiceover - so it is the one most likely to be wanted
 * across a whole import. Doing it per asset meant opening two hundred clips to
 * press the same button, which is why the two workflows built on top of it were
 * reachable in bulk and the one underneath them was not.
 *
 * **Modes are narrowed per asset, not just per selection.** Speech needs audio
 * and OCR needs frames, so a mixed selection supports each on a different
 * subset. Sending both for every asset would queue readings that can only fail
 * - an image has no speech to find - so each asset is sent the modes it can
 * actually answer, and anything left with none is skipped and counted.
 *
 * **A provider that is not ready is offered as the download.** The API refuses
 * at the click rather than in the worker so the answer can be acted on while
 * somebody is looking at it; showing the switch here means that refusal is
 * usually never reached.
 */

type Mode = "speech" | "ocr";

type Target = {
  id: string;
  title: string;
  mediaKind: string;
};

/** The provider each mode needs, in the shape `useMediaAi` names them. */
const MODE_PROVIDER: Record<Mode, ProviderKey> = { speech: "speech", ocr: "ocr" };

const MODE_LABEL: Record<Mode, string> = {
  speech: "Transcribe speech",
  ocr: "Read on-screen text",
};

/** What each reading needs to exist at all. */
function supports(mediaKind: string, mode: Mode): boolean {
  return mode === "speech"
    ? mediaKind === "video" || mediaKind === "audio"
    : mediaKind === "video" || mediaKind === "image";
}

export function BatchTranscribe({
  open,
  workspaceId,
  targets,
  canEdit,
  apiFetch,
  onClose,
  onQueued,
}: {
  open: boolean;
  workspaceId: string;
  targets: Target[];
  canEdit: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onQueued?: (message: string, assetIds: string[]) => void;
}) {
  // Only `refresh` from the jobs drawer, deliberately. The neighbouring
  // editors also push their new jobs straight into the drawer, but that
  // call is mid-rename in an uncommitted change - depending on it would
  // make this commit uncompilable without that one. A refresh reaches the
  // same drawer a moment later and owes nothing to work in flight.
  const { refresh: refreshJobs } = useJobs();
  const mediaAi = useMediaAi(apiFetch, open);
  const [chosen, setChosen] = useState<Record<Mode, boolean>>({ speech: true, ocr: false });
  const [language, setLanguage] = useState("");
  const [queueing, setQueueing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [queued, setQueued] = useState<string | null>(null);

  const modes = useMemo(
    () => (Object.keys(chosen) as Mode[]).filter((mode) => chosen[mode]),
    [chosen],
  );

  // How many of the selection each reading can even apply to, so the counts on
  // the checkboxes are the truth about this selection rather than a general
  // statement about what the mode does.
  const reach = useMemo(() => ({
    speech: targets.filter((target) => supports(target.mediaKind, "speech")).length,
    ocr: targets.filter((target) => supports(target.mediaKind, "ocr")).length,
  }), [targets]);

  /** Each asset paired with the readings it can actually answer. */
  const work = useMemo(() => targets
    .map((target) => ({
      target,
      modes: modes.filter((mode) => supports(target.mediaKind, mode)),
    }))
    .filter((item) => item.modes.length > 0), [targets, modes]);

  const skipped = targets.length - work.length;
  // A chosen mode whose provider is not ready. Offered as the download rather
  // than as an error, because that is the actual answer.
  const missing = modes.filter(
    (mode) => !providerOf(mediaAi.state, MODE_PROVIDER[mode])?.ready,
  );
  const ready = canEdit && work.length > 0 && missing.length === 0;

  async function start() {
    setQueueing(true);
    setProblem(null);
    setQueued(null);
    try {
      const queuedIds: string[] = [];
      const queuedJobs: unknown[] = [];
      const failures: string[] = [];
      // Four at a time, the same as the caption batch: enough to make a
      // configured run quick without turning a large selection into a request
      // storm from the browser.
      for (let at = 0; at < work.length; at += 4) {
        const group = work.slice(at, at + 4);
        const results = await Promise.all(group.map(async (item) => {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/media/library/assets/${item.target.id}/transcription`,
            {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                modes: item.modes,
                ...(language.trim() ? { language: language.trim() } : {}),
              }),
            },
          );
          const body = await response.json().catch(() => ({}));
          return { item, response, body };
        }));
        for (const result of results) {
          if (result.response.ok) {
            queuedIds.push(result.item.target.id);
            if (result.body?.job) queuedJobs.push(result.body.job);
          } else {
            failures.push(typeof result.body?.detail === "string"
              ? `${result.item.target.title}: ${result.body.detail}`
              : `${result.item.target.title}: the reading could not be started.`);
          }
        }
      }
      if (queuedJobs.length) void refreshJobs();
      const summary = `Reading ${queuedIds.length} ${queuedIds.length === 1 ? "clip" : "clips"}.`;
      setQueued(summary);
      if (failures.length) setProblem(`${failures.length} could not start. ${failures[0]}`);
      if (queuedIds.length) onQueued?.(summary, queuedIds);
    } catch {
      setProblem("The readings could not be queued.");
    } finally {
      setQueueing(false);
    }
  }

  return (
    <Dialog
      open={open}
      title="Transcribe"
      description={`${targets.length} selected`}
      onClose={onClose}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            busy={queueing}
            disabled={!ready || queueing}
            onClick={() => void start()}
          >{work.length > 0 ? `Read ${work.length}` : "Read"}</Button>
        </>
      }
    >
      <div className="batch-transcribe-modes">
        {(["speech", "ocr"] as Mode[]).map((mode) => (
          <label key={mode} className="batch-transcribe-mode">
            <input
              type="checkbox"
              checked={chosen[mode]}
              disabled={reach[mode] === 0}
              onChange={(event) => setChosen(
                (current) => ({ ...current, [mode]: event.target.checked }),
              )}
            />
            <span>
              <strong>{MODE_LABEL[mode]}</strong>
              {/* The count for this selection, not a general claim about the
                  mode. A checkbox reading "0 of 40" explains itself; one that
                  is simply greyed out does not. */}
              <small>{reach[mode] === 0
                ? "nothing in this selection can be read this way"
                : `${reach[mode]} of ${targets.length} selected`}</small>
            </span>
          </label>
        ))}
      </div>

      <label className="batch-transcribe-language">
        <span>Language <em>optional</em></span>
        <input
          value={language}
          maxLength={40}
          placeholder="Detected per clip when left empty"
          onChange={(event) => setLanguage(event.target.value)}
        />
        <small>
          Naming a language the model then disagrees with is worse than letting
          it detect one - but music over speech detects badly, and you usually
          know the answer.
        </small>
      </label>

      {/* The download, where a missing provider is what stands in the way. */}
      {missing.map((mode) => (
        <ProviderSwitch
          key={mode}
          label={MODE_LABEL[mode]}
          provider={MODE_PROVIDER[mode]}
          state={mediaAi.state}
          busy={mediaAi.busy}
          onPrepare={(provider) => void mediaAi.prepare(provider)}
          onToggle={(provider, on) => void mediaAi.setActive(provider, on)}
        />
      ))}

      {skipped > 0 && modes.length > 0 && (
        <p className="batch-transcribe-note">
          {skipped} of the selection {skipped === 1 ? "has" : "have"} nothing the
          chosen readings can find, and {skipped === 1 ? "is" : "are"} left out.
        </p>
      )}
      {modes.length === 0 && (
        <p className="batch-transcribe-note">Choose at least one reading.</p>
      )}

      {queued && <p className="batch-transcribe-note" aria-live="polite">{queued}</p>}
      {problem && <p className="batch-transcribe-note problem" role="alert">{problem}</p>}
    </Dialog>
  );
}
