"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Switch } from "../ui/primitives";

/**
 * Turning local transcription on, from wherever the operator ran into it.
 *
 * This used to be a sentence of documentation — "run `python scripts/media_ai.py
 * install-speech`" — printed in the one dialog that noticed the provider was
 * missing. An interface that ends in "now open a terminal" ends there: it cannot
 * show how far a download got, cannot say why one failed, and asks somebody who
 * came to caption a video to go and be a system administrator instead.
 *
 * So the same three states live here and are read from one endpoint:
 *
 * *Not downloaded.* A pinned checkout, a few hundred megabytes of wheels and a
 * model. Minutes of work, so it is a durable job like every other minutes-long
 * thing here, and what is rendered is its progress.
 *
 * *Downloaded but switched off.* One click either way, instantly, because the
 * bytes are already on disk. This is the quick switch — a provider you have
 * paid for once should not cost a page of setup to use again.
 *
 * *Running.* Nothing to say beyond which provider it is.
 *
 * One hook and one control, used by the Library header and the caption editor,
 * so the two cannot come to disagree about what is installed.
 */

export type MediaAiProvider = {
  provider: string;
  tool_id: string;
  source_active: boolean;
  runtime_ready: boolean;
  model?: string;
  model_cached?: boolean;
  prepared: boolean;
  ready: boolean;
  pairs?: { from: string; to: string; label: string }[];
};

export type MediaAiJob = {
  id: string;
  status: string;
  stalled: boolean;
  progress: number | null;
  progress_stage: string | null;
  error: string | null;
  result: { skipped?: string[] } | null;
};

export type MediaAiState = {
  providers: { speech: MediaAiProvider; ocr: MediaAiProvider; translation: MediaAiProvider };
  setup_jobs: Record<string, MediaAiJob | undefined>;
};

/** How often to ask again while a download is running. */
const WATCH_MS = 2000;

export type ProviderKey = "speech" | "ocr" | "translate";

/** Which key of `providers` a provider is filed under. Only translate differs. */
const STATUS_KEY: Record<ProviderKey, keyof MediaAiState["providers"]> = {
  speech: "speech",
  ocr: "ocr",
  translate: "translation",
};

export function useMediaAi(
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>,
  active = true,
) {
  const [state, setState] = useState<MediaAiState | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState<ProviderKey | null>(null);
  // Read by the poll without making it a dependency, or every answer would
  // rebuild the timer that asked for it.
  const running = useRef(false);

  const refresh = useCallback(async () => {
    const response = await apiFetch("/api/media-ai/providers");
    if (!response.ok) throw new Error("Provider status is unavailable.");
    const payload = (await response.json()) as MediaAiState;
    running.current = Object.values(payload.setup_jobs).some(
      (job) => job && !job.stalled && (job.status === "queued" || job.status === "running"),
    );
    setState(payload);
    return payload;
  }, [apiFetch]);

  useEffect(() => {
    if (!active) return;
    let live = true;
    let timer = 0;
    const tick = async () => {
      try {
        await refresh();
      } catch {
        // A poll that fails says nothing about the download, which is running
        // in another process. The last known state stays on screen.
      }
      if (!live) return;
      // Only while something is actually downloading. A settled machine asks
      // once, when the page opens.
      if (running.current) timer = window.setTimeout(() => void tick(), WATCH_MS);
    };
    void tick();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [active, refresh]);

  /** Queue the download. The poll above takes over from there. */
  const prepare = useCallback(
    async (provider: ProviderKey) => {
      setBusy(provider);
      setFailure("");
      try {
        const response = await apiFetch(`/api/media-ai/providers/${provider}/prepare`, {
          method: "POST",
          body: JSON.stringify({ confirm_external_action: true }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail ?? "The download could not be started.");
        running.current = true;
        await refresh();
      } catch (reason) {
        setFailure(reason instanceof Error ? reason.message : "The download could not be started.");
      } finally {
        setBusy(null);
      }
    },
    [apiFetch, refresh],
  );

  /** The quick switch: no download, just whether TrendRelay may use it. */
  const setActive = useCallback(
    async (provider: ProviderKey, on: boolean) => {
      const toolId = state?.providers[STATUS_KEY[provider]].tool_id;
      if (!toolId) return;
      setBusy(provider);
      setFailure("");
      try {
        const response = await apiFetch(`/api/tools/${toolId}/activation`, {
          method: "POST",
          body: JSON.stringify({ active: on }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail ?? "That could not be changed.");
        await refresh();
      } catch (reason) {
        setFailure(reason instanceof Error ? reason.message : "That could not be changed.");
      } finally {
        setBusy(null);
      }
    },
    [apiFetch, refresh, state],
  );

  return { state, failure, busy, prepare, setActive, refresh };
}

export function providerOf(state: MediaAiState | null, provider: ProviderKey) {
  return state?.providers[STATUS_KEY[provider]] ?? null;
}

/**
 * The Library's transcription indicator, as a control rather than a tooltip.
 *
 * The dot beside the heading already knew whether transcription was available.
 * It said so and stopped, which made it a notice about a problem the operator
 * could not do anything about from where they were standing. The same dot now
 * opens, and what opens is the switch.
 */
export function TranscriptionSwitch({
  apiFetch,
}: {
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement | null>(null);
  // Polled while the popover is open, and once on mount so the dot can show the
  // right colour without being clicked.
  const mediaAi = useMediaAi(apiFetch, true);
  const speech = providerOf(mediaAi.state, "speech");

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const state = speech?.ready ? "ready" : "setup";
  const summary = speech?.ready
    ? "Transcription: on"
    : speech?.prepared
      ? "Transcription: downloaded, switched off"
      : "Transcription: reviewed text import only";

  return (
    <div className="library-status-menu" ref={wrapper}>
      <button
        type="button"
        className={`library-status-dot ${state}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={summary}
        onClick={() => setOpen((current) => !current)}
      >
        {speech?.ready
          ? <CircleCheck size={14} aria-hidden="true" />
          : <CircleAlert size={14} aria-hidden="true" />}
      </button>
      {open && (
        <div className="library-status-popover" role="dialog" aria-label="Transcription">
          <p className="library-status-popover-title">{summary}</p>
          <ProviderSwitch
            label="Transcribe speech"
            provider="speech"
            state={mediaAi.state}
            busy={mediaAi.busy}
            onPrepare={(provider) => void mediaAi.prepare(provider)}
            onToggle={(provider, on) => void mediaAi.setActive(provider, on)}
          />
          <ProviderSwitch
            label="Translate captions"
            provider="translate"
            state={mediaAi.state}
            busy={mediaAi.busy}
            onPrepare={(provider) => void mediaAi.prepare(provider)}
            onToggle={(provider, on) => void mediaAi.setActive(provider, on)}
          />
          {mediaAi.failure && (
            <p className="library-status-popover-problem" role="alert">{mediaAi.failure}</p>
          )}
          <p className="library-status-popover-note">
            Runs on this machine. Nothing is uploaded, and every transcript it
            produces is a draft to review. <Link href="/tools">More in Tools</Link>
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * The control itself: a switch once the runtime is there, a download before it.
 *
 * Compact on purpose. It appears inside a dialog that is already about
 * something else and beside a heading, so it is a line rather than a panel.
 */
export function ProviderSwitch({
  label,
  provider,
  state,
  busy,
  onPrepare,
  onToggle,
}: {
  label: string;
  provider: ProviderKey;
  state: MediaAiState | null;
  busy: ProviderKey | null;
  onPrepare: (provider: ProviderKey) => void;
  onToggle: (provider: ProviderKey, on: boolean) => void;
}) {
  const status = providerOf(state, provider);
  const job = state?.setup_jobs[provider];
  const downloading = Boolean(job && !job.stalled && (job.status === "queued" || job.status === "running"));
  const working = busy === provider || downloading;

  if (!status) return null;

  return (
    <div className="provider-switch" data-state={status.ready ? "on" : status.prepared ? "off" : "absent"}>
      <span className="provider-switch-name">
        <strong>{label}</strong>
        <small>{status.provider}</small>
      </span>

      {status.prepared ? (
        /* Downloaded already, so this is the quick switch and nothing more: a
           switch, not a wizard, because the expensive part is behind them. */
        <Switch
          checked={status.source_active}
          disabled={working}
          label={status.source_active ? "On" : "Off"}
          onChange={(next) => onToggle(provider, next)}
        />
      ) : (
        <Button
          variant="secondary"
          size="sm"
          busy={working}
          disabled={working}
          onClick={() => onPrepare(provider)}
        >
          {downloading ? "Downloading…" : "Set up"}
        </Button>
      )}

      {/* Progress only while it is running: a bar frozen at 100% after the fact
          reads as something still happening. */}
      {downloading && (
        <span className="provider-switch-progress">
          <progress max={1} value={job?.progress ?? undefined} />
          <small>{job?.progress_stage ?? "Starting…"}</small>
        </span>
      )}
      {/* A failed setup attempt is history once the runtime is demonstrably
          prepared. Keeping the old 401 or interrupted-worker message under a
          working On switch made the current state contradict itself. */}
      {!status.prepared && !downloading && job?.status === "failed" && job.error && (
        <span className="provider-switch-problem" role="alert">{job.error}</span>
      )}
      {!downloading && job?.result?.skipped?.length ? (
        <span className="provider-switch-problem">
          {job.result.skipped.length} language package(s) unavailable: {job.result.skipped.join(", ")}
        </span>
      ) : null}
    </div>
  );
}
