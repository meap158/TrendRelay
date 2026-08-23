"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { AutoTranscribe } from "./auto-transcribe";
import { ProviderSwitch, providerOf, useMediaAi } from "./transcription-setup";
import { useT } from "../i18n-provider";
import { useJobs } from "../jobs-provider";

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
}: {
  preset: CaptionStyle;
  cue?: Cue | null;
  activeMs?: number;
  sample?: string;
  compact?: boolean;
  highlightWords?: boolean;
  sourceWidth?: number;
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
      style={captionTextStyle(preset.style, compact, sourceWidth)}
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
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  // A preview asked for after a newer one must not overwrite it: the requests
  // are independent and the slower one can land last.
  const latest = useRef(0);
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

  const load = useCallback(async () => {
    if (!open || !workspaceId || !primaryAssetId) return;
    const ticket = ++latest.current;
    setBusy(true);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${primaryAssetId}/captions/preview`,
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
  }, [open, workspaceId, primaryAssetId, styleId, translateTo, apiFetch]);

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
    refreshJobs,
    styleId,
    t,
    translateTo,
    workspaceId,
  ]);

  const chosen = catalogue?.styles.find((item) => item.id === styleId);
  const previewPreset = chosen ?? catalogue?.styles[0] ?? null;
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
            </p>
            <AutoTranscribe
              workspaceId={workspaceId}
              assetId={primary.id}
              hasAudio={hasAudio}
              mediaKind={primary.mediaKind}
              modesAvailable={["speech"]}
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
              {busy && <Badge tone="neutral">Updating…</Badge>}
            </header>
            <div className="caption-media-frame">
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
                  onLoadedMetadata={(event) => setMediaSourceWidth(event.currentTarget.videoWidth || 1920)}
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
              {previewPreset && activeCue && (
                <div
                  className="caption-media-overlay"
                  style={captionPosition(previewPreset.style.alignment)}
                  aria-live="off"
                >
                  <CaptionLook
                    preset={previewPreset}
                    cue={activeCue}
                    activeMs={playbackMs}
                    highlightWords={!translateTo}
                    sourceWidth={mediaSourceWidth}
                  />
                </div>
              )}
            </div>
            <p className="caption-media-help">
              {previewPreset
                ? `${previewPreset.label} · ${activeCue ? `${timecode(activeCue.start_ms)}–${timecode(activeCue.end_ms)}` : "No caption at this position"}`
                : "Choose a preset to preview it."}
            </p>
          </section>

          <div className="caption-editor-controls">

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
                <span className="caption-style-swatch" style={captionPosition(item.style.alignment)} aria-hidden="true">
                  <CaptionLook preset={item} sample={catalogue?.sample} compact />
                </span>
                <span className="caption-style-copy">
                  <strong>{item.label}</strong>
                  <small>{item.summary}</small>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="caption-editor-language" aria-label="Language">
          <h4>Language</h4>
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
              This style highlights the word being spoken, which a translation
              cannot carry — word order changes, so the measured timings no
              longer match the words. It will show whole cues instead.
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
