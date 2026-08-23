"use client";

/**
 * Filing a post written in Publish into a campaign.
 *
 * Publish and Campaigns write the same object from opposite ends. Publish is
 * where somebody crafts one post by hand and sends it; a campaign is a queue of
 * posts something else sends on a schedule. Until now the only way from the
 * first to the second was to write the post twice.
 *
 * Not the Library's picker with a different label. That one adds *media* -
 * deliberately without copy, because copy is written where campaigns write
 * copy. This adds a *post*: the caption somebody wrote, the first comment, the
 * thread, the title, and the product it was written around. Anything less and
 * the crafting is thrown away at the door, which is the thing being fixed.
 *
 * The product comes with it. A campaign that has never been told about that
 * offer would ordinarily refuse a post pinned to it - a pin chooses among a
 * campaign's products rather than around them - but a post arriving whole is
 * the case that rule reads wrong, so the request says `carry_offers` and the
 * campaign learns the product from the post. See `QueueItemCreate`.
 */

import { useCallback, useEffect, useState } from "react";

import { Dialog } from "../ui/dialog";
import { Badge } from "../ui/primitives";

type Campaign = {
  id: string;
  name: string;
  status: "draft" | "active" | "archived";
  tagged_products?: number;
};

/** Everything a crafted post carries into the queue. */
export type CraftedPost = {
  /** One or the other, the way a queue item is one or the other. */
  videoPath: string;
  imagePaths: string[];
  title: string;
  caption: string;
  firstComment: string;
  thread: string[];
  /** The product it was written around, when it was written around one. */
  offerIds: string[];
  /** The Library asset behind it, when the media came from there. */
  assetId?: string | null;
};

export function AddToCampaign({
  open,
  workspaceId,
  post,
  apiFetch,
  onClose,
  onAdded,
}: {
  open: boolean;
  workspaceId: string;
  post: CraftedPost;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onAdded: (message: string) => void;
}) {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    apiFetch(`/api/workspaces/${workspaceId}/campaigns`, { signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as { campaigns?: Campaign[]; detail?: string };
        if (!response.ok) throw new Error(payload.detail ?? "Campaigns could not be read.");
        // Archived ones are left out, as in the Library's picker: filing a post
        // into a campaign that has been put away is not a choice anybody makes
        // on purpose from here.
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
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/campaigns/${campaign.id}/queue`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            ...(post.imagePaths.length
              ? { image_paths: post.imagePaths }
              : { video_path: post.videoPath }),
            asset_id: post.assetId ?? null,
            title: post.title || null,
            body: post.caption,
            first_comment: post.firstComment || null,
            thread: post.thread,
            offer_ids: post.offerIds,
            // The post brings its product. See the note at the top.
            carry_offers: true,
          }),
        },
      );
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "The post could not be added.");
      onAdded(
        `Added to ${campaign.name}.`
        + (post.offerIds.length
          ? " Its product came with it, so the campaign can promote it too."
          : "")
        + " It is in the rotation from the next run.",
      );
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The post could not be added.");
    } finally {
      setBusy(null);
    }
  }, [post, apiFetch, workspaceId, onAdded, onClose]);

  const hasMedia = Boolean(post.videoPath || post.imagePaths.length);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add this post to a campaign"
      description={
        hasMedia
          ? "The caption, the first comment, the thread and the product all go with it."
          : "Choose the media for this post first — a campaign package needs something to post."
      }
    >
      {error && <p className="console-error" role="alert">{error}</p>}
      {!hasMedia ? (
        <p className="autopilot-empty">
          There is nothing to file yet. Pick a video or the pictures for this
          post, then add it.
        </p>
      ) : campaigns === null ? (
        <p className="autopilot-empty">Reading campaigns…</p>
      ) : campaigns.length === 0 ? (
        <p className="autopilot-empty">
          No campaigns yet. Make one in Campaigns, then this post can go in it.
        </p>
      ) : (
        <ul className="campaign-choice-list">
          {campaigns.map((campaign) => (
            <li key={campaign.id}>
              {/* The whole row is the button, the way the Library's picker
                  does it: "which campaign" is the only question here. */}
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void add(campaign)}
              >
                <span>
                  <strong>{campaign.name}</strong>
                  <small>
                    {campaign.tagged_products
                      ? `${campaign.tagged_products} ${
                        campaign.tagged_products === 1 ? "product" : "products"} tagged`
                      : "No products tagged"}
                  </small>
                </span>
                {busy === campaign.id
                  ? <Badge tone="neutral">Adding…</Badge>
                  : campaign.status === "draft"
                    ? <Badge tone="neutral">draft</Badge>
                    : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
