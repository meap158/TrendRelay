"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";

/**
 * Captions: their own class of work, not an effect.
 *
 * An effect takes frames and returns frames, stacks with other effects, and its
 * order matters. A caption comes from the audio, depends on a transcript that
 * may already exist, need not touch the picture at all, and does not stack —
 * which is why it has its own editor rather than a row in the effect stack.
 *
 * Like the effect editor, every control here is generated from what the API
 * declares. The styles, their settings, which of them need word timings, and
 * whether the machine can currently transcribe or translate all arrive from
 * `/captions/styles`, so a style added to the registry appears here with a
 * working form and no change to this file.
 *
 * Preview is free and render is not, so the preview runs on every change and
 * the render stays a separate, deliberate act.
 */

type CaptionStyle = {
  id: string;
  label: string;
  summary: string;
  style: Record<string, unknown>;
  layout: Record<string, unknown>;
  needs_word_timings: boolean;
};

type Provider = {
  provider: string;
  ready: boolean;
  runtime_ready?: boolean;
  model_cached?: boolean;
  source_active?: boolean;
  pairs?: { from: string; to: string; label: string }[];
};

type StylesResponse = {
  styles: CaptionStyle[];
  deliveries: string[];
  sample: string;
  speech: Provider;
  translation: Provider;
};

type Cue = {
  index: number;
  start_ms: number;
  end_ms: number;
  lines: string[];
  cps: number;
};

type Preview = {
  transcript_id: string;
  source_language: string | null;
  cue_count: number;
  duration_ms: number;
  cues: Cue[];
  notes: string[];
};

function timecode(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function CaptionEditor({
  open,
  workspaceId,
  assetId,
  assetTitle,
  onClose,
  apiFetch,
  canEdit,
}: {
  open: boolean;
  workspaceId: string;
  assetId: string;
  assetTitle: string;
  onClose: () => void;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  canEdit: boolean;
}) {
  const [catalogue, setCatalogue] = useState<StylesResponse | null>(null);
  const [styleId, setStyleId] = useState("broadcast");
  const [translateTo, setTranslateTo] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [delivery, setDelivery] = useState("sidecar");
  const [queued, setQueued] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  // A preview asked for after a newer one must not overwrite it: the requests
  // are independent and the slower one can land last.
  const latest = useRef(0);

  useEffect(() => {
    if (!open || !workspaceId) return;
    let live = true;
    void (async () => {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/captions/styles`,
      );
      if (!live || !response.ok) return;
      setCatalogue((await response.json()) as StylesResponse);
    })();
    return () => {
      live = false;
    };
  }, [open, workspaceId, apiFetch]);

  const load = useCallback(async () => {
    if (!open || !workspaceId || !assetId) return;
    const ticket = ++latest.current;
    setBusy(true);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/captions/preview`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            style_id: styleId,
            translate_to: translateTo || null,
          }),
        },
      );
      if (ticket !== latest.current) return;
      const body = await response.json();
      if (!response.ok) {
        setPreview(null);
        setProblem(typeof body?.detail === "string" ? body.detail : "That did not work.");
        return;
      }
      setProblem(null);
      setPreview(body as Preview);
    } catch {
      if (ticket === latest.current) setProblem("The preview could not be built.");
    } finally {
      if (ticket === latest.current) setBusy(false);
    }
  }, [open, workspaceId, assetId, styleId, translateTo, apiFetch]);

  useEffect(() => {
    // Deferred out of the effect body: `load` sets state on its first line, and
    // doing that synchronously here cascades a second render before the first
    // has painted.
    queueMicrotask(() => void load());
  }, [load]);

  const render = useCallback(async () => {
    setRendering(true);
    setQueued(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/captions`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            style_id: styleId,
            translate_to: translateTo || null,
            delivery,
          }),
        },
      );
      const body = await response.json();
      if (!response.ok) {
        setProblem(typeof body?.detail === "string" ? body.detail : "That did not work.");
        return;
      }
      setProblem(null);
      // Burning re-encodes every frame, so the honest answer is that it has
      // been queued rather than that it is done.
      setQueued(
        delivery === "sidecar"
          ? "Subtitle files are being written."
          : "Queued. The captioned cut appears here when the render finishes.",
      );
    } catch {
      setProblem("The render could not be queued.");
    } finally {
      setRendering(false);
    }
  }, [workspaceId, assetId, styleId, translateTo, delivery, apiFetch]);

  const chosen = catalogue?.styles.find((item) => item.id === styleId);
  const pairs = catalogue?.translation.pairs ?? [];
  const speechReady = catalogue?.speech.ready ?? false;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Captions"
      description={assetTitle}
      size="wide"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          {/* The delivery choice sits with the button that acts on it, because
              it changes what that button costs: sidecars are a kilobyte,
              burning in re-encodes every frame. */}
          <label className="caption-delivery">
            <span>Deliver</span>
            <select
              value={delivery}
              onChange={(event) => setDelivery(event.target.value)}
            >
              <option value="sidecar">Subtitle files only</option>
              <option value="burned">Burned into the video</option>
              <option value="both">Both</option>
            </select>
          </label>
          <Button
            variant="primary"
            disabled={!canEdit || !preview}
            busy={rendering}
            title={
              canEdit
                ? "Write the subtitle files, and encode a captioned cut if asked for"
                : "You do not have permission to render here"
            }
            onClick={() => void render()}
          >
            Create captions
          </Button>
        </>
      }
    >
      <div className="caption-editor">
        {/* The runtime is the first thing to say, because every control below
            depends on it and an empty style list would otherwise read as a
            missing feature rather than a missing install. */}
        {catalogue && !speechReady && (
          <p className="caption-editor-setup">
            Automatic transcription is not prepared yet, so captions can only be
            built from a transcript entered by hand. Run{" "}
            <code>python scripts/media_ai.py install-speech</code> to enable it.
          </p>
        )}

        <section className="caption-editor-styles" aria-label="Caption style">
          <h4>Style</h4>
          <div className="caption-style-grid">
            {(catalogue?.styles ?? []).map((item) => (
              <button
                key={item.id}
                type="button"
                className="caption-style-choice"
                data-on={item.id === styleId ? "" : undefined}
                aria-pressed={item.id === styleId}
                onClick={() => setStyleId(item.id)}
              >
                <strong>{item.label}</strong>
                <small>{item.summary}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="caption-editor-language" aria-label="Language">
          <h4>Language</h4>
          {pairs.length === 0 ? (
            <p className="caption-editor-note">
              Captions will be in the language spoken. To translate them, run{" "}
              <code>python scripts/media_ai.py install-translate</code>.
            </p>
          ) : (
            <select
              value={translateTo}
              onChange={(event) => setTranslateTo(event.target.value)}
            >
              <option value="">As spoken — no translation</option>
              {pairs.map((pair) => (
                <option key={`${pair.from}-${pair.to}`} value={pair.to}>
                  {pair.label}
                </option>
              ))}
            </select>
          )}
          {translateTo && chosen?.needs_word_timings && (
            <p className="caption-editor-note">
              This style highlights the word being spoken, which a translation
              cannot carry — word order changes, so the measured timings no
              longer match the words. It will show whole cues instead.
            </p>
          )}
        </section>

        <section className="caption-editor-preview" aria-label="Preview">
          <h4>
            Preview
            {preview && (
              <Badge tone="neutral">
                {preview.cue_count} cues · {timecode(preview.duration_ms)}
              </Badge>
            )}
          </h4>
          {problem && <p className="caption-editor-problem">{problem}</p>}
          {queued && <p className="caption-editor-queued">{queued}</p>}
          {busy && !preview && <p className="caption-editor-note">Building…</p>}
          {preview && (
            <ol className="caption-cue-list">
              {preview.cues.map((cue) => (
                <li key={cue.index}>
                  <span className="caption-cue-time">
                    {timecode(cue.start_ms)}
                  </span>
                  <span className="caption-cue-text">
                    {cue.lines.map((line, at) => (
                      <span key={at}>{line}</span>
                    ))}
                  </span>
                  {/* Reading speed is the number that decides whether a caption
                      works, so it is shown rather than kept internal. */}
                  <span className="caption-cue-cps" title="Characters per second">
                    {cue.cps}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {preview?.notes.map((note) => (
            <p className="caption-editor-note" key={note}>
              {note}
            </p>
          ))}
        </section>

      </div>
    </Dialog>
  );
}
