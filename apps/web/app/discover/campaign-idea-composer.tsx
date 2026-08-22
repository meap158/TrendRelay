"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { Lightbulb, X } from "lucide-react";

import {
  buildCampaignIdea,
  campaignSignalKind,
  type DiscoverySeed,
} from "../../lib/discovery-ideas";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "The campaign could not be created.");
  return body;
}
export function CampaignIdeaComposer({
  seeds,
  workspaceId,
  canCreate,
  apiFetch,
  onRemove,
  onClear,
}: {
  seeds: DiscoverySeed[];
  workspaceId: string;
  canCreate: boolean;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null);
  const idea = useMemo(() => buildCampaignIdea(seeds), [seeds]);

  if (!seeds.length) return null;

  async function createCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !canCreate) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const body = await json<{ campaign: { id: string; name: string } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/campaigns`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: form.get("name"),
            objective: form.get("objective"),
            audience: form.get("audience"),
            markets: String(form.get("markets") ?? "")
              .split(",").map((item) => item.trim()).filter(Boolean),
            languages: String(form.get("languages") ?? "")
              .split(",").map((item) => item.trim()).filter(Boolean),
            // The evidence itself, not just the sentence synthesised from it.
            // Without this the campaign cannot answer "why this?" a week later,
            // and the signal cannot be watched, refreshed, or retired as it
            // moves — everything the basket knew died at this click.
            signals: seeds.map((seed) => ({
              external_id: seed.id,
              // News is a topic signal to the Campaign model: it describes
              // something to make content about, not a social post we own.
              // Keep `story` in the basket UI so the operator can distinguish
              // the evidence, but never send a kind the durable signal model
              // rejects.
              kind: campaignSignalKind(seed),
              label: seed.label,
              provider: seed.source,
              source_url: seed.url,
              region: seed.region,
              evidence: seed.evidence,
              tags: seed.tags,
              // Shared across the basket: the angles were proposed from all of
              // it together, so attributing them to one seed would be a guess.
              angles: idea.angles,
            })),
          }),
        }),
      );
      setCreated(body.campaign);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The campaign could not be created.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {/* Named so the feed above can point at it. That link is the only
          feedback "Use" gets: this tray sits below the list and does not
          exist at all until something is in it. */}
      <aside
        id="discovery-idea-composer"
        className="discovery-idea-tray"
        aria-label="Selected campaign inspiration"
      >
        <span className="discovery-idea-count">
          <Lightbulb size={16} aria-hidden="true" />
          <strong>{seeds.length} selected</strong>
          <small>Mix topics and posts into one editable brief.</small>
        </span>
        <span className="discovery-idea-chips">
          {seeds.slice(0, 4).map((seed) => (
            <button key={seed.id} type="button" onClick={() => onRemove(seed.id)} title={`Remove ${seed.label}`}>
              <span>{seed.label}</span><X size={12} aria-hidden="true" />
            </button>
          ))}
          {seeds.length > 4 && <small>+{seeds.length - 4} more</small>}
        </span>
        <span className="discovery-idea-actions">
          <Button variant="quiet" size="sm" onClick={onClear}>Clear</Button>
          <Button variant="primary" size="sm" onClick={() => { setCreated(null); setError(""); setOpen(true); }}>
            Build campaign brief
          </Button>
        </span>
      </aside>

      <Dialog
        open={open}
        size="wide"
        title="Campaign idea from selected evidence"
        description="A transparent first draft. Review every field before creating the Campaign."
        onClose={() => setOpen(false)}
      >
        {created ? (
          <section className="discovery-idea-created" role="status">
            <Lightbulb size={24} aria-hidden="true" />
            <div>
              <strong>{created.name} is ready.</strong>
              <p>The selected evidence is now a draft Campaign, ready for media and a publish plan.</p>
            </div>
            <Link href={`/campaigns?campaign=${encodeURIComponent(created.id)}`}>Open Campaign</Link>
          </section>
        ) : (
          <form className="discovery-idea-form" onSubmit={createCampaign}>
            <div className="discovery-idea-evidence">
              <strong>Evidence used</strong>
              <ul>
                {seeds.map((seed) => (
                  <li key={seed.id}>
                    <span>{seed.kind}</span>
                    <div><strong>{seed.label}</strong><small>{seed.source} · {seed.region} · {seed.evidence}</small></div>
                  </li>
                ))}
              </ul>
            </div>

            <div className="discovery-idea-fields">
              {error && <p className="registry-error" role="alert">{error}</p>}
              <label>Campaign name
                <input name="name" required minLength={2} maxLength={160} defaultValue={idea.name} />
              </label>
              <label>Objective
                <textarea name="objective" required minLength={2} maxLength={1000} rows={5} defaultValue={idea.objective} />
              </label>
              <label>Audience
                <textarea name="audience" required minLength={2} maxLength={1000} rows={4} defaultValue={idea.audience} />
              </label>
              <div className="discovery-idea-field-row">
                <label>Markets<input name="markets" defaultValue={idea.markets.join(", ")} placeholder="US, VN" /></label>
                <label>Languages<input name="languages" defaultValue={idea.languages.join(", ")} placeholder="en, vi" /></label>
              </div>
              <div className="discovery-idea-angles">
                <strong>Starting angles</strong>
                <ul>{idea.angles.map((angle) => <li key={angle}>{angle}</li>)}</ul>
              </div>
              {!canCreate && <p className="registry-error">An owner or editor must create the Campaign.</p>}
              <Button type="submit" variant="primary" busy={busy} disabled={!workspaceId || !canCreate}>
                Create draft Campaign
              </Button>
            </div>
          </form>
        )}
      </Dialog>
    </>
  );
}
