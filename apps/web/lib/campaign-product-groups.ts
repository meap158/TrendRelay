import type { OfferChoice } from "../app/publish/offer-rows";

/** Preserve distinct affiliate offers while drawing a product only once. */
export function campaignProductGroups(offers: readonly OfferChoice[], selected: ReadonlySet<string>) {
  const byProduct = new Map<string, OfferChoice[]>();
  for (const offer of offers) {
    const group = byProduct.get(offer.product_id) ?? [];
    if (!group.some((entry) => entry.offer_id === offer.offer_id)) group.push(offer);
    byProduct.set(offer.product_id, group);
  }
  return [...byProduct.values()].map((choices) => ({
    choices,
    offer: choices.find((choice) => selected.has(choice.offer_id))
      ?? choices.filter((choice) => choice.availability !== "unavailable")
        .sort((a, b) => (b.commission_bps ?? -1) - (a.commission_bps ?? -1))[0]
      ?? choices[0],
  }));
}

/** Switching an offer is one choice for this product; other products stay pinned. */
export function chooseProductOffer(selected: ReadonlySet<string>, productOffers: readonly OfferChoice[], offerId: string) {
  const next = new Set(selected);
  productOffers.forEach((offer) => next.delete(offer.offer_id));
  if (offerId) next.add(offerId);
  return next;
}
