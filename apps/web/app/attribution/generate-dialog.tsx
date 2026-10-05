"use client";

import { useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { useT } from "../i18n-provider";
import { useWorkspace } from "../workspace-provider";
import { AssetThumbnail, type LibraryAsset, MediaPicker } from "../publish/composer";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Select } from "../ui/select";
import { money } from "./format";
import {
  DEFAULT_LISTING_FIELDS,
  LISTING_FIELD_KEYS,
  readListingFields,
  writeListingFields,
  type ListingFieldKey,
} from "./listing-fields";

/** The same ceiling the API stores and a thumbnail read can fetch at once. */
const SUBJECT_LIMIT = 8;

/** Previews in flight at once when each product is asked about separately. */
const PREVIEW_CONCURRENCY = 4;

/** The intake ceilings the API applies to a submitted file, in megabytes. */
const FILE_LIMIT_MB = { image: 25, video: 512 } as const;

/** A background has to be a full https address with a host. */
function isHttpsUrl(text: string): boolean {
  try {
    const parsed = new URL(text.trim());
    return parsed.protocol === "https:" && parsed.hostname !== "";
  } catch {
    return false;
  }
}

/** Run at most `limit` calls at once, keeping the input order. */
async function mapLimit<T, R>(items: T[], limit: number, call: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await call(items[index]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

/** Chip labels. Title, description, and listing pictures start on. */
const FIELD_LABEL: Record<ListingFieldKey, string> = {
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

function pictureUrls(member: DraftMember): string[] {
  return (member.product_images ?? []).filter((url) => Boolean(url));
}

/** Listing files are full size. The checkbox still stores the original address. */
function picturePreview(url: string): string {
  try {
    const parsed = new URL(url);
    if (!parsed.hostname.endsWith("susercontent.com")) return url;
    if (!parsed.pathname.startsWith("/file/")) return url;
    if (parsed.pathname.includes("@")) return url;
    parsed.pathname += "@resize_w96_nl";
    return parsed.toString();
  } catch {
    return url;
  }
}

/** A checkbox that can also sit between on and off. */
function TriCheckbox({
  checked,
  indeterminate,
  label,
  title,
  labelClassName,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  label: string;
  title?: string;
  labelClassName?: string;
  onChange: (checked: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <label className="campaign-dialog-check generate-picture-all">
      <input
        ref={ref}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className={labelClassName} title={title}>{label}</span>
    </label>
  );
}

type Kind = "image" | "carousel" | "video";

type ExistingDraft = {
  id: string;
  kind: string;
  recipe: string;
  status: string;
  owed: number;
  product_count?: number;
  group_number?: number | null;
};
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
  /** A group shot's number, the same one the table shows. */
  group_number?: number | null;
  products?: DraftMember[];
};

/**
 * Queue a reviewed prompt for the products the operator already selected.
 *
 * Each product is its own draft. Together, when two or more are selected, is
 * one draft of all of them, and the finished file links to every product.
 * What will be sent starts collapsed: the prompt the API returns, then any
 * pictures and listing fields. When more than one product is in the ask,
 * those stay under the product they belong to. A file is submitted
 * for that one draft, or while a single product is open. A video draft that
 * still owes a file can also be sent to a ready video provider.
 */
export function GenerateDialog({
  open,
  products,
  draftId = null,
  onClose,
  onChanged,
  onOpenDraft,
}: {
  open: boolean;
  products: {
    id: string;
    name: string;
    /** What each product already has, so a duplicate can be named before it is queued. */
    creative_drafts?: ExistingDraft[] | null;
  }[];
  /** An existing draft. The dialog then shows that draft's stored configuration. */
  draftId?: string | null;
  onClose: () => void;
  onChanged?: () => void;
  /** Open an existing draft in place of queuing its twin. */
  onOpenDraft?: (productId: string, draftId: string) => void;
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
  /** Providers whose check has passed. The list comes from the registry. */
  const [providers, setProviders] = useState<{ id: string; label: string }[]>([]);
  /** A generation the dialog is waiting on. Empty when it is not. */
  const [generation, setGeneration] = useState("");
  const [retrying, setRetrying] = useState(false);
  /** Library images the operator picked, in the order generation will see them. */
  const [subjects, setSubjects] = useState<LibraryAsset[]>([]);
  const [storedSubjects, setStoredSubjects] = useState<StoredSubject[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedFields, setSelectedFields] = useState<Set<ListingFieldKey>>(() => {
    // A review shows that draft. A new ask starts from the remembered chips.
    if (draftId) return new Set();
    if (typeof window !== "undefined" && workspaceId) {
      return new Set(readListingFields(window.localStorage, workspaceId));
    }
    return new Set(DEFAULT_LISTING_FIELDS);
  });
  const [listingSnapshot, setListingSnapshot] = useState<ListingSnapshot>({});
  const [scope, setScope] = useState<"each" | "together">("each");
  const [members, setMembers] = useState<DraftMember[]>([]);
  /** Listing pictures the operator turned off, keyed by product. Absent means all on. */
  const [skipped, setSkipped] = useState<Record<string, string[]>>({});
  /** Products whose draft was refused in the last each-product queue. */
  const [failedIds, setFailedIds] = useState<string[]>([]);
  const [queuedCount, setQueuedCount] = useState(0);
  // The page passes a fresh callback on every render. Holding it here keeps
  // the generation poll from restarting, and asking again, each time.
  const onChangedRef = useRef(onChanged);
  useEffect(() => {
    onChangedRef.current = onChanged;
  }, [onChanged]);

  const reviewing = Boolean(draftId);
  const mirror = recipe === "mirror_selfie";
  const backgroundOn = mirror || backgroundEnabled;
  const backgroundValid = isHttpsUrl(backgroundReference);
  const backgroundReady = !backgroundOn || backgroundValid;
  const locked = draft !== null || reviewing;
  const many = products.length > 1;
  const together = !locked && many && scope === "together" && products.length <= SUBJECT_LIMIT;
  const groupShot = together || (draft?.product_count ?? 0) > 1;
  const leadId = products[0]?.id ?? "";
  const fieldsKey = LISTING_FIELD_KEYS.filter((key) => selectedFields.has(key)).join(",");
  const shape = `${kind}|${recipe}|${variant}|${backgroundOn}|${cardCount}|${backgroundReady}|${together ? "together" : "each"}`;

  useEffect(() => {
    if (draft) return;
    setPrompt("");
    setMembers([]);
  }, [shape, draft]);

  function imageOn(productId: string, url: string) {
    if (!selectedFields.has("gallery")) return true;
    return !(skipped[productId] ?? []).includes(url);
  }

  function pictureSelection(onlyProductId?: string) {
    if (!selectedFields.has("gallery")) return {};
    const included = members.flatMap((member) => {
      if (onlyProductId && member.product_id !== onlyProductId) return [];
      const urls = pictureUrls(member);
      const off = new Set(skipped[member.product_id] ?? []);
      if (!urls.some((url) => off.has(url))) return [];
      return [{
        product_id: member.product_id,
        urls: urls.filter((url) => !off.has(url)),
      }];
    });
    return included.length > 0 ? { included_images: included } : {};
  }

  function requestBody(productId: string, withSelection = false) {
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
      listing_fields: LISTING_FIELD_KEYS.filter((key) => selectedFields.has(key)),
      ...(withSelection ? pictureSelection(together ? undefined : productId) : {}),
      ...(together
        ? { together: true, product_ids: products.map((item) => item.id) }
        : {}),
    };
  }

  function setAllPictures(on: boolean) {
    setError("");
    if (on) {
      setSkipped({});
      return;
    }
    const next: Record<string, string[]> = {};
    for (const member of members) {
      const urls = pictureUrls(member);
      if (urls.length > 0) next[member.product_id] = urls;
    }
    setSkipped(next);
  }

  function setProductPictures(productId: string, on: boolean) {
    setError("");
    setSkipped((current) => {
      const next = { ...current };
      if (on) delete next[productId];
      else {
        const member = members.find((item) => item.product_id === productId);
        next[productId] = pictureUrls(member ?? { product_id: productId, name: "" });
      }
      return next;
    });
  }

  function togglePicture(productId: string, url: string) {
    setError("");
    setSkipped((current) => {
      const member = members.find((item) => item.product_id === productId);
      const urls = pictureUrls(member ?? { product_id: productId, name: "" });
      const off = new Set(current[productId] ?? []);
      if (off.has(url)) off.delete(url);
      else off.add(url);
      const next = { ...current };
      const skippedUrls = urls.filter((item) => off.has(item));
      if (skippedUrls.length === 0) delete next[productId];
      else next[productId] = skippedUrls;
      return next;
    });
  }

  function toggleField(key: ListingFieldKey) {
    if (locked) return;
    const next = new Set(selectedFields);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelectedFields(next);
    setError("");
    if (!workspaceId) return;
    try {
      writeListingFields(window.localStorage, workspaceId, next);
    } catch {
      // A private window can refuse storage. The choice still applies now.
    }
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
    setSelectedFields(new Set(LISTING_FIELD_KEYS.filter((key) => key in fields)));
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
        // Together is one preview of every product. Each is one preview per
        // product: a preview without together returns only the named product.
        // Picture choices stay off the preview so an unchecked picture can
        // be turned back on.
        try {
          const asks = together ? [leadId] : products.map((item) => item.id);
          // A large selection would otherwise send every preview at once.
          const responses = await mapLimit(asks, PREVIEW_CONCURRENCY, async (productId) => {
            if (cancelled) throw new Error("cancelled");
            const response = await apiFetch(
              `/api/workspaces/${workspaceId}/attribution/creative-drafts/preview`,
              {
                method: "POST",
                body: JSON.stringify(requestBody(together ? leadId : productId)),
              },
            );
            if (!response.ok) {
              throw new Error(await errorDetail(response, t("attribution.generate.requestFailed")));
            }
            return response.json() as Promise<{
              draft?: { prompt?: string; listing_fields?: ListingSnapshot; products?: DraftMember[] };
            }>;
          });
          if (cancelled) return;
          const lead = responses[0]?.draft;
          setPrompt(typeof lead?.prompt === "string" ? lead.prompt : "");
          setListingSnapshot(lead?.listing_fields ?? {});
          setMembers(responses.flatMap((payload) => (
            Array.isArray(payload.draft?.products) ? payload.draft.products : []
          )));
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

  const chosenFields = LISTING_FIELD_KEYS.filter((key) => selectedFields.has(key));
  const oneCreative = !many || (draft?.product_count ?? 0) > 1;
  const videoOpen = Boolean(
    draft && draft.kind === "video" && draft.owed > 0 && oneCreative,
  );

  useEffect(() => {
    if (!workspaceId || !videoOpen || !draft) {
      setProviders([]);
      return;
    }
    let cancelled = false;
    void apiFetch(`/api/workspaces/${workspaceId}/attribution/video-providers`)
      .then(async (response) => {
        if (!response.ok) return { providers: [] as { id: string; label: string }[] };
        return response.json() as Promise<{ providers?: { id: string; label: string }[] }>;
      })
      .then((payload) => {
        if (!cancelled) setProviders(payload.providers ?? []);
      })
      .catch(() => {
        if (!cancelled) setProviders([]);
      });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, draft, videoOpen, workspaceId]);

  // A generation keeps running after the dialog closes. Opening the draft
  // again picks it up, so its progress shows and nothing offers a second run.
  const openDraftId = videoOpen && draft ? draft.id : "";
  useEffect(() => {
    if (!workspaceId || !openDraftId) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/attribution/creative-drafts/${openDraftId}/generation`,
        );
        if (!response.ok || cancelled) return;
        const payload = await response.json() as { generation?: { id?: string; status?: string } };
        const running = payload.generation;
        if (running?.id && (running.status === "queued" || running.status === "running")) {
          setGeneration((current) => current || running.id || "");
          setNotice(t("attribution.generate.generationRunning"));
        }
      } catch {
        // Without the answer the buttons stay; the API still refuses a second run.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiFetch, openDraftId, t, workspaceId]);

  // A provider is sent one Library image. Without one it refuses, and a shot
  // of several products would come back showing none of their own pictures.
  const providerSubject = (draft?.subject_assets ?? []).find((asset) => !asset.missing) ?? null;
  const providerBlocked = !videoOpen
    ? ""
    : (draft?.product_count ?? 1) > 1
      ? t("attribution.generate.providerTogether")
      : providerSubject === null
        ? t("attribution.generate.providerNeedsSubject")
        : "";
  const queueNeedsLibrary = !together;
  const attachedLibrary = reviewing ? storedSubjects.length : subjects.length;
  const pictureRows = members.flatMap((member) => (
    pictureUrls(member).map((url) => ({ productId: member.product_id, url }))
  ));
  const skippedPictures = pictureRows.filter(
    (row) => (skipped[row.productId] ?? []).includes(row.url),
  ).length;
  const allPicturesOn = pictureRows.length > 0 && skippedPictures === 0;
  const everyPictureOff = pictureRows.length > 0 && skippedPictures === pictureRows.length;
  const uncoveredNames = together && attachedLibrary === 0
    ? members
      .filter((member) => !pictureUrls(member).some((url) => imageOn(member.product_id, url)))
      .map((member) => member.name || member.product_id)
    : [];
  const togetherUncovered = uncoveredNames.length > 0;
  // Why Queue is off, said beside it. The picture list that would explain it
  // is hidden while Listing pictures is off.
  const queueBlocker = draft || reviewing || !prompt
    ? ""
    : queueNeedsLibrary && subjects.length === 0
      ? t("attribution.generate.blockedPick")
      : togetherUncovered
        ? t("attribution.generate.blockedPictures", { names: uncoveredNames.join(", ") })
        : "";

  // What is about to be queued may already exist. A group shot of exactly
  // these products, or the same recipe already pending on some of them, is
  // named here with the way to it - queuing a twin is the mistake, and a
  // second draft of the same thing is never what the operator went looking
  // for. Said, not blocked: a deliberate second take stays possible.
  const sameGroup = !locked && together
    ? (products[0]?.creative_drafts ?? []).find((existing) => (
      (existing.product_count ?? 0) === products.length
      && existing.kind === kind
      && existing.recipe === recipe
      && products.every((item) => (item.creative_drafts ?? []).some((other) => other.id === existing.id))
    )) ?? null
    : null;
  const sameGroupPending = sameGroup !== null
    && (sameGroup.status !== "succeeded" || sameGroup.owed > 0);
  const sameGroupName = sameGroup?.group_number
    ? t("attribution.generate.groupLabel", { number: sameGroup.group_number })
    : t("attribution.generate.draftGroupTogether");
  const singlesPending = !locked && !together
    ? products.filter((item) => (item.creative_drafts ?? []).some((existing) => (
      (existing.product_count ?? 1) <= 1
      && existing.kind === kind
      && existing.recipe === recipe
      && (existing.status !== "succeeded" || existing.owed > 0)
    ))).length
    : 0;

  async function queue() {
    if (products.length === 0 || !workspaceId || !prompt) return;
    if (together ? products.length > SUBJECT_LIMIT || togetherUncovered : subjects.length === 0) return;
    setBusy("queue");
    setError("");
    if (together) {
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/attribution/creative-drafts`,
          { method: "POST", body: JSON.stringify(requestBody(leadId, true)) },
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
        onChangedRef.current?.();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
      } finally {
        setBusy("");
      }
      return;
    }
    // A retry asks again only for the products that were refused, so a
    // product that already has its draft does not get a second one.
    const targets = failedIds.length > 0
      ? products.filter((item) => failedIds.includes(item.id))
      : products;
    const failures: string[] = [];
    const refused: string[] = [];
    let first: DraftView | null = draft;
    let queued = 0;
    try {
      for (const item of targets) {
        try {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/attribution/creative-drafts`,
            { method: "POST", body: JSON.stringify(requestBody(item.id, true)) },
          );
          if (!response.ok) {
            refused.push(item.id);
            failures.push(`${item.name}: ${await errorDetail(response, t("attribution.generate.requestFailed"))}`);
            continue;
          }
          const payload = await response.json() as { draft: DraftView };
          queued += 1;
          if (!first) first = payload.draft;
        } catch (caught) {
          refused.push(item.id);
          const detail = caught instanceof Error ? caught.message : t("attribution.generate.requestFailed");
          failures.push(`${item.name}: ${detail}`);
        }
      }
      setFailedIds(refused);
      const total = queuedCount + queued;
      setQueuedCount(total);
      if (first) {
        setDraft(first);
        setPrompt(first.prompt);
        if (first.listing_fields) setListingSnapshot(first.listing_fields);
        setNotice(many
          ? t("attribution.generate.queuedMany", { count: total })
          : t("attribution.generate.queued"));
        if (queued > 0) onChangedRef.current?.();
      }
      if (failures.length > 0) setError(failures.join(" "));
    } finally {
      setBusy("");
    }
  }

  async function generateWith(provider: { id: string; label: string }) {
    if (!draft || !workspaceId) return;
    if (!window.confirm(t("attribution.generate.generateConfirm", { provider: provider.label }))) return;
    setBusy(provider.id);
    setError("");
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/creative-drafts/${draft.id}/generate`,
        {
          method: "POST",
          body: JSON.stringify({ provider_id: provider.id, confirm_external_action: true }),
        },
      );
      if (!response.ok) {
        setError(await errorDetail(response, t("attribution.generate.requestFailed")));
        return;
      }
      const payload = await response.json() as { job: { id: string } };
      setGeneration(payload.job.id);
      setNotice(t("attribution.generate.generateQueued"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    if (!workspaceId || !draft || !generation) return;
    let stopped = false;
    async function tick() {
      if (!draft) return;
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/attribution/creative-drafts/${draft.id}/generation`,
        );
        if (!response.ok || stopped) return;
        const payload = await response.json() as {
          generation?: { status?: string; error?: string | null; id?: string; attempt?: number };
        };
        const status = payload.generation?.status;
        if (payload.generation?.id && payload.generation.id !== generation) return;
        // Queued again after an attempt: the next one polls the same paid request.
        setRetrying(status === "queued" && (payload.generation?.attempt ?? 0) > 0);
        if (status === "succeeded") {
          const again = await apiFetch(
            `/api/workspaces/${workspaceId}/attribution/creative-drafts/${draft.id}`,
          );
          if (!again.ok || stopped) return;
          const filed = await again.json() as { draft: DraftView };
          setDraft(filed.draft);
          setPrompt(filed.draft.prompt);
          setNotice((filed.draft.product_count ?? 1) > 1
            ? t("attribution.generate.linkedAll")
            : t("attribution.generate.linked"));
          setGeneration("");
          onChangedRef.current?.();
        } else if (status === "failed" || status === "cancelled") {
          setError(payload.generation?.error || t("attribution.generate.requestFailed"));
          setGeneration("");
        }
      } catch {
        // The next tick asks again. A dropped poll is not a failed generation.
      }
    }
    void tick();
    const timer = window.setInterval(() => void tick(), 3000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [apiFetch, draft, generation, t, workspaceId]);

  async function submitFile() {
    if (!draft || !file || !workspaceId) return;
    const limitMb = draft.kind === "video" ? FILE_LIMIT_MB.video : FILE_LIMIT_MB.image;
    if (file.size > limitMb * 1024 * 1024) {
      setError(t("attribution.generate.fileTooLarge", { size: limitMb }));
      return;
    }
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
      onChangedRef.current?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
    } finally {
      setBusy("");
    }
  }

  function keptPictures(member: DraftMember): string[] {
    return pictureUrls(member).filter((url) => locked || imageOn(member.product_id, url));
  }

  function sentSnapshot(member: DraftMember): ListingSnapshot {
    const snapshot: ListingSnapshot = { ...(member.listing_fields ?? {}) };
    if (selectedFields.has("gallery")) snapshot.gallery = keptPictures(member);
    return snapshot;
  }

  function renderFields(snapshot: ListingSnapshot | undefined) {
    return chosenFields.map((key) => (
      <div className="generate-use" key={key}>
        <strong>{t(`attribution.generate.${FIELD_LABEL[key]}`)}</strong>
        <ListingFieldValue
          field={key}
          value={snapshot?.[key]}
          empty={t("attribution.generate.fieldEmpty")}
          truncated={t("attribution.generate.descriptionTruncated")}
          galleryCount={(count) => t("attribution.generate.galleryCount", { count })}
          stockLine={(count) => t("attribution.generate.stockLine", { count })}
          stockUnknown={t("attribution.generate.stockUnknown")}
        />
      </div>
    ));
  }

  function renderPictureRow(urls: string[]) {
    if (urls.length === 0) {
      return <p className="generate-field-value">{t("attribution.generate.sendNoPictures")}</p>;
    }
    return (
      <ol className="generate-gallery">
        {urls.map((url) => (
          <li key={url}>
            {/* eslint-disable-next-line @next/next/no-img-element -- listing CDN */}
            <img src={picturePreview(url)} alt="" />
          </li>
        ))}
      </ol>
    );
  }

  // A group shot under review is named the way the table names it.
  const reviewGroup = reviewing && (draft?.product_count ?? 0) > 1
    ? draft?.group_number
      ? t("attribution.generate.groupLabel", { number: draft.group_number })
      : t("attribution.generate.draftGroupTogether")
    : "";

  const libraryLabels = reviewing
    ? storedSubjects.map((asset) => ({
      id: asset.asset_id,
      title: asset.missing
        ? `${asset.title || asset.asset_id} — ${t("attribution.generate.subjectMissing")}`
        : (asset.title || asset.asset_id),
    }))
    : subjects.map((asset) => ({ id: asset.id, title: asset.title }));
  // Listing pictures that are the subject go out even with Listing pictures
  // off: every product of a group shot, or one product without a Library pick.
  const subjectPicturesHidden = !selectedFields.has("gallery")
    && (groupShot || libraryLabels.length === 0);

  return (
    <>
    <Dialog
      className="generate-dialog"
      open={open && products.length > 0}
      title={reviewing
        ? reviewGroup
          ? `${t("attribution.generate.reviewTitle")} · ${reviewGroup}`
          : t("attribution.generate.reviewTitle")
        : t("attribution.generate.title")}
      description={reviewing
        ? reviewGroup
          ? t("attribution.generate.reviewDescriptionGroup", { count: draft?.product_count ?? 0 })
          : products[0]
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
          {draft && !reviewing && failedIds.length > 0 && (
            <Button
              variant="primary"
              busy={busy === "queue"}
              disabled={busy !== ""}
              onClick={() => void queue()}
            >{busy === "queue"
              ? t("attribution.generate.queuing")
              : t("attribution.generate.queueRetry", { count: failedIds.length })}</Button>
          )}
          {draft && oneCreative && draft.owed > 0 && (
            <Button
              variant="primary"
              busy={busy === "file"}
              disabled={!file || busy !== "" || generation !== ""}
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
        {sameGroup && (
          <div className="generate-duplicate" role="status">
            <p>
              {t(sameGroupPending
                ? "attribution.generate.duplicateGroupPending"
                : "attribution.generate.duplicateGroupDone", { group: sameGroupName })}
            </p>
            {onOpenDraft && products[0] && (
              <Button
                variant={sameGroupPending ? "primary" : "secondary"}
                size="sm"
                onClick={() => onOpenDraft(products[0].id, sameGroup.id)}
              >{t("attribution.generate.openGroup", { group: sameGroupName })}</Button>
            )}
          </div>
        )}
        {singlesPending > 0 && (
          <p className="generate-duplicate" role="status">
            {t("attribution.generate.duplicateSingles", { count: singlesPending, total: products.length })}
          </p>
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
                {backgroundValid && (
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
            {LISTING_FIELD_KEYS.map((key) => (
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
        {selectedFields.has("gallery") && members.length > 0 && (
          <div className="generate-scope generate-listing-pictures">
            <strong>{t("attribution.generate.members")}</strong>
            {!locked && pictureRows.length > 0 && (
              <>
                <p>{t("attribution.generate.picturesHelp")}</p>
                <TriCheckbox
                  checked={allPicturesOn}
                  indeterminate={!allPicturesOn && !everyPictureOff}
                  label={t("attribution.generate.checkAllPictures")}
                  onChange={setAllPictures}
                />
              </>
            )}
            <ul className="generate-member-list">
              {members.map((member) => {
                const urls = pictureUrls(member);
                const kept = urls.filter((url) => locked || imageOn(member.product_id, url));
                const uncovered = !locked && attachedLibrary === 0 && kept.length === 0;
                const productAll = urls.length > 0 && kept.length === urls.length;
                return (
                  <li key={member.product_id}>
                    <div className="generate-member-head">
                      {!locked && urls.length > 0 ? (
                        <TriCheckbox
                          checked={productAll}
                          indeterminate={kept.length > 0 && !productAll}
                          label={member.name}
                          title={member.name}
                          labelClassName="generate-member-name"
                          onChange={(on) => setProductPictures(member.product_id, on)}
                        />
                      ) : (
                        <>
                          {urls.length === 0 && (
                            <span className="generate-member-missing" aria-hidden="true" />
                          )}
                          <span className="generate-member-name" title={member.name}>{member.name}</span>
                        </>
                      )}
                    </div>
                    {urls.length > 0 && (
                      <ul className="generate-picture-choices">
                        {urls.map((url, index) => {
                          const on = locked || imageOn(member.product_id, url);
                          if (locked) {
                            return (
                              <li key={url}>
                                <img src={picturePreview(url)} alt="" />
                              </li>
                            );
                          }
                          return (
                            <li key={url}>
                              <label className={on ? "is-on" : "is-off"}>
                                <input
                                  type="checkbox"
                                  checked={on}
                                  aria-label={t("attribution.generate.pictureLabel", {
                                    name: member.name,
                                    index: index + 1,
                                  })}
                                  onChange={() => togglePicture(member.product_id, url)}
                                />
                                <img src={picturePreview(url)} alt="" />
                              </label>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                    {uncovered && (
                      <small>
                        {urls.length === 0
                          ? t("attribution.generate.noListingPicture")
                          : t("attribution.generate.noPictureSelected")}
                      </small>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
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
        <details className="generate-send">
          <summary>{t("attribution.generate.sendPreview")}</summary>
          <div className="generate-send-body">
            <label className="generate-prompt">
              {t("attribution.generate.prompt")}
              <textarea readOnly rows={8} value={prompt} />
              <small>{reviewing || draft
                ? t("attribution.generate.promptStored")
                : t("attribution.generate.promptHelp")}</small>
            </label>
            {backgroundOn && backgroundValid && (
              <div className="generate-use">
                <strong>{t("attribution.generate.backgroundHeading")}</strong>
                <p className="generate-field-value">{backgroundReference.trim()}</p>
              </div>
            )}
            {libraryLabels.length > 0 && (
              <div className="generate-use">
                <strong>{groupShot
                  ? t("attribution.generate.sendExtra")
                  : t("attribution.generate.sendPictures")}</strong>
                <ol className="generate-offer-list">
                  {libraryLabels.map((item, index) => (
                    <li key={item.id}>{index + 1}. {item.title}</li>
                  ))}
                </ol>
              </div>
            )}
            {members.length > 1 ? (
              (chosenFields.length > 0 || subjectPicturesHidden) && members.map((member) => (
                <section className="generate-send-product" key={member.product_id}>
                  <span className="generate-member-name" title={member.name}>{member.name}</span>
                  {subjectPicturesHidden && (
                    <div className="generate-use">
                      <strong>{t("attribution.generate.sendPictures")}</strong>
                      {renderPictureRow(keptPictures(member))}
                    </div>
                  )}
                  {renderFields(sentSnapshot(member))}
                </section>
              ))
            ) : (
              <>
                {many && !reviewing && members.length <= 1 && products[0] && chosenFields.length > 0 && (
                  <p>{t("attribution.generate.listingShared", { name: products[0].name })}</p>
                )}
                {members.length === 1 && subjectPicturesHidden && (
                  <div className="generate-use">
                    <strong>{t("attribution.generate.sendPictures")}</strong>
                    {renderPictureRow(keptPictures(members[0]))}
                  </div>
                )}
                {renderFields(members.length === 1 ? sentSnapshot(members[0]) : listingSnapshot)}
              </>
            )}
          </div>
        </details>
        {draft && oneCreative && (
          <p role="status">
            {draft.linked
              ? t("attribution.generate.statusSucceeded")
              : t("attribution.generate.statusPending")}
            {" · "}
            {t("attribution.generate.owed", { count: draft.owed })}
          </p>
        )}
        {videoOpen && providers.length > 0 && providerBlocked && (
          <p className="generate-provider-note">{providerBlocked}</p>
        )}
        {videoOpen && providers.length > 0 && !providerBlocked && (
          <div className="generate-providers">
            {providerSubject && (
              <p className="generate-provider-note">
                {t("attribution.generate.providerFirstSubject", {
                  title: providerSubject.title || providerSubject.asset_id,
                })}
              </p>
            )}
            {providers.map((provider) => (
              <Button
                key={provider.id}
                variant="primary"
                busy={busy === provider.id || generation !== ""}
                disabled={busy !== "" || generation !== ""}
                onClick={() => void generateWith(provider)}
              >{busy === provider.id
                ? t("attribution.generate.generating")
                : t("attribution.generate.generateWith", { provider: provider.label })}</Button>
            ))}
          </div>
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
        {generation && retrying && (
          <p role="status">{t("attribution.generate.generationRetrying")}</p>
        )}
        {queueBlocker && <p className="generate-blocker">{queueBlocker}</p>}
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
  field: ListingFieldKey;
  value: ListingSnapshot[ListingFieldKey];
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
              <img src={picturePreview(url)} alt="" />
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
