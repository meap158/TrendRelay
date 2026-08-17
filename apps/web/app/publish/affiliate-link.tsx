"use client";

/**
 * Attaching a product's affiliate link to a post, and putting it where it works.
 *
 * The composer had a first-comment field and no way to reach Attribution, so
 * the only route was to open another page, copy a link, and paste it back.
 * The disclosure that has to accompany it was left to memory.
 *
 * What is attached is the network's own affiliate URL. This used to offer
 * TrendRelay's minted `/c/` codes; ADR 0022 retired that redirector, so nothing
 * mints one and the codes it listed resolve nowhere. The product is chosen from
 * the catalogue itself - see `OfferPicker` - because a code was never something
 * anybody recognised a product by.
 *
 * Two rules govern this, and both come from outside the interface:
 *
 * The disclosure leads the caption. The endorsement guides ask for one that is
 * near the endorsement, no later than the link, prominent, and on every post -
 * so it goes in with the link, at the top, not as a checkbox someone can skip.
 *
 * Where the link goes is the network's decision. A URL in an Instagram or
 * TikTok caption is not a link, and a comment link on Instagram now costs reach
 * and gets hidden. The placement comes from the API, which reads it from the
 * same policy the campaign autopilot composes with - two copies of "does a link
 * work here" drift, and the one that drifts is the one nobody tests.
 */

import { useMemo, useState } from "react";

import { Button } from "../ui/button";
import { useT } from "../i18n-provider";
import { withDisclosure } from "../../lib/publish-rules";
import { commissionLabel } from "../commission";
import { OfferPicker, type OfferChoice } from "./offer-picker";
import type { ProductRow } from "../attribution/types";

export type LinkPlacement = { placement: string; reason: string };

/**
 * What every post says about being an advertisement.
 *
 * The same sentence a campaign starts with, so a post written by hand and one
 * written by an autopilot disclose identically. An offer carries no disclosure
 * of its own - the obligation belongs to the post, not to the merchant.
 */
export const DEFAULT_DISCLOSURE = "Affiliate link; we may earn a commission.";

export function AffiliateLink({
  products,
  placementByPlatform,
  platforms,
  caption,
  firstComment,
  onCaption,
  onFirstComment,
  commentPlatforms,
  disabled,
}: {
  products: ProductRow[];
  placementByPlatform: Record<string, LinkPlacement>;
  /** The networks this post is actually going to. */
  platforms: string[];
  caption: string;
  firstComment: string;
  onCaption: (next: string) => void;
  onFirstComment: (next: string) => void;
  /** Networks among the chosen that accept a first comment at all. */
  commentPlatforms: string[];
  disabled?: boolean;
}) {
  const t = useT();
  const [picking, setPicking] = useState(false);
  const [offer, setOffer] = useState<OfferChoice | null>(null);

  /** What each chosen network will do with this link, grouped by outcome. */
  const outcomes = useMemo(() => {
    const grouped: Record<string, string[]> = {};
    for (const platform of platforms) {
      const where = placementByPlatform[platform]?.placement ?? "bio";
      (grouped[where] ??= []).push(platform);
    }
    return grouped;
  }, [platforms, placementByPlatform]);

  const captionWorks = (outcomes.caption ?? []).length > 0;
  const bioOnly = (outcomes.bio ?? []).length > 0;

  function addToCaption() {
    if (!offer) return;
    const body = withDisclosure(caption, DEFAULT_DISCLOSURE);
    onCaption(
      body.includes(offer.affiliate_url)
        ? body
        : `${body.trimEnd()}\n\n${offer.affiliate_url}`,
    );
  }

  function addToComment() {
    if (!offer) return;
    // The disclosure still goes in the caption, not the comment. In a comment it
    // discloses nothing to a reader who never opens the comments.
    onCaption(withDisclosure(caption, DEFAULT_DISCLOSURE));
    onFirstComment(
      firstComment.includes(offer.affiliate_url)
        ? firstComment
        : `${firstComment.trim()}${firstComment.trim() ? "\n" : ""}${offer.affiliate_url}`.trim(),
    );
  }

  if (!products.some((product) => product.offers.some((item) => item.affiliate_url))) {
    return (
      <div className="affiliate-link empty">
        <span>{t("publish.noProductsToLink")}</span>
      </div>
    );
  }

  return (
    <div className="affiliate-link">
      <div className="affiliate-chooser">
        <span className="affiliate-chooser-label">{t("publish.affiliateLink")}</span>
        {/* The chosen product stated in full rather than as a code. What it
            pays is part of that: the rate is usually why this offer was
            attached instead of another on the same product. */}
        {offer ? (
          <span className="affiliate-chosen">
            <strong>{offer.name}</strong>
            <small>{[offer.network, commissionLabel(offer)].filter(Boolean).join(" · ")}</small>
          </span>
        ) : (
          <span className="affiliate-chosen empty">{t("publish.chooseProductPrompt")}</span>
        )}
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled}
          onClick={() => setPicking(true)}
        >{offer ? t("publish.changeProduct") : t("publish.chooseProductAction")}</Button>
      </div>

      <OfferPicker
        open={picking}
        products={products}
        chosen={offer?.offer_id ?? ""}
        onChoose={(next) => { setOffer(next); setPicking(false); }}
        onClose={() => setPicking(false)}
      />

      {offer && (
        <>
          {/* Said before inserting, not after. Someone attaching a link to an
              Instagram post needs to know it will not be tappable there while
              they can still decide what to do about it. */}
          <ul className="affiliate-outcomes">
            {Object.entries(outcomes).map(([where, list]) => (
              <li key={where} className={where}>
                <b>{t(`autopilot.placement.${where}`)}</b>
                <span>{list.map((platform) => platform).join(", ")}</span>
              </li>
            ))}
            {!platforms.length && <li><span>{t("publish.chooseDestinationsFirst")}</span></li>}
          </ul>

          <div className="affiliate-actions">
            <Button
              variant="secondary"
              size="sm"
              disabled={disabled || !captionWorks}
              title={captionWorks ? undefined : t("publish.noCaptionLinkHere")}
              onClick={addToCaption}
            >{t("publish.addToCaption")}</Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={disabled || !commentPlatforms.length}
              title={commentPlatforms.length
                ? t("publish.commentReach")
                : t("publish.noCommentHere")}
              onClick={addToComment}
            >{t("publish.addToFirstComment")}</Button>
          </div>

          {/* The disclosure travels with the link either way, so it is stated
              rather than left as a surprise edit to the caption. */}
          <small className="affiliate-note">
            {t("publish.disclosureGoesFirst", { disclosure: DEFAULT_DISCLOSURE })}
          </small>
          {bioOnly && (
            <small className="affiliate-note warn">
              {t("publish.bioOnlyHere", {
                platforms: (outcomes.bio ?? []).join(", "),
              })}
            </small>
          )}
        </>
      )}
    </div>
  );
}
