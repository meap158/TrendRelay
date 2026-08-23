"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { SearchSelect } from "../ui/search-select";
import { Select } from "../ui/select";
import { SegmentedControl } from "../ui/segmented";
import { useT } from "../i18n-provider";
import { useJobs } from "../jobs-provider";

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
  description?: string | null;
  languages: string[];
  locales: string[];
  regions: string[];
  accents: string[];
  compatible_model_ids: string[];
  preview_url?: string | null;
};

type VoiceModel = {
  model_id: string;
  name: string;
  description?: string | null;
  languages: Array<{ language_id: string; name: string }>;
  can_use_style: boolean;
  can_use_speaker_boost: boolean;
  character_cost_multiplier: number;
  max_characters_free?: number | null;
  max_characters_paid?: number | null;
  maximum_text_length?: number | null;
};

type VoiceStatus = {
  configured: boolean;
  reachable: boolean;
  reason?: string | null;
  tier?: string | null;
  characters_remaining?: number | null;
  plan_is_free?: boolean;
  next_reset_unix?: number | null;
};

type VoiceSettings = {
  stability: number;
  similarity_boost: number;
  style: number;
  use_speaker_boost: boolean;
  speed: number;
};

type VoiceDefaults = {
  voice_id?: string | null;
  model_id?: string | null;
  language_code?: string | null;
  voice_settings?: VoiceSettings;
};

const DEFAULT_SETTINGS: VoiceSettings = {
  stability: 0.5,
  similarity_boost: 0.75,
  style: 0,
  use_speaker_boost: true,
  speed: 1,
};
const NO_VOICES: Voice[] = [];
const NO_MODELS: VoiceModel[] = [];

type Transcript = {
  id: string;
  kind: string;
  status: string;
  language?: string | null;
  text?: string | null;
  /** Who produced it - "faster-whisper", "operator-reviewed". The API has
      always sent this; it was read here before it was declared. */
  provider?: string | null;
};

type VoiceTarget = {
  id: string;
  title: string;
  mediaKind: string;
};

type PreparedVoiceTarget = VoiceTarget & { transcript: Transcript | null };

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
  targets,
  onQueued,
  canEdit,
  apiFetch,
  onClose,
}: {
  open: boolean;
  workspaceId: string;
  assetId?: string;
  assetTitle?: string;
  /** Only a video has a picture to put a voiceover on. */
  mediaKind?: string;
  targets?: VoiceTarget[];
  onQueued?: (message: string, assetIds: string[]) => void;
  canEdit: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
}) {
  const t = useT();
  const { announceMediaJobs, refresh: refreshJobs } = useJobs();
  const requestedTargets = useMemo<VoiceTarget[]>(() => targets?.length
    ? targets
    : assetId
      ? [{ id: assetId, title: assetTitle ?? "Media", mediaKind: mediaKind ?? "video" }]
      : [], [assetId, assetTitle, mediaKind, targets]);
  const compatibleTargets = useMemo(
    () => requestedTargets.filter((target) => ["video", "audio"].includes(target.mediaKind)),
    [requestedTargets],
  );
  const skippedTargets = requestedTargets.length - compatibleTargets.length;
  const primaryTarget = compatibleTargets[0];
  const primaryAssetId = primaryTarget?.id ?? "";
  const batch = requestedTargets.length > 1;
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
    models: VoiceModel[];
    status: VoiceStatus | null;
    defaults: VoiceDefaults;
    preparedTargets: PreparedVoiceTarget[];
  } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [voiceId, setVoiceId] = useState("");
  const [modelId, setModelId] = useState("");
  const [languageCode, setLanguageCode] = useState("");
  const [languageFilter, setLanguageFilter] = useState("");
  const [regionFilter, setRegionFilter] = useState("");
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings>(DEFAULT_SETTINGS);
  /** The override. Empty means "use the transcript", which is the common case. */
  const [script, setScript] = useState("");
  const [deliver, setDeliver] = useState<Deliver>("audio");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const polling = useRef<number | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();

    Promise.all([
      apiFetch(`/api/workspaces/${workspaceId}/media/library/voice/voices`,
        { signal: controller.signal }),
      Promise.all(compatibleTargets.map((target) =>
        apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${target.id}`,
          { signal: controller.signal })
          .then(async (response) => ({
            target,
            response,
            payload: (await response.json()) as { transcripts?: Transcript[]; detail?: string },
          })),
      )),
    ])
      .then(async ([voiceResponse, assetResponses]) => {
        const voicePayload = (await voiceResponse.json()) as {
          voices?: Voice[]; models?: VoiceModel[]; status?: VoiceStatus;
          defaults?: VoiceDefaults; detail?: string;
        };
        if (!voiceResponse.ok) {
          throw new Error(voicePayload.detail ?? "The voices could not be read.");
        }
        const preparedTargets = assetResponses.map(({ target, response, payload }) => {
          if (!response.ok) throw new Error(payload.detail ?? `${target.title} could not be read.`);
          // Reviewed only. A machine draft is deliberately not offered here -
          // see the note at the top of this file.
          const transcript = (payload.transcripts ?? []).find(
            (item) => item.kind === "speech" && item.status === "reviewed"
              && (item.text ?? "").trim(),
          ) ?? null;
          return { ...target, transcript };
        });
        const reviewed = preparedTargets[0]?.transcript ?? null;
        setData({
          voices: voicePayload.voices ?? [],
          models: voicePayload.models ?? [],
          status: voicePayload.status ?? null,
          defaults: voicePayload.defaults ?? {},
          preparedTargets,
        });
        setLoadError(null);
        const configuredVoice = voicePayload.voices?.find(
          (voice) => voice.voice_id === voicePayload.defaults?.voice_id,
        );
        setVoiceId((current) => current || configuredVoice?.voice_id
          || voicePayload.voices?.[0]?.voice_id || "");
        const firstModel = voicePayload.models?.find(
          (model) => model.model_id === voicePayload.defaults?.model_id,
        ) ?? voicePayload.models?.[0];
        setModelId((current) => current || firstModel?.model_id || "");
        setVoiceSettings((current) => voicePayload.defaults?.voice_settings ?? current);
        const transcriptLanguage = reviewed?.language?.split("-")[0]?.toLowerCase() ?? "";
        const configuredLanguage = voicePayload.defaults?.language_code ?? "";
        if (configuredLanguage && firstModel?.languages.some(
          (item) => item.language_id === configuredLanguage,
        )) setLanguageCode((current) => current || configuredLanguage);
        else if (transcriptLanguage && firstModel?.languages.some(
          (item) => item.language_id === transcriptLanguage,
        )) setLanguageCode((current) => current || transcriptLanguage);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setLoadError(
          reason instanceof Error ? reason.message : "Voice generation is unavailable.",
        );
      });

    return () => controller.abort();
  }, [open, reload, apiFetch, workspaceId, compatibleTargets]);

  // A job left running must not keep a timer alive behind a closed screen.
  useEffect(() => () => {
    if (polling.current) window.clearInterval(polling.current);
  }, []);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const refreshJob = useCallback(async () => {
    try {
      const response = await apiFetch(`/api/workspaces/${workspaceId}/media/library/voice/jobs`);
      const payload = (await response.json()) as { jobs?: Job[] };
      const mine = (payload.jobs ?? []).find((item) => item.payload?.asset_id === primaryAssetId);
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
  }, [apiFetch, workspaceId, primaryAssetId]);

  // A recording has no picture, so the mux cannot run and the API refuses it
  // at the queue. Not offering it here means that refusal is never reached -
  // the same reason the character count is on screen beside the button.
  const canMux = (data?.preparedTargets ?? compatibleTargets).every(
    (target) => target.mediaKind === "video",
  );
  const delivery = canMux ? DELIVERY : DELIVERY.slice(0, 1);

  const voices = data?.voices ?? NO_VOICES;
  const models = data?.models ?? NO_MODELS;
  const status = data?.status ?? null;
  const preparedTargets = data?.preparedTargets ?? [];
  const transcript = preparedTargets[0]?.transcript ?? null;
  const voicableTargets = preparedTargets.filter((target) => target.transcript);
  const missingTranscripts = preparedTargets.length - voicableTargets.length;
  const loading = open && !data && !loadError;

  const typed = script.trim();
  const spoken = typed || (transcript?.text ?? "").trim();
  const scripts = batch
    ? voicableTargets.map((target) => (target.transcript?.text ?? "").trim())
    : [spoken];
  const characters = scripts.reduce((sum, text) => sum + text.length, 0);
  const largestScript = scripts.reduce((largest, text) => Math.max(largest, text.length), 0);
  const selectedVoice = voices.find((voice) => voice.voice_id === voiceId) ?? null;
  const selectedModel = models.find((model) => model.model_id === modelId) ?? null;
  const languages = useMemo(
    () => [...new Set(voices.flatMap((voice) => voice.languages))].sort(),
    [voices],
  );
  const regions = useMemo(
    () => [...new Set(voices.flatMap((voice) => voice.regions))].sort(),
    [voices],
  );
  const regionNames = useMemo(() => {
    const names = new Intl.DisplayNames(["en"], { type: "region" });
    return Object.fromEntries(regions.map((region) => [region, names.of(region) ?? region]));
  }, [regions]);
  const filteredVoices = useMemo(
    () => voices.filter((voice) =>
      (!languageFilter || voice.languages.includes(languageFilter))
      && (!regionFilter || voice.regions.includes(regionFilter))),
    [voices, languageFilter, regionFilter],
  );
  const voiceOptions = useMemo(() => filteredVoices.map((voice) => {
    const qualifiers = [
      voice.accents[0],
      voice.labels?.gender,
      voice.labels?.age,
      ...voice.regions.map((region) => regionNames[region]),
      voice.category,
    ].filter(Boolean);
    return {
      value: voice.voice_id,
      label: voice.name,
      description: qualifiers.join(" · "),
      keywords: [voice.description, ...voice.languages, ...voice.locales,
        ...Object.values(voice.labels ?? {})].filter(Boolean).join(" "),
    };
  }), [filteredVoices, regionNames]);
  const remaining = status?.characters_remaining ?? null;
  const estimatedCredits = Math.ceil(
    characters * (selectedModel?.character_cost_multiplier ?? 1),
  );
  const requestLimit = status?.plan_is_free
    ? selectedModel?.max_characters_free
    : selectedModel?.max_characters_paid;
  const effectiveRequestLimit = requestLimit ?? selectedModel?.maximum_text_length ?? null;
  // Known and short is the only state worth blocking on. An unknown allowance
  // must not read as an empty account: a flaky status call is not a refusal.
  const tooLong = remaining !== null && estimatedCredits > remaining;
  const exceedsRequest = effectiveRequestLimit !== null
    && effectiveRequestLimit !== undefined && largestScript > effectiveRequestLimit;
  const ready = Boolean(voiceId) && Boolean(modelId) && characters > 0
    && !tooLong && !exceedsRequest && canEdit;

  async function generate() {
    setQueueing(true);
    setError(null);
    try {
      const queueTargets = batch
        ? voicableTargets
        : preparedTargets.slice(0, 1);
      const queuedIds: string[] = [];
      const failures: string[] = [];
      const queuedJobs: Job[] = [];
      let firstJob: Job | null = null;
      for (let at = 0; at < queueTargets.length; at += 4) {
        const results = await Promise.all(queueTargets.slice(at, at + 4).map(async (target) => {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/media/library/assets/${target.id}/voiceover`,
            {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                voice_id: voiceId,
                model_id: modelId,
                ...(languageCode ? { language_code: languageCode } : {}),
                voice_settings: voiceSettings,
                deliver,
                ...(batch
                  ? { transcript_id: target.transcript!.id }
                  : typed
                    ? { text: typed }
                    : target.transcript
                      ? { transcript_id: target.transcript.id }
                      : {}),
              }),
            },
          );
          const payload = await response.json().catch(() => ({})) as { job?: Job; detail?: string };
          return { target, response, payload };
        }));
        for (const result of results) {
          if (result.response.ok) {
            queuedIds.push(result.target.id);
            firstJob ??= result.payload.job ?? null;
            if (result.payload.job) queuedJobs.push(result.payload.job);
          } else {
            failures.push(`${result.target.title}: ${result.payload.detail ?? t("library.actionCouldNotStart")}`);
          }
        }
      }
      if (!queuedIds.length) throw new Error(failures[0] ?? "The voiceover could not be queued.");
      announceMediaJobs(queuedJobs);
      void refreshJobs();
      if (batch) {
        const summary = t("library.voiceBatchQueued", { count: queuedIds.length });
        if (failures.length) setError(`${t("library.actionBatchFailed", { count: failures.length })} ${failures[0]}`);
        onQueued?.(summary, queuedIds);
      } else {
        setJob(firstJob);
      }
      if (polling.current) window.clearInterval(polling.current);
      if (!batch) polling.current = window.setInterval(() => void refreshJob(), 2000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The voiceover could not be queued.");
    } finally {
      setQueueing(false);
    }
  }

  async function previewVoice() {
    setPreviewing(true);
    setError(null);
    try {
      const previewText = (spoken || "This is a preview of the selected voice.").slice(0, 300);
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/voice/preview`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            voice_id: voiceId,
            model_id: modelId,
            text: previewText,
            ...(languageCode ? { language_code: languageCode } : {}),
            voice_settings: voiceSettings,
          }),
        },
      );
      const payload = await response.json().catch(() => ({})) as {
        mime_type?: string; content_base64?: string; detail?: string;
      };
      if (!response.ok || !payload.content_base64) {
        throw new Error(payload.detail ?? "The voice preview could not be generated.");
      }
      const binary = window.atob(payload.content_base64);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], {
        type: payload.mime_type ?? "audio/mpeg",
      }));
      setPreviewUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return url;
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The voice preview could not be generated.");
    } finally {
      setPreviewing(false);
    }
  }

  const unavailable = status && !status.reachable;

  return (
    <Dialog
      open={open}
      title="Voiceover"
      description={batch && primaryTarget
        ? t("library.selectionDialogDescription", {
            count: compatibleTargets.length,
            title: primaryTarget.title,
          })
        : primaryTarget?.title ?? assetTitle}
      onClose={onClose}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            busy={queueing}
            disabled={!ready || queueing || Boolean(unavailable)}
            onClick={() => void generate()}
          >{batch
            ? t("library.generateVoiceoversFor", { count: voicableTargets.length })
            : "Generate"}</Button>
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
          {batch && skippedTargets > 0 && (
            <p className="voice-note">{t("library.actionSkippedIncompatible", { count: skippedTargets })}</p>
          )}
          {batch && (
            <p className="voice-note">
              {t("library.voiceBatchTranscriptNote")}{" "}
              {missingTranscripts > 0
                ? t("library.voiceBatchMissingTranscript", { count: missingTranscripts })
                : null}
            </p>
          )}
          <div className="voice-filter-grid">
            <label className="voice-field">
              <span>Voice language</span>
              <Select value={languageFilter} onChange={(event) => {
                setLanguageFilter(event.target.value);
                setVoiceId("");
              }}>
                <option value="">All languages</option>
                {languages.map((language) => <option key={language} value={language}>{language}</option>)}
              </Select>
            </label>
            <label className="voice-field">
              <span>Voice region</span>
              <Select value={regionFilter} onChange={(event) => {
                setRegionFilter(event.target.value);
                setVoiceId("");
              }}>
                <option value="">All countries and regions</option>
                {regions.map((region) => (
                  <option key={region} value={region}>{regionNames[region]} · {region}</option>
                ))}
              </Select>
            </label>
          </div>

          <div className="voice-field">
            <span>Voice <em>{filteredVoices.length} available</em></span>
            <SearchSelect
              value={voiceId}
              options={voiceOptions}
              onChange={setVoiceId}
              placeholder={voiceOptions.length ? "Choose a voice" : "No voices match these filters"}
              searchPlaceholder="Search name, accent, country, gender, or use case…"
              emptyLabel="No matching voices"
              ariaLabel="ElevenLabs voice"
              clearable={false}
              disabled={!voiceOptions.length}
            />
            {selectedVoice?.description && <small>{selectedVoice.description}</small>}
          </div>

          <div className="voice-filter-grid">
            <label className="voice-field">
              <span>Model</span>
              <Select value={modelId} onChange={(event) => {
                const next = event.target.value;
                setModelId(next);
                const supported = models.find((model) => model.model_id === next)?.languages ?? [];
                if (languageCode && !supported.some((item) => item.language_id === languageCode)) {
                  setLanguageCode("");
                }
              }}>
                {models.length === 0 && <option value="">No TTS models available</option>}
                {models.map((model) => (
                  <option key={model.model_id} value={model.model_id}>{model.name}</option>
                ))}
              </Select>
              {selectedModel?.description && <small>{selectedModel.description}</small>}
            </label>
            <label className="voice-field">
              <span>Spoken language</span>
              <Select value={languageCode} onChange={(event) => setLanguageCode(event.target.value)}>
                <option value="">Detect from text</option>
                {(selectedModel?.languages ?? []).map((language) => (
                  <option key={language.language_id} value={language.language_id}>
                    {language.name} · {language.language_id}
                  </option>
                ))}
              </Select>
              <small>Restricts normalization where the selected model supports it.</small>
            </label>
          </div>

          {!batch && <label className="voice-field">
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
          </label>}

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

          <details className="voice-controls">
            <summary>Voice controls</summary>
            <div className="voice-slider-grid">
              {([
                ["stability", "Stability", 0, 1, 0.05],
                ["similarity_boost", "Similarity", 0, 1, 0.05],
                ["speed", "Speed", 0.7, 1.2, 0.05],
                ...(selectedModel?.can_use_style
                  ? [["style", "Style", 0, 1, 0.05] as const] : []),
              ] as const).map(([key, label, min, max, step]) => (
                <label key={key}>
                  <span>{label}<output>{voiceSettings[key].toFixed(2)}</output></span>
                  <input type="range" min={min} max={max} step={step}
                    value={voiceSettings[key]}
                    onChange={(event) => setVoiceSettings((current) => ({
                      ...current, [key]: Number(event.target.value),
                    }))} />
                </label>
              ))}
              {selectedModel?.can_use_speaker_boost && (
                <label className="voice-boost">
                  <input type="checkbox" checked={voiceSettings.use_speaker_boost}
                    onChange={(event) => setVoiceSettings((current) => ({
                      ...current, use_speaker_boost: event.target.checked,
                    }))} />
                  <span>Speaker boost<small>Closer to the original voice; slightly slower.</small></span>
                </label>
              )}
            </div>
          </details>

          <div className="voice-preview">
            <Button
              variant="secondary"
              busy={previewing}
              disabled={!voiceId || !modelId || previewing || !canEdit}
              onClick={() => void previewVoice()}
            >Preview voice</Button>
            <span>
              Uses up to 300 characters from this script with the selected model and controls.
            </span>
            {previewUrl && (
              <audio controls autoPlay preload="metadata" src={previewUrl}>
                Your browser cannot play this voice preview.
              </audio>
            )}
          </div>

          {/* The cost, beside what is left to spend. This is the whole reason
              the refusal lives at the queue rather than in the worker: at this
              moment it is still a number somebody can act on. */}
          <p className={`voice-cost${tooLong || exceedsRequest ? " problem" : ""}`}>
            <strong>{characters.toLocaleString()} characters</strong>
            {selectedModel && selectedModel.character_cost_multiplier !== 1
              ? ` · about ${estimatedCredits.toLocaleString()} credits`
              : ""}
            {remaining === null
              ? " · allowance unknown"
              : ` · ${remaining.toLocaleString()} left on the ${status?.tier ?? "current"} plan`}
            {status?.next_reset_unix
              ? ` · resets ${new Date(status.next_reset_unix * 1000).toLocaleDateString()}`
              : ""}
            {tooLong && (
              <>
                {" "}— {(estimatedCredits - remaining!).toLocaleString()} more than the plan has.
                Shorten the script, or top up the plan.
              </>
            )}
            {exceedsRequest && effectiveRequestLimit && (
              <> — This model accepts {effectiveRequestLimit.toLocaleString()} characters per request on this plan.</>
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
