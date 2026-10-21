"use client";

import { DraftThumbnail } from "../ui/draft-thumbnail";
import { useT } from "../i18n-provider";
import { Button } from "../ui/button";
import { pendingDrafts } from "./creative-filter";
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
  const drafts = pendingDrafts(products);
  if (drafts.length === 0) {
    return (
      <section className="pending-drafts" aria-label={t("attribution.pendingDrafts.title", { count: 0 })}>
        <p className="pending-drafts-empty">{t("attribution.pendingDrafts.none")}</p>
      </section>
    );
  }
  return (
    <section className="pending-drafts" aria-labelledby="pending-drafts-title">
      <header>
        <h3 id="pending-drafts-title">{t("attribution.pendingDrafts.title", { count: drafts.length })}</h3>
        <p>{t("attribution.pendingDrafts.help")}</p>
      </header>
      <ul>
        {drafts.map(({ draft, products: featured }) => {
          const together = (draft.product_count ?? featured.length) > 1;
          const focused = activeGroup === draft.id;
          return (
            <li key={draft.id} className="pending-draft">
              <DraftThumbnail kind={draft.kind} count={draft.product_count ?? featured.length} />
              <div className="pending-draft-head">
                {together
                  ? <GroupChip number={draft.group_number} />
                  : <span className="pending-draft-single">{t("attribution.pendingDrafts.single")}</span>}
                <span className="pending-draft-what">
                  {[
                    creativeKind(t, draft.kind),
                    creativeRecipe(t, draft.recipe),
                    t("attribution.productCount", { count: draft.product_count ?? featured.length }),
                  ].join(" · ")}
                </span>
                <span className="pending-draft-owed">
                  {t("attribution.generate.owed", { count: Math.max(1, draft.owed) })}
                </span>
              </div>
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
              </ul>
              <div className="pending-draft-actions">
                <Button variant="secondary" size="sm" onClick={() => onReview(featured[0], draft.id)}>
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
    </section>
  );
}
