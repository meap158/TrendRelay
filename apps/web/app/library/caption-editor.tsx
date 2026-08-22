"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { AutoTranscribe } from "./auto-transcribe";
import { ProviderSwitch, providerOf, useMediaAi } from "./transcription-setup";
import { useT } from "../i18n-provider";

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
  const requestedTargets = useMemo(() => targets?.length
    ? targets
    : assetId
      ? [{ id: assetId, title: assetTitle ?? "Media", mediaKind: "video" }]
      : [], [assetId, assetTitle, targets]);
  const compatibleTargets = useMemo(() => requestedTargets.filter((target) =>
    ["video", "audio"].includes(target.mediaKind),
  ), [requestedTargets]);
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
                delivery,
                batch: { id: batchId, total: compatibleTargets.length },
              }),
            },
          );
          const body = await response.json().catch(() => ({}));
          return { target, response, body };
        }));
        for (const result of results) {
          if (result.response.ok) queuedIds.push(result.target.id);
          else failures.push(
            typeof result.body?.detail === "string"
              ? `${result.target.title}: ${result.body.detail}`
              : `${result.target.title}: ${t("library.actionCouldNotStart")}`,
          );
        }
      }
      // Burning re-encodes every frame, so the honest answer is that it has
      // been queued rather than that it is done.
      const summary = batch
        ? t("library.captionBatchQueued", { count: queuedIds.length })
        : delivery === "sidecar"
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
    batch,
    compatibleTargets,
    delivery,
    loadFiles,
    onQueued,
    styleId,
    t,
    translateTo,
    workspaceId,
  ]);

  const chosen = catalogue?.styles.find((item) => item.id === styleId);
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
              value={delivery}
              onChange={(event) => setDelivery(event.target.value)}
              preferredSide="above"
            >
              <option value="sidecar">Subtitle files only</option>
              <option value="burned">Burned into the video</option>
              <option value="both">Both</option>
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
      <div className="caption-editor">
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
          {needsSpeechTranscript && primary && (
            <div className="caption-editor-setup">
              <p>
                <strong>Transcribe speech here.</strong>{" "}
                The machine draft will unlock caption timing automatically when it finishes.
                Review the wording before publishing.
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
