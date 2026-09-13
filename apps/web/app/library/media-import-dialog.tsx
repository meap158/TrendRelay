"use client";

import {
  AlertCircle,
  Check,
  Film,
  Folder,
  Image as ImageIcon,
  Loader2,
  Music,
  Plus,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useId, useRef, useState } from "react";
import type { DragEvent, ChangeEvent } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { useT } from "../i18n-provider";

export type StagedFile = {
  id: string;
  file: File;
  title: string;
  status: "idle" | "uploading" | "queued" | "duplicate" | "error";
  error?: string;
};

function displaySize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileIcon(file: File) {
  if (file.type.startsWith("video/") || /\.(mp4|mov|mkv|webm)$/i.test(file.name)) {
    return <Film size={18} aria-hidden="true" />;
  }
  if (file.type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(file.name)) {
    return <Music size={18} aria-hidden="true" />;
  }
  return <ImageIcon size={18} aria-hidden="true" />;
}

function cleanTitle(filename: string): string {
  const dotIndex = filename.lastIndexOf(".");
  const stem = dotIndex > 0 ? filename.slice(0, dotIndex) : filename;
  return stem.replace(/[-_]+/g, " ").trim();
}

export function MediaImportDialog({
  open,
  onClose,
  workspaceId,
  apiFetch,
  onImportQueued,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onImportQueued?: (message: string) => void;
}) {
  const t = useT();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [activeTab, setActiveTab] = useState<"upload" | "paths">("upload");
  const [isDragging, setIsDragging] = useState(false);
  const [stagedFiles, setStagedFiles] = useState<StagedFile[]>([]);
  const [localPathsText, setLocalPathsText] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);
  const [uploadIndex, setUploadIndex] = useState(0);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const textareaId = useId();

  function resetState() {
    setStagedFiles([]);
    setLocalPathsText("");
    setIsProcessing(false);
    setUploadIndex(0);
    setStatusMessage(null);
    setErrorMessage(null);
  }

  function handleClose() {
    if (isProcessing) return;
    resetState();
    onClose();
  }

  function addFiles(newFiles: FileList | File[]) {
    const validFiles: StagedFile[] = [];
    for (let i = 0; i < newFiles.length; i++) {
      const file = newFiles[i];
      if (!file) continue;
      const id = `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 7)}`;
      validFiles.push({
        id,
        file,
        title: cleanTitle(file.name),
        status: "idle",
      });
    }
    if (validFiles.length > 0) {
      setStagedFiles((prev) => [...prev, ...validFiles]);
      setErrorMessage(null);
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
    if (event.dataTransfer?.files?.length) {
      addFiles(event.dataTransfer.files);
    }
  }

  function handleFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files?.length) {
      addFiles(event.target.files);
    }
    event.target.value = "";
  }

  function removeStagedFile(id: string) {
    if (isProcessing) return;
    setStagedFiles((prev) => prev.filter((item) => item.id !== id));
  }

  function updateStagedTitle(id: string, newTitle: string) {
    setStagedFiles((prev) =>
      prev.map((item) => (item.id === id ? { ...item, title: newTitle } : item)),
    );
  }

  const totalBytes = stagedFiles.reduce((acc, item) => acc + item.file.size, 0);

  async function handleUploadSubmit() {
    if (!stagedFiles.length || isProcessing) return;
    setIsProcessing(true);
    setErrorMessage(null);
    setStatusMessage(null);

    let queuedCount = 0;
    let duplicateCount = 0;
    let errorCount = 0;

    for (let i = 0; i < stagedFiles.length; i++) {
      const item = stagedFiles[i];
      // Skip files already successfully queued or stored
      if (item.status === "queued") {
        queuedCount++;
        continue;
      }
      if (item.status === "duplicate") {
        duplicateCount++;
        continue;
      }

      setUploadIndex(i + 1);
      setStagedFiles((prev) =>
        prev.map((f, idx) => (idx === i ? { ...f, status: "uploading", error: undefined } : f)),
      );

      try {
        const formData = new FormData();
        formData.append("file", item.file);
        formData.append("title", item.title.trim() || item.file.name);
        formData.append("confirm_external_action", "true");

        const res = await apiFetch(`/api/workspaces/${workspaceId}/media/library/imports/upload`, {
          method: "POST",
          body: formData,
        });

        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          let errorMsg = `Upload failed (${res.status})`;
          if (typeof body.detail === "string") {
            errorMsg = body.detail;
          } else if (Array.isArray(body.detail)) {
            errorMsg = body.detail
              .map((d: Record<string, unknown>) => (typeof d === "object" && d ? String(d.msg || d.detail || JSON.stringify(d)) : String(d)))
              .join("; ");
          } else if (body.detail && typeof body.detail === "object") {
            errorMsg = JSON.stringify(body.detail);
          }
          throw new Error(errorMsg);
        }

        const data = await res.json();
        const isDuplicate = Boolean(data.job?.duplicate);
        if (isDuplicate) duplicateCount++;
        else queuedCount++;

        setStagedFiles((prev) =>
          prev.map((f, idx) =>
            idx === i ? { ...f, status: isDuplicate ? "duplicate" : "queued" } : f,
          ),
        );
      } catch (err) {
        errorCount++;
        const msg = err instanceof Error ? err.message : "Upload failed";
        setStagedFiles((prev) =>
          prev.map((f, idx) => (idx === i ? { ...f, status: "error", error: msg } : f)),
        );
      }
    }

    setIsProcessing(false);

    const parts: string[] = [];
    if (queuedCount > 0) parts.push(`${queuedCount} media ${queuedCount === 1 ? "item" : "items"} queued`);
    if (duplicateCount > 0) parts.push(`${duplicateCount} duplicate already stored`);
    if (errorCount > 0) parts.push(`${errorCount} failed`);

    const finalSummary = parts.join(", ");
    if (queuedCount > 0 || duplicateCount > 0) {
      onImportQueued?.(finalSummary);
      if (errorCount === 0) {
        setTimeout(() => handleClose(), 700);
      } else {
        setStatusMessage(finalSummary);
      }
    } else if (errorCount > 0) {
      setErrorMessage(`Failed to import files. ${finalSummary}`);
    }
  }

  async function handlePathsSubmit() {
    const rawLines = localPathsText
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean);

    if (!rawLines.length || isProcessing) return;

    setIsProcessing(true);
    setErrorMessage(null);
    setStatusMessage(null);

    try {
      const folderCandidate = rawLines.length === 1 && !/\.[a-z0-9]+$/i.test(rawLines[0])
        ? rawLines[0]
        : null;

      const res = await apiFetch(`/api/workspaces/${workspaceId}/media/library/imports/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paths: folderCandidate ? [] : rawLines,
          folder_path: folderCandidate,
          confirm_external_action: true,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Batch import failed (${res.status})`);
      }

      const body = await res.json();
      const queued = body.queued_count ?? 0;
      const errors = body.errors ?? [];

      if (queued > 0) {
        const msg = `${queued} media ${queued === 1 ? "item" : "items"} queued for ingestion${
          errors.length ? ` (${errors.length} skipped)` : ""
        }.`;
        onImportQueued?.(msg);
        if (!errors.length) {
          setTimeout(() => handleClose(), 500);
        } else {
          setStatusMessage(msg);
          setErrorMessage(errors.slice(0, 3).join("; "));
        }
      } else {
        setErrorMessage(
          errors.length
            ? `Could not queue media: ${errors.slice(0, 3).join("; ")}`
            : "No supported media files found at the specified path(s).",
        );
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Batch import failed.");
    } finally {
      setIsProcessing(false);
    }
  }

  const pathsCount = localPathsText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean).length;

  return (
    <Dialog
      open={open}
      title={t("library.importMedia")}
      description={t("library.importMediaDescription")}
      size="wide"
      onClose={handleClose}
      footer={
        <div className="library-import-footer">
          <Button
            variant="secondary"
            disabled={isProcessing}
            onClick={handleClose}
          >
            {t("common.cancel")}
          </Button>

          {activeTab === "upload" ? (
            <Button
              variant="primary"
              disabled={isProcessing || stagedFiles.length === 0}
              busy={isProcessing}
              onClick={() => void handleUploadSubmit()}
            >
              <ActionIcon name="add" />
              {isProcessing
                ? `Importing (${uploadIndex}/${stagedFiles.length})…`
                : stagedFiles.some((f) => f.status === "error")
                ? `Retry ${stagedFiles.filter((f) => f.status !== "queued" && f.status !== "duplicate").length} ${stagedFiles.filter((f) => f.status !== "queued" && f.status !== "duplicate").length === 1 ? "file" : "files"}`
                : stagedFiles.length
                ? `Import ${stagedFiles.length} ${stagedFiles.length === 1 ? "file" : "files"}`
                : "Select files to import"}
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={isProcessing || pathsCount === 0}
              busy={isProcessing}
              onClick={() => void handlePathsSubmit()}
            >
              <ActionIcon name="add" />
              {isProcessing
                ? "Queuing paths…"
                : pathsCount
                ? `Import from ${pathsCount} ${pathsCount === 1 ? "path" : "paths"}`
                : "Enter paths to import"}
            </Button>
          )}
        </div>
      }
    >
      <div className="library-import-modal">
        {/* Hidden inputs for file and directory picking */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="video/*,audio/*,image/*,.mp4,.mov,.mkv,.webm,.mp3,.wav,.m4a,.aac,.flac,.ogg,.jpg,.jpeg,.png,.webp"
          style={{ display: "none" }}
          onChange={handleFileInputChange}
        />
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error webkitdirectory is standard in browsers but non-standard TS attribute
          webkitdirectory=""
          directory=""
          style={{ display: "none" }}
          onChange={handleFileInputChange}
        />

        {/* Tab switcher */}
        <div className="library-import-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "upload"}
            className={`library-import-tab ${activeTab === "upload" ? "active" : ""}`}
            onClick={() => setActiveTab("upload")}
          >
            <UploadCloud size={15} aria-hidden="true" />
            <span>Upload files</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "paths"}
            className={`library-import-tab ${activeTab === "paths" ? "active" : ""}`}
            onClick={() => setActiveTab("paths")}
          >
            <Folder size={15} aria-hidden="true" />
            <span>Local file / folder paths</span>
          </button>
        </div>

        {activeTab === "upload" && (
          <div className="library-import-upload-pane">
            {/* Drag and Drop Zone */}
            <div
              className={`library-dropzone ${isDragging ? "dropzone-dragging" : ""}`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
            >
              <div className="library-dropzone-icon">
                <UploadCloud size={32} strokeWidth={1.5} aria-hidden="true" />
              </div>
              <p className="library-dropzone-primary">
                <strong>Drag and drop media here</strong>, or{" "}
                <span className="library-dropzone-browse">browse files</span>
              </p>
              <p className="library-dropzone-sub">
                Supports video (MP4, MOV, WEBM), audio (MP3, WAV, M4A), and images (PNG, JPG, WEBP)
              </p>
              <div className="library-dropzone-folder-action" onClick={(e) => e.stopPropagation()}>
                <Button
                  type="button"
                  variant="quiet"
                  size="sm"
                  onClick={() => folderInputRef.current?.click()}
                >
                  <Folder size={14} aria-hidden="true" />
                  Select a whole folder
                </Button>
              </div>
            </div>

            {/* Staged files list */}
            {stagedFiles.length > 0 && (
              <div className="library-staged-container">
                <header className="library-staged-header">
                  <strong>
                    {stagedFiles.length} {stagedFiles.length === 1 ? "file" : "files"} selected (
                    {displaySize(totalBytes)})
                  </strong>
                  <div className="library-staged-actions">
                    <Button
                      type="button"
                      variant="quiet"
                      size="sm"
                      disabled={isProcessing}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <Plus size={14} aria-hidden="true" />
                      Add more
                    </Button>
                    <Button
                      type="button"
                      variant="quiet"
                      size="sm"
                      disabled={isProcessing}
                      onClick={() => setStagedFiles([])}
                    >
                      Clear all
                    </Button>
                  </div>
                </header>

                {/* Progress bar if active */}
                {isProcessing && (
                  <div className="library-import-progress-bar">
                    <div
                      className="library-import-progress-fill"
                      style={{
                        width: `${Math.round((uploadIndex / stagedFiles.length) * 100)}%`,
                      }}
                    />
                  </div>
                )}

                <ul className="library-staged-list">
                  {stagedFiles.map((item) => (
                    <li key={item.id} className={`library-staged-item status-${item.status}`}>
                      <div className="library-staged-icon">{getFileIcon(item.file)}</div>
                      <div className="library-staged-info">
                        <input
                          type="text"
                          className="library-staged-title-input"
                          value={item.title}
                          disabled={isProcessing}
                          onChange={(e) => updateStagedTitle(item.id, e.target.value)}
                          placeholder={item.file.name}
                          title="Click to edit title"
                        />
                        <div className="library-staged-meta">
                          <span>{item.file.name}</span>
                          <span>·</span>
                          <span>{displaySize(item.file.size)}</span>
                        </div>
                      </div>
                      <div className="library-staged-status">
                        {item.status === "uploading" && (
                          <span className="staged-badge uploading">
                            <Loader2 size={12} className="spin" aria-hidden="true" />
                            Uploading…
                          </span>
                        )}
                        {item.status === "queued" && (
                          <span className="staged-badge success">
                            <Check size={12} aria-hidden="true" />
                            Queued
                          </span>
                        )}
                        {item.status === "duplicate" && (
                          <span className="staged-badge neutral">Stored</span>
                        )}
                        {item.status === "error" && (
                          <span className="staged-badge error" title={item.error}>
                            <AlertCircle size={12} aria-hidden="true" />
                            Failed
                          </span>
                        )}
                        {item.status === "idle" && !isProcessing && (
                          <button
                            type="button"
                            className="library-staged-remove"
                            onClick={() => removeStagedFile(item.id)}
                            aria-label={`Remove ${item.file.name}`}
                          >
                            <Trash2 size={14} aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {activeTab === "paths" && (
          <div className="library-import-paths-pane">
            <label htmlFor={textareaId} className="library-paths-label">
              Local directory or file paths
            </label>
            <textarea
              id={textareaId}
              className="library-paths-textarea"
              rows={6}
              disabled={isProcessing}
              placeholder={`Example directory:\nS:\\Media\\ProductClips\n\nOr individual files (one per line):\n.data/downloads/clip1.mp4\n.data/downloads/clip2.mp4`}
              value={localPathsText}
              onChange={(e) => setLocalPathsText(e.target.value)}
            />
            <p className="library-paths-hint">
              Paths must be on the local filesystem accessible to the server (such as{" "}
              <code>.data/downloads</code> or <code>.data/media</code>). Any directory path will be
              scanned recursively for supported video, audio, and image formats.
            </p>
          </div>
        )}

        {/* Global messages / errors */}
        {statusMessage && (
          <div className="library-import-feedback note" role="status">
            <Check size={14} aria-hidden="true" />
            <span>{statusMessage}</span>
          </div>
        )}
        {errorMessage && (
          <div className="library-import-feedback error" role="alert">
            <AlertCircle size={14} aria-hidden="true" />
            <span>{errorMessage}</span>
          </div>
        )}
      </div>
    </Dialog>
  );
}

function ActionIcon({ name }: { name: "add" }) {
  if (name === "add") return <Plus size={15} strokeWidth={2} aria-hidden="true" />;
  return null;
}
