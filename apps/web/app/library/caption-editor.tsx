"use client";

import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { AutoTranscribe } from "./auto-transcribe";
import { ProviderSwitch, providerOf, useMediaAi } from "./transcription-setup";
import { useT } from "../i18n-provider";
import { useJobs } from "../jobs-provider";
import {
  ALIGNMENTS,
  checkedMargin,
  contentRect,
  checkedSize,
  movable,
  nudge,
  overridesFrom,
  placementFromPoint,
  placementStyle,
  sizeFromDrag,
  usableWidth,
  type Alignment,
  type Placement,
} from "../../lib/caption-placement";

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
  /** The gallery shelf this sits on; the API sends styles already grouped. */
  category: string;
  style: CaptionStyleSettings;
  layout: Record<string, unknown>;
  needs_word_timings: boolean;
};

type CaptionStyleSettings = {
  font?: string;
  size?: number;
  bold?: boolean;
  italic?: boolean;
  colour?: string;
  outline_colour?: string;
  outline_alpha?: number;
  back_colour?: string;
  back_alpha?: number;
  highlight_colour?: string;
  outline?: number;
  shadow?: number;
  border?: number;
  alignment?: string;
  margin_h?: number;
  margin_v?: number;
  spacing?: number;
  uppercase?: boolean;
  highlight_active_word?: boolean;
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
  words: { text: string; start_ms: number; end_ms: number }[];
  cps: number;
  /**
   * Where this line goes, when it has an opinion.
   *
   * `[x, y, width, height]` as shares of the frame. Null for a spoken caption,
   * which goes wherever the style says; set for a line replacing on-screen
   * text, which has exactly one place it can be.
   */
  place?: [number, number, number, number] | null;
};

function blobFromBase64(content: string, mimeType: string): Blob {
  const binary = window.atob(content);
  const bytes = new Uint8Array(binary.length);
  for (let at = 0; at < binary.length; at += 1) bytes[at] = binary.charCodeAt(at);
  return new Blob([bytes], { type: mimeType });
}

function colourWithTransparency(colour = "#000000", transparency = 0): string {
  const clean = colour.replace("#", "");
  const full = clean.length === 3 ? clean.split("").map((part) => part + part).join("") : clean;
  const red = Number.parseInt(full.slice(0, 2), 16) || 0;
  const green = Number.parseInt(full.slice(2, 4), 16) || 0;
  const blue = Number.parseInt(full.slice(4, 6), 16) || 0;
  return `rgba(${red}, ${green}, ${blue}, ${Math.max(0, Math.min(1, (255 - transparency) / 255))})`;
}

/**
 * A measured box, as padding inside the picture.
 *
 * The same shape `placementStyle` returns, so the overlay does not care which
 * of the two produced it. Centred in the box on both axes, which is what the
 * ASS override does: `n5` puts the line on a point rather than against a
 * corner, so it sits in the middle of the box however it over- or under-fills
 * it.
 */
function boxStyle(
  place: [number, number, number, number],
): { alignItems: string; justifyContent: string; padding: string } {
  const [x, y, width, height] = place;
  return {
    alignItems: "center",
    justifyContent: "center",
    padding: `${y * 100}% ${(1 - x - width) * 100}% ${(1 - y - height) * 100}% ${x * 100}%`,
  };
}

function captionPosition(alignment = "bottom"): CSSProperties {
  const vertical = alignment.startsWith("top") ? "flex-start"
    : ["middle", "left", "right"].includes(alignment) ? "center" : "flex-end";
  const horizontal = alignment.endsWith("left") || alignment === "left" ? "flex-start"
    : alignment.endsWith("right") || alignment === "right" ? "flex-end" : "center";
  return { alignItems: horizontal, justifyContent: vertical };
}

function captionTextStyle(
  style: CaptionStyleSettings,
  compact = false,
  sourceWidth = 1920,
): CSSProperties {
  const outline = Math.max(0, Number(style.outline ?? 0)) * (compact ? 0.25 : 0.45);
  const shadow = Math.max(0, Number(style.shadow ?? 0)) * (compact ? 0.35 : 0.7);
  const box = Number(style.border) === 3;
  return {
    color: style.colour ?? "#FFFFFF",
    fontFamily: style.font ? `${style.font}, Arial, sans-serif` : undefined,
    fontSize: compact
      ? `${Math.max(8, Math.min(13, Number(style.size ?? 48) / 4.5))}px`
      : `clamp(11px, ${Number(style.size ?? 48) * 100 / Math.max(1, sourceWidth)}cqw, ${Math.max(24, Number(style.size ?? 48) / 1.55)}px)`,
    fontWeight: style.bold ? 800 : 500,
    fontStyle: style.italic ? "italic" : "normal",
    letterSpacing: `${Number(style.spacing ?? 0) * (compact ? 0.2 : 0.45)}px`,
    textTransform: style.uppercase ? "uppercase" : "none",
    WebkitTextStroke: !box && outline > 0
      ? `${outline}px ${colourWithTransparency(style.outline_colour, style.outline_alpha)}`
      : undefined,
    textShadow: shadow > 0
      ? `${shadow}px ${shadow}px ${Math.max(1, shadow * 1.5)}px ${colourWithTransparency(style.back_colour, style.back_alpha)}`
      : undefined,
    background: box ? colourWithTransparency(style.outline_colour, style.outline_alpha) : undefined,
    padding: box ? (compact ? "2px 4px" : "0.18em 0.38em") : undefined,
  };
}

function CaptionLook({
  preset,
  cue,
  activeMs,
  sample,
  compact = false,
  highlightWords = true,
  sourceWidth = 1920,
  sizeOverride,
}: {
  preset: CaptionStyle;
  cue?: Cue | null;
  activeMs?: number;
  sample?: string;
  compact?: boolean;
  highlightWords?: boolean;
  sourceWidth?: number;
  /** A size being dragged, which the preset does not know about yet. */
  sizeOverride?: number;
}) {
  const sampleWords = (sample || "Caption preview").split(/\s+/).filter(Boolean);
  const compactSample = sampleWords.slice(
    0,
    Number(preset.layout.max_words ?? Math.min(6, sampleWords.length)),
  ).join(" ");
  const displayLines = cue?.lines.length
    ? cue.lines
    : [(compact ? compactSample : sample) || "Caption preview"];
  const wordsByLine = displayLines.map((line) => line.split(/\s+/).filter(Boolean));
  const words = wordsByLine.flat();
  const measuredWord = highlightWords && preset.style.highlight_active_word && activeMs !== undefined
    ? cue?.words?.findIndex((word) => activeMs >= word.start_ms && activeMs <= word.end_ms) ?? -1
    : -1;
  const activeWordIndex = highlightWords && preset.style.highlight_active_word
    ? measuredWord >= 0 ? measuredWord : Math.min(1, words.length - 1)
    : -1;
  return (
    <span
      className={`caption-look${compact ? " caption-look-compact" : ""}`}
      style={captionTextStyle(
        sizeOverride === undefined ? preset.style : { ...preset.style, size: sizeOverride },
        compact,
        sourceWidth,
      )}
    >
      {wordsByLine.map((line, lineIndex) => {
        const wordOffset = wordsByLine
          .slice(0, lineIndex)
          .reduce((total, previous) => total + previous.length, 0);
        return (
          <span className="caption-look-line" key={`${line.join("-")}-${lineIndex}`}>
            {line.map((word, index) => {
              const absoluteIndex = wordOffset + index;
              const on = absoluteIndex === activeWordIndex;
              return (
                <span
                  key={`${word}-${absoluteIndex}`}
                  style={on ? { color: preset.style.highlight_colour ?? "#FFD400" } : undefined}
                >
                  {word}{index < line.length - 1 ? " " : ""}
                </span>
              );
            })}
          </span>
        );
      })}
    </span>
  );
}

type CaptionFile = {
  name: string;
  path: string;
  language: string | null;
  format: string;
  size_bytes: number;
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
  targets,
  hasAudio = true,
  onQueued,
  onTranscribed,
  onClose,
  apiFetch,
  canEdit,
}: {
  open: boolean;
  workspaceId: string;
  assetId?: string;
  assetTitle?: string;
  targets?: { id: string; title: string; mediaKind: string }[];
  hasAudio?: boolean;
  onQueued?: (message: string, assetIds: string[]) => void;
  onTranscribed?: () => void;
  onClose: () => void;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  canEdit: boolean;
}) {
  const t = useT();
  const { announceMediaJobs, refresh: refreshJobs } = useJobs();
  const requestedTargets = useMemo(() => targets?.length
    ? targets
    : assetId
      ? [{ id: assetId, title: assetTitle ?? "Media", mediaKind: "video" }]
      : [], [assetId, assetTitle, targets]);
  const compatibleTargets = useMemo(() => requestedTargets.filter((target) =>
    ["video", "audio"].includes(target.mediaKind),
  ), [requestedTargets]);
  const canBurn = compatibleTargets.length > 0
    && compatibleTargets.every((target) => target.mediaKind === "video");
  const skippedTargets = requestedTargets.length - compatibleTargets.length;
  const primary = compatibleTargets[0];
  const primaryAssetId = primary?.id ?? "";
  const batch = requestedTargets.length > 1;
  const [catalogue, setCatalogue] = useState<StylesResponse | null>(null);
  const [styleId, setStyleId] = useState("broadcast");
  /**
   * What is being captioned: what was said, or what is written on the picture.
   *
   * The second is a different reading of the same clip and lands somewhere
   * else entirely - over the words it replaces, because a translation of
   * on-screen text at the bottom of the frame is a second thing to read beside
   * the thing it translates.
   */
  const [captionOf, setCaptionOf] = useState<"speech" | "on_screen">("speech");
  const [translateTo, setTranslateTo] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [delivery, setDelivery] = useState("sidecar");
  const [queued, setQueued] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [files, setFiles] = useState<CaptionFile[]>([]);
  const [mediaUrl, setMediaUrl] = useState("");
  const [mediaLoading, setMediaLoading] = useState(false);
  const [mediaProblem, setMediaProblem] = useState("");
  const [playbackMs, setPlaybackMs] = useState(0);
  const [mediaSourceWidth, setMediaSourceWidth] = useState(1920);
  const [mediaSourceHeight, setMediaSourceHeight] = useState(1080);
  /**
   * Where the caption sits and how big it is, once somebody has said.
   *
   * Null until then, and null means "whatever the preset says" rather than a
   * copy of it: the presets are the API's to change, and a placement captured
   * at open time would quietly pin this style to the values it had that day.
   */
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [sizeOverride, setSizeOverride] = useState<number | null>(null);
  const [dragging, setDragging] = useState<null | "move" | "resize">(null);
  /**
   * How big the preview box currently is.
   *
   * Measured rather than assumed, because the picture inside it is letterboxed
   * and the overlay has to land on the picture. `aspect-ratio` looked like it
   * could do this in CSS alone, and cannot: a grid item needs one real
   * dimension, and giving it one makes the ratio lose to it - the overlay came
   * out 640px wide inside a 470px frame.
   */
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });
  const frameRef = useRef<HTMLDivElement | null>(null);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  // A preview asked for after a newer one must not overwrite it: the requests
  // are independent and the slower one can land last.
  const latest = useRef(0);
  /**
   * What language the last preview said this clip is in.
   *
   * Held in a ref rather than read from `preview`, because the request that
   * needs it is the one that produces it: depending on the state would have
   * each answer ask for the next one, forever.
   */
  const knownLanguage = useRef<string | null>(null);
  // Only polled while this dialog is open; a closed one has no reason to keep
  // asking whether a runtime appeared.
  const mediaAi = useMediaAi(apiFetch, open);

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

  useEffect(() => {
    if (!open || !workspaceId || !primaryAssetId) return;
    let live = true;
    let objectUrl = "";
    queueMicrotask(() => {
      if (!live) return;
      setMediaLoading(true);
      setMediaProblem("");
      setPlaybackMs(0);
    });
    void apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets/${primaryAssetId}/preview?cut=original`,
      { method: "POST" },
    )
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail ?? "Media preview unavailable.");
        objectUrl = URL.createObjectURL(blobFromBase64(body.content_base64, body.mime_type));
        if (!live) return;
        setMediaUrl(objectUrl);
      })
      .catch((reason) => {
        if (live) setMediaProblem(reason instanceof Error ? reason.message : "Media preview unavailable.");
      })
      .finally(() => {
        if (live) setMediaLoading(false);
      });
    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setMediaUrl("");
    };
  }, [apiFetch, open, primaryAssetId, workspaceId]);

  const chosen = catalogue?.styles.find((item) => item.id === styleId);
  const previewPreset = chosen ?? catalogue?.styles[0] ?? null;

  // --- where the caption sits ------------------------------------------------
  const source = useMemo(
    () => ({ width: mediaSourceWidth, height: mediaSourceHeight }),
    [mediaSourceWidth, mediaSourceHeight],
  );
  /** The preset's own placement, which is what an untouched caption uses. */
  const presetPlacement: Placement = useMemo(() => ({
    alignment: (previewPreset?.style.alignment ?? "bottom") as Alignment,
    margin_h: previewPreset?.style.margin_h ?? 0,
    margin_v: previewPreset?.style.margin_v ?? 0,
  }), [previewPreset]);
  const activePlacement = placement ?? presetPlacement;
  const activeSize = sizeOverride ?? previewPreset?.style.size ?? 48;
  const canMove = movable(activePlacement.alignment);
  /** Nothing to send while it matches the preset, which is the common case. */
  const styleOverrides = useMemo(
    () => overridesFrom(activePlacement, activeSize, previewPreset?.style ?? {}),
    [activePlacement, activeSize, previewPreset],
  );
  const moved = Object.keys(styleOverrides).length > 0;

  /** The preview drawn where the render will put it, not at a fixed inset. */
  const overlayStyle = useMemo(
    () => placementStyle(activePlacement, source),
    [activePlacement, source],
  );
  /** The picture's own box, which is what those percentages are of. */
  const picture = useMemo(
    () => contentRect(frameSize, source),
    [frameSize, source],
  );

  const watcher = useRef<ResizeObserver | null>(null);
  /**
   * Read the frame's size, and only disturb React when it has actually moved.
   *
   * Called from a layout effect on every render as well as from the observer.
   * Belt and braces on purpose: the frame grows when the video's own
   * proportions arrive, and that resize was not reliably reaching the observer
   * - the overlay stayed sized against a 360px-tall frame after it had become
   * 477. Measuring after every render cannot miss it, and the equality check
   * is what stops that turning into a loop.
   */
  const measureFrame = useCallback(() => {
    const node = frameRef.current;
    if (!node) return;
    const width = node.clientWidth;
    const height = node.clientHeight;
    setFrameSize((current) => (
      current.width === width && current.height === height
        ? current
        : { width, height }
    ));
  }, []);
  useLayoutEffect(measureFrame);
  /**
   * Attach the observer when the frame appears, not when the dialog opens.
   *
   * The frame is rendered conditionally - there is nothing to preview until
   * the styles and the media have arrived - so an effect keyed on `open` runs
   * while the ref is still null and observes nothing. A callback ref fires on
   * the mount itself, which is the moment there is something to measure.
   */
  const attachFrame = useCallback((node: HTMLDivElement | null) => {
    frameRef.current = node;
    watcher.current?.disconnect();
    watcher.current = null;
    if (!node || typeof ResizeObserver === "undefined") return;
    // `clientWidth`/`clientHeight` rather than the observer's own rectangle:
    // that one is the content box in fractional pixels and lags a layout the
    // observer did not cause, which had the overlay sized against a frame
    // eight pixels wider than the one on screen. These two are what the
    // absolutely-positioned overlay is actually laid out against.
    watcher.current = new ResizeObserver(() => measureFrame());
    watcher.current.observe(node);
    measureFrame();
    // `measureFrame` is declared below and never changes; naming it in the
    // deps would only re-run this on a ref React already calls once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Pick a preset, and take its placement with it.
   *
   * Carrying the last one's numbers across would make choosing a style do
   * nothing visible - its placement is most of what distinguishes it. Done
   * here rather than in an effect on `styleId`, because this click is the
   * thing that causes it and an effect would also fire on the first render.
   */
  const chooseStyle = useCallback((next: string) => {
    setStyleId(next);
    setPlacement(null);
    setSizeOverride(null);
  }, []);

  const pointerPlacement = useCallback(
    (event: { clientX: number; clientY: number; altKey?: boolean }) => {
      const frame = frameRef.current?.getBoundingClientRect();
      if (!frame) return;
      setPlacement((current) => placementFromPoint(
        { x: event.clientX - frame.left, y: event.clientY - frame.top },
        { width: frame.width, height: frame.height },
        source,
        current ?? presetPlacement,
        // Alt turns the snap off, the way it does in a drawing tool. Snapping
        // is right almost always and wrong exactly when somebody is placing a
        // caption deliberately near the middle.
        !event.altKey,
      ));
    }, [source, presetPlacement],
  );

  /**
   * Arrow keys, once the caption has focus.
   *
   * The exact instrument beside the rough one: a drag gets it near and the
   * arrows put it right. It is also the only way to place a caption without a
   * pointer at all, which a drag handle on its own quietly rules out.
   */
  const nudgeBy = useCallback((event: React.KeyboardEvent) => {
    const direction = {
      ArrowLeft: { x: -1 }, ArrowRight: { x: 1 },
      ArrowUp: { y: -1 }, ArrowDown: { y: 1 },
    }[event.key];
    if (!direction) return;
    event.preventDefault();
    // Ten at a time with Shift, which is the step everything else uses for
    // "the same thing, faster".
    const step = event.shiftKey ? 10 : 1;
    setPlacement((current) => nudge(current ?? presetPlacement, direction, step));
  }, [presetPlacement]);

  // Pointer capture rather than window listeners: the gesture belongs to the
  // element it started on, so a pointer that leaves the frame - or a dialog
  // that closes mid-drag - cannot leave a listener behind.
  useEffect(() => {
    if (!dragging) return;
    const startY = { value: 0, size: activeSize };
    function move(event: PointerEvent) {
      if (dragging === "move") { pointerPlacement(event); return; }
      const frame = frameRef.current?.getBoundingClientRect();
      if (!frame) return;
      if (!startY.value) startY.value = event.clientY;
      setSizeOverride(sizeFromDrag(startY.size, event.clientY - startY.value, frame.height, source));
    }
    function stop() { setDragging(null); }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
    // `activeSize` is read once at the start of a drag on purpose: depending on
    // it would restart the gesture on every frame of it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging, pointerPlacement, source]);

  const load = useCallback(async () => {
    if (!open || !workspaceId || !primaryAssetId) return;
    const ticket = ++latest.current;
    setBusy(true);
    try {
      // Two readings, two previews. Captioning the picture places every line
      // over the words it replaces, so it is answered by the endpoint that
      // knows where those words are rather than by the caption builder.
      const base = `/api/workspaces/${workspaceId}/media/library/assets/${primaryAssetId}`;
      const response = captionOf === "on_screen"
        ? await apiFetch(`${base}/text-overlay/preview`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            // No target means place the lines as they are, which is how the
            // boxes get checked before paying for a translation.
            target: translateTo || null,
            source: knownLanguage.current,
          }),
        })
        : await apiFetch(`${base}/captions/preview`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            style_id: styleId,
            translate_to: translateTo || null,
            // Only what was actually changed. The preview has to be built with
            // the same overrides the render will use, or dragging a caption
            // would move it on screen and nowhere else.
            style_overrides: styleOverrides,
          }),
        });
      if (ticket !== latest.current) return;
      const body = await response.json();
      if (!response.ok) {
        setPreview(null);
        setProblem(typeof body?.detail === "string" ? body.detail : "That did not work.");
        return;
      }
      setProblem(null);
      knownLanguage.current = (body as Preview).source_language ?? knownLanguage.current;
      setPreview(body as Preview);
    } catch {
      if (ticket === latest.current) setProblem("The preview could not be built.");
    } finally {
      if (ticket === latest.current) setBusy(false);
    }
  }, [open, workspaceId, primaryAssetId, styleId, translateTo, styleOverrides,
      captionOf, apiFetch]);

  useEffect(() => {
    // Deferred out of the effect body: `load` sets state on its first line, and
    // doing that synchronously here cascades a second render before the first
    // has painted.
    queueMicrotask(() => void load());
  }, [load]);

  const loadFiles = useCallback(async () => {
    if (!open || !workspaceId || !primaryAssetId) return;
    const response = await apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets/${primaryAssetId}/captions/files`,
    );
    if (!response.ok) return;
    const body = await response.json();
    setFiles((body.files ?? []) as CaptionFile[]);
  }, [open, workspaceId, primaryAssetId, apiFetch]);

  useEffect(() => {
    queueMicrotask(() => void loadFiles());
  }, [loadFiles]);

  const download = useCallback(
    async (file: CaptionFile) => {
      // Fetched rather than linked. `apiFetch` is what knows the API's origin
      // and carries the session, and a bare href would resolve against the web
      // app instead - a link that looks right and 404s.
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${primaryAssetId}` +
          `/captions/file?path=${encodeURIComponent(file.path)}`,
      );
      if (!response.ok) {
        setProblem("That file could not be fetched.");
        return;
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.name;
      anchor.click();
      URL.revokeObjectURL(url);
    },
    [workspaceId, primaryAssetId, apiFetch],
  );

  const render = useCallback(async () => {
    setRendering(true);
    setQueued(null);
    setProblem(null);
    try {
      const queuedIds: string[] = [];
      const queuedJobs: any[] = [];
      const failures: string[] = [];
      // One identity for this run, sent with every request in it. These are
      // queued one per asset, so without being told, the notification list
      // groups them by category, status and title - identical for every job
      // of a kind - and two runs merge into one row.
      const batchId = `captions-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      // Four at a time keeps a large selection from turning into a browser-side
      // request storm while still making a configured batch quick to queue.
      for (let at = 0; at < compatibleTargets.length; at += 4) {
        const group = compatibleTargets.slice(at, at + 4);
        const results = await Promise.all(group.map(async (target) => {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/media/library/assets/${target.id}/captions`,
            {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                style_id: styleId,
                translate_to: translateTo || null,
                delivery: canBurn ? delivery : "sidecar",
                style_overrides: styleOverrides,
                source: captionOf,
                batch: { id: batchId, total: compatibleTargets.length },
              }),
            },
          );
          const body = await response.json().catch(() => ({}));
          return { target, response, body };
        }));
        for (const result of results) {
          if (result.response.ok) {
            queuedIds.push(result.target.id);
            if (result.body?.job) queuedJobs.push(result.body.job);
          }
          else failures.push(
            typeof result.body?.detail === "string"
              ? `${result.target.title}: ${result.body.detail}`
              : `${result.target.title}: ${t("library.actionCouldNotStart")}`,
          );
        }
      }
      announceMediaJobs(queuedJobs);
      if (queuedJobs.length) void refreshJobs();
      // Burning re-encodes every frame, so the honest answer is that it has
      // been queued rather than that it is done.
      const summary = batch
        ? t("library.captionBatchQueued", { count: queuedIds.length })
        : !canBurn || delivery === "sidecar"
          ? "Subtitle files are being written."
          : "Queued. The captioned cut appears here when the render finishes.";
      setQueued(summary);
      if (failures.length) {
        setProblem(
          `${t("library.actionBatchFailed", { count: failures.length })} ${failures[0]}`,
        );
      }
      if (queuedIds.length) onQueued?.(summary, queuedIds);
      // Sidecars are written almost immediately; a burn is not. Looking once
      // shortly after covers the first without pretending to wait for the
      // second, which the jobs drawer is already following.
      window.setTimeout(() => void loadFiles(), 1500);
    } catch {
      setProblem("The render could not be queued.");
    } finally {
      setRendering(false);
    }
  }, [
    apiFetch,
    announceMediaJobs,
    batch,
    canBurn,
    compatibleTargets,
    delivery,
    loadFiles,
    onQueued,
    captionOf,
    refreshJobs,
    styleId,
    styleOverrides,
    t,
    translateTo,
    workspaceId,
  ]);

  const activeCue = preview?.cues.find(
    (cue) => playbackMs >= cue.start_ms && playbackMs <= cue.end_ms,
  ) ?? (playbackMs === 0 ? preview?.cues[0] ?? null : null);

  function seekToCue(cue: Cue) {
    if (mediaRef.current) {
      mediaRef.current.currentTime = cue.start_ms / 1000;
      setPlaybackMs(cue.start_ms);
      void mediaRef.current.play().catch(() => undefined);
    }
  }
  // The catalogue is fetched once when the dialog opens; the provider hook keeps
  // watching. So the fresher of the two answers wins, and a runtime that
  // finished downloading while this dialog was open lights the controls up
  // without asking the operator to close it and come back.
  const liveSpeech = providerOf(mediaAi.state, "speech");
  const liveTranslate = providerOf(mediaAi.state, "translate");
  const pairs = liveTranslate
    ? liveTranslate.ready
      ? liveTranslate.pairs ?? []
      : []
    : catalogue?.translation.ready
      ? catalogue.translation.pairs ?? []
      : [];
  const speechReady = liveSpeech?.ready ?? catalogue?.speech.ready ?? false;
  const needsSpeechTranscript = !batch
    && Boolean(problem?.toLowerCase().includes("no speech transcript"));
  // The provider reports every reachable direction. The selector is for this
  // transcript, so offering directions whose source is another language made
  // duplicate targets and choices that could never apply to this clip.
  const sourceLanguage = preview?.source_language ?? null;
  const availableTranslations = sourceLanguage
    ? pairs.filter((pair) => pair.from === sourceLanguage && pair.to !== sourceLanguage)
    : [];
  /**
   * What the captions are being turned into, when they are being turned into
   * anything.
   *
   * Said out loud wherever the captions are, and not only while the request is
   * in the air. A translated caption is still a translation once it has
   * finished arriving, and the difference between "these are the words" and
   * "these are the words in another language" is exactly the thing somebody
   * proofreading needs to know before they trust what they are reading.
   */
  const translatingTo = translateTo
    ? availableTranslations
      .find((pair) => pair.to === translateTo)?.label.replace(/^.*? to /, "")
      ?? translateTo.toUpperCase()
    : "";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Captions"
      description={batch && primary
        ? t("library.selectionDialogDescription", {
            count: compatibleTargets.length,
            title: primary.title,
          })
        : primary?.title ?? assetTitle}
      size="wide"
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          {/* The delivery choice sits with the button that acts on it, because
              it changes what that button costs: sidecars are a kilobyte,
              burning in re-encodes every frame. */}
          <label className="caption-delivery">
            <span>Deliver</span>
            <Select
              value={canBurn ? delivery : "sidecar"}
              onChange={(event) => setDelivery(event.target.value)}
              preferredSide="above"
            >
              <option value="sidecar">Subtitle files only</option>
              {canBurn && <option value="burned">Burned into the video</option>}
              {canBurn && <option value="both">Both</option>}
            </Select>
          </label>
          <Button
            variant="primary"
            disabled={!canEdit || (!batch && !preview) || compatibleTargets.length === 0}
            busy={rendering}
            title={
              canEdit
                ? "Write the subtitle files, and encode a captioned cut if asked for"
                : "You do not have permission to render here"
            }
            onClick={() => void render()}
          >
            {batch
              ? t("library.createCaptionsFor", { count: compatibleTargets.length })
              : "Create captions"}
          </Button>
        </>
      }
    >
      <div className="caption-editor caption-editor-with-media">
        {/* Without a transcript nothing below can build a caption, so the way to
            make one is the first thing in the modal rather than the last - the
            same control, moved out from under the timing list where it was
            missed. */}
        {needsSpeechTranscript && primary && (
          <div className="caption-editor-setup caption-editor-transcribe-cta">
            <p>
              <strong>This clip has no speech transcript yet.</strong>{" "}
              Transcribe it here and the machine draft unlocks caption timing
              automatically when it finishes — review the wording before publishing.
              Reading the on-screen text is the other half: it does not feed the
              captions, it records what is already written on the picture and
              where, which is what covering or replacing it needs.
            </p>
            <AutoTranscribe
              workspaceId={workspaceId}
              assetId={primary.id}
              hasAudio={hasAudio}
              mediaKind={primary.mediaKind}
              // Both readings, not just speech. Captions are built from the
              // spoken transcript, but this is the screen somebody is on when
              // they are thinking about the words in a video - and the
              // on-screen reading is what the cover-and-replace step needs,
              // so sending them elsewhere to start it was a detour with no
              // reason behind it.
              modesAvailable={["speech", "ocr"]}
              apiFetch={apiFetch}
              canEdit={canEdit}
              onFinished={() => {
                void load();
                onTranscribed?.();
              }}
            />
          </div>
        )}
        {batch && skippedTargets > 0 && (
          <p className="caption-editor-note">
            {t("library.actionSkippedIncompatible", { count: skippedTargets })}
          </p>
        )}
        {/* The runtime is the first thing to say, because every control below
            depends on it and an empty style list would otherwise read as a
            missing feature rather than a missing install. */}
        {catalogue && !speechReady && !needsSpeechTranscript && (
          <div className="caption-editor-setup">
            <p>
              Automatic transcription is off, so captions can only be built from
              a transcript entered by hand.
            </p>
            <ProviderSwitch
              label="Transcribe speech"
              provider="speech"
              state={mediaAi.state}
              busy={mediaAi.busy}
              onPrepare={(provider) => void mediaAi.prepare(provider)}
              onToggle={(provider, on) => void mediaAi.setActive(provider, on)}
            />
            {mediaAi.failure && <p className="caption-editor-problem" role="alert">{mediaAi.failure}</p>}
          </div>
        )}

        <div className="caption-editor-workspace">
          <section className="caption-media-preview" aria-label="Caption preview on media">
            <header>
              <div>
                <h4>On-media preview</h4>
                <small>Play the clip or choose a cue to inspect its real timing.</small>
              </div>
              {/* Translating is the slow pass and the one worth naming: a
                  generic "updating" on a wait that is several times longer
                  reads as the dialog having stalled. */}
              {busy && <Badge tone="neutral">{translatingTo ? "Translating…" : "Updating…"}</Badge>}
              {!busy && translatingTo && (
                <Badge tone="info">Translated · {translatingTo}</Badge>
              )}
            </header>
            <div className="caption-media-frame" ref={attachFrame} data-dragging={dragging || undefined}>
              {mediaLoading && !mediaUrl && (
                <div className="caption-media-placeholder">Loading media…</div>
              )}
              {mediaUrl && primary?.mediaKind === "video" && (
                <video
                  ref={(node) => { mediaRef.current = node; }}
                  src={mediaUrl}
                  controls
                  muted
                  playsInline
                  preload="metadata"
                  onLoadedMetadata={(event) => {
                    setMediaSourceWidth(event.currentTarget.videoWidth || 1920);
                    // The height matters as much as the width now: a vertical
                    // margin is a fraction of it, and assuming 1080 on a
                    // portrait clip puts every caption in the wrong place.
                    setMediaSourceHeight(event.currentTarget.videoHeight || 1080);
                  }}
                  onTimeUpdate={(event) => setPlaybackMs(event.currentTarget.currentTime * 1000)}
                  onSeeked={(event) => setPlaybackMs(event.currentTarget.currentTime * 1000)}
                />
              )}
              {primary?.mediaKind === "audio" && (
                <div className="caption-audio-preview">
                  <span>{mediaProblem || "Audio caption preview"}</span>
                  {mediaUrl && (
                    <audio
                      ref={(node) => { mediaRef.current = node; }}
                      src={mediaUrl}
                      controls
                      preload="metadata"
                      onTimeUpdate={(event) => setPlaybackMs(event.currentTarget.currentTime * 1000)}
                      onSeeked={(event) => setPlaybackMs(event.currentTarget.currentTime * 1000)}
                    />
                  )}
                </div>
              )}
              {!mediaLoading && !mediaUrl && primary?.mediaKind !== "audio" && (
                <div className="caption-media-placeholder">
                  {mediaProblem || "Media preview unavailable — showing the caption look."}
                </div>
              )}
              {/* The thirds a drop snaps to, shown only while dragging. The
                  anchor grid is the actual model, so hiding it would make the
                  snap feel like the drag failing to track the pointer. */}
              {dragging === "move" && <div className="caption-drop-grid" aria-hidden="true" />}
              {previewPreset && activeCue && (
                <div className="caption-media-overlay" aria-live="off">
                  {/* An inner box the exact shape of the picture, so a margin
                      written as a percentage is a percentage of the video and
                      not of the black around it. `aspect-ratio` lets the
                      browser do the letterbox arithmetic the same way
                      `object-fit: contain` does for the video itself, which is
                      the only way the two are guaranteed to agree. */}
                  <div
                    className="caption-media-picture"
                    style={{
                      width: picture.width || undefined,
                      height: picture.height || undefined,
                      // A line replacing on-screen text carries the box it was
                      // measured onto, and that beats the style: drawing it
                      // where the style says would show a placement the render
                      // is not going to use.
                      ...(activeCue?.place
                        ? boxStyle(activeCue.place)
                        : overlayStyle),
                    }}
                  >
                  {/* Draggable, and it says so. The caption itself is the
                      handle - there is nothing else on the frame it could
                      mean - and the whole gesture is pointer events so a pen
                      or a finger works the same as a mouse. */}
                  <span
                    className="caption-media-handle"
                    role="application"
                    tabIndex={canEdit && captionOf === "speech" ? 0 : -1}
                    aria-label={
                      `Caption position: ${activePlacement.alignment}, `
                      + `${activePlacement.margin_h} from the side, `
                      + `${activePlacement.margin_v} from the edge. `
                      + "Drag to move, arrow keys to nudge, hold Shift for ten at a time, "
                      + "hold Alt while dragging to place it without snapping."
                    }
                    data-dragging={dragging === "move" || undefined}
                    onKeyDown={canEdit && captionOf === "speech" ? nudgeBy : undefined}
                    data-fixed={captionOf === "on_screen" || undefined}
                    onPointerDown={(event) => {
                      // Nothing to drag when the reading decides the place.
                      if (!canEdit || captionOf === "on_screen") return;
                      event.preventDefault();
                      event.currentTarget.focus();
                      setDragging("move");
                      pointerPlacement(event);
                    }}
                  >
                    <CaptionLook
                      preset={previewPreset}
                      cue={activeCue}
                      activeMs={playbackMs}
                      // Trust the cues: a word-paced style keeps (estimated)
                      // words on a translated track now, and a reading style's
                      // translated cues come back with none - so the cue
                      // itself says whether there is anything to light.
                      highlightWords
                      sourceWidth={mediaSourceWidth}
                      sizeOverride={activeSize}
                    />
                    {canEdit && (
                      <button
                        type="button"
                        className="caption-size-handle"
                        aria-label={`Caption size ${activeSize}. Drag to resize.`}
                        title="Drag to resize"
                        onPointerDown={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          setDragging("resize");
                        }}
                      />
                    )}
                  </span>
                  </div>
                </div>
              )}
            </div>
            <p className="caption-media-help">
              {previewPreset
                ? `${previewPreset.label} · ${activeCue ? `${timecode(activeCue.start_ms)}–${timecode(activeCue.end_ms)}` : "No caption at this position"}`
                : "Choose a preset to preview it."}
            </p>
            {/* The same placement as numbers. A drag is quick and a field is
                exact, and the two are the same three values - so a caption
                nudged by hand can be squared off by typing, and one typed can
                be checked against the frame. */}
            {/* Placement belongs to the reading when the reading is the
                picture: every line goes over the words it replaces, so an
                anchor and a margin have nothing to decide and a drag would
                move something the render puts back. Said, not just hidden. */}
            {captionOf === "on_screen" && (
              <p className="caption-editor-note">
                Position comes from where the words are on the picture, so there
                is nothing to place by hand. The style still decides how they
                look.
              </p>
            )}
            {canEdit && previewPreset && captionOf === "speech" && (
              <div className="caption-place-fields">
                <label>
                  <span>Anchor</span>
                  <Select
                    value={activePlacement.alignment}
                    onChange={(event) => setPlacement({
                      ...activePlacement,
                      alignment: event.target.value as Alignment,
                    })}
                  >
                    {ALIGNMENTS.map((name) => (
                      <option key={name} value={name}>{name.replace("-", " ")}</option>
                    ))}
                  </Select>
                </label>
                <label>
                  <span>Side margin</span>
                  <input
                    type="number" min={0} max={2000} step={2}
                    value={activePlacement.margin_h}
                    onChange={(event) => setPlacement({
                      ...activePlacement,
                      margin_h: checkedMargin(event.target.valueAsNumber),
                    })}
                  />
                </label>
                <label data-off={!canMove.vertical || undefined}>
                  <span>Edge margin</span>
                  <input
                    type="number" min={0} max={2000} step={2}
                    value={activePlacement.margin_v}
                    disabled={!canMove.vertical}
                    onChange={(event) => setPlacement({
                      ...activePlacement,
                      margin_v: checkedMargin(event.target.valueAsNumber),
                    })}
                  />
                </label>
                <label>
                  <span>Size</span>
                  <input
                    type="number" min={8} max={400} step={1}
                    value={activeSize}
                    onChange={(event) => setSizeOverride(
                      checkedSize(event.target.valueAsNumber, activeSize),
                    )}
                  />
                </label>
                {moved && (
                  <Button variant="quiet" size="sm"
                    onClick={() => { setPlacement(null); setSizeOverride(null); }}>
                    Reset to preset
                  </Button>
                )}
              </div>
            )}
            {/* What moving sideways costs. The format writes one horizontal
                margin into both sides, so pushing a caption towards the middle
                squeezes it from both at once - and the render will do it
                whether or not anybody was told. */}
            {canEdit && previewPreset && captionOf === "speech" && canMove.horizontal
              && usableWidth(activePlacement, source) < source.width * 0.35 && (
              <p className="caption-editor-note">
                Only {Math.round(usableWidth(activePlacement, source))} of{" "}
                {source.width} pixels are left for the text to run in, because a
                side margin applies to both sides at once. It will wrap into a
                narrow column. Move it back towards its edge, or centre it.
              </p>
            )}
            {/* Said rather than left to be discovered by pulling at something
                that will not move. The format writes one horizontal margin to
                both sides, so a centred caption cannot be nudged sideways, and
                a middle one has no edge to measure a vertical margin from. */}
            {canEdit && previewPreset && captionOf === "speech"
              && !(canMove.horizontal && canMove.vertical) && (
              <p className="caption-editor-note">
                {!canMove.horizontal && !canMove.vertical
                  ? "A middle anchor is fixed to the centre of the frame. Choose an edge or a corner to place it by hand."
                  : !canMove.horizontal
                    ? "Centred captions stay centred: the side margin sets how wide they may run, not where they sit. Anchor left or right to move it across."
                    : "This anchor has no edge to measure from, so the edge margin does nothing. Anchor to the top or bottom to set it."}
              </p>
            )}
          </section>

          <div className="caption-editor-controls">

        {/* Which reading is being captioned. Two different things about one
            clip: what the speaker said, and what is written on the picture -
            and the second lands over the words it replaces rather than at the
            bottom, which is the only place a translation of on-screen text can
            go without becoming a second thing to read beside it. */}
        <section className="caption-editor-source" aria-label="What to caption">
          <h4>What to caption</h4>
          <Select
            value={captionOf}
            aria-label="What to caption"
            onChange={(event) => setCaptionOf(event.target.value as "speech" | "on_screen")}
          >
            <option value="speech">Speech — what is said</option>
            <option value="on_screen">On-screen text — what is written on the picture</option>
          </Select>
          {captionOf === "on_screen" && (
            <p className="caption-editor-note">
              Each line is placed over the words it replaces, from this
              clip&rsquo;s on-screen text reading — so the style decides how it looks and the
              reading decides where it goes. Each line draws its own solid
              backdrop over the original text; a cover rendered in the Effects
              step makes a nicer patch, and the burn lands on that cut when
              one exists.
            </p>
          )}
        </section>

        <section className="caption-editor-styles" aria-label="Caption style">
          <h4>Style</h4>
          {/* Shelved the way the caption tools people know arrange theirs -
              the styles that move with the voice, the flat social shapes, the
              broadcast-safe set, the throwbacks. The API sends the styles
              already grouped; a heading appears where its group starts. */}
          <div className="caption-style-grid">
            {(catalogue?.styles ?? []).map((item, at, all) => (
              <Fragment key={item.id}>
                {(at === 0 || all[at - 1].category !== item.category) && (
                  <h5 className="caption-style-shelf">{item.category}</h5>
                )}
                <button
                  type="button"
                  className="caption-style-choice"
                  data-on={item.id === styleId ? "" : undefined}
                  aria-pressed={item.id === styleId}
                  onClick={() => chooseStyle(item.id)}
                >
                  <span className="caption-style-swatch" style={captionPosition(item.style.alignment)} aria-hidden="true">
                    <CaptionLook preset={item} sample={catalogue?.sample} compact />
                  </span>
                  <span className="caption-style-copy">
                    <strong>{item.label}</strong>
                    <small>{item.summary}</small>
                  </span>
                </button>
              </Fragment>
            ))}
          </div>
        </section>

        <section className="caption-editor-language" aria-label="Language">
          <h4>Caption language</h4>
          {/* Named as the action rather than as a noun. "Language" beside a
              dropdown reads as which language the clip is in - a fact - when
              it is actually an instruction to translate, and the difference
              is a feature people did not know was here. */}
          <p className="caption-editor-note">
            Captions are written in the language spoken unless you choose
            another here, and the transcript itself is left as it was — this
            translates the captions, not the words on record.
          </p>
          {pairs.length === 0 ? (
            <div className="caption-editor-setup">
              <p>Captions will be in the language spoken.</p>
              <ProviderSwitch
                label="Translate captions"
                provider="translate"
                state={mediaAi.state}
                busy={mediaAi.busy}
                onPrepare={(provider) => void mediaAi.prepare(provider)}
                onToggle={(provider, on) => void mediaAi.setActive(provider, on)}
              />
            </div>
          ) : availableTranslations.length > 0 ? (
            <Select
              value={translateTo}
              onChange={(event) => setTranslateTo(event.target.value)}
            >
              <option value="">As spoken — no translation</option>
              {availableTranslations.map((pair) => (
                <option key={`${pair.from}-${pair.to}`} value={pair.to}>
                  {pair.label.replace(/^.*? to /, "")}
                </option>
              ))}
            </Select>
          ) : (
            <p className="caption-editor-note">
              No installed translation starts from {sourceLanguage ?? "this transcript's language"}.
            </p>
          )}
          {translateTo && chosen?.needs_word_timings && (
            <p className="caption-editor-note">
              {typeof chosen.layout.max_words === "number"
                ? "Word timing on a translated track is estimated: the "
                  + "translated words are spread across each cue's measured "
                  + "span, so the style keeps its rhythm."
                : "This style highlights the word being spoken, which a "
                  + "translation cannot carry — word order changes, so the "
                  + "measured timings no longer match the words. It will show "
                  + "whole cues instead."}
            </p>
          )}
          {!canBurn && compatibleTargets.length > 0 && (
            <p className="caption-editor-note">
              Subtitle files are the compatible delivery for this selection. Burned captions need video.
            </p>
          )}
        </section>

        <section className="caption-editor-preview" aria-label="Caption timing and readability">
          <h4>
            Timing & readability
            {preview && (
              <Badge tone="neutral">
                {preview.cue_count} cues · {timecode(preview.duration_ms)}
              </Badge>
            )}
            {/* Again here, because this is the list somebody actually reads
                the wording in - and cues that are a translation should not
                have to be recognised as one from the words themselves. */}
            {translatingTo && <Badge tone="info">in {translatingTo}</Badge>}
          </h4>
          {problem && <p className="caption-editor-problem">{problem}</p>}
          {queued && <p className="caption-editor-queued">{queued}</p>}
          {busy && !preview && <p className="caption-editor-note">Building the first cues…</p>}
          {preview && (
            <ol className="caption-cue-list">
              {preview.cues.map((cue) => (
                <li key={cue.index}>
                  <button type="button" onClick={() => seekToCue(cue)}>
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
                      {cue.cps} cps
                    </span>
                  </button>
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
        </div>

        {/* What has already been made. Listed from the directory rather than
            from the job records, so a track outlives the job that produced it
            and stays reachable after the queue is swept. */}
        {files.length > 0 && (
          <section className="caption-editor-files" aria-label="Rendered tracks">
            <h4>Rendered</h4>
            <ul className="caption-file-list">
              {files.map((file) => (
                <li key={file.path}>
                  <button
                    type="button"
                    className="caption-file-download"
                    onClick={() => void download(file)}
                  >
                    {file.name}
                  </button>
                  <span>{file.format.toUpperCase()}</span>
                  <span>{Math.max(1, Math.round(file.size_bytes / 1024))} KB</span>
                </li>
              ))}
            </ul>
          </section>
        )}

      </div>
    </Dialog>
  );
}
