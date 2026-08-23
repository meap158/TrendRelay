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
import { offerChoices } from "./offer-rows";
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

/** How a post decides which product it carries. */
export type OfferMode = "smart" | "manual" | "none";

/**
 * One ranked candidate, as the matcher returns it.
 *
 * Deliberately not an `OfferChoice`. The matcher answers "how well does this
 * offer fit this content" and knows nothing about the picture, the price or
 * the brand; the catalogue the panel already holds knows all three. Accepting
 * a match looks the real offer up rather than carrying a thinner copy of it
 * around, which is how a chosen product ends up missing its own name.
 */
export type OfferMatch = {
  offer_id: string;
  product_name: string;
  score: number;
  confidence: "high" | "medium" | "low";
  matched_terms: string[];
  reasons: string[];
};

export type OfferSuggestion = {
  matches: OfferMatch[];
  /** What it read, what it ranked against, and what it makes of the result. */
  advice: string;
  read_from: string[];
  candidate_scope: string;
};

export function AffiliateLink({
  products,
  campaigns,
  campaignsByOffer,
  placementByPlatform,
  platforms,
  offer,
  onOffer,
  mode,
  onMode,
  suggestion,
  suggesting,
  onSuggest,
  caption,
  firstComment,
  disclosure,
  disclose,
  onCaption,
  onFirstComment,
  onDisclosure,
  onDisclose,
  commentPlatforms,
  commentUnavailableReason,
  disabled,
}: {
  products: ProductRow[];
  /** Passed straight through to the picker, which filters and columns by them. */
  campaigns?: { id: string; name: string; status: string; tagged_products: number }[];
  campaignsByOffer?: Record<string, string[]>;
  placementByPlatform: Record<string, LinkPlacement>;
  /** The networks this post is actually going to. */
  platforms: string[];
  /**
   * The product this post is written around, owned by the page.
   *
   * Lifted out of here because two other things need it: filing the post into
   * a campaign carries the product with it, and the offer mode above picks it
   * automatically. A choice that three surfaces read is not this component's
   * private state.
   */
  offer: OfferChoice | null;
  onOffer: (next: OfferChoice | null) => void;
  /**
   * How this post gets its product, in the campaign's own three words.
   *
   * The same choice a campaign makes, made for one post: fit the content
   * automatically, use the one you pick, or attach nothing. Publish had only
   * the middle one, which meant the operator did the matching in their head
   * against a catalogue of hundreds.
   */
  mode: OfferMode;
  onMode: (next: OfferMode) => void;
  /** What smart match came back with, best first. */
  suggestion: OfferSuggestion | null;
  suggesting: boolean;
  onSuggest: () => void;
  caption: string;
  firstComment: string;
  /** The sentence that leads the caption. Editable: see below. */
  disclosure: string;
  /**
   * Whether that sentence is added at all.
   *
   * Off by default, and what it turns off is a legal safeguard - so it is a
   * decision somebody makes rather than one this makes for them. The wording
   * stays editable while it is off, because the two are separate choices and
   * losing what was typed on switching it off would be its own small cruelty.
   */
  disclose: boolean;
  onCaption: (next: string) => void;
  onFirstComment: (next: string) => void;
  onDisclosure: (next: string) => void;
  onDisclose: (next: boolean) => void;
  /** Networks among the chosen that accept a first comment at all. */
  commentPlatforms: string[];
  /** Why none will carry one - the plan or the engine, already worded. */
  commentUnavailableReason?: string | null;
  disabled?: boolean;
}) {
  const t = useT();
  const [picking, setPicking] = useState(false);
  /** The catalogue by offer, so a ranked match can become a real choice. */
  const choices = useMemo(() => {
    const byId = new Map<string, OfferChoice>();
    for (const choice of offerChoices(products)) byId.set(choice.offer_id, choice);
    return byId;
  }, [products]);

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
    const body = disclose ? withDisclosure(caption, disclosure) : caption;
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
    if (disclose) onCaption(withDisclosure(caption, disclosure));
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
      <div className="affiliate-head">
        <span className="affiliate-head-label">{t("publish.affiliateLink")}</span>
        {mode === "manual" && (
          <Button
            variant={offer ? "quiet" : "secondary"}
            size="sm"
            disabled={disabled}
            onClick={() => setPicking(true)}
          >{offer ? t("publish.changeProduct") : t("publish.chooseProductAction")}</Button>
        )}
      </div>

      {/* How this post gets its product, in the same three words a campaign
          uses for the same decision. Publish only ever had the middle one,
          which left the operator matching a catalogue of hundreds by eye. */}
      <div className="campaign-mode-options" role="radiogroup"
        aria-label="Products for this post">
        <button type="button" role="radio" disabled={disabled}
          aria-checked={mode === "smart"}
          className={mode === "smart" ? "active" : ""}
          onClick={() => { onMode("smart"); onSuggest(); }}>
          <strong>Smart match</strong>
          <small>Fit content automatically</small>
        </button>
        <button type="button" role="radio" disabled={disabled}
          aria-checked={mode === "manual"}
          className={mode === "manual" ? "active" : ""}
          onClick={() => onMode("manual")}>
          <strong>One product</strong>
          <small>Use one offer everywhere</small>
        </button>
        <button type="button" role="radio" disabled={disabled}
          aria-checked={mode === "none"}
          className={mode === "none" ? "active" : ""}
          onClick={() => { onMode("none"); onOffer(null); }}>
          <strong>No products</strong>
          <small>Organic posts only</small>
        </button>
      </div>

      {mode === "smart" && (
        <div className="affiliate-smart">
          <div className="affiliate-smart-head">
            <small>
              {suggesting
                ? "Reading the post and ranking your products…"
                : suggestion?.advice ?? "Match this post against your products."}
            </small>
            <Button variant="quiet" size="sm" busy={suggesting} disabled={disabled}
              onClick={onSuggest}>Match again</Button>
          </div>
          {/* What it read, said out loud. A ranking is only as good as what it
              had to go on, and a caption nobody has written yet ranks on
              commission alone - which looks confident and means nothing. */}
          {suggestion && suggestion.read_from.length > 0 && (
            <small className="affiliate-note">
              Read from {suggestion.read_from.join(", ")} against{" "}
              {suggestion.candidate_scope}.
            </small>
          )}
          {suggestion && suggestion.matches.length > 0 && (
            <ul className="affiliate-suggestions">
              {suggestion.matches.slice(0, 4).map((match) => (
                <li key={match.offer_id} data-on={match.offer_id === offer?.offer_id || undefined}>
                  <button type="button"
                    // An offer ranked but no longer in the catalogue the panel
                    // was given cannot be chosen: the link and the price come
                    // from there, and attaching a name alone attaches nothing.
                    disabled={disabled || !choices.has(match.offer_id)}
                    onClick={() => onOffer(choices.get(match.offer_id) ?? null)}>
                    <span>
                      <strong>{choices.get(match.offer_id)?.name ?? match.product_name}</strong>
                      <small>{match.matched_terms.slice(0, 5).join(", ")
                        || "Nothing in the content matched this one."}</small>
                    </span>
                    <span className={`affiliate-score ${match.confidence}`}>
                      {match.score}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* The product as a card rather than a line of text: the name is what
          somebody checks they picked the right thing by, and the rate is why
          they picked it over another offer. Empty, it is a place for a product
          rather than a sentence saying there is none. */}
      {mode !== "none" && offer ? (
        <div className="affiliate-product">
          {/* Only when there is one. A Shopee export carries no image URL, so a
              placeholder here would be a grey square standing in for data the
              file cannot provide - and initials drawn from these names would
              collapse half the catalogue onto the same two letters. */}
          {offer.image_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img className="product-thumb" src={offer.image_url} alt="" loading="lazy" />
          )}
          <span className="affiliate-product-named">
            <strong>{offer.name}</strong>
            <small>{offer.network}</small>
          </span>
          {commissionLabel(offer) && (
            <span className="affiliate-rate">{commissionLabel(offer)}</span>
          )}
        </div>
      ) : mode === "manual" ? (
        <button
          type="button"
          className="affiliate-product empty"
          disabled={disabled}
          onClick={() => setPicking(true)}
        >
          <span className="affiliate-product-named">
            <strong>{t("publish.chooseProductPrompt")}</strong>
            <small>{t("publish.chooseProductHint")}</small>
          </span>
        </button>
      ) : null}

      <OfferPicker
        open={picking}
        products={products}
        campaigns={campaigns}
        campaignsByOffer={campaignsByOffer}
        chosen={offer?.offer_id ?? ""}
        onChoose={(next) => { onOffer(next); setPicking(false); }}
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

          {/* Above the two buttons, because it decides what they insert.
           *
           * It sat under them, which meant the switch that changes what "Add
           * to caption" writes came after the thing it changes: by the time
           * anybody saw the option the caption already had a disclosure in it,
           * or was missing one. A control that governs an action belongs
           * before it, and this one is off by default, so its absence is the
           * state somebody most needs to notice.
           *
           * The wording is editable and was not always. It was a fixed
           * sentence read off the tracking link, which made the one line of a
           * post carrying a legal obligation the one line that could not be
           * changed here - and it is not one sentence for everybody, since
           * wording differs by market and by each network's own rules. */}
          <div className="affiliate-disclosure-block">
            <label className="affiliate-disclosure-switch">
              <input
                type="checkbox"
                checked={disclose}
                disabled={disabled}
                onChange={(event) => onDisclose(event.target.checked)}
              />
              <span>{t("publish.discloseAffiliate")}</span>
            </label>
            <label className="affiliate-disclosure">
              <span>{t("publish.disclosure")}</span>
              <input
                type="text"
                value={disclosure}
                maxLength={500}
                // Editable while it is off: the wording and whether to use it
                // are separate choices, and clearing what somebody typed for
                // their market on the way past would be its own small loss.
                disabled={disabled}
                placeholder={DEFAULT_DISCLOSURE}
                onChange={(event) => onDisclosure(event.target.value)}
              />
            </label>
          </div>
          {/* Three states, not two. Off is the default and says what it costs;
              on with nothing written cannot disclose anything; on with wording
              says where it lands. */}
          <small className={`affiliate-note${disclose && !disclosure.trim() ? " warn" : ""}`}>
            {!disclose
              ? t("publish.disclosureOff")
              : disclosure.trim()
                ? t("publish.disclosureLeads")
                : t("publish.disclosureEmpty")}
          </small>

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
                // The network, the plan and the engine are different
                // culprits. Facebook takes a first comment; a Buffer Free
                // login does not send one, and Zernio cannot - and blaming
                // the network sent the operator investigating the wrong
                // thing.
                : commentUnavailableReason ?? t("publish.noCommentHere")}
              onClick={addToComment}
            >{t("publish.addToFirstComment")}</Button>
          </div>

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
