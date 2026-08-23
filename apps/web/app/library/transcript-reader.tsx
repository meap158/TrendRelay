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
  const [pairs, setPairs] = useState<
    { from: string; to: string; label: string; from_label?: string; to_label?: string }[]
  >([]);
  const [target, setTarget] = useState("");
  /**
   * What language the reader says this is, when the reading does not know.
   *
   * OCR reports `und` - it reads glyphs, and no amount of looking at them says
   * whether they are Vietnamese or Malay. This used to end the matter: the
   * control said there was nothing to translate from, which made on-screen
   * text the one reading that could never be translated. A person can see
   * which language it is, so they are asked.
   *
   * Held here and sent with the request rather than saved onto the transcript.
   * A language chosen in order to read a translation is not a finding about
   * the clip.
   */
  const [assumedSource, setAssumedSource] = useState("");
  /**
   * The translation, and which reading it is of.
   *
   * Carrying the id is what makes it impossible to show one clip's words under
   * another's times. That used to be an effect that cleared this whenever the
   * draft changed, which is the same guarantee arrived at by running something
   * after the wrong thing has already been rendered once.
   */
  const [translated, setTranslated] = useState<
    { forId: string; text: string; lines: TranslatedLine[] } | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ forId: string; message: string } | null>(null);
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

  const stated = (transcript?.language ?? "").toLowerCase();
  /** Whether the reading itself knows. Speech does; glyphs never do. */
  const knowsItsOwn = Boolean(stated) && stated !== "und";
  const source = knowsItsOwn ? stated : assumedSource;
  /** Every language something is installed to translate out of. */
  const sources = [...new Map(
    pairs.map((pair) => [pair.from, pair.from_label ?? pair.from]),
  )].sort((a, b) => a[1].localeCompare(b[1]));
  const targets = pairs.filter((pair) => pair.from === source);
  /**
   * The target as it currently stands, which is not always the one stored.
   *
   * A target belongs to a direction rather than to a language on its own, so
   * changing what this is read as can leave the stored one translating out of
   * something else. Derived rather than corrected in an effect: there is no
   * moment where the interface shows a direction that does not exist, and
   * nothing has to run to put it right.
   */
  const chosenTarget = targets.some((pair) => pair.to === target) ? target : "";

  const translate = useCallback(async () => {
    if (!transcript || !chosenTarget) return;
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
            target: chosenTarget,
            // Only meaningful when the reading does not know its own; the API
            // ignores it when it does, so the reading always wins.
            ...(knowsItsOwn ? {} : { source }),
          }),
        },
      );
      const payload = (await response.json()) as {
        text?: string; lines?: TranslatedLine[]; detail?: string;
      };
      if (!response.ok) throw new Error(payload.detail ?? "That could not be translated.");
      setTranslated({
        forId: transcript.id, text: payload.text ?? "", lines: payload.lines ?? [],
      });
    } catch (reason) {
      setError({
        forId: transcript.id,
        message: reason instanceof Error
          ? reason.message : "That could not be translated.",
      });
    } finally {
      setBusy(false);
    }
  }, [transcript, chosenTarget, source, knowsItsOwn, apiFetch, workspaceId, assetId]);

  if (!transcript) return null;

  // A different draft is a different reading, so anything belonging to another
  // one simply is not shown - no clearing, and no frame where it was.
  const shown = translated?.forId === transcript.id ? translated : null;
  const problem = error?.forId === transcript.id ? error.message : null;

  const lines = timedLines(transcript.segments);
  const byTime = shown?.lines.length
    ? shown.lines.map((line) => ({ at: line.start_ms, text: line.text, source: line.source }))
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
          {shown && (
            <Button variant="secondary" onClick={() => onUse(shown.text)}>
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
        <span className="transcript-reader-translate">
          {/* Asked first, because nothing downstream means anything until it
              is answered - the targets on offer are the ones this language can
              actually be turned into. */}
          {!knowsItsOwn && (
            <select
              value={assumedSource}
              onChange={(event) => setAssumedSource(event.target.value)}
              aria-label="What language is this in"
            >
              <option value="">Read as…</option>
              {sources.map(([code, label]) => (
                <option key={code} value={code}>{label}</option>
              ))}
            </select>
          )}
            <select
              value={chosenTarget}
              onChange={(event) => setTarget(event.target.value)}
              aria-label="Translate into"
            >
              <option value="">Translate into…</option>
              {targets.map((pair) => (
                // Named by the language alone once the direction is already
                // settled by the control beside it.
                <option key={pair.to} value={pair.to}>{pair.to_label ?? pair.label}</option>
              ))}
            </select>
            <Button
              variant="secondary"
              size="sm"
              busy={busy}
              disabled={!chosenTarget || !source || busy}
              title={source ? undefined : "Say what language this is in first"}
              onClick={() => void translate()}
            >Translate</Button>
            {shown && (
              <Button variant="quiet" size="sm" onClick={() => setTranslated(null)}>
                Show the original
              </Button>
            )}
        </span>
      </div>

      {!knowsItsOwn && !assumedSource && (
        <p className="transcript-reader-note">
          Read as glyphs rather than as a language, so it cannot say which one
          it is. Choose what to read it as and it can be translated.
        </p>
      )}

      {source && targets.length === 0 && (
        <p className="transcript-reader-note">
          No language packages are installed to translate out of {source}. Add
          them from Tools → Argos Translate.
        </p>
      )}

      {problem && <p className="transcript-reader-problem" role="alert">{problem}</p>}

      {shown && (
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
        <p className="transcript-reader-whole">{shown?.text ?? transcript.text}</p>
      )}
    </Dialog>
  );
}
