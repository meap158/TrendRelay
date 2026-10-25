"use client";

import { useMemo } from "react";
import { useT } from "../i18n-provider";
import { Button } from "../ui/button";
import { creativeRecipe } from "../../lib/recipe-library";
import { pendingDrafts } from "./creative-filter";
import { DraftSection, type SectionProduct } from "./draft-section";
import { DiscardDraftButton, type CreativeDraftRow } from "./product-creatives";

type DraftProduct = SectionProduct & {
  creative_drafts?: CreativeDraftRow[] | null;
};

/**
 * What the Pending filter is actually about: the drafts, not their products.
 *
 * The filter narrows the table to products, and a group shot of six products
 * is six rows and one draft - so the count said 1, the table showed six, and
 * none of the rows said which draft was pending or how to take it back. Each
 * pending draft is a card here: what it is, what it still owes, the products
 * it features, and its three actions - review it, show only its products, or
 * remove it from the queue. The shell and card are shared with the Library
 * view (`draft-section`).
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
  const drafts = useMemo(() => pendingDrafts(products), [products]);
  const owed = (draft: CreativeDraftRow) => Math.max(1, draft.owed);
  return (
    <DraftSection
      id="pending-drafts"
      variant="pending"
      entries={drafts}
      copy={{
        eyebrow: t("attribution.pendingDrafts.eyebrow"),
        heading: t("attribution.pendingDrafts.heading"),
        subhead: t("attribution.pendingDrafts.subhead"),
        none: t("attribution.pendingDrafts.none"),
        label: t("attribution.pendingDrafts.title", { count: 0 }),
      }}
      summary={(matching) =>
        t("attribution.pendingDrafts.filesRemaining", { count: matching.reduce((total, { draft }) => total + owed(draft), 0) })}
      facts={({ draft }) => (
        <span className="pending-draft-owed">{t("attribution.pendingDrafts.filesRemaining", { count: owed(draft) })}</span>
      )}
      actions={({ draft, products: featured }) => {
        const together = (draft.product_count ?? featured.length) > 1;
        const focused = activeGroup === draft.id;
        return (
          <>
            <Button variant="secondary" size="sm" aria-label={`Review ${creativeRecipe(t, draft.recipe)} for ${featured[0]?.name ?? "these products"}`} onClick={() => onReview(featured[0], draft.id)}>
              {t("attribution.pendingDrafts.review")}
            </Button>
            {together && (
              <Button variant="quiet" size="sm" aria-pressed={focused} onClick={() => onShowProducts(draft.id)}>
                {focused ? t("attribution.generate.showEveryGroup") : t("attribution.pendingDrafts.showProducts")}
              </Button>
            )}
            {canDiscard && draft.status === "pending" && <DiscardDraftButton draftId={draft.id} onDiscarded={onDiscarded} />}
          </>
        );
      }}
    />
  );
}
