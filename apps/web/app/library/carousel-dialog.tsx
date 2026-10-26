"use client";

/**
 * Carousel, in the Library: a script cut into slides, drawn in one of three looks, filed as images.
 *
 * Slides are separated by blank lines, so the script reads the way it will be posted. The preview is
 * drawn by the server from the same renderer that files the slides, so what is shown is what is
 * saved. Nothing is kept until Save: a preview is thrown away with the dialog.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { SegmentedControl } from "../ui/segmented";
import { DismissibleStatus } from "../ui/status";

type Rules = {
  styles: { id: string }[];
  default_style: string;
  sizes: Record<string, { width: number; height: number }>;
  default_size: string;
  max_slides: number;
  max_slide_chars: number;
};
type Preview = { slides: { index: number; text: string; image: string }[] };

const STYLE_LABELS: Record<string, string> = {
  "big-centre": "Big centre text",
  subtitle: "Subtitle",
  "stick-man": "Stick man",
};
const SIZE_LABELS: Record<string, string> = { portrait: "Post (4:5)", story: "Story (9:16)" };

export function CarouselDialog({
  open,
  workspaceId,
  apiFetch,
  onClose,
  onSaved,
  onError,
}: {
  open: boolean;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const base = `/api/workspaces/${workspaceId}/carousels`;
  const [rules, setRules] = useState<Rules | null>(null);
  const [script, setScript] = useState("");
  const [style, setStyle] = useState("big-centre");
  const [size, setSize] = useState("portrait");
  const [title, setTitle] = useState("Carousel");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<number | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    if (!open || rules) return;
    let live = true;
    void apiFetch(`${base}/rules`)
      .then((response) => (response.ok ? response.json() : null))
      .then((next: Rules | null) => {
        if (!live || !next) return;
        setRules(next);
        setStyle(next.default_style);
        setSize(next.default_size);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [open, rules, apiFetch, base]);

  const render = useCallback(async () => {
    const mine = ++latest.current;
    if (!script.trim()) {
      setPreview(null);
      setProblem(null);
      return;
    }
    setBusy(true);
    try {
      const response = await apiFetch(`${base}/preview`, {
        method: "POST",
        body: JSON.stringify({ script, style, size }),
      });
      const body = (await response.json()) as Preview & { detail?: string };
      if (mine !== latest.current) return;
      if (!response.ok) {
        setProblem(body.detail ?? "The preview could not be drawn.");
        setPreview(null);
      } else {
        setProblem(null);
        setPreview(body);
      }
    } catch (reason) {
      if (mine === latest.current)
        setProblem(reason instanceof Error ? reason.message : "The preview could not be drawn.");
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }, [apiFetch, base, script, size, style]);

  // Redraw shortly after the last change, so typing does not draw a preview per keystroke.
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => void render(), 600);
    return () => window.clearTimeout(timer);
  }, [open, render]);

  async function save() {
    setBusy(true);
    try {
      const response = await apiFetch(`${base}/save`, {
        method: "POST",
        body: JSON.stringify({ script, style, size, title: title.trim() || "Carousel" }),
      });
      const body = (await response.json()) as { slides?: number; detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "The carousel could not be saved.");
      setSaved(body.slides ?? 0);
      onSaved();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "The carousel could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  const styles = (rules?.styles ?? [{ id: "big-centre" }, { id: "subtitle" }, { id: "stick-man" }]).map(
    (entry) => ({ value: entry.id, label: STYLE_LABELS[entry.id] ?? entry.id }),
  );
  const sizes = Object.keys(rules?.sizes ?? { portrait: 0, story: 0 }).map((id) => ({
    value: id,
    label: SIZE_LABELS[id] ?? id,
  }));

  return (
    <Dialog
      open={open}
      title="Carousel"
      description="Write the slides, one block of text each, with a blank line between them."
      onClose={onClose}
      size="wide"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button variant="primary" disabled={busy || !preview} onClick={() => void save()}>
            Save {preview ? `${preview.slides.length} slides` : "slides"} to Library
          </Button>
        </>
      }
    >
      <div className="stack-form" style={{ display: "grid", gap: 14 }}>
        <label>
          Script
          <textarea
            value={script}
            rows={8}
            maxLength={(rules?.max_slides ?? 12) * ((rules?.max_slide_chars ?? 220) + 2)}
            placeholder={"Stop scrolling. This habit saves an hour a day.\n\nStep one: ..."}
            onChange={(event) => {
              setScript(event.target.value);
              setSaved(null);
            }}
          />
        </label>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <SegmentedControl label="Look" value={style} options={styles} onChange={setStyle} />
          <SegmentedControl label="Size" value={size} options={sizes} onChange={setSize} />
        </div>
        <label>
          Name in the Library
          <input value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} />
        </label>
        {problem && (
          <DismissibleStatus tone="bad" onDismiss={() => setProblem(null)}>
            {problem}
          </DismissibleStatus>
        )}
        {saved !== null && (
          <DismissibleStatus tone="good" onDismiss={() => setSaved(null)}>
            {saved} slides filed in the Library as images.
          </DismissibleStatus>
        )}
        {preview && (
          <ol
            aria-label="Slide preview"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
              gap: 10,
              listStyle: "none",
              padding: 0,
              margin: 0,
              opacity: busy ? 0.6 : 1,
            }}
          >
            {preview.slides.map((slide) => (
              <li key={slide.index}>
                {/* eslint-disable-next-line @next/next/no-img-element -- a data URL drawn by the server */}
                <img
                  src={slide.image}
                  alt={`Slide ${slide.index}: ${slide.text}`}
                  style={{ width: "100%", height: "auto", borderRadius: 6, display: "block" }}
                />
              </li>
            ))}
          </ol>
        )}
      </div>
    </Dialog>
  );
}
