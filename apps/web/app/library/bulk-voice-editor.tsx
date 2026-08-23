"use client";

import { useEffect, useMemo, useState } from "react";

import { useT } from "../i18n-provider";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { SearchSelect } from "../ui/search-select";
import { SegmentedControl } from "../ui/segmented";

type Target = { id: string; title: string; mediaKind: string };
type Transcript = { id: string; kind: string; status: string; text?: string | null };
type PreparedTarget = Target & { transcript: Transcript | null };
type Deliver = "audio" | "video" | "both";

/**
 * A deliberately bounded bulk adapter around the per-asset voiceover API.
 *
 * The full single-asset editor can evolve independently (models, previews,
 * advanced controls). A selection has a different safety rule: every clip uses
 * its own reviewed transcript, billed work is shown before queueing, and at
 * most four requests are started at once.
 */
export function BulkVoiceEditor({
  open,
  workspaceId,
  targets,
  canEdit,
  apiFetch,
  onClose,
  onQueued,
}: {
  open: boolean;
  workspaceId: string;
  targets: Target[];
  canEdit: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onQueued: (message: string, assetIds: string[]) => void;
}) {
  const t = useT();
  const compatible = useMemo(
    () => targets.filter((target) => ["video", "audio"].includes(target.mediaKind)),
    [targets],
  );
  const skipped = targets.length - compatible.length;
  const [data, setData] = useState<{
    voices: Array<{ voice_id: string; name: string; category?: string | null }>;
    configured: boolean;
    reachable: boolean;
    reason?: string | null;
    remaining?: number | null;
    prepared: PreparedTarget[];
  } | null>(null);
  const [voiceId, setVoiceId] = useState("");
  const [deliver, setDeliver] = useState<Deliver>("audio");
  const [problem, setProblem] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    Promise.all([
      apiFetch(`/api/workspaces/${workspaceId}/media/library/voice/voices`, {
        signal: controller.signal,
      }),
      Promise.all(compatible.map(async (target) => {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/media/library/assets/${target.id}`,
          { signal: controller.signal },
        );
        const payload = await response.json() as { transcripts?: Transcript[]; detail?: string };
        if (!response.ok) throw new Error(payload.detail ?? t("library.actionCouldNotStart"));
        const transcript = (payload.transcripts ?? []).find((item) =>
          item.kind === "speech" && item.status === "reviewed" && (item.text ?? "").trim(),
        ) ?? null;
        return { ...target, transcript };
      })),
    ]).then(async ([voiceResponse, prepared]) => {
      const payload = await voiceResponse.json() as {
        voices?: Array<{ voice_id: string; name: string; category?: string | null }>;
        status?: {
          configured?: boolean; reachable?: boolean; reason?: string | null;
          characters_remaining?: number | null;
        };
        detail?: string;
      };
      if (!voiceResponse.ok) throw new Error(payload.detail ?? t("library.voiceUnavailable"));
      const voices = payload.voices ?? [];
      setData({
        voices,
        configured: Boolean(payload.status?.configured),
        reachable: Boolean(payload.status?.reachable),
        reason: payload.status?.reason,
        remaining: payload.status?.characters_remaining,
        prepared,
      });
      setVoiceId((current) => current || voices[0]?.voice_id || "");
      setProblem(null);
    }).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setProblem(reason instanceof Error ? reason.message : t("library.voiceUnavailable"));
    });
    return () => controller.abort();
  }, [apiFetch, compatible, open, t, workspaceId]);

  const readyTargets = data?.prepared.filter((target) => target.transcript) ?? [];
  const missing = (data?.prepared.length ?? 0) - readyTargets.length;
  // Composed before counting, the way the API counts and the service bills:
  // decomposed Vietnamese is the same sentence at about a fifth more
  // characters, and a figure that disagrees with the charge is worse than none.
  const characters = readyTargets.reduce(
    (sum, target) => sum + (target.transcript?.text?.trim().normalize("NFC").length ?? 0), 0,
  );
  const allVideo = compatible.every((target) => target.mediaKind === "video");
  const deliveryOptions = [
    { value: "audio" as const, label: t("library.voiceDeliveryAudio") },
    ...(allVideo ? [
      { value: "video" as const, label: t("library.voiceDeliveryVideo") },
      { value: "both" as const, label: t("library.voiceDeliveryBoth") },
    ] : []),
  ];
  const overAllowance = data?.remaining != null && characters > data.remaining;
  const unavailable = data && (!data.configured || !data.reachable);

  async function generate() {
    setQueueing(true);
    setProblem(null);
    const queued: string[] = [];
    const failures: string[] = [];
    // One identity for this run, sent with every request in it. These are
    // queued one per asset, so without being told, the notification list
    // groups them by category, status and title - identical for every job
    // of a kind - and two runs merge into one row.
    const batchId = `voice-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    try {
      for (let at = 0; at < readyTargets.length; at += 4) {
        const results = await Promise.all(readyTargets.slice(at, at + 4).map(async (target) => {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/media/library/assets/${target.id}/voiceover`,
            {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                voice_id: voiceId,
                deliver,
                transcript_id: target.transcript!.id,
                batch: { id: batchId, total: readyTargets.length },
              }),
            },
          );
          const payload = await response.json().catch(() => ({})) as { detail?: string };
          return { target, response, payload };
        }));
        for (const result of results) {
          if (result.response.ok) queued.push(result.target.id);
          else failures.push(
            `${result.target.title}: ${result.payload.detail ?? t("library.actionCouldNotStart")}`,
          );
        }
      }
      if (!queued.length) throw new Error(failures[0] ?? t("library.actionCouldNotStart"));
      const summary = t("library.voiceBatchQueued", { count: queued.length });
      if (failures.length) {
        setProblem(`${t("library.actionBatchFailed", { count: failures.length })} ${failures[0]}`);
      }
      onQueued(summary, queued);
    } catch (reason) {
      setProblem(reason instanceof Error ? reason.message : t("library.actionCouldNotStart"));
    } finally {
      setQueueing(false);
    }
  }

  return (
    <Dialog
      open={open}
      title={t("library.selectionActionVoiceover")}
      description={compatible[0]
        ? t("library.selectionDialogDescription", {
            count: compatible.length,
            title: compatible[0].title,
          })
        : undefined}
      onClose={onClose}
      footer={<>
        <Button variant="quiet" onClick={onClose}>{t("common.close")}</Button>
        <Button
          variant="primary"
          busy={queueing}
          disabled={!canEdit || !voiceId || !readyTargets.length || overAllowance || Boolean(unavailable)}
          onClick={() => void generate()}
        >{t("library.generateVoiceoversFor", { count: readyTargets.length })}</Button>
      </>}
    >
      {!data && !problem && <p className="voice-note">{t("common.loading")}</p>}
      {problem && <p className="voice-note problem" role="alert">{problem}</p>}
      {unavailable && <p className="voice-note problem" role="alert">
        {data.reason ?? t("library.voiceUnavailable")}
      </p>}
      {data && !unavailable && <>
        <p className="voice-note">
          {t("library.voiceBatchTranscriptNote")}{" "}
          {missing ? t("library.voiceBatchMissingTranscript", { count: missing }) : null}
          {skipped ? ` ${t("library.actionSkippedIncompatible", { count: skipped })}` : null}
        </p>
        <div className="voice-field">
          <span>{t("library.voiceChoose")}</span>
          <SearchSelect
            value={voiceId}
            onChange={setVoiceId}
            options={data.voices.map((voice) => ({
              value: voice.voice_id,
              label: voice.name,
              description: voice.category ?? undefined,
            }))}
            placeholder={t("library.voiceChoosePlaceholder")}
            emptyLabel={t("library.voiceNoVoices")}
            searchPlaceholder={t("library.voiceChoosePlaceholder")}
            ariaLabel={t("library.voiceChoose")}
            clearable={false}
          />
        </div>
        <div className="voice-field">
          <span>{t("library.voiceDelivery")}</span>
          <SegmentedControl
            value={deliver}
            options={deliveryOptions}
            onChange={setDeliver}
            label={t("library.voiceDelivery")}
          />
          {!allVideo && <small>{t("library.voiceBatchAudioOnly")}</small>}
        </div>
        <p className={`voice-cost${overAllowance ? " problem" : ""}`}>
          <strong>{data.remaining != null
            ? t("library.voiceAllowance", {
                characters: characters.toLocaleString(),
                remaining: data.remaining.toLocaleString(),
              })
            : characters.toLocaleString()}</strong>
        </p>
      </>}
    </Dialog>
  );
}
