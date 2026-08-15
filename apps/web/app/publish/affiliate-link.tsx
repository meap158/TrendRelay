"use client";

/**
 * Attaching a tracking link to a post, and putting it where it works.
 *
 * The composer had a first-comment field and no way to reach Attribution, so
 * the only route was to open another page, copy a code, and paste it back. The
 * disclosure that has to accompany it was left to memory.
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
import { SearchSelect } from "../ui/search-select";
import { useT } from "../i18n-provider";
import { withDisclosure } from "../../lib/publish-rules";

export type TrackingLink = {
  id: string;
  code: string;
  url: string;
  campaign_id: string;
  product_id: string | null;
  destination_host: string;
  disclosure: string;
  status: string;
  clicks: number;
};

export type LinkPlacement = { placement: string; reason: string };

export function AffiliateLink({
  links,
  placementByPlatform,
  platforms,
  caption,
  firstComment,
  onCaption,
  onFirstComment,
  commentPlatforms,
  disabled,
}: {
  links: TrackingLink[];
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
  const active = links.filter((item) => item.status === "active");
  const [chosen, setChosen] = useState("");
  const link = active.find((item) => item.id === chosen) ?? null;

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
    if (!link) return;
    const body = withDisclosure(caption, link.disclosure);
    onCaption(body.includes(link.url) ? body : `${body.trimEnd()}\n\n${link.url}`);
  }

  function addToComment() {
    if (!link) return;
    // The disclosure still goes in the caption, not the comment. In a comment it
    // discloses nothing to a reader who never opens the comments.
    onCaption(withDisclosure(caption, link.disclosure));
    onFirstComment(
      firstComment.includes(link.url)
        ? firstComment
        : `${firstComment.trim()}${firstComment.trim() ? "\n" : ""}${link.url}`.trim(),
    );
  }

  if (!active.length) {
    return (
      <div className="affiliate-link empty">
        <span>{t("publish.noTrackingLinks")}</span>
      </div>
    );
  }

  return (
    <div className="affiliate-link">
      <label>{t("publish.affiliateLink")}
        <SearchSelect
          value={chosen}
          disabled={disabled}
          onChange={setChosen}
          placeholder={t("publish.chooseTrackingLink")}
          searchPlaceholder="Search links, products, or destinations…"
          options={active.map((item) => ({
            value: item.id,
            label: item.code,
            description: `${item.destination_host}${item.clicks > 0
              ? ` · ${t("publish.linkClicks", { count: item.clicks })}` : ""}`,
            keywords: `${item.product_id ?? ""} ${item.campaign_id}`,
          }))}
        />
      </label>

      {link && (
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
            {t("publish.disclosureGoesFirst", { disclosure: link.disclosure })}
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
