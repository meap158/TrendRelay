"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { SegmentedControl } from "../ui/segmented";

/**
 * A machine reading, read properly.
 *
 * The draft used to expand in place, under the field it is a candidate for.
 * That is fine for a sentence of speech and wrong for on-screen text: a clip
 * sampled every second or two produces hundreds of deduplicated lines, and the
 * whole form - the campaign metadata below it, the save button - was pushed off
 * the screen by a wall of glyphs nobody could read in that shape anyway.
 *
 * So the full reading opens here instead, where it has room, and three things
 * become possible that a paragraph could not offer:
 *
 * **Times.** Both readings already carry them - speech a segment per utterance,
 * OCR a frame holding several lines - and neither was ever shown. A line with a
 * time can be found in the clip; a line in a wall cannot.
 *
 * **Translation.** Read in a language you do not speak, a transcript is
 * unreviewable, which makes "reviewed" a rubber stamp. The translation is shown
 * beside the original rather than replacing it, because what gets saved is
 * still the reading of the clip, and a translation is a way to check it.
 *
 * **The original, next to it.** A reader who can read both is the one who can
 * tell whether the machine heard correctly.
 */

export type TranscriptSegment = {
  start_ms?: number;
  end_ms?: number;
  timestamp_ms?: number;
  text?: string;
  lines?: { text: string; confidence?: number }[];
};

export type ReadableTranscript = {
  id: string;
  kind: "speech" | "ocr";
  language: string;
  text: string;
  provider?: string;
  status?: string;
  segments?: TranscriptSegment[];
};

type TranslatedLine = { start_ms: number; text: string; source: string };

/** One `{at, text}` per timed line, whichever reading produced it. */
function timedLines(segments: TranscriptSegment[] | undefined): { at: number; text: string }[] {
  const lines: { at: number; text: string }[] = [];
  for (const segment of segments ?? []) {
    if (segment.text) {
      lines.push({ at: segment.start_ms ?? segment.timestamp_ms ?? 0, text: segment.text });
      continue;
    }
    for (const line of segment.lines ?? []) {
      if (line.text) lines.push({ at: segment.timestamp_ms ?? 0, text: line.text });
    }
  }
  return lines;
}

/** m:ss, which is how somebody scrubbing a clip thinks about where they are. */
function atLabel(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function TranscriptReader({
  open,
  transcript,
  workspaceId,
  assetId,
  apiFetch,
  onClose,
  onUse,
}: {
  open: boolean;
  transcript: ReadableTranscript | null;
  workspaceId: string;
  assetId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  /** Put a reading into the reviewed field. The original, or the translation. */
  onUse: (text: string) => void;
}) {
  const [pairs, setPairs] = useState<{ from: string; to: string; label: string }[]>([]);
  const [target, setTarget] = useState("");
  const [translated, setTranslated] = useState<{ text: string; lines: TranslatedLine[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"timed" | "whole">("timed");

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    apiFetch(`/api/workspaces/${workspaceId}/media/library/translation/pairs`,
      { signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as { pairs?: typeof pairs };
        setPairs(payload.pairs ?? []);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [open, apiFetch, workspaceId]);

  // A different draft is a different reading; carrying the last translation
  // across would show one clip's words under another's times.
  useEffect(() => {
    setTranslated(null);
    setError(null);
  }, [transcript?.id]);

  const source = (transcript?.language ?? "").toLowerCase();
  // OCR reads glyphs and reports `und`, so there is no direction to translate
  // from and the control says so rather than offering a choice that fails.
  const translatable = Boolean(source) && source !== "und";
  const targets = pairs.filter((pair) => pair.from === source);

  const translate = useCallback(async () => {
    if (!transcript || !target) return;
    setBusy(true);
    setError(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/transcripts/translate`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            kind: transcript.kind,
            status: transcript.status ?? "machine",
            target,
          }),
        },
      );
      const payload = (await response.json()) as {
        text?: string; lines?: TranslatedLine[]; detail?: string;
      };
      if (!response.ok) throw new Error(payload.detail ?? "That could not be translated.");
      setTranslated({ text: payload.text ?? "", lines: payload.lines ?? [] });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That could not be translated.");
    } finally {
      setBusy(false);
    }
  }, [transcript, target, apiFetch, workspaceId, assetId]);

  if (!transcript) return null;

  const lines = timedLines(transcript.segments);
  const byTime = translated?.lines.length
    ? translated.lines.map((line) => ({ at: line.start_ms, text: line.text, source: line.source }))
    : lines.map((line) => ({ ...line, source: "" }));

  return (
    <Dialog
      open={open}
      size="wide"
      title={transcript.kind === "speech" ? "What the clip says" : "What the clip shows"}
      description={`${transcript.provider ?? "machine"} · ${transcript.language}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>Close</Button>
          {translated && (
            <Button variant="secondary" onClick={() => onUse(translated.text)}>
              Use the translation
            </Button>
          )}
          <Button variant="primary" onClick={() => onUse(transcript.text)}>
            Use this reading
          </Button>
        </>
      }
    >
      <div className="transcript-reader-tools">
        {lines.length > 0 && (
          <SegmentedControl
            value={view}
            options={[
              { value: "timed" as const, label: "By time" },
              { value: "whole" as const, label: "Whole text" },
            ]}
            onChange={setView}
            label="How to read it"
          />
        )}
        {translatable ? (
          <span className="transcript-reader-translate">
            <select
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              aria-label="Translate into"
            >
              <option value="">Translate into…</option>
              {targets.map((pair) => (
                <option key={pair.to} value={pair.to}>{pair.label}</option>
              ))}
            </select>
            <Button
              variant="secondary"
              size="sm"
              busy={busy}
              disabled={!target || busy}
              onClick={() => void translate()}
            >Translate</Button>
            {translated && (
              <Button variant="quiet" size="sm" onClick={() => setTranslated(null)}>
                Show the original
              </Button>
            )}
          </span>
        ) : (
          <small className="transcript-reader-note">
            Read as glyphs rather than as a language, so there is nothing to
            translate from.
          </small>
        )}
      </div>

      {targets.length === 0 && translatable && (
        <p className="transcript-reader-note">
          No language packages are installed for {transcript.language}. Add them from
          Tools → Argos Translate.
        </p>
      )}

      {error && <p className="transcript-reader-problem" role="alert">{error}</p>}

      {translated && (
        <p className="transcript-reader-note">
          <Badge tone="accent">Translated</Badge> Shown to help you check the reading.
          What gets saved is still whichever you choose below.
        </p>
      )}

      {view === "timed" && byTime.length > 0 ? (
        <ol className="transcript-reader-lines">
          {byTime.map((line, index) => (
            <li key={`${line.at}-${index}`}>
              <time>{atLabel(line.at)}</time>
              <span>
                {line.text}
                {/* The machine's own words under the translation, because the
                    reader who can see both is the one who can tell whether it
                    heard correctly. */}
                {line.source ? <em>{line.source}</em> : null}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="transcript-reader-whole">{translated?.text ?? transcript.text}</p>
      )}
    </Dialog>
  );
}
