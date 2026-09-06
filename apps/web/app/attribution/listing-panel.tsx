"use client";

import { useState } from "react";

import { money } from "./format";
import type { ProductRow } from "./types";

/**
 * The full stored listing, as the product's own listing endpoint returns it.
 * Optional everywhere: a listing distilled from a page holds what the page
 * held, and an older snapshot may predate a field.
 */
export type FullListing = {
  title?: string | null;
  description?: string | null;
  images?: string[];
  brand?: string | null;
  discount_percent?: number | null;
  categories?: string[];
  attributes?: { name: string; value: string }[];
  tier_variations?: { name: string; options: string[]; images?: string[] }[];
  vouchers?: {
    code: string;
    min_spend?: number | null;
    discount_value?: number | null;
    discount_percentage?: number | null;
  }[];
  shop_location?: string | null;
  has_video?: boolean;
  listed_at?: string | null;
  withheld_signed_out?: string[];
};

/** Shopee stores VND money at a 100000 multiplier; render it as dong. */
function shopeeMoney(value: number | null | undefined): string | null {
  if (typeof value !== "number" || value <= 0) return null;
  return `₫${Math.round(value / 100000).toLocaleString("vi-VN")}`;
}

/**
 * The expanded row's listing, laid out the way the listing itself is.
 *
 * Shopee's page is a gallery beside the buying facts with the long-form
 * details underneath, and that order is what anyone who has seen the page
 * expects - so the panel keeps it: pictures left; title, price, variations
 * and vouchers right; product details and description across the bottom.
 * Optimised to this table's setup: the price is the export's (the page
 * withholds it signed-out), the commission the row already shows is not
 * repeated, and everything renders from the stored snapshot - no request to
 * Shopee happens here.
 */
export function ListingPanel({
  product,
  listing,
}: {
  product: ProductRow;
  listing: FullListing;
}) {
  const images = listing.images ?? [];
  const [heroIndex, setHeroIndex] = useState(0);
  const [fullDescription, setFullDescription] = useState(false);
  // Clamped at render rather than corrected by an effect: a re-fetch can
  // shorten the gallery, and the hero must not point past its end.
  const hero = Math.min(heroIndex, Math.max(images.length - 1, 0));

  const offer = product.offers[0];
  // The exact formatter the row's Price column uses, so the two can never
  // disagree - a hand-rolled divide-by-100 here showed ₫3.150 under a row
  // saying ₫315,000, because đồng has no minor unit to divide away.
  const price = offer?.price_cents != null
    ? money(offer.price_cents, offer.currency)
    : null;
  const attributes = listing.attributes ?? [];
  const vouchers = listing.vouchers ?? [];
  const description = (listing.description ?? "").trim();

  return (
    <div className="listing-pdp">
      {images.length > 0 && (
        <div className="listing-gallery">
          {/* eslint-disable-next-line @next/next/no-img-element -- Shopee CDN */}
          <img className="listing-hero" src={images[hero]} alt={listing.title ?? product.name} loading="lazy" />
          {images.length > 1 && (
            <div className="listing-thumbs" role="group" aria-label="Pictures">
              {images.map((url, index) => (
                <button
                  key={url}
                  type="button"
                  className={index === hero ? "selected" : ""}
                  aria-label={`Picture ${index + 1}`}
                  onMouseEnter={() => setHeroIndex(index)}
                  onClick={() => setHeroIndex(index)}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- Shopee CDN */}
                  <img src={url} alt="" loading="lazy" />
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="listing-main">
        <h4>{listing.title ?? product.name}</h4>
        {(listing.categories ?? []).length > 0 && (
          <small className="listing-crumbs">{listing.categories!.join(" › ")}</small>
        )}
        {(price || listing.discount_percent) && (
          <div className="listing-price">
            {price && <strong>{price}</strong>}
            {listing.discount_percent ? <em>−{listing.discount_percent}%</em> : null}
            {price && <small>from your export</small>}
          </div>
        )}
        {(listing.tier_variations ?? []).map((tier) => (
          <div key={tier.name} className="listing-tier">
            <small>{tier.name}</small>
            <div className="listing-options">
              {tier.options.map((option, index) => (
                <span key={option}>
                  {tier.images?.[index] && (
                    /* eslint-disable-next-line @next/next/no-img-element -- Shopee CDN */
                    <img src={tier.images[index]} alt="" loading="lazy" />
                  )}
                  {option}
                </span>
              ))}
            </div>
          </div>
        ))}
        {vouchers.length > 0 && (
          <div className="listing-tier">
            <small>Shop vouchers</small>
            <div className="listing-vouchers">
              {vouchers.map((voucher) => (
                <span key={voucher.code}>
                  {voucher.code}
                  {voucher.discount_percentage
                    ? ` · ${voucher.discount_percentage}% off`
                    : shopeeMoney(voucher.discount_value)
                      ? ` · ${shopeeMoney(voucher.discount_value)} off`
                      : ""}
                  {shopeeMoney(voucher.min_spend) ? ` from ${shopeeMoney(voucher.min_spend)}` : ""}
                </span>
              ))}
            </div>
          </div>
        )}
        <small className="listing-facts">
          {[
            listing.shop_location ? `Ships from ${listing.shop_location}` : null,
            listing.listed_at ? `Listed ${new Date(listing.listed_at).toLocaleDateString()}` : null,
            listing.has_video ? "Has video" : null,
            listing.brand ? `Brand: ${listing.brand}` : null,
          ].filter(Boolean).join(" · ")}
        </small>
        {(listing.withheld_signed_out ?? []).length > 0 && (
          <small className="listing-withheld">
            Shopee shows {listing.withheld_signed_out!.join(", ")} only to signed-in
            visitors; the price here comes from your export.
          </small>
        )}
      </div>
      {(attributes.length > 0 || description) && (
        <div className="listing-body">
          {attributes.length > 0 && (
            <section>
              <h5>Product details</h5>
              <dl>
                {attributes.map((attribute) => (
                  <div key={attribute.name}>
                    <dt>{attribute.name}</dt>
                    <dd>{attribute.value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
          {description && (
            <section>
              <h5>Description</h5>
              <p className={fullDescription ? "" : "listing-description-clamped"}>{description}</p>
              <button
                type="button"
                className="listing-description-toggle"
                onClick={() => setFullDescription((current) => !current)}
              >{fullDescription ? "Show less" : "Show more"}</button>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
