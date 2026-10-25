"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { DraftThumbnail } from "../ui/draft-thumbnail";
import { useLocale, useT } from "../i18n-provider";
import { useAuth } from "../auth-provider";
import { useWorkspace } from "../workspace-provider";
import { useRecipeExamples, useRecipeLibrary } from "../ui/use-recipe-library";
import { AssetThumbnail } from "../publish/composer";
import { creativeRecipeHelp, localized } from "../../lib/recipe-library";
import { RecipePreview } from "./recipe-previews";
import { RecipeGallery } from "./recipe-gallery";
import { Button } from "../ui/button";
import { Lightbox } from "../ui/lightbox";
import { HoverPreview } from "../ui/hover-preview";
import { pendingDrafts } from "./creative-filter";
import { DraftFilterStrip, useDraftFilters } from "./draft-filter-strip";
import { filterDrafts } from "../../lib/draft-filters";
import {
  creativeKind,
  creativeRecipe,
  DiscardDraftButton,
  GroupChip,
  type CreativeDraftRow,
} from "./product-creatives";

type DraftProduct = {
  id: string;
  name: string;
  image_url?: string | null;
  creative_drafts?: CreativeDraftRow[] | null;
};

/** How many product pictures a card shows before it says "+N". */
const PICTURES = 6;
const PAGE_SIZE = 8;

/**
 * What the Pending filter is actually about: the drafts, not their products.
 *
 * The filter narrows the table to products, and a group shot of six products
 * is six rows and one draft - so the count said 1, the table showed six, and
 * none of the rows said which draft was pending or how to take it back. Each
 * pending draft is a card here: what it is, what it still owes, the products
 * it features, and its three actions - review it, show only its products, or
 * remove it from the queue.
 */
export function PendingDrafts<P extends DraftProduct>({
  products,
  canDiscard,
  activeGroup,
  onReview,
  onShowProducts,
  onDiscarded,
}: {
  products: readonly P[];
  canDiscard: boolean;
  /** The group shot the table is narrowed to, if any. */
  activeGroup: string;
  onReview: (product: P, draftId: string) => void;
  onShowProducts: (draftId: string) => void;
  onDiscarded?: () => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const { recipes } = useRecipeLibrary(workspaceId, apiFetch);
  const examples = useRecipeExamples(workspaceId, apiFetch, true);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [productPreview, setProductPreview] = useState<{ src: string; name: string } | null>(null);
  const [filters, setFilters] = useDraftFilters();
  const [page, setPage] = useState(0);
  const drafts = useMemo(() => pendingDrafts(products), [products]);
  const labels = useMemo(
    () => ({
      recipe: (id: string) => creativeRecipe(t, id),
      kind: (value: string) => creativeKind(t, value),
    }),
    [t],
  );
  const matching = useMemo(() => filterDrafts(drafts, filters, labels), [drafts, filters, labels]);
  const pages = Math.max(1, Math.ceil(matching.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  const visible = matching.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  if (drafts.length === 0) {
    return (
      <section className="pending-drafts" aria-label={t("attribution.pendingDrafts.title", { count: 0 })}>
        <p className="pending-drafts-empty">{t("attribution.pendingDrafts.none")}</p>
      </section>
    );
  }
  return (
    <section className="pending-drafts" aria-labelledby="pending-drafts-title">
      <header className="pending-drafts-header">
        <div><span className="pending-drafts-eyebrow">{t("attribution.pendingDrafts.eyebrow")}</span>
          <h3 id="pending-drafts-title">{t("attribution.pendingDrafts.heading")} <span className="pending-drafts-count" aria-live="polite">{matching.length}</span></h3>
          <p>{t("attribution.pendingDrafts.subhead")}</p>
        </div>
        <span className="pending-drafts-summary" aria-live="polite">{t("attribution.pendingDrafts.filesRemaining", { count: matching.reduce((total, { draft }) => total + Math.max(1, draft.owed), 0) })}</span>
      </header>
      <DraftFilterStrip
        entries={drafts}
        filters={filters}
        labels={labels}
        shown={matching.length}
        onChange={(next) => {
          setFilters(next);
          setPage(0);
        }}
      />
      <ul className="pending-drafts-list">
        {visible.map(({ draft, products: featured }) => {
          const together = (draft.product_count ?? featured.length) > 1;
          const focused = activeGroup === draft.id;
          const recipe = recipes.find((entry) => entry.id === draft.recipe);
          const made = examples[draft.recipe]?.assets ?? [];
          const help = creativeRecipeHelp(t, draft.recipe) || localized(recipe?.help, locale);
          return (
            <li key={draft.id} className="pending-draft">
              <div className="pending-draft-preview-pair">
              <div className="pending-draft-preview-group">
              <button type="button" className="pending-draft-preview" aria-label={`Preview product image: ${featured[0]?.name ?? "Product"}`} disabled={!featured[0]?.image_url} onClick={() => { const product = featured[0]; if (product?.image_url) setProductPreview({ src: product.image_url, name: product.name }); }}>
                {featured[0]?.image_url
                  // eslint-disable-next-line @next/next/no-img-element -- product listing hero
                  ? <img src={featured[0].image_url} alt="" loading="lazy" />
                  : <DraftThumbnail kind={draft.kind} size="compact" />}
              </button>
              <span className="pending-draft-preview-label">Product</span>
              </div>
              <div className="pending-draft-preview-group">
              <HoverPreview className="pending-draft-preview" label={`Preview ${creativeRecipe(t, draft.recipe)} recipe`} ratio={1.5}
                media={<span className="pending-draft-schematic-popover"><RecipePreview recipe={draft.recipe} mined={recipe?.builtin ? null : recipe} /></span>}
                caption={creativeRecipe(t, draft.recipe)} onActivate={made.length ? () => setPreviewing(draft.recipe) : undefined}>
                {made.length && workspaceId ? <AssetThumbnail asset={made[0]} workspaceId={workspaceId} apiFetch={apiFetch} />
                  : recipe || ["bed_flat_lay", "sleepwear_lookbook", "character_editorial_carousel"].includes(draft.recipe) ? <RecipePreview recipe={draft.recipe} mined={recipe?.builtin ? null : recipe} />
                  : <DraftThumbnail kind={draft.kind} size="compact" />}
              </HoverPreview>
              <span className="pending-draft-preview-label">Recipe</span>
              </div>
              </div>
              <div className="pending-draft-content">
              <div className="pending-draft-head">
                <h4>{creativeRecipe(t, draft.recipe)}</h4>
                {together && <GroupChip number={draft.group_number} />}
              </div>
              {help && <p className="pending-draft-recipe-help" title={help}>{help}</p>}
              <p className="pending-draft-description" title={featured.map((product) => product.name).join(", ")}>
                <span>{featured[0]?.name}{featured.length > 1 ? ` +${featured.length - 1} more` : ""}</span>
              </p>
              <div className="pending-draft-meta"><span>{creativeKind(t, draft.kind)}</span><span>{t("attribution.productCount", { count: draft.product_count ?? featured.length })}</span><span className="pending-draft-owed">{t("attribution.pendingDrafts.filesRemaining", { count: Math.max(1, draft.owed) })}</span></div>
              {together &&
              <ul className="pending-draft-products" aria-label={t("attribution.productCount", { count: featured.length })}>
                {featured.slice(0, PICTURES).map((product) => (
                  <li key={product.id} title={product.name}>
                    {product.image_url
                      // eslint-disable-next-line @next/next/no-img-element
                      ? <img src={product.image_url} alt="" loading="lazy" />
                      : <span aria-hidden="true">{(product.name.trim()[0] ?? "?").toUpperCase()}</span>}
                    <small>{product.name}</small>
                  </li>
                ))}
                {featured.length > PICTURES && (
                  <li className="pending-draft-more">+{featured.length - PICTURES}</li>
                )}
              </ul>}
              </div>
              <div className="pending-draft-actions">
                <Button variant="secondary" size="sm" aria-label={`Review ${creativeRecipe(t, draft.recipe)} for ${featured[0]?.name ?? "these products"}`} onClick={() => onReview(featured[0], draft.id)}>
                  {t("attribution.pendingDrafts.review")}
                </Button>
                {together && (
                  <Button variant="quiet" size="sm" aria-pressed={focused} onClick={() => onShowProducts(draft.id)}>
                    {focused
                      ? t("attribution.generate.showEveryGroup")
                      : t("attribution.pendingDrafts.showProducts")}
                  </Button>
                )}
                {canDiscard && draft.status === "pending" && (
                  <DiscardDraftButton draftId={draft.id} onDiscarded={onDiscarded} />
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {!matching.length && <div className="pending-drafts-no-results"><Search size={22} aria-hidden="true" /><strong>{t("attribution.pendingDrafts.filters.emptyTitle")}</strong><p>{t("attribution.pendingDrafts.filters.emptyHint")}</p></div>}
      <footer className="pending-drafts-footer"><span aria-live="polite">{matching.length ? t("attribution.pendingDrafts.pager.range", { from: currentPage * PAGE_SIZE + 1, to: Math.min((currentPage + 1) * PAGE_SIZE, matching.length), total: matching.length }) : t("attribution.pendingDrafts.filters.count", { shown: 0, total: drafts.length })}</span><div><Button variant="quiet" size="sm" aria-label={t("attribution.pendingDrafts.pager.previousLabel")} disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} />{t("attribution.pendingDrafts.pager.previous")}</Button><span>{t("attribution.pendingDrafts.pager.page", { page: currentPage + 1, pages })}</span><Button variant="quiet" size="sm" aria-label={t("attribution.pendingDrafts.pager.nextLabel")} disabled={currentPage >= pages - 1} onClick={() => setPage(currentPage + 1)}>{t("attribution.pendingDrafts.pager.next")}<ChevronRight size={16} /></Button></div></footer>
      {previewing && workspaceId && <RecipeGallery recipe={previewing} name={creativeRecipe(t, previewing)} initial={examples[previewing]?.assets ?? []} selected={examples[previewing]?.assets[0]?.id ?? ""} workspaceId={workspaceId} apiFetch={apiFetch} onClose={() => setPreviewing(null)} />}
      {productPreview && <Lightbox open src={productPreview.src} alt={productPreview.name} onClose={() => setProductPreview(null)} />}
    </section>
  );
}
