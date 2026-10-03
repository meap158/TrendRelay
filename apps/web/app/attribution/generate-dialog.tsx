"use client";

import { useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { useT } from "../i18n-provider";
import { useWorkspace } from "../workspace-provider";
import { AssetThumbnail, type LibraryAsset, MediaPicker } from "../publish/composer";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Select } from "../ui/select";
import { money } from "./format";

/** The same ceiling the API stores and a thumbnail read can fetch at once. */
const SUBJECT_LIMIT = 8;

/** Listing values an operator can attach. Off until selected. The order matches the API. */
const LISTING_KEYS = ["title", "price", "description", "gallery", "variations"] as const;
type ListingKey = (typeof LISTING_KEYS)[number];

const FIELD_LABEL: Record<ListingKey, string> = {
  title: "omitTitle",
  price: "omitPrice",
  description: "omitDescription",
  gallery: "omitGallery",
  variations: "omitVariations",
};

type ListingSnapshot = Partial<{
  title: string;
  price: { offers?: { price_cents: number; currency: string; merchant?: string | null }[] };
  description: { text?: string; truncated?: boolean };
  gallery: string[];
  variations: {
    tiers?: { name?: string; options?: string[] }[];
    models?: string[];
    stock?: number | null;
  };
}>;

type StoredSubject = { asset_id: string; title: string; missing?: boolean };

type DraftMember = {
  product_id: string;
  name: string;
  listing_fields?: ListingSnapshot;
  product_images?: string[];
};

type Kind = "image" | "carousel" | "video";
type Recipe = "bed_flat_lay" | "mannequin_transition" | "mirror_selfie";

type DraftView = {
  id: string;
  prompt: string;
  status: string;
  owed: number;
  linked: boolean;
  card_count: number;
  kind?: Kind;
  recipe?: Recipe;
  variant?: "female" | "male" | null;
  background_enabled?: boolean;
  background_reference?: string | null;
  subject_assets?: StoredSubject[];
  listing_fields?: ListingSnapshot;
  together?: boolean;
  product_count?: number;
  products?: DraftMember[];
};

/**
 * Queue a reviewed prompt for the products the operator already selected.
 *
 * Each product is its own draft. Together, when two or more are selected, is
 * one draft of all of them, and the finished file links to every product.
 * The textarea shows only the prompt the API returns. TrendRelay does not
 * generate the pixels. A file is submitted for that one draft, or while a
 * single product is open.
 */
export function GenerateDialog({
  open,
  products,
  draftId = null,
  onClose,
  onChanged,
}: {
  open: boolean;
  products: { id: string; name: string }[];
  /** An existing draft. The dialog then shows that draft's stored configuration. */
  draftId?: string | null;
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
  const [storedSubjects, setStoredSubjects] = useState<StoredSubject[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedFields, setSelectedFields] = useState<Set<ListingKey>>(() => new Set());
  const [listingSnapshot, setListingSnapshot] = useState<ListingSnapshot>({});
  const [scope, setScope] = useState<"each" | "together">("each");
  const [members, setMembers] = useState<DraftMember[]>([]);

  const reviewing = Boolean(draftId);
  const mirror = recipe === "mirror_selfie";
  const backgroundOn = mirror || backgroundEnabled;
  const backgroundReady = !backgroundOn || backgroundReference.trim().startsWith("https://");
  const locked = draft !== null || reviewing;
  const many = products.length > 1;
  const together = !locked && many && scope === "together" && products.length <= SUBJECT_LIMIT;
  const groupShot = together || (draft?.product_count ?? 0) > 1;
  const leadId = products[0]?.id ?? "";
  const fieldsKey = LISTING_KEYS.filter((key) => selectedFields.has(key)).join(",");
  const shape = `${kind}|${recipe}|${variant}|${backgroundOn}|${cardCount}|${backgroundReady}|${together ? "together" : "each"}`;

  useEffect(() => {
    if (draft) return;
    setPrompt("");
    setMembers([]);
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
      listing_fields: LISTING_KEYS.filter((key) => selectedFields.has(key)),
      ...(together
        ? { together: true, product_ids: products.map((item) => item.id) }
        : {}),
    };
  }

  function toggleField(key: ListingKey) {
    setSelectedFields((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setError("");
  }

  function applyStored(stored: DraftView) {
    const nextKind = stored.kind ?? "image";
    setKind(nextKind);
    setRecipe(stored.recipe ?? (nextKind === "video" ? "mannequin_transition" : "bed_flat_lay"));
    setVariant(stored.variant === "male" ? "male" : "female");
    setBackgroundEnabled(Boolean(stored.background_enabled));
    setBackgroundReference(stored.background_reference ?? "");
    setCardCount(stored.card_count > 1 ? stored.card_count : 2);
    setPrompt(stored.prompt);
    setStoredSubjects(stored.subject_assets ?? []);
    const fields = stored.listing_fields ?? {};
    setSelectedFields(new Set(LISTING_KEYS.filter((key) => key in fields)));
    setListingSnapshot(fields);
    setMembers(stored.products ?? []);
    setDraft(stored);
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
    if (!open || !reviewing || !draftId || !workspaceId) return;
    let cancelled = false;
    setBusy("load");
    setError("");
    void (async () => {
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/attribution/creative-drafts/${draftId}`,
        );
        if (cancelled) return;
        if (!response.ok) {
          setError(await errorDetail(response, t("attribution.generate.requestFailed")));
          return;
        }
        const payload = await response.json() as { draft?: DraftView };
        if (!payload.draft?.prompt) {
          setError(t("attribution.generate.requestFailed"));
          return;
        }
        applyStored(payload.draft);
      } catch (caught) {
        if (cancelled) return;
        setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
      } finally {
        if (!cancelled) setBusy("");
      }
    })();
    return () => {
      cancelled = true;
    };
    // applyStored reads the setters from this render. The draft id is the load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, reviewing, draftId, workspaceId, apiFetch, t]);

  useEffect(() => {
    if (!open || reviewing || products.length === 0 || !workspaceId || draft) return;
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
            setListingSnapshot({});
            setMembers([]);
            setError(await errorDetail(response, t("attribution.generate.requestFailed")));
            return;
          }
          const payload = await response.json() as {
            draft?: { prompt?: string; listing_fields?: ListingSnapshot; products?: DraftMember[] };
          };
          setPrompt(typeof payload.draft?.prompt === "string" ? payload.draft.prompt : "");
          setListingSnapshot(payload.draft?.listing_fields ?? {});
          setMembers(together && Array.isArray(payload.draft?.products) ? payload.draft.products : []);
          setError("");
        } catch (caught) {
          if (cancelled) return;
          setPrompt("");
          setListingSnapshot({});
          setMembers([]);
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
    open, reviewing, leadId, products, workspaceId, draft, kind, recipe, variant,
    backgroundEnabled, backgroundReference, cardCount, backgroundReady, fieldsKey,
    together, apiFetch, t,
  ]);

  function chooseKind(next: Kind) {
    setKind(next);
    setRecipe(next === "video" ? "mannequin_transition" : "bed_flat_lay");
    setBackgroundEnabled(false);
    setBackgroundReference("");
    setError("");
    setNotice("");
  }

  const chosenFields = LISTING_KEYS.filter((key) => selectedFields.has(key));
  const oneCreative = !many || (draft?.product_count ?? 0) > 1;
  const queueNeedsLibrary = !together;
  const attachedLibrary = reviewing ? storedSubjects.length : subjects.length;
  const togetherUncovered = together
    && attachedLibrary === 0
    && members.some((member) => !(member.product_images ?? []).some((url) => Boolean(url)));

  async function queue() {
    if (products.length === 0 || !workspaceId || !prompt) return;
    if (together ? products.length > SUBJECT_LIMIT || togetherUncovered : subjects.length === 0) return;
    setBusy("queue");
    setError("");
    if (together) {
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/attribution/creative-drafts`,
          { method: "POST", body: JSON.stringify(requestBody(leadId)) },
        );
        if (!response.ok) {
          setError(await errorDetail(response, t("attribution.generate.requestFailed")));
          return;
        }
        const payload = await response.json() as { draft: DraftView };
        setDraft(payload.draft);
        setPrompt(payload.draft.prompt);
        setMembers(payload.draft.products ?? []);
        if (payload.draft.listing_fields) setListingSnapshot(payload.draft.listing_fields);
        setNotice(t("attribution.generate.queuedTogether"));
        onChanged?.();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
      } finally {
        setBusy("");
      }
      return;
    }
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
        if (first.listing_fields) setListingSnapshot(first.listing_fields);
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
        ? (payload.draft.product_count ?? 1) > 1
          ? t("attribution.generate.linkedAll")
          : t("attribution.generate.linked")
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

  return (
    <>
    <Dialog
      open={open && products.length > 0}
      title={reviewing ? t("attribution.generate.reviewTitle") : t("attribution.generate.title")}
      description={reviewing
        ? products[0]
          ? t("attribution.generate.reviewDescription", { name: products[0].name })
          : undefined
        : together
          ? t("attribution.generate.descriptionTogether", { count: products.length })
          : many
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
          {!draft && !reviewing && (
            <Button
              variant="primary"
              busy={busy === "queue"}
              disabled={!prompt || busy !== "" || togetherUncovered || (queueNeedsLibrary && subjects.length === 0)}
              onClick={() => void queue()}
            >{busy === "queue"
              ? t("attribution.generate.queuing")
              : many && !together
                ? t("attribution.generate.queueMany")
                : t("attribution.generate.queue")}</Button>
          )}
          {draft && oneCreative && draft.owed > 0 && (
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
        {reviewing && !draft ? (
          <p role="status">{error || t("attribution.generate.loadingDraft")}</p>
        ) : (
        <>
        {many && !locked && (
          <div className="generate-scope">
            <strong>{t("attribution.generate.scope")}</strong>
            <div
              className="product-listing-filter"
              role="group"
              aria-label={t("attribution.generate.scope")}
            >
              <button
                type="button"
                aria-pressed={scope === "each"}
                className={scope === "each" ? "selected" : undefined}
                onClick={() => {
                  setScope("each");
                  setError("");
                }}
              >{t("attribution.generate.scopeEach")}</button>
              <button
                type="button"
                aria-pressed={scope === "together"}
                className={scope === "together" ? "selected" : undefined}
                disabled={products.length > SUBJECT_LIMIT}
                onClick={() => {
                  setScope("together");
                  setError("");
                }}
              >{t("attribution.generate.scopeTogether")}</button>
            </div>
            <p>{products.length > SUBJECT_LIMIT
              ? t("attribution.generate.scopeLimit")
              : t("attribution.generate.scopeHelp")}</p>
          </div>
        )}
        {groupShot && members.length > 1 && (
          <div className="generate-scope">
            <strong>{t("attribution.generate.members")}</strong>
            <ul className="generate-member-list">
              {members.map((member) => {
                const picture = (member.product_images ?? []).find((url) => Boolean(url)) ?? "";
                const uncovered = attachedLibrary === 0 && !picture;
                return (
                  <li key={member.product_id}>
                    {picture
                      ? <img src={picture} alt="" />
                      : <span className="generate-member-missing" aria-hidden="true" />}
                    <span className="generate-member-name" title={member.name}>{member.name}</span>
                    {uncovered && <small>{t("attribution.generate.noListingPicture")}</small>}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
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
            {together && <p>{t("attribution.generate.subjectTogether")}</p>}
            {many && !together && <p>{t("attribution.generate.subjectShared")}</p>}
            {!together && (draft?.product_count ?? 0) > 1 && (
              <p>{t("attribution.generate.subjectTogether")}</p>
            )}
            {reviewing ? (
              storedSubjects.length === 0 ? (
                <p>{t("attribution.generate.subjectReviewEmpty")}</p>
              ) : (
                <ol className="generate-subject-list">
                  {storedSubjects.map((asset, index) => (
                    <li key={asset.asset_id}>
                      <span>
                        {index + 1}. {asset.title || asset.asset_id}
                        {asset.missing ? ` — ${t("attribution.generate.subjectMissing")}` : ""}
                      </span>
                    </li>
                  ))}
                </ol>
              )
            ) : subjects.length === 0 ? (
              together ? null : <p>{t("attribution.generate.subjectEmpty")}</p>
            ) : (
              <ol className="generate-subject-list">
                {subjects.map((asset, index) => (
                  <li key={asset.id}>
                    {workspaceId && (
                      <AssetThumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
                    )}
                    <span>{index + 1}. {asset.title}</span>
                    {!locked && (
                      <Button
                        variant="quiet"
                        size="sm"
                        onClick={() => setSubjects((current) => current.filter((item) => item.id !== asset.id))}
                      >{t("attribution.generate.removeSubject")}</Button>
                    )}
                  </li>
                ))}
              </ol>
            )}
            {!locked && (
              <div className="generate-subject-actions">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!workspaceId}
                  onClick={() => setPickerOpen(true)}
                >{t("attribution.generate.chooseLibrary")}</Button>
                <small>
                  {t("attribution.generate.subjectCount", {
                    count: subjects.length,
                    limit: SUBJECT_LIMIT,
                  })}
                </small>
              </div>
            )}
            {reviewing && (
              <small>
                {t("attribution.generate.subjectCount", {
                  count: storedSubjects.length,
                  limit: SUBJECT_LIMIT,
                })}
              </small>
            )}
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
        <div className="generate-omitted" role="group" aria-label={t("attribution.generate.listingFields")}>
          <strong>{t("attribution.generate.listingFields")}</strong>
          <p>{reviewing
            ? t("attribution.generate.listingFieldsReview")
            : t("attribution.generate.listingFieldsHelp")}</p>
          <ul>
            {LISTING_KEYS.map((key) => (
              <li key={key}>
                <button
                  type="button"
                  aria-pressed={selectedFields.has(key)}
                  disabled={locked}
                  onClick={() => toggleField(key)}
                >{t(`attribution.generate.${FIELD_LABEL[key]}`)}</button>
              </li>
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
          <small>{reviewing || draft
            ? t("attribution.generate.promptStored")
            : t("attribution.generate.promptHelp")}</small>
        </label>
        {chosenFields.length > 0 && (
          <section className="generate-attached" aria-label={t("attribution.generate.listingFields")}>
            {groupShot && members.length > 1 ? (
              members.map((member) => (
                <div className="generate-member" key={member.product_id}>
                  <strong>{member.name}</strong>
                  {chosenFields.map((key) => (
                    <div className="generate-use" key={key}>
                      <strong>{t(`attribution.generate.${FIELD_LABEL[key]}`)}</strong>
                      <ListingFieldValue
                        field={key}
                        value={member.listing_fields?.[key]}
                        empty={t("attribution.generate.fieldEmpty")}
                        truncated={t("attribution.generate.descriptionTruncated")}
                        galleryCount={(count) => t("attribution.generate.galleryCount", { count })}
                        stockLine={(count) => t("attribution.generate.stockLine", { count })}
                        stockUnknown={t("attribution.generate.stockUnknown")}
                      />
                    </div>
                  ))}
                </div>
              ))
            ) : (
              <>
                {many && !reviewing && products[0] && (
                  <p>{t("attribution.generate.listingShared", { name: products[0].name })}</p>
                )}
                {chosenFields.map((key) => (
                  <div className="generate-use" key={key}>
                    <strong>{t(`attribution.generate.${FIELD_LABEL[key]}`)}</strong>
                    <ListingFieldValue
                      field={key}
                      value={listingSnapshot[key]}
                      empty={t("attribution.generate.fieldEmpty")}
                      truncated={t("attribution.generate.descriptionTruncated")}
                      galleryCount={(count) => t("attribution.generate.galleryCount", { count })}
                      stockLine={(count) => t("attribution.generate.stockLine", { count })}
                      stockUnknown={t("attribution.generate.stockUnknown")}
                    />
                  </div>
                ))}
              </>
            )}
          </section>
        )}
        {draft && oneCreative && (
          <p role="status">
            {draft.linked
              ? t("attribution.generate.statusSucceeded")
              : t("attribution.generate.statusPending")}
            {" · "}
            {t("attribution.generate.owed", { count: draft.owed })}
          </p>
        )}
        {draft && oneCreative && draft.owed > 0 && (
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
        {error && !reviewing && <p role="alert">{error}</p>}
        {error && reviewing && draft && <p role="alert">{error}</p>}
        </>
        )}
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

function ListingFieldValue({
  field,
  value,
  empty,
  truncated,
  galleryCount,
  stockLine,
  stockUnknown,
}: {
  field: ListingKey;
  value: ListingSnapshot[ListingKey];
  empty: string;
  truncated: string;
  galleryCount: (count: number) => string;
  stockLine: (count: number) => string;
  stockUnknown: string;
}) {
  if (value === undefined) return null;
  if (field === "title") {
    const text = typeof value === "string" ? value.trim() : "";
    return <p className="generate-field-value">{text || empty}</p>;
  }
  if (field === "price") {
    const offers = value && typeof value === "object" && "offers" in value ? value.offers ?? [] : [];
    if (offers.length === 0) return <p className="generate-field-value">{empty}</p>;
    return (
      <ul className="generate-offer-list">
        {offers.map((offer, index) => (
          <li key={`${offer.currency}-${index}`}>
            {formatOffer(offer)}
            {offer.merchant ? ` · ${offer.merchant}` : ""}
          </li>
        ))}
      </ul>
    );
  }
  if (field === "description") {
    const text = value && typeof value === "object" && "text" in value ? (value.text ?? "").trim() : "";
    const wasCut = Boolean(value && typeof value === "object" && "truncated" in value && value.truncated);
    if (!text) return <p className="generate-field-value">{empty}</p>;
    return (
      <>
        <p className="generate-field-value generate-field-scroll">{text}</p>
        {wasCut && <small>{truncated}</small>}
      </>
    );
  }
  if (field === "gallery") {
    const urls = Array.isArray(value) ? value.filter((item) => typeof item === "string" && item) : [];
    if (urls.length === 0) return <p className="generate-field-value">{empty}</p>;
    return (
      <>
        <ol className="generate-gallery">
          {urls.slice(0, 8).map((url) => (
            <li key={url}>
              {/* eslint-disable-next-line @next/next/no-img-element -- listing CDN */}
              <img src={url} alt="" />
            </li>
          ))}
        </ol>
        <small>{galleryCount(urls.length)}</small>
      </>
    );
  }
  const variations = value && typeof value === "object" && "tiers" in value ? value : null;
  const tiers = variations?.tiers ?? [];
  const models = variations?.models ?? [];
  const stock = variations?.stock;
  if (tiers.length === 0 && models.length === 0 && (stock === null || stock === undefined)) {
    return <p className="generate-field-value">{empty}</p>;
  }
  return (
    <div className="generate-field-value">
      {tiers.map((tier) => (
        <p key={tier.name || "tier"}>{tier.name}: {(tier.options ?? []).join(", ")}</p>
      ))}
      {models.length > 0 && <p>{models.join(", ")}</p>}
      {typeof stock === "number" ? <p>{stockLine(stock)}</p> : <p>{stockUnknown}</p>}
    </div>
  );
}

function formatOffer(offer: { price_cents: number; currency: string }): string {
  try {
    return money(offer.price_cents, offer.currency);
  } catch {
    return `${offer.price_cents} ${offer.currency}`;
  }
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
