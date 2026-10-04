"use client";

/**
 * A product's creatives, each shown as what it is.
 *
 * The expanded row used to list one "Open in Library" link per linked file
 * above a list of drafts, so a product with a Single image and a Together
 * shot showed two identical links and no way to tell which was which. Each
 * draft is now one card: its files as thumbnails that open in the
 * lightbox, Open in Library as its own control, what it is, where it
 * stands, and - collapsed - the prompt and settings it was made from. A
 * draft that still owes a file says how to finish it right there: generate
 * it when a video provider is ready, add the file otherwise.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { useAuth } from "../auth-provider";
import { useT } from "../i18n-provider";
import { useWorkspace } from "../workspace-provider";
import { AssetThumbnail, type LibraryAsset } from "../publish/composer";
import { Button } from "../ui/button";
import { Lightbox, useLightboxSet } from "../ui/lightbox";
import { opaquePreviewUrl } from "../../lib/media-preview";
import { partitionDrafts } from "./draft-groups";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

export type CreativeDraftRow = {
  id: string;
  kind: string;
  recipe: string;
  status: string;
  card_count: number;
  owed: number;
  product_count?: number;
};

export type CreativeAssetRow = { asset_id: string; draft_id: string; position: number };

export type CreativeProduct = {
  id: string;
  creative_assets?: CreativeAssetRow[];
  creative_drafts?: CreativeDraftRow[];
};

type Provider = { id: string; label: string };

/** Only a still is fetched, so the shape the thumbnail needs is mostly empty. */
function thumbnailOf(assetId: string): LibraryAsset {
  return {
    id: assetId,
    title: "",
    platform: null,
    creator: null,
    original_path: "",
    duration_ms: null,
    width: null,
    height: null,
    media_kind: "image",
    versions: [{ id: `${assetId}-thumbnail`, kind: "thumbnail" }],
  };
}

export function isPendingDraft(draft: Pick<CreativeDraftRow, "status" | "owed">): boolean {
  return draft.status !== "succeeded" || draft.owed > 0;
}

/** This draft's linked files, in card order. */
export function draftAssets(product: CreativeProduct, draftId: string): CreativeAssetRow[] {
  return (product.creative_assets ?? [])
    .filter((item) => item.draft_id === draftId)
    .sort((a, b) => a.position - b.position);
}

/**
 * Ready video providers, asked once per workspace for every open row.
 *
 * Every pending card wants the same answer, and an open table can show many
 * of them. The answer is kept for a minute: a provider switched on in Tools
 * shows up without a reload, and a page of cards does not ask a page of
 * times.
 */
const providerCache = new Map<string, { at: number; request: Promise<Provider[]> }>();

export function useVideoProviders(enabled: boolean): Provider[] {
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const [providers, setProviders] = useState<Provider[]>([]);
  useEffect(() => {
    if (!enabled || !workspaceId) return;
    let cancelled = false;
    const cached = providerCache.get(workspaceId);
    const request = cached && Date.now() - cached.at < 60_000
      ? cached.request
      : apiFetch(`/api/workspaces/${workspaceId}/attribution/video-providers`)
        .then(async (response) => {
          if (!response.ok) return [] as Provider[];
          const payload = await response.json() as { providers?: Provider[] };
          return payload.providers ?? [];
        })
        .catch(() => [] as Provider[]);
    if (request !== cached?.request) providerCache.set(workspaceId, { at: Date.now(), request });
    void request.then((rows) => {
      if (!cancelled) setProviders(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, enabled, workspaceId]);
  return enabled ? providers : [];
}

/**
 * The full-size bytes of one creative file, for the lightbox.
 *
 * Fetched through the API like every served byte - the asset endpoints want
 * the workspace identity a bare `<img>` does not send - asked for opaque and
 * retyped into a blob. A picture is its original; a clip uses the stream
 * endpoint the Library player uses. Empty while loading, which the lightbox
 * shows as a steady dark stage rather than a broken image.
 */
function useCreativeSource(assetId: string | null, video: boolean): string {
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const [loaded, setLoaded] = useState<{ id: string; url: string } | null>(null);
  useEffect(() => {
    if (!assetId || !workspaceId) return;
    let active = true;
    let objectUrl = "";
    const controller = new AbortController();
    const base = `/api/workspaces/${workspaceId}/media/library/assets/${assetId}`;
    const path = video ? `${base}/preview/stream?cut=original` : `${base}/content/original`;
    apiFetch(opaquePreviewUrl(path), { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("unavailable");
        return response.arrayBuffer();
      })
      .then((bytes) => {
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: video ? "video/mp4" : "image/jpeg" }));
        if (active) setLoaded({ id: assetId, url: objectUrl });
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, assetId, video, workspaceId]);
  return loaded && loaded.id === assetId ? loaded.url : "";
}

/** The explicit way to the Library: every file of this creative, as one set. */
export function OpenInLibrary({ assets }: { assets: CreativeAssetRow[] }) {
  const t = useT();
  if (assets.length === 0) return null;
  const ids = assets.map((item) => item.asset_id).join(",");
  return (
    <Link className="product-creative-library" href={`/library?assets=${encodeURIComponent(ids)}`}>
      {t("attribution.generate.assetLink")}
    </Link>
  );
}

/**
 * The files a draft holds, then a dashed tile for each one it still owes.
 *
 * A thumbnail is pressed to look at it: the shared lightbox opens on that
 * file, and a carousel is walked with the arrows in card order, the way
 * every other strip of pictures in the app behaves. Going to the Library is
 * the separate `OpenInLibrary` control, so looking never leaves the page.
 */
export function CreativeMedia({
  assets,
  owed,
  kind,
  size = "md",
}: {
  assets: CreativeAssetRow[];
  owed: number;
  /** The draft's kind. A video opens in the player, anything else as a picture. */
  kind: string;
  size?: "sm" | "md";
}) {
  const t = useT();
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const { openAt, open, close, previous, next } = useLightboxSet(assets.length);
  const video = kind === "video";
  const source = useCreativeSource(openAt === null ? null : assets[openAt]?.asset_id ?? null, video);
  if (!workspaceId) return null;
  return (
    <span className="product-creative-media" data-size={size}>
      {assets.map((item, index) => (
        <button
          key={item.asset_id}
          type="button"
          className="product-creative-thumb"
          aria-label={t("attribution.generate.previewFile", { index: index + 1, count: assets.length })}
          title={t("attribution.generate.previewFile", { index: index + 1, count: assets.length })}
          onClick={() => open(index)}
        >
          <AssetThumbnail asset={thumbnailOf(item.asset_id)} workspaceId={workspaceId} apiFetch={apiFetch} hoverPreview />
        </button>
      ))}
      {owed > 0 && (
        <span className="product-creative-owed" title={t("attribution.generate.owed", { count: owed })}>
          {owed > 1 ? `+${owed}` : "+"}
        </span>
      )}
      {openAt !== null && (
        <Lightbox
          open
          src={source}
          kind={video ? "video" : "image"}
          alt={t("attribution.generate.previewFile", { index: openAt + 1, count: assets.length })}
          onClose={close}
          onPrevious={previous}
          onNext={next}
        />
      )}
    </span>
  );
}

type Generation = { status?: string; provider_label?: string | null; error?: string | null };

/**
 * Where the latest generation for a pending video draft has got to.
 *
 * Shown on the card so a generation started earlier, and the dialog closed
 * since, is visible without opening the draft again.
 */
function GenerationState({ draftId }: { draftId: string }) {
  const t = useT();
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const [generation, setGeneration] = useState<Generation | null>(null);
  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    void apiFetch(`/api/workspaces/${workspaceId}/attribution/creative-drafts/${draftId}/generation`)
      .then(async (response): Promise<{ generation?: Generation }> => (
        response.ok ? response.json() : {}
      ))
      .then((payload) => {
        if (!cancelled) setGeneration(payload.generation ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [apiFetch, draftId, workspaceId]);
  if (generation?.status === "queued" || generation?.status === "running") {
    return (
      <span className="product-creative-state" data-state="running">
        {t("attribution.generate.generationRunningWith", {
          provider: generation.provider_label || t("attribution.generate.kindVideo"),
        })}
      </span>
    );
  }
  if (generation?.status === "failed" && generation.error) {
    return (
      <span className="product-creative-state" data-state="stopped" title={generation.error}>
        {t("attribution.generate.generationStopped", { error: generation.error })}
      </span>
    );
  }
  return null;
}

/** What finishing a pending draft means here: generate it, or add its file. */
export function ResumeButton({
  draft,
  providers,
  onResume,
  size = "sm",
}: {
  draft: CreativeDraftRow;
  providers: Provider[];
  onResume: () => void;
  size?: "sm" | "md";
}) {
  const t = useT();
  // A provider is sent one image of one product, so a group shot is filled
  // from outside even when a provider is ready.
  const generate = draft.kind === "video" && (draft.product_count ?? 1) <= 1 && providers.length > 0;
  return (
    <Button variant={generate ? "primary" : "secondary"} size={size} onClick={onResume}>
      {generate ? t("attribution.generate.resumeGenerate") : t("attribution.generate.resumeAddFile")}
    </Button>
  );
}

function CreativeCard({
  product,
  draft,
  providers,
  onReview,
}: {
  product: CreativeProduct;
  draft: CreativeDraftRow;
  providers: Provider[];
  onReview: (draftId: string) => void;
}) {
  const t = useT();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const assets = draftAssets(product, draft.id);
  const pending = isPendingDraft(draft);
  const together = (draft.product_count ?? 0) > 1;
  return (
    <li className="product-creative-card" data-pending={pending || undefined}>
      <CreativeMedia assets={assets} owed={pending ? draft.owed : 0} kind={draft.kind} />
      <div className="product-creative-body">
        <p className="product-creative-title">
          <strong>{creativeKind(t, draft.kind)} · {creativeRecipe(t, draft.recipe)}</strong>
          {together && (
            <span className="product-creative-badge">
              {t("attribution.generate.featuresProducts", { count: draft.product_count ?? 0 })}
            </span>
          )}
        </p>
        <p className="product-creative-meta">
          <span className="product-creative-state" data-state={pending ? "pending" : "done"}>
            {pending ? t("attribution.generate.statusPending") : t("attribution.generate.statusSucceeded")}
          </span>
          {pending && <span>{t("attribution.generate.owed", { count: draft.owed })}</span>}
          {pending && draft.kind === "video" && <GenerationState draftId={draft.id} />}
        </p>
        <div className="product-creative-actions">
          {pending && (
            <ResumeButton draft={draft} providers={providers} onResume={() => onReview(draft.id)} />
          )}
          <Button variant="quiet" size="sm" onClick={() => onReview(draft.id)}>
            {t("attribution.generate.viewDraft")}
          </Button>
          <OpenInLibrary assets={assets} />
        </div>
        <details
          className="product-creative-settings"
          onToggle={(event) => setSettingsOpen(event.currentTarget.open)}
        >
          <summary>{t("attribution.generate.promptSettings")}</summary>
          {/* Read only when asked for: an open table can hold many drafts. */}
          {settingsOpen && <CreativeDraftSummary draftId={draft.id} status={draft.status} owed={draft.owed} />}
        </details>
      </div>
    </li>
  );
}

/**
 * Every creative on one product, under Single and Together.
 *
 * A linked file whose draft is not in the list (an older link) still shows,
 * under its own heading, so nothing that is linked disappears.
 */
export function ProductCreatives({
  product,
  onReview,
}: {
  product: CreativeProduct;
  onReview: (draftId: string) => void;
}) {
  const t = useT();
  const drafts = product.creative_drafts ?? [];
  const groups = partitionDrafts(drafts);
  const wantsProviders = drafts.some((draft) => draft.kind === "video" && isPendingDraft(draft));
  const providers = useVideoProviders(wantsProviders);
  const known = new Set(drafts.map((draft) => draft.id));
  const loose = (product.creative_assets ?? []).filter((item) => !known.has(item.draft_id));
  if (drafts.length === 0 && loose.length === 0) {
    return <p className="product-no-data">{t("attribution.generate.noneLinked")}</p>;
  }
  return (
    <div className="product-creatives">
      {(["single", "together"] as const).map((kind) => {
        const rows = groups[kind];
        if (rows.length === 0) return null;
        const labelId = `${product.id}-${kind}-drafts`;
        return (
          <section key={kind} className="product-creative-group" aria-labelledby={labelId}>
            <h5 id={labelId} className="product-creative-group-label">
              {t(kind === "single"
                ? "attribution.generate.draftGroupSingle"
                : "attribution.generate.draftGroupTogether")}
            </h5>
            <ul>
              {rows.map((draft) => (
                <CreativeCard
                  key={draft.id}
                  product={product}
                  draft={draft}
                  providers={providers}
                  onReview={onReview}
                />
              ))}
            </ul>
          </section>
        );
      })}
      {loose.length > 0 && (
        <section className="product-creative-group">
          <h5 className="product-creative-group-label">{t("attribution.generate.otherFiles")}</h5>
          <CreativeMedia assets={loose} owed={0} kind="image" />
          <OpenInLibrary assets={loose} />
        </section>
      )}
    </div>
  );
}

export function creativeKind(t: (path: string) => string, kind: string): string {
  if (kind === "carousel") return t("attribution.generate.kindCarousel");
  if (kind === "video") return t("attribution.generate.kindVideo");
  return t("attribution.generate.kindImage");
}

export function creativeRecipe(t: (path: string) => string, recipe: string): string {
  if (recipe === "mannequin_transition") return t("attribution.generate.recipeMannequin");
  if (recipe === "mirror_selfie") return t("attribution.generate.recipeMirror");
  return t("attribution.generate.recipeBed");
}

/** The product list carries only id, kind, recipe, status, and owed. */
const LISTING_FIELD_ORDER = ["title", "price", "description", "gallery", "variations"] as const;

const LISTING_FIELD_LABEL: Record<(typeof LISTING_FIELD_ORDER)[number], string> = {
  title: "omitTitle",
  price: "omitPrice",
  description: "omitDescription",
  gallery: "omitGallery",
  variations: "omitVariations",
};

type StoredSubject = { asset_id: string; title?: string; missing?: boolean };

type DraftConfig = {
  prompt: string;
  variant?: string | null;
  card_count?: number;
  background_enabled?: boolean;
  background_reference?: string | null;
  subject_assets?: StoredSubject[];
  listing_fields?: Record<string, unknown>;
  products?: { product_id: string; name: string }[];
};

const draftConfigCache = new Map<string, DraftConfig>();

function draftConfigKey(draftId: string, status: string, owed: number): string {
  return `${draftId}:${status}:${owed}`;
}

/**
 * The saved ask for one draft, under its row.
 *
 * Loaded when that row is open. A later file submit changes status or owed,
 * so the cache key changes and the row reads the draft again.
 */
export function CreativeDraftSummary({
  draftId,
  status,
  owed,
}: {
  draftId: string;
  status: string;
  owed: number;
}) {
  const t = useT();
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const key = draftConfigKey(draftId, status, owed);
  const [config, setConfig] = useState<DraftConfig | null>(() => draftConfigCache.get(key) ?? null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const cached = draftConfigCache.get(key);
    if (cached) {
      setConfig(cached);
      setFailed(false);
      return;
    }
    if (!workspaceId) return;
    let cancelled = false;
    setConfig(null);
    setFailed(false);
    void (async () => {
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/attribution/creative-drafts/${draftId}`,
        );
        if (cancelled) return;
        if (!response.ok) {
          setFailed(true);
          return;
        }
        const payload = await response.json() as { draft?: DraftConfig };
        if (!payload.draft?.prompt) {
          setFailed(true);
          return;
        }
        draftConfigCache.set(key, payload.draft);
        setConfig(payload.draft);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiFetch, draftId, key, workspaceId]);

  if (failed) {
    return <p className="product-creative-note">{t("attribution.generate.requestFailed")}</p>;
  }
  if (!config) {
    return (
      <p className="product-creative-note" aria-busy="true">
        {t("attribution.generate.loadingDraft")}
      </p>
    );
  }

  const fields = config.listing_fields ?? {};
  const fieldNames = LISTING_FIELD_ORDER
    .filter((name) => Object.prototype.hasOwnProperty.call(fields, name))
    .map((name) => t(`attribution.generate.${LISTING_FIELD_LABEL[name]}`));
  const subjects = config.subject_assets ?? [];
  const subjectText = subjects.length === 0
    ? t("attribution.generate.subjectReviewEmpty")
    : subjects.map((item) => {
      const title = item.title?.trim() || item.asset_id;
      return item.missing
        ? `${title} — ${t("attribution.generate.subjectMissing")}`
        : title;
    }).join(", ");
  const backgroundText = config.background_enabled && config.background_reference?.trim()
    ? config.background_reference.trim()
    : t("attribution.generate.backgroundNone");
  const cardCount = config.card_count ?? 1;

  const memberRows = (config.products ?? []).filter((item) => item.name);

  return (
    <dl className="product-creative-config">
      {memberRows.length > 1 && (
        <div>
          <dt>{t("attribution.generate.members")}</dt>
          <dd>
            <ul className="product-creative-members">
              {memberRows.map((item) => (
                <li key={item.product_id}>{item.name}</li>
              ))}
            </ul>
          </dd>
        </div>
      )}
      <div>
        <dt>{t("attribution.generate.backgroundHeading")}</dt>
        <dd>{backgroundText}</dd>
      </div>
      <div>
        <dt>{t("attribution.generate.subject")}</dt>
        <dd>{subjectText}</dd>
      </div>
      <div>
        <dt>{t("attribution.generate.listingFields")}</dt>
        <dd>{fieldNames.length > 0 ? fieldNames.join(", ") : t("attribution.generate.fieldsNone")}</dd>
      </div>
      {(config.variant === "female" || config.variant === "male") && (
        <div>
          <dt>{t("attribution.generate.variant")}</dt>
          <dd>
            {config.variant === "male"
              ? t("attribution.generate.variantMale")
              : t("attribution.generate.variantFemale")}
          </dd>
        </div>
      )}
      {cardCount > 1 && (
        <div>
          <dt>{t("attribution.generate.cards")}</dt>
          <dd>{cardCount}</dd>
        </div>
      )}
      <div>
        <dt>{t("attribution.generate.prompt")}</dt>
        <dd className="product-creative-prompt">{config.prompt}</dd>
      </div>
    </dl>
  );
}
