"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useT } from "../i18n-provider";
import { Button, buttonClass } from "../ui/button";
import { creativeRecipe } from "../../lib/recipe-library";
import { libraryItems } from "./creative-filter";
import { DraftSection, type SectionProduct } from "./draft-section";
import type { CreativeAssetRow, CreativeDraftRow } from "./product-creatives";

type LibraryProduct = SectionProduct & {
  creative_drafts?: CreativeDraftRow[] | null;
  creative_assets?: CreativeAssetRow[] | null;
};

/** Progress means nothing for something already made. */
const HIDDEN_FILTERS: ["status"] = ["status"];

/**
 * What the In Library filter is about: the drafts whose files are already in the Library.
 *
 * The same card and shell as the pending drafts (`draft-section`), with the facts and actions
 * that fit finished work: how many files are in the Library, a link that opens exactly those
 * files there, and the way to narrow the table to the products it features. There is no review
 * or remove - nothing here is waiting to be made.
 */
export function LibraryItems<P extends LibraryProduct>({
  products,
  activeGroup,
  onShowProducts,
}: {
  products: readonly P[];
  /** The group shot the table is narrowed to, if any. */
  activeGroup: string;
  onShowProducts: (draftId: string) => void;
}) {
  const t = useT();
  const items = useMemo(
    () => libraryItems(products).map(({ draft, products: featured, assetIds }) => ({ draft, products: featured, assetIds })),
    [products],
  );
  const filesOf = (draftId: string) => items.find((item) => item.draft.id === draftId)?.assetIds ?? [];
  return (
    <DraftSection
      id="library-items"
      variant="library"
      entries={items}
      hideFilters={HIDDEN_FILTERS}
      copy={{
        eyebrow: t("attribution.libraryItems.eyebrow"),
        heading: t("attribution.libraryItems.heading"),
        subhead: t("attribution.libraryItems.subhead"),
        none: t("attribution.libraryItems.none"),
        label: t("attribution.libraryItems.heading"),
      }}
      summary={(matching) =>
        t("attribution.libraryItems.filesInLibrary", { count: new Set(matching.flatMap(({ draft }) => filesOf(draft.id))).size })}
      facts={({ draft }) => (
        <span className="pending-draft-files">{t("attribution.libraryItems.filesInLibrary", { count: filesOf(draft.id).length })}</span>
      )}
      actions={({ draft, products: featured }) => {
        const together = (draft.product_count ?? featured.length) > 1;
        const focused = activeGroup === draft.id;
        return (
          <>
            <Link
              className={buttonClass({ variant: "secondary", size: "sm" })}
              href={`/library?assets=${encodeURIComponent(filesOf(draft.id).join(","))}`}
              aria-label={`${t("attribution.libraryItems.open")}: ${creativeRecipe(t, draft.recipe)}, ${featured[0]?.name ?? ""}`}
            >
              {t("attribution.libraryItems.open")}
            </Link>
            {together && (
              <Button variant="quiet" size="sm" aria-pressed={focused} onClick={() => onShowProducts(draft.id)}>
                {focused ? t("attribution.generate.showEveryGroup") : t("attribution.pendingDrafts.showProducts")}
              </Button>
            )}
          </>
        );
      }}
    />
  );
}
