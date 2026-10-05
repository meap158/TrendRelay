"use client";

import { useEffect, useRef, useState } from "react";

import { useT } from "../i18n-provider";
import { useJobs } from "../jobs-provider";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";

type Provider = { id: string; label: string };

type Generation = {
  id?: string;
  status: string;
  provider_label?: string | null;
  error?: string | null;
  asset_id?: string | null;
  /** Attempts already made. Queued again after one means a free retry. */
  attempt?: number;
};

/**
 * One confirmed clip from the image open in the Library.
 *
 * The prompt is what the operator writes here. It is not filled from a recipe.
 * Each ready provider is a button. A refusal is shown and stops: nothing is
 * sent again, and nothing is rewritten.
 */
export function GenerateVideoDialog({
  open,
  workspaceId,
  assetId,
  apiFetch,
  onClose,
  onFinished,
}: {
  open: boolean;
  workspaceId: string;
  assetId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onFinished: (assetId: string) => void;
}) {
  const t = useT();
  const { refresh: refreshJobs } = useJobs();
  const [prompt, setPrompt] = useState("");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [generation, setGeneration] = useState<Generation | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [watch, setWatch] = useState(0);
  const reported = useRef("");
  /** A success only navigates away if this dialog started that generation. */
  const armed = useRef(false);
  const finished = useRef(onFinished);
  // Kept current after each render; a ref is not written while rendering.
  useEffect(() => {
    finished.current = onFinished;
  }, [onFinished]);

  // Another image starts a fresh ask. Adjusted while rendering, so the last
  // image's prompt and outcome are never shown under the new one.
  const [askedFor, setAskedFor] = useState(assetId);
  if (askedFor !== assetId) {
    setAskedFor(assetId);
    setPrompt("");
    setNotice("");
    setError("");
    setGeneration(null);
  }
  // The refs belong to effects and handlers, so they are reset there.
  useEffect(() => {
    reported.current = "";
    armed.current = false;
  }, [assetId]);

  useEffect(() => {
    if (!open) armed.current = false;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    void apiFetch(`/api/workspaces/${workspaceId}/media/library/video-providers`)
      .then(async (response) => {
        if (!response.ok) return [] as Provider[];
        const payload = await response.json() as { providers?: Provider[] };
        return payload.providers ?? [];
      })
      .then((rows) => {
        if (!cancelled) setProviders(rows);
      })
      .catch(() => {
        if (!cancelled) setProviders([]);
      });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, open, workspaceId]);

  useEffect(() => {
    if (!open) return undefined;
    let stopped = false;
    let timer = 0;

    async function tick() {
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/generation`,
        );
        if (!response.ok || stopped) return;
        const payload = await response.json() as { generation?: Generation };
        const next = payload.generation ?? null;
        if (stopped) return;
        setGeneration(next);
        if (next?.status === "failed" && next.error) setError(next.error);
        if (
          armed.current
          && next?.status === "succeeded"
          && next.asset_id
          && reported.current !== next.asset_id
        ) {
          reported.current = next.asset_id;
          armed.current = false;
          setNotice(t("library.generateVideoReady"));
          refreshJobs();
          finished.current(next.asset_id);
        }
        if (!stopped && (next?.status === "queued" || next?.status === "running")) {
          timer = window.setTimeout(() => void tick(), 3000);
        }
      } catch {
        // A missed poll is the next open. The job itself is unchanged.
      }
    }

    void tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [apiFetch, assetId, open, refreshJobs, t, watch, workspaceId]);

  const inflight = generation?.status === "queued" || generation?.status === "running";
  // Back in the queue after an attempt: the provider was slow or the network
  // dropped, and the next attempt polls the same paid request.
  const retrying = generation?.status === "queued" && (generation.attempt ?? 0) > 0;

  async function generateWith(provider: Provider) {
    const text = prompt.trim();
    if (!text || inflight || busy) return;
    if (!window.confirm(t("library.generateVideoConfirm", { provider: provider.label }))) return;
    setBusy(provider.id);
    setError("");
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/generate-video`,
        {
          method: "POST",
          body: JSON.stringify({
            provider_id: provider.id,
            prompt: text,
            confirm_external_action: true,
          }),
        },
      );
      if (!response.ok) {
        setError(await errorDetail(response, t("library.generateVideoHelp")));
        return;
      }
      armed.current = true;
      setNotice(t("library.generateVideoQueued"));
      setWatch((current) => current + 1);
      refreshJobs();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("library.generateVideoHelp"));
    } finally {
      setBusy("");
    }
  }

  return (
    <Dialog
      open={open}
      title={t("library.generateVideoTitle")}
      description={t("library.generateVideoHelp")}
      onClose={onClose}
    >
      <label className="voice-field library-generate-prompt">
        <span>{t("library.generateVideoPrompt")}</span>
        <textarea
          value={prompt}
          maxLength={4000}
          rows={6}
          disabled={inflight}
          onChange={(event) => setPrompt(event.target.value)}
        />
        <small>{t("library.generateVideoPromptHelp")}</small>
      </label>
      <div className="library-generate-providers">
        {providers.map((provider) => (
          <Button
            key={provider.id}
            variant="primary"
            busy={busy === provider.id}
            disabled={busy !== "" || inflight || prompt.trim().length === 0}
            onClick={() => void generateWith(provider)}
          >{busy === provider.id || inflight
            ? t("library.generateVideoGenerating")
            : t("library.generateVideoWith", { provider: provider.label })}</Button>
        ))}
      </div>
      {retrying && <p role="status">{t("library.generateVideoRetrying")}</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p className="voice-note problem" role="alert">{error}</p>}
    </Dialog>
  );
}

async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json() as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail;
  } catch {
    // The status line is enough when the body is not JSON.
  }
  return fallback;
}
