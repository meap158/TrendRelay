"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { SegmentedControl } from "../ui/segmented";
import { Badge } from "../ui/primitives";
import { handoffPath, type VersionedAsset } from "../../lib/media-rules";
import {
  CAMPAIGN_IMPORT_CHUNK_SIZE,
  campaignImportChunks,
} from "../../lib/campaign-import";
/**
 * The shape this needs: whatever `handoffPath` reads, plus enough to name it.
 *
 * `VersionedAsset` is that first part already, and reusing it means this cannot
 * disagree with the function it feeds. The Library page calls its own type
 * `Asset` and the publish composer exports a `LibraryAsset`; both satisfy this
 * structurally, and neither is this component's to depend on.
 */
type ChosenAsset = VersionedAsset & {
  /** The Library id, so the queue item links back to the asset it came from -
      which is what lets the campaign show its thumbnail and preview. Without
      it the post arrives as a bare path the panel has no still for. */
  id: string;
  title: string;
  media_kind: "video" | "audio" | "image";
};

/**
 * Put chosen media into a campaign, without leaving the Library.
 *
 * This used to be "Plan campaign", and it navigated: one clip became
 * `/campaigns?add=<id>`, which opened the campaign workspace, opened its media
 * picker, and pre-selected the clip - leaving somebody in a different tab, in a
 * campaign they may not have meant, with a picker open over it.
 *
 * The question being asked is smaller than that. "Which campaign does this go
 * in" has a list of answers and one click, so it is a list and a click. What it
 * is *not* is a second place to compose a post: copy is written where campaigns
 * write copy, and a package added here arrives without any, which the queue
 * already understands and marks.
 */

/**
 * The most pictures one carousel can hold.
 *
 * The queue's own ceiling, so the choice is refused here rather than by a
 * request somebody has already committed to. Networks cap lower - ten on
 * Instagram, thirty-five on TikTok - and the campaign settles that per
 * destination when it composes; this is only the shape the package can be.
 */
const MAX_CAROUSEL = 20;

type Campaign = {
  id: string;
  name: string;
  status: "draft" | "active" | "archived";
  tagged_products?: number;
};

export function CampaignPicker({
  open,
  workspaceId,
  assets,
  assetIds,
  apiFetch,
  onClose,
  onAdded,
}: {
  open: boolean;
  workspaceId: string;
  /** Loaded details for labels and the optional small image-carousel flow. */
  assets: ChosenAsset[];
  /** Every selected row, including filter-wide selections not loaded in the grid. */
  assetIds: string[];
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onAdded: (message: string) => void;
}) {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ campaignId: string; added: number } | null>(null);
  /**
   * Whether several pictures become several posts or one carousel.
   *
   * They became several, always, because the picker sends one request per
   * asset and never asked. Five pictures chosen together are usually five
   * views of one thing, and filing them as five posts spends the queue five
   * times to say it.
   *
   * Separate stays the default: it is what this did before, and it is the
   * answer that cannot be wrong - a carousel filed by mistake has to be taken
   * apart by hand, while five posts merged by mistake never happened.
   */
  const [shape, setShape] = useState<"each" | "carousel">("each");
  const count = assetIds.length;
  /**
   * Whether a carousel is even a choice here.
   *
   * Every one of them has to be a picture - a video cannot be a frame of a
   * carousel and the API refuses the mixture - and there has to be more than
   * one, because a carousel of one is a post.
   */
  const canCarousel = count > 1
    && count <= MAX_CAROUSEL
    && assets.length === count
    && assets.every((asset) => asset.media_kind === "image");
  const asCarousel = canCarousel && shape === "carousel";

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    apiFetch(`/api/workspaces/${workspaceId}/campaigns`, { signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as { campaigns?: Campaign[]; detail?: string };
        if (!response.ok) throw new Error(payload.detail ?? "Campaigns could not be read.");
        // Archived ones are left out: adding media to a campaign that has been
        // put away is a choice nobody makes on purpose from here.
        setCampaigns((payload.campaigns ?? []).filter((item) => item.status !== "archived"));
        setError(null);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Campaigns could not be read.");
      });
    return () => controller.abort();
  }, [open, apiFetch, workspaceId]);

  const close = useCallback(() => {
    setProgress(null);
    setShape("each");
    setError(null);
    onClose();
  }, [onClose]);

  const add = useCallback(async (campaign: Campaign) => {
    setBusy(campaign.id);
    setError(null);
    let added = progress?.campaignId === campaign.id ? progress.added : 0;
    try {
      // One carousel is one package, so it is one request carrying every
      // path in the order they were picked - which is the order they are
      // swiped. The queue item points at the first picture for its identity:
      // one row standing for several files still needs a thumbnail to draw and
      // something for the rest window to recognise, and the first is the one
      // somebody will see on the card.
      if (asCarousel) {
        const item = {
          asset_id: assets[0].id,
          title: assets[0].title,
          image_paths: assets.map(handoffPath),
        };
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${campaign.id}/queue`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(item),
          },
        );
        if (!response.ok) {
          const payload = (await response.json()) as { detail?: string };
          throw new Error(payload.detail ?? `${item.title} could not be added.`);
        }
        added = count;
      } else {
        // A filter-wide selection can contain thousands of ids while the grid
        // intentionally holds only one page of full asset objects. Resolve and
        // insert those ids server-side in bounded chunks: a handful of atomic
        // requests instead of loading every object and issuing one write per
        // post. Chunks remain sequential so queue order is deterministic.
        for (const chunk of campaignImportChunks(assetIds, added)) {
          const response = await apiFetch(
            `/api/workspaces/${workspaceId}/campaigns/${campaign.id}/queue/assets`,
            {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ asset_ids: chunk }),
            },
          );
          const payload = (await response.json()) as { added?: number; detail?: string };
          if (!response.ok) {
            throw new Error(payload.detail ?? "The selected media could not be added.");
          }
          if (payload.added !== chunk.length) {
            throw new Error("The campaign did not confirm the complete media batch.");
          }
          added += chunk.length;
          setProgress({ campaignId: campaign.id, added });
        }
      }
      onAdded(
        (asCarousel
          ? `A carousel of ${assets.length} pictures added to ${campaign.name}.`
          : `${count.toLocaleString()} ${count === 1 ? "post" : "posts"} added to ${campaign.name}.`)
        + " Write their copy in the campaign to put them in the rotation.",
      );
      close();
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "The media could not be added.";
      setError(added > 0 && added < count
        ? `${added.toLocaleString()} of ${count.toLocaleString()} posts were added. ${detail} Choose the same campaign to continue with the remaining posts.`
        : detail);
    } finally {
      setBusy(null);
    }
  }, [assetIds, assets, asCarousel, apiFetch, workspaceId, onAdded, close, count, progress]);


  return (
    <Dialog
      open={open}
      title="Add to campaign"
      description={count === 1
        ? assets[0]?.title ?? "1 selected item"
        : asCarousel
          ? `${count} pictures will be added as one carousel.`
          : `${count} items will be added as ${count} posts.`}
      onClose={busy ? () => undefined : close}
      footer={<Button variant="quiet" disabled={busy !== null} onClick={close}>Cancel</Button>}
    >
      {/* Asked before the campaign, because it changes what is being filed
          rather than where it goes - and answered in the consequence rather
          than the jargon: "5 posts" and "1 carousel" are the two outcomes. */}
      {canCarousel && (
        <div className="campaign-shape-choice">
          <SegmentedControl
            value={shape}
            options={[
              { value: "each" as const, label: `${count} posts` },
              { value: "carousel" as const, label: "One carousel" },
            ]}
            onChange={setShape}
            label="How these pictures are filed"
          />
          <small>{asCarousel
            ? "One post, swiped in the order you picked them."
            : "One post each, in the order you picked them."}</small>
        </div>
      )}
      {/* Said rather than silently filed as separate posts: somebody who picked
          twenty-five pictures meant them to go together. */}
      {count > MAX_CAROUSEL
        && assets.length === count
        && assets.every((asset) => asset.media_kind === "image") && (
        <p className="voice-note">
          A carousel holds {MAX_CAROUSEL} pictures. These will be added as
          {" "}{count} separate posts.
        </p>
      )}

      {error && <p className="voice-note problem" role="alert">{error}</p>}

      {campaigns === null && !error && <p className="voice-note">Reading campaigns…</p>}

      {campaigns?.length === 0 && (
        <p className="voice-note">
          No campaigns yet. Create one in Campaigns, then add media to it from here.
        </p>
      )}

      {campaigns && campaigns.length > 0 && (
        <ul className="campaign-choice-list">
          {campaigns.map((campaign) => (
            <li key={campaign.id}>
              {/* The whole row is the button. "Which campaign" is the only
                  question here, so choosing one is the only thing to do and
                  it should not need aiming at a control inside the row. */}
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void add(campaign)}
              >
                <span>
                  <strong>{campaign.name}</strong>
                  <small>
                    {campaign.tagged_products
                      ? `${campaign.tagged_products} ${campaign.tagged_products === 1 ? "product" : "products"} tagged`
                      : "No products tagged"}
                  </small>
                </span>
                {busy === campaign.id
                  ? <Badge tone="neutral">
                    {count > CAMPAIGN_IMPORT_CHUNK_SIZE
                      ? `Adding ${(progress?.campaignId === campaign.id ? progress.added : 0).toLocaleString()} / ${count.toLocaleString()}…`
                      : "Adding…"}
                  </Badge>
                  : campaign.status === "active"
                    ? <Badge tone="good">Running</Badge>
                    : <Badge tone="neutral">Draft</Badge>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
