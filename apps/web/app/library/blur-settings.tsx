"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";
import { useT } from "../i18n-provider";

/**
 * Choose how wide the blur sits, and see it on a real frame before paying for
 * a render.
 *
 * A still is the right unit: the only question here is whether the blur covers
 * the face or half the torso, and one frame answers it in under a second where
 * the whole render takes minutes.
 */
export function BlurSettings({
  open,
  workspaceId,
  path,
  padding,
  onPadding,
  onClose,
  onBlur,
  busy,
  apiFetch,
}: {
  open: boolean;
  workspaceId: string;
  path: string;
  padding: number;
  onPadding: (next: number) => void;
  onClose: () => void;
  onBlur: () => void;
  busy: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  const t = useT();
  const [frame, setFrame] = useState("");
  const [faces, setFaces] = useState<number | null>(null);
  /** Null until a frame has been fetched, so the first load can auto-pick. */
  const [position, setPosition] = useState<number | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [detector, setDetector] = useState<string | null>(null);
  const objectUrl = useRef("");

  const load = useCallback(async (ratio: number, at?: number | null) => {
    if (!workspaceId || !path) return;
    setLoading(true);
    setFailure(null);
    try {
      const params = new URLSearchParams({ path, padding_ratio: String(ratio) });
      // Omitted on the first look so the clip is searched for a frame that
      // actually holds a face; sent once the operator starts choosing.
      if (at !== null && at !== undefined) params.set("at", at.toFixed(4));
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/media/library/face-blur/frame?${params}`,
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "The frame could not be rendered.");
      }
      setFaces(Number(response.headers.get("X-Faces-Found") ?? "0"));
      const landed = Number(response.headers.get("X-Frame-Position") ?? "");
      if (Number.isFinite(landed)) setPosition(landed);
      const clip = Number(response.headers.get("X-Clip-Duration") ?? "");
      setDuration(Number.isFinite(clip) && clip > 0 ? clip : null);
      const blob = await response.blob();
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(blob);
      setFrame(objectUrl.current);
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "The frame could not be rendered.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, path, workspaceId]);

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => {
      setPosition(null);
      void load(padding);
      // Which detector is running decides whether the coverage slider is even
      // the right lever, so it is read alongside the frame.
      void apiFetch(`/api/workspaces/${workspaceId}/media/library/face-blur/status`)
        .then((response) => (response.ok ? response.json() : null))
        .then((body) => setDetector(body?.status?.detector ?? null))
        .catch(() => undefined);
    });
    // Opening renders once; moving the slider re-renders on release, not on
    // every pixel of travel, which would be a request per frame of drag.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, path]);

  useEffect(() => () => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);

  const fallback = detector === "haar-cascade";

  return (
    <Dialog
      open={open}
      title={t("blurSettings.heading")}
      description="Check the coverage on one frame before rendering the clip."
      onClose={onClose}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>{t("common.close")}</Button>
          <Button variant="primary" busy={busy} onClick={onBlur}>{t("blurSettings.blurFaces")}</Button>
        </>
      }
    >
      <div className="blur-settings">
        <div className="blur-frame">
          {frame ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img alt={t("blurSettings.onePreviewFrame")} src={frame} />
          ) : (
            <p>{loading ? "Rendering a frame…" : failure ?? "No frame yet."}</p>
          )}
          {loading && frame && <span className="blur-frame-busy">{t("blurSettings.rendering")}</span>}
        </div>

        <div className="blur-controls">
          <label>
            <span>
              Position
              <b>
                {position === null
                  ? "finding a frame with a face"
                  : duration
                    ? `${(position * duration).toFixed(1)}s of ${duration.toFixed(1)}s`
                    : `${Math.round(position * 100)}% through the clip`}
              </b>
            </span>
            <input
              type="range"
              min={0}
              max={1000}
              step={5}
              value={Math.round((position ?? 0) * 1000)}
              disabled={loading && position === null}
              onChange={(event) => setPosition(Number(event.target.value) / 1000)}
              onPointerUp={() => void load(padding, position)}
              onKeyUp={() => void load(padding, position)}
            />
            <small>
              The opening guess is the first frame holding a face. Drag to check
              a moment you are unsure about — a turn of the head, or the shot
              where someone else walks in.
            </small>
          </label>
          <label>
            <span>
              Coverage
              <b>{Math.round(padding * 100)}% wider than the detected face</b>
            </span>
            <input
              type="range"
              min={0}
              max={40}
              step={2}
              value={Math.round(padding * 100)}
              onChange={(event) => onPadding(Number(event.target.value) / 100)}
              onPointerUp={() => void load(padding, position)}
              onKeyUp={() => void load(padding, position)}
            />
            <small>
              Grown on every side, so this much again is added to the width and the
              height. Enough to cover the edges of a face, no more.
            </small>
          </label>

          {faces !== null && !failure && (
            <p className="blur-faces">
              {faces === 0
                ? "No face found on this frame — the blur below shows nothing."
                : `${faces} face${faces === 1 ? "" : "s"} found on this frame.`}
            </p>
          )}

          {/* Stated here because the preview is where anyone will first see the
              detector getting it wrong, and the coverage slider cannot fix it. */}
          {fallback && (
            <div className="blur-detector-note">
              <Badge tone="warn">{t("blurSettings.fallbackDetector")}</Badge>
              <p>
                The accurate face model is not installed, so TrendRelay is using
                OpenCV&apos;s bundled cascade. It mistakes patterned clothing for faces
                and misses faces at an angle. Install the YuNet model at{" "}
                <code>.data/models/face_detection_yunet.onnx</code> to fix that — the
                coverage setting cannot.
              </p>
            </div>
          )}
        </div>
      </div>
    </Dialog>
  );
}
