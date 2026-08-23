"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
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
      // One request per package, in order, because each is its own queue item
      // and the API takes them singly. Sequential rather than parallel: twenty
      // at once is twenty writes racing for the same position counter, and the
      // order somebody chose is the order they should arrive in.
      for (const asset of assets) {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/campaigns/${campaign.id}/queue`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(
              asset.media_kind === "image"
                ? { image_paths: [handoffPath(asset)] }
                : { video_path: handoffPath(asset) },
            ),
          },
        );
        if (!response.ok) {
          const payload = (await response.json()) as { detail?: string };
          throw new Error(payload.detail ?? `${asset.title} could not be added.`);
        }
      }
      onAdded(
        `${assets.length} ${assets.length === 1 ? "post" : "posts"} added to ${campaign.name}.`
        + " Write their copy in the campaign to put them in the rotation.",
      );
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The media could not be added.");
    } finally {
      setBusy(null);
    }
  }, [assets, apiFetch, workspaceId, onAdded, onClose]);

  const count = assets.length;

  return (
    <Dialog
      open={open}
      title="Add to campaign"
      description={count === 1
        ? assets[0]?.title
        : `${count} items will be added as ${count} posts.`}
      onClose={onClose}
      footer={<Button variant="quiet" onClick={onClose}>Cancel</Button>}
    >
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
