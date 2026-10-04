/**
 * Single versus Together in the Attribution product list.
 *
 * A draft is Together when it features more than one product. Products that
 * share that draft id are one group. Each product is drawn once: a product
 * that also has a Single draft stays with its Together draft, and the open
 * row is where the two kinds are named separately.
 *
 * With no Together draft on screen the sorted rows are left alone.
 */

export type GroupDraft = {
  id: string;
  kind: string;
  recipe: string;
  product_count?: number;
  /** Carried through so a group heading can say where its creative stands. */
  status?: string;
  owed?: number;
  card_count?: number;
};

export type GroupProduct = {
  id: string;
  creative_drafts?: GroupDraft[] | null;
};

export type TogetherSection<T> = {
  kind: "together";
  draft: GroupDraft;
  products: T[];
};

export type SingleSection<T> = {
  kind: "single";
  products: T[];
};

export type PlainSection<T> = {
  kind: "plain";
  products: T[];
};

export type DraftSection<T extends GroupProduct> =
  | TogetherSection<T>
  | SingleSection<T>
  | PlainSection<T>;

export function isTogetherDraft(draft: { product_count?: number } | null | undefined): boolean {
  return (draft?.product_count ?? 0) > 1;
}

/** Split one product's drafts. A missing count is Single. */
export function partitionDrafts<T extends { product_count?: number }>(
  drafts: readonly T[] | null | undefined,
): { single: T[]; together: T[] } {
  const single: T[] = [];
  const together: T[] = [];
  for (const draft of drafts ?? []) {
    if (isTogetherDraft(draft)) together.push(draft);
    else single.push(draft);
  }
  return { single, together };
}

/**
 * Group the rows currently on screen.
 *
 * A product in two Together drafts is drawn once. The larger draft is
 * placed first, so a smaller draft that only repeats some of those products
 * does not split the larger set. Equal sizes keep the order the drafts
 * first appear. Members keep the order the sort already gave them.
 * Products with only Single drafts follow in one block, then products
 * with no draft.
 */
export function groupShownDrafts<T extends GroupProduct>(shown: readonly T[]): {
  grouped: boolean;
  sections: DraftSection<T>[];
} {
  const togetherSeen: GroupDraft[] = [];
  const seenIds = new Set<string>();
  for (const product of shown) {
    for (const draft of product.creative_drafts ?? []) {
      if (!isTogetherDraft(draft) || seenIds.has(draft.id)) continue;
      seenIds.add(draft.id);
      togetherSeen.push(draft);
    }
  }
  if (togetherSeen.length === 0) {
    return {
      grouped: false,
      sections: [{ kind: "plain", products: [...shown] }],
    };
  }
  togetherSeen.sort((left, right) => (right.product_count ?? 0) - (left.product_count ?? 0));

  const placed = new Set<string>();
  const sections: DraftSection<T>[] = [];
  for (const draft of togetherSeen) {
    const products = shown.filter((product) => {
      if (placed.has(product.id)) return false;
      return (product.creative_drafts ?? []).some(
        (item) => item.id === draft.id && isTogetherDraft(item),
      );
    });
    if (products.length === 0) continue;
    for (const product of products) placed.add(product.id);
    sections.push({ kind: "together", draft, products });
  }

  const single: T[] = [];
  const plain: T[] = [];
  for (const product of shown) {
    if (placed.has(product.id)) continue;
    const drafts = product.creative_drafts ?? [];
    const onlySingle = drafts.length > 0 && drafts.every((draft) => !isTogetherDraft(draft));
    if (onlySingle) single.push(product);
    else plain.push(product);
  }
  if (single.length > 0) sections.push({ kind: "single", products: single });
  if (plain.length > 0) sections.push({ kind: "plain", products: plain });
  return { grouped: true, sections };
}
