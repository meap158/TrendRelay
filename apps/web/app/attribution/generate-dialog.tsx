"use client";

import { useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { useT } from "../i18n-provider";
import { useWorkspace } from "../workspace-provider";
import { AssetThumbnail, type LibraryAsset, MediaPicker } from "../publish/composer";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Select } from "../ui/select";

/** The same ceiling the API stores and a thumbnail read can fetch at once. */
const SUBJECT_LIMIT = 8;

type Kind = "image" | "carousel" | "video";
type Recipe = "bed_flat_lay" | "mannequin_transition" | "mirror_selfie";

type DraftView = {
  id: string;
  prompt: string;
  status: string;
  owed: number;
  linked: boolean;
  card_count: number;
};

/**
 * Queue one reviewed prompt for every product the operator already selected.
 *
 * The wording is the recipe, so one dialog covers a single row or many. Each
 * product still gets its own draft. The textarea shows only the prompt the
 * API returns. TrendRelay does not generate the pixels: a finished file can
 * be submitted into the Library only while one product is open, because each
 * product needs its own file.
 */
export function GenerateDialog({
  open,
  products,
  onClose,
  onChanged,
}: {
  open: boolean;
  products: { id: string; name: string }[];
  onClose: () => void;
  onChanged?: () => void;
}) {
  const t = useT();
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const [kind, setKind] = useState<Kind>("image");
  const [recipe, setRecipe] = useState<Recipe>("bed_flat_lay");
  const [variant, setVariant] = useState<"female" | "male">("female");
  const [backgroundEnabled, setBackgroundEnabled] = useState(false);
  const [backgroundReference, setBackgroundReference] = useState("");
  const [cardCount, setCardCount] = useState(2);
  const [prompt, setPrompt] = useState("");
  const [draft, setDraft] = useState<DraftView | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileEpoch, setFileEpoch] = useState(0);
  /** Library images the operator picked, in the order generation will see them. */
  const [subjects, setSubjects] = useState<LibraryAsset[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);

  const mirror = recipe === "mirror_selfie";
  const backgroundOn = mirror || backgroundEnabled;
  const backgroundReady = !backgroundOn || backgroundReference.trim().startsWith("https://");
  const locked = draft !== null;
  const many = products.length > 1;
  const leadId = products[0]?.id ?? "";
  const shape = `${kind}|${recipe}|${variant}|${backgroundOn}|${cardCount}|${backgroundReady}`;

  useEffect(() => {
    if (draft) return;
    setPrompt("");
  }, [shape, draft]);

  function requestBody(productId: string) {
    return {
      product_id: productId,
      kind,
      recipe,
      variant: mirror ? variant : null,
      background_enabled: backgroundOn,
      background_reference: backgroundOn ? backgroundReference.trim() : null,
      card_count: kind === "carousel" ? cardCount : null,
      ...(subjects.length > 0
        ? { subject_asset_ids: subjects.map((asset) => asset.id) }
        : {}),
    };
  }

  function toggleSubject(asset: LibraryAsset) {
    setSubjects((current) => (
      current.some((item) => item.id === asset.id)
        ? current.filter((item) => item.id !== asset.id)
        : current.length >= SUBJECT_LIMIT
          ? current
          : [...current, asset]
    ));
    setError("");
  }

  useEffect(() => {
    if (!open || products.length === 0 || !workspaceId || draft) return;
    if (!backgroundReady) {
      setPrompt("");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        // The wording does not name the product, so the first selected row
        // is enough to show the shared prompt.
        try {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/attribution/creative-drafts/preview`,
            { method: "POST", body: JSON.stringify(requestBody(leadId)) },
          );
          if (cancelled) return;
          if (!response.ok) {
            setPrompt("");
            setError(await errorDetail(response, t("attribution.generate.requestFailed")));
            return;
          }
          const payload = await response.json() as { draft?: { prompt?: string } };
          setPrompt(typeof payload.draft?.prompt === "string" ? payload.draft.prompt : "");
          setError("");
        } catch (caught) {
          if (cancelled) return;
          setPrompt("");
          setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
        }
      })();
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // requestBody is derived from the same state listed here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    open, leadId, products, workspaceId, draft, kind, recipe, variant,
    backgroundEnabled, backgroundReference, cardCount, backgroundReady, apiFetch, t,
  ]);

  function chooseKind(next: Kind) {
    setKind(next);
    setRecipe(next === "video" ? "mannequin_transition" : "bed_flat_lay");
    setBackgroundEnabled(false);
    setBackgroundReference("");
    setError("");
    setNotice("");
  }

  async function queue() {
    if (products.length === 0 || !workspaceId || !prompt || subjects.length === 0) return;
    setBusy("queue");
    setError("");
    const failures: string[] = [];
    let first: DraftView | null = null;
    let queued = 0;
    try {
      for (const item of products) {
        try {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/attribution/creative-drafts`,
            { method: "POST", body: JSON.stringify(requestBody(item.id)) },
          );
          if (!response.ok) {
            failures.push(`${item.name}: ${await errorDetail(response, t("attribution.generate.requestFailed"))}`);
            continue;
          }
          const payload = await response.json() as { draft: DraftView };
          queued += 1;
          if (!first) first = payload.draft;
        } catch (caught) {
          const detail = caught instanceof Error ? caught.message : t("attribution.generate.requestFailed");
          failures.push(`${item.name}: ${detail}`);
        }
      }
      if (first) {
        setDraft(first);
        setPrompt(first.prompt);
        setNotice(many
          ? t("attribution.generate.queuedMany", { count: queued })
          : t("attribution.generate.queued"));
        onChanged?.();
      }
      if (failures.length > 0) setError(failures.join(" "));
    } finally {
      setBusy("");
    }
  }

  async function submitFile() {
    if (!draft || !file || !workspaceId) return;
    setBusy("file");
    setError("");
    try {
      const mediaBase64 = await fileToBase64(file);
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/creative-drafts/${draft.id}/media`,
        {
          method: "POST",
          body: JSON.stringify({ media_base64: mediaBase64, filename: file.name }),
        },
      );
      if (!response.ok) {
        setError(await errorDetail(response, t("attribution.generate.requestFailed")));
        return;
      }
      const payload = await response.json() as { draft: DraftView; linked?: boolean };
      setDraft(payload.draft);
      setPrompt(payload.draft.prompt);
      setNotice(payload.linked
        ? t("attribution.generate.linked")
        : t("attribution.generate.partial"));
      setFile(null);
      setFileEpoch((current) => current + 1);
      onChanged?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
    } finally {
      setBusy("");
    }
  }

  const omitted = [
    "omitTitle",
    "omitPrice",
    "omitDescription",
    "omitGallery",
    "omitVariations",
  ] as const;

  return (
    <>
    <Dialog
      open={open && products.length > 0}
      title={t("attribution.generate.title")}
      description={many
        ? t("attribution.generate.descriptionMany", { count: products.length })
        : products[0]
          ? t("attribution.generate.description", { name: products[0].name })
          : undefined}
      onClose={() => {
        if (pickerOpen) return;
        onClose();
      }}
      suspendDismiss={pickerOpen}
      footer={(
        <>
          <Button variant="quiet" onClick={onClose}>{t("attribution.generate.close")}</Button>
          {!draft && (
            <Button
              variant="primary"
              busy={busy === "queue"}
              disabled={!prompt || subjects.length === 0 || busy !== ""}
              onClick={() => void queue()}
            >{busy === "queue"
              ? t("attribution.generate.queuing")
              : many
                ? t("attribution.generate.queueMany")
                : t("attribution.generate.queue")}</Button>
          )}
          {draft && !many && draft.owed > 0 && (
            <Button
              variant="primary"
              busy={busy === "file"}
              disabled={!file || busy !== ""}
              onClick={() => void submitFile()}
            >{busy === "file" ? t("attribution.generate.submitting") : t("attribution.generate.submitFile")}</Button>
          )}
        </>
      )}
    >
      <div className="campaign-dialog-form">
        <label>
          {t("attribution.generate.kind")}
          <Select
            aria-label={t("attribution.generate.kind")}
            value={kind}
            disabled={locked}
            onChange={(event) => chooseKind(event.target.value as Kind)}
          >
            <option value="image">{t("attribution.generate.kindImage")}</option>
            <option value="carousel">{t("attribution.generate.kindCarousel")}</option>
            <option value="video">{t("attribution.generate.kindVideo")}</option>
          </Select>
        </label>
        <label>
          {t("attribution.generate.recipe")}
          <Select
            aria-label={t("attribution.generate.recipe")}
            value={recipe}
            disabled={locked || kind !== "video"}
            onChange={(event) => {
              setRecipe(event.target.value as Recipe);
              setError("");
            }}
          >
            {kind === "video" ? (
              <>
                <option value="mannequin_transition">{t("attribution.generate.recipeMannequin")}</option>
                <option value="mirror_selfie">{t("attribution.generate.recipeMirror")}</option>
              </>
            ) : (
              <option value="bed_flat_lay">{t("attribution.generate.recipeBed")}</option>
            )}
          </Select>
        </label>
        {mirror && (
          <label>
            {t("attribution.generate.variant")}
            <Select
              aria-label={t("attribution.generate.variant")}
              value={variant}
              disabled={locked}
              onChange={(event) => setVariant(event.target.value as "female" | "male")}
            >
              <option value="female">{t("attribution.generate.variantFemale")}</option>
              <option value="male">{t("attribution.generate.variantMale")}</option>
            </Select>
          </label>
        )}
        <section className="generate-uses">
          <h3>{t("attribution.generate.uses")}</h3>
          <p>{t("attribution.generate.usesHelp")}</p>
          <div className="generate-use">
            <strong>{t("attribution.generate.subject")}</strong>
            <p>{t("attribution.generate.subjectHelp")}</p>
            {many && <p>{t("attribution.generate.subjectShared")}</p>}
            {subjects.length === 0 ? (
              <p>{t("attribution.generate.subjectEmpty")}</p>
            ) : (
              <ol className="generate-subject-list">
                {subjects.map((asset, index) => (
                  <li key={asset.id}>
                    {workspaceId && (
                      <AssetThumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
                    )}
                    <span>{index + 1}. {asset.title}</span>
                    <Button
                      variant="quiet"
                      size="sm"
                      disabled={locked}
                      onClick={() => setSubjects((current) => current.filter((item) => item.id !== asset.id))}
                    >{t("attribution.generate.removeSubject")}</Button>
                  </li>
                ))}
              </ol>
            )}
            <div className="generate-subject-actions">
              <Button
                variant="secondary"
                size="sm"
                disabled={locked || !workspaceId}
                onClick={() => setPickerOpen(true)}
              >{t("attribution.generate.chooseLibrary")}</Button>
              <small>
                {t("attribution.generate.subjectCount", {
                  count: subjects.length,
                  limit: SUBJECT_LIMIT,
                })}
              </small>
            </div>
          </div>
          <div className="generate-use">
            <strong>{t("attribution.generate.backgroundHeading")}</strong>
            {!mirror && (
              <label className="campaign-dialog-check">
                <input
                  type="checkbox"
                  checked={backgroundEnabled}
                  disabled={locked}
                  onChange={(event) => {
                    setBackgroundEnabled(event.target.checked);
                    if (!event.target.checked) setBackgroundReference("");
                    setError("");
                  }}
                />
                <span>
                  {t("attribution.generate.background")}
                  <small>{t("attribution.generate.backgroundHint")}</small>
                </span>
              </label>
            )}
            {mirror && <p>{t("attribution.generate.backgroundHint")}</p>}
            {backgroundOn && (
              <label>
                {t("attribution.generate.backgroundUrl")}
                <input
                  type="url"
                  value={backgroundReference}
                  disabled={locked}
                  placeholder={t("attribution.generate.backgroundPlaceholder")}
                  onChange={(event) => setBackgroundReference(event.target.value)}
                />
                {!backgroundReady && <small>{t("attribution.generate.backgroundNeeded")}</small>}
                {backgroundReady && backgroundReference.trim().startsWith("https://") && (
                  <small>{t("attribution.generate.backgroundSent")}</small>
                )}
              </label>
            )}
            {!backgroundOn && (
              <p className="generate-background-none">{t("attribution.generate.backgroundNone")}</p>
            )}
          </div>
        </section>
        <div className="generate-omitted">
          <strong>{t("attribution.generate.notUsed")}</strong>
          <p>{t("attribution.generate.notUsedHelp")}</p>
          <ul>
            {omitted.map((key) => (
              <li key={key}>{t(`attribution.generate.${key}`)}</li>
            ))}
          </ul>
        </div>
        {kind === "carousel" && (
          <label>
            {t("attribution.generate.cards")}
            <input
              type="number"
              min={2}
              max={10}
              value={cardCount}
              disabled={locked}
              onChange={(event) => {
                const next = Number(event.target.value);
                if (Number.isFinite(next)) setCardCount(Math.min(10, Math.max(2, next)));
              }}
            />
          </label>
        )}
        <label className="generate-prompt">
          {t("attribution.generate.prompt")}
          <textarea readOnly rows={12} value={prompt} />
          <small>{t("attribution.generate.promptHelp")}</small>
        </label>
        {draft && !many && (
          <p role="status">{t("attribution.generate.owed", { count: draft.owed })}</p>
        )}
        {draft && !many && draft.owed > 0 && (
          <label>
            {t("attribution.generate.file")}
            <input
              key={fileEpoch}
              type="file"
              accept={kind === "video" ? "video/mp4,video/quicktime,video/webm,video/x-matroska" : "image/png,image/jpeg,image/webp"}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
        )}
        {notice && <p role="status">{notice}</p>}
        {error && <p role="alert">{error}</p>}
      </div>
    </Dialog>
    {workspaceId && (
      <MediaPicker
        open={pickerOpen}
        workspaceId={workspaceId}
        apiFetch={apiFetch}
        mediaKind="image"
        capacity={SUBJECT_LIMIT}
        chosen={subjects.map((asset) => asset.id)}
        pathOf={(asset) => asset.id}
        title={t("attribution.generate.pickerTitle")}
        description={t("attribution.generate.pickerHelp")}
        onPick={toggleSubject}
        onClose={() => setPickerOpen(false)}
      />
    )}
    </>
  );
}

async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
    if (Array.isArray(body.detail)) {
      const text = body.detail
        .map((item) => (
          item && typeof item === "object" && "msg" in item ? String(item.msg) : ""
        ))
        .filter(Boolean)
        .join(" ");
      if (text) return text;
    }
  } catch {
    // A non-JSON refusal still needs a sentence.
  }
  return fallback;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      const comma = text.indexOf(",");
      resolve(comma >= 0 ? text.slice(comma + 1) : text);
    };
    reader.onerror = () => reject(reader.error ?? new Error("unreadable file"));
    reader.readAsDataURL(file);
  });
}
