"use client";

import { useMemo, useState, type ReactNode } from "react";
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
import { DraftFilterStrip, useDraftFilters } from "./draft-filter-strip";
import { filterDrafts, type DraftEntry, type DraftFilters } from "../../lib/draft-filters";
import { creativeKind, creativeRecipe, GroupChip, type CreativeDraftRow } from "./product-creatives";

/**
 * The Drafts panel's pieces, shared by every view of "a draft and its products".
 *
 * Pending drafts and In Library items are the same card in the same shell; they differ only in
 * which entries they list, the copy, the extra facts on a card and the actions under it. Those
 * come in as props, so a change to the layout, the filters or the pager lands in both. The class
 * names keep the `pending-draft` prefix the stylesheet already uses for this layout.
 */

/** How many product pictures a card shows before it says "+N". */
const PICTURES = 6;
const PAGE_SIZE = 8;

export type SectionProduct = { id: string; name: string; image_url?: string | null };

export type SectionEntry<P extends SectionProduct = SectionProduct> = {
  draft: CreativeDraftRow;
  products: P[];
};

type Preview = { src: string; name: string };

/** What a card needs from the workspace, asked once for the whole section. */
type CardContext = {
  recipes: ReturnType<typeof useRecipeLibrary>["recipes"];
  examples: ReturnType<typeof useRecipeExamples>;
  workspaceId: string | null;
  apiFetch: ReturnType<typeof useAuth>["apiFetch"];
  openRecipe: (recipe: string) => void;
  openProduct: (preview: Preview) => void;
};

/** The product chips of a group draft: a picture (or initial) and name each, then "+N". */
export function ProductChipRow({ products, label }: { products: readonly SectionProduct[]; label: string }) {
  return (
    <ul className="pending-draft-products" aria-label={label}>
      {products.slice(0, PICTURES).map((product) => (
        <li key={product.id} title={product.name}>
          {product.image_url
            // eslint-disable-next-line @next/next/no-img-element
            ? <img src={product.image_url} alt="" loading="lazy" />
            : <span aria-hidden="true">{(product.name.trim()[0] ?? "?").toUpperCase()}</span>}
          <small>{product.name}</small>
        </li>
      ))}
      {products.length > PICTURES && <li className="pending-draft-more">+{products.length - PICTURES}</li>}
    </ul>
  );
}

/** One draft: its product and recipe pictures, what it is, the facts the view adds, and its actions. */
export function DraftCard({
  entry,
  context,
  facts,
  actions,
  variant,
}: {
  entry: SectionEntry;
  context: CardContext;
  /** Variant-specific facts after the format and product count (files remaining, files in Library). */
  facts: ReactNode;
  actions: ReactNode;
  variant: string;
}) {
  const t = useT();
  const { locale } = useLocale();
  const { draft, products: featured } = entry;
  const { recipes, examples, workspaceId, apiFetch } = context;
  const together = (draft.product_count ?? featured.length) > 1;
  const recipe = recipes.find((item) => item.id === draft.recipe);
  const made = examples[draft.recipe]?.assets ?? [];
  const help = creativeRecipeHelp(t, draft.recipe) || localized(recipe?.help, locale);
  const first = featured[0];
  return (
    <li className="pending-draft" data-variant={variant}>
      <div className="pending-draft-preview-pair">
        <div className="pending-draft-preview-group">
          <button
            type="button"
            className="pending-draft-preview"
            aria-label={`Preview product image: ${first?.name ?? "Product"}`}
            disabled={!first?.image_url}
            onClick={() => { if (first?.image_url) context.openProduct({ src: first.image_url, name: first.name }); }}
          >
            {first?.image_url
              // eslint-disable-next-line @next/next/no-img-element -- product listing hero
              ? <img src={first.image_url} alt="" loading="lazy" />
              : <DraftThumbnail kind={draft.kind} size="compact" />}
          </button>
          <span className="pending-draft-preview-label">Product</span>
        </div>
        <div className="pending-draft-preview-group">
          <HoverPreview
            className="pending-draft-preview"
            label={`Preview ${creativeRecipe(t, draft.recipe)} recipe`}
            ratio={1.5}
            media={<span className="pending-draft-schematic-popover"><RecipePreview recipe={draft.recipe} mined={recipe?.builtin ? null : recipe} /></span>}
            caption={creativeRecipe(t, draft.recipe)}
            onActivate={made.length ? () => context.openRecipe(draft.recipe) : undefined}
          >
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
          <span>{first?.name}{featured.length > 1 ? ` +${featured.length - 1} more` : ""}</span>
        </p>
        <div className="pending-draft-meta">
          <span>{creativeKind(t, draft.kind)}</span>
          <span>{t("attribution.productCount", { count: draft.product_count ?? featured.length })}</span>
          {facts}
        </div>
        {together && <ProductChipRow products={featured} label={t("attribution.productCount", { count: featured.length })} />}
      </div>
      <div className="pending-draft-actions">{actions}</div>
    </li>
  );
}

/**
 * The shell: header with a count and summary, search and filters, the card grid, the empty and
 * no-match states, and the pager. `copy` holds the variant's words as already-translated text.
 */
export function DraftSection<P extends SectionProduct>({
  id,
  variant,
  entries,
  copy,
  summary,
  hideFilters,
  facts,
  actions,
}: {
  /** Prefix for the heading id, unique per view. */
  id: string;
  variant: string;
  entries: readonly SectionEntry<P>[];
  copy: { eyebrow: string; heading: string; subhead: string; none: string; label: string };
  /** The header's right-hand pill, from the entries the filters leave. */
  summary: (matching: readonly SectionEntry<P>[]) => string;
  /** Filters that mean nothing for this view (a library item has no progress). */
  hideFilters?: (keyof DraftFilters)[];
  facts: (entry: SectionEntry<P>) => ReactNode;
  actions: (entry: SectionEntry<P>) => ReactNode;
}) {
  const t = useT();
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const { recipes } = useRecipeLibrary(workspaceId, apiFetch);
  const examples = useRecipeExamples(workspaceId, apiFetch, true);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [productPreview, setProductPreview] = useState<Preview | null>(null);
  const [stored, setFilters] = useDraftFilters();
  // A filter this view hides must not keep narrowing it from a shared address.
  const filters = useMemo(
    () => ({ ...stored, ...Object.fromEntries((hideFilters ?? []).map((key) => [key, ""])) }) as DraftFilters,
    [stored, hideFilters],
  );
  const [page, setPage] = useState(0);
  const labels = useMemo(
    () => ({
      recipe: (recipe: string) => creativeRecipe(t, recipe),
      kind: (value: string) => creativeKind(t, value),
    }),
    [t],
  );
  const matching = useMemo(
    () => filterDrafts(entries as readonly (DraftEntry<P> & SectionEntry<P>)[], filters, labels) as readonly SectionEntry<P>[],
    [entries, filters, labels],
  );
  const pages = Math.max(1, Math.ceil(matching.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  const visible = matching.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const context: CardContext = {
    recipes,
    examples,
    workspaceId,
    apiFetch,
    openRecipe: setPreviewing,
    openProduct: setProductPreview,
  };
  if (entries.length === 0) {
    return (
      <section className="pending-drafts" data-variant={variant} aria-label={copy.label}>
        <p className="pending-drafts-empty">{copy.none}</p>
      </section>
    );
  }
  const p = "attribution.pendingDrafts";
  return (
    <section className="pending-drafts" data-variant={variant} aria-labelledby={`${id}-title`}>
      <header className="pending-drafts-header">
        <div>
          <span className="pending-drafts-eyebrow">{copy.eyebrow}</span>
          <h3 id={`${id}-title`}>{copy.heading} <span className="pending-drafts-count" aria-live="polite">{matching.length}</span></h3>
          <p>{copy.subhead}</p>
        </div>
        <span className="pending-drafts-summary" aria-live="polite">{summary(matching)}</span>
      </header>
      <DraftFilterStrip
        entries={entries}
        filters={filters}
        labels={labels}
        shown={matching.length}
        hide={hideFilters}
        onChange={(next) => {
          setFilters(next);
          setPage(0);
        }}
      />
      <ul className="pending-drafts-list">
        {visible.map((entry) => (
          <DraftCard key={entry.draft.id} entry={entry} context={context} variant={variant} facts={facts(entry)} actions={actions(entry)} />
        ))}
      </ul>
      {!matching.length && (
        <div className="pending-drafts-no-results">
          <Search size={22} aria-hidden="true" />
          <strong>{t(`${p}.filters.emptyTitle`)}</strong>
          <p>{t(`${p}.filters.emptyHint`)}</p>
        </div>
      )}
      <footer className="pending-drafts-footer">
        <span aria-live="polite">
          {matching.length
            ? t(`${p}.pager.range`, { from: currentPage * PAGE_SIZE + 1, to: Math.min((currentPage + 1) * PAGE_SIZE, matching.length), total: matching.length })
            : t(`${p}.filters.count`, { shown: 0, total: entries.length })}
        </span>
        <div>
          <Button variant="quiet" size="sm" aria-label={t(`${p}.pager.previousLabel`)} disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>
            <ChevronLeft size={16} />{t(`${p}.pager.previous`)}
          </Button>
          <span>{t(`${p}.pager.page`, { page: currentPage + 1, pages })}</span>
          <Button variant="quiet" size="sm" aria-label={t(`${p}.pager.nextLabel`)} disabled={currentPage >= pages - 1} onClick={() => setPage(currentPage + 1)}>
            {t(`${p}.pager.next`)}<ChevronRight size={16} />
          </Button>
        </div>
      </footer>
      {previewing && workspaceId && (
        <RecipeGallery
          recipe={previewing}
          name={creativeRecipe(t, previewing)}
          initial={examples[previewing]?.assets ?? []}
          selected={examples[previewing]?.assets[0]?.id ?? ""}
          workspaceId={workspaceId}
          apiFetch={apiFetch}
          onClose={() => setPreviewing(null)}
        />
      )}
      {productPreview && <Lightbox open src={productPreview.src} alt={productPreview.name} onClose={() => setProductPreview(null)} />}
    </section>
  );
}
