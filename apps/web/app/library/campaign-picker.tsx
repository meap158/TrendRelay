"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { SegmentedControl } from "../ui/segmented";
import { Badge } from "../ui/primitives";
import { handoffPath, type VersionedAsset } from "../../lib/media-rules";
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
  apiFetch,
  onClose,
  onAdded,
}: {
  open: boolean;
  workspaceId: string;
  /** What to add. One from the panel, or everything ticked in the grid. */
  assets: ChosenAsset[];
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onAdded: (message: string) => void;
}) {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
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
  const count = assets.length;
  /**
   * Whether a carousel is even a choice here.
   *
   * Every one of them has to be a picture - a video cannot be a frame of a
   * carousel and the API refuses the mixture - and there has to be more than
   * one, because a carousel of one is a post.
   */
  const canCarousel = count > 1
    && count <= MAX_CAROUSEL
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

  const add = useCallback(async (campaign: Campaign) => {
    setBusy(campaign.id);
    setError(null);
    try {
      // One carousel is one package, so it is one request carrying every
      // path in the order they were picked - which is the order they are
      // swiped. The queue item points at the first picture for its identity:
      // one row standing for several files still needs a thumbnail to draw and
      // something for the rest window to recognise, and the first is the one
      // somebody will see on the card.
      const packages = asCarousel
        ? [{
          asset_id: assets[0].id,
          title: assets[0].title,
          image_paths: assets.map(handoffPath),
        }]
        : assets.map((asset) => ({
          // The identity and the name, both of which the queue item keeps
          // and neither of which the file path carries.
          //
          // `asset_id` is how the scheduler resolves the newest rendered
          // cut, how the rest window recognises the same clip queued
          // twice, and how the timeline draws a thumbnail. `title` is what
          // that timeline shows - without it every row added this way read
          // "Untitled campaign video", which is what sent somebody looking
          // at this flow in the first place.
          asset_id: asset.id,
          title: asset.title,
          ...(asset.media_kind === "image"
            ? { image_paths: [handoffPath(asset)] }
            : { video_path: handoffPath(asset) }),
        }));

      // One request per package, in order, because each is its own queue item
      // and the API takes them singly. Sequential rather than parallel: twenty
      // at once is twenty writes racing for the same position counter, and the
      // order somebody chose is the order they should arrive in.
      for (const item of packages) {
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
      }
      onAdded(
        (asCarousel
          ? `A carousel of ${assets.length} pictures added to ${campaign.name}.`
          : `${packages.length} ${packages.length === 1 ? "post" : "posts"} added to ${campaign.name}.`)
        + " Write their copy in the campaign to put them in the rotation.",
      );
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The media could not be added.");
    } finally {
      setBusy(null);
    }
  }, [assets, asCarousel, apiFetch, workspaceId, onAdded, onClose]);


  return (
    <Dialog
      open={open}
      title="Add to campaign"
      description={count === 1
        ? assets[0]?.title
        : asCarousel
          ? `${count} pictures will be added as one carousel.`
          : `${count} items will be added as ${count} posts.`}
      onClose={onClose}
      footer={<Button variant="quiet" onClick={onClose}>Cancel</Button>}
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
      {count > MAX_CAROUSEL && assets.every((asset) => asset.media_kind === "image") && (
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
                  ? <Badge tone="neutral">Adding…</Badge>
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
