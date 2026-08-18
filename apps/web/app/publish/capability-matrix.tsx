"use client";

/**
 * Which engine can do what, on which network, in one table.
 *
 * The question this answers came up every time a post was refused after it had
 * been written: can this engine post a gallery here, will the link be tappable,
 * can anything go after the post. The answers lived in four frozen sets and a
 * placement policy, and the interface only ever gave them as a refusal.
 *
 * Two axes that must not be collapsed, because a capability needs both:
 *
 *   the *network* decides what exists  - Instagram has carousels, and no link
 *                                        in one of its posts is clickable;
 *   the *engine*  decides what is reachable - Buffer posts no carousel at all,
 *                                        and is the only engine that can put
 *                                        text after a post.
 *
 * So a cell is the pair. "Instagram, carousel" reads as a capability the
 * network has and no engine here can reach, which is exactly the trap this is
 * meant to spring before somebody composes into it.
 *
 * Every value is served by the API from the declarations delivery enforces.
 * Nothing here is a second copy of the rules: a table typed into a frontend is
 * accurate the day it is written and wrong from the next engine onwards.
 */

import { useEffect, useState } from "react";
import { Check, Info, Link2, Link2Off, Minus } from "lucide-react";

import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { PlatformIcon, type PublishingPlatform } from "../publishing-icons";

type LinkPlacement = {
  id: string;
  label: string;
  clickable: boolean;
  detail: string;
};

type PlatformRow = {
  id: string;
  label: string;
  engines: string[];
  link: LinkPlacement;
  carousel_limit: number;
  carousel_engines: string[];
  thread: boolean;
  first_comment: boolean;
  follow_up_label: string | null;
  follow_up_engines: string[];
  topic_engines: string[];
  caption_limit: number;
  title_limit: number | null;
  max_video_width: number | null;
  needs: string | null;
  post_types: { id: string; label: string; detail: string }[];
};

type EngineRow = {
  id: string;
  label: string;
  tagline: string;
  platforms: string[];
  carousel_platforms: string[];
  topic_platforms: string[];
  follow_up_platforms: string[];
  requires_public_media: boolean;
  ingests_media_url: boolean;
  media_note: string;
  supports_approval: boolean;
};

export type CapabilityMatrix = {
  engines: EngineRow[];
  platforms: PlatformRow[];
  limits: {
    max_thread_parts: number;
    carousel: Record<string, number>;
    max_video_width: Record<string, number>;
  };
  notes: string[];
};

/** The marks a cell can carry, named once so the legend cannot drift from them. */
const MARKS = [
  { id: "carousel", glyph: "▦", label: "Photo carousel" },
  { id: "follow_up", glyph: "↳", label: "First comment or thread reply" },
  { id: "topic", glyph: "#", label: "Topic tag" },
] as const;

export function CapabilityMatrixButton({ workspaceId }: { workspaceId: string }) {
  const [open, setOpen] = useState(false);
  const [matrix, setMatrix] = useState<CapabilityMatrix | null>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    // Read when it is first opened, not on page load: nobody pays for a table
    // they never ask for, and it cannot change while the page is up.
    if (!open || matrix || !workspaceId) return;
    let cancelled = false;
    fetch(`/api/workspaces/${workspaceId}/publishing/capabilities`)
      .then((response) => {
        if (!response.ok) throw new Error(`Could not read the table (${response.status}).`);
        return response.json();
      })
      .then((body: CapabilityMatrix) => { if (!cancelled) setMatrix(body); })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setProblem(reason instanceof Error ? reason.message : "Could not read the table.");
        }
      });
    return () => { cancelled = true; };
  }, [open, matrix, workspaceId]);

  return (
    <>
      <Button
        variant="quiet"
        size="sm"
        aria-label="What each engine can post, and where"
        title="What each engine can post, and where"
        onClick={() => setOpen(true)}
      ><Info size={14} aria-hidden="true" /></Button>

      <Dialog
        open={open}
        size="wide"
        title="What posts where"
        description="Every network against every engine. A capability needs both: the network has to have the feature and the engine has to reach it."
        onClose={() => setOpen(false)}
      >
        {problem && <p className="engine-warning" role="status">{problem}</p>}
        {!problem && !matrix && <p className="offer-picker-empty">Reading the table…</p>}
        {matrix && <MatrixTables matrix={matrix} />}
      </Dialog>
    </>
  );
}

function MatrixTables({ matrix }: { matrix: CapabilityMatrix }) {
  const engines = matrix.engines;
  return (
    <div className="capability-matrix">
      <ul className="capability-notes">
        {matrix.notes.map((note) => <li key={note}>{note}</li>)}
      </ul>

      <div className="capability-scroll">
        <table className="product-table capability-table">
          <thead>
            <tr>
              <th scope="col">Network</th>
              <th scope="col">Affiliate link</th>
              <th scope="col">After the post</th>
              <th scope="col" className="numeric">Carousel</th>
              {engines.map((engine) => (
                <th key={engine.id} scope="col" className="capability-engine" title={engine.tagline}>
                  {engine.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.platforms.map((platform) => (
              <tr key={platform.id}>
                <th scope="row">
                  <span className="capability-network">
                    <PlatformIcon platform={platform.id as PublishingPlatform} size={18} />
                    <span>
                      {platform.label}
                      {platform.needs && (
                        <small>needs a {platform.needs}</small>
                      )}
                    </span>
                  </span>
                </th>

                {/* The affiliate question, and the one the operator asked for:
                    a URL on Instagram or TikTok is text, not a link. */}
                <td>
                  <span
                    className={`capability-link ${platform.link.clickable ? "yes" : "no"}`}
                    title={platform.link.detail}
                  >
                    {platform.link.clickable
                      ? <Link2 size={13} aria-hidden="true" />
                      : <Link2Off size={13} aria-hidden="true" />}
                    {platform.link.clickable ? "In the caption" : "Profile only"}
                  </span>
                </td>

                {/* Named per network: on a thread network the reply is the next
                    post, and calling that a comment describes nothing real. */}
                <td>
                  {platform.follow_up_label ? (
                    <span className="capability-followup">
                      {platform.follow_up_label}
                      <small>{platform.follow_up_engines.length
                        ? `via ${platform.follow_up_engines
                            .map((id) => engines.find((item) => item.id === id)?.label ?? id)
                            .join(", ")}`
                        : "no engine here"}</small>
                    </span>
                  ) : <span className="capability-none">—</span>}
                </td>

                {/* The network's ceiling and whether anything can reach it. Ten
                    on Instagram with no engine behind it is the point. */}
                <td className="numeric">
                  {platform.carousel_limit ? (
                    <span className={platform.carousel_engines.length
                      ? "capability-carousel"
                      : "capability-carousel unreachable"}>
                      {platform.carousel_limit}
                      <small>{platform.carousel_engines.length
                        ? "images"
                        : "no engine"}</small>
                    </span>
                  ) : <span className="capability-none">—</span>}
                </td>

                {engines.map((engine) => {
                  const reaches = platform.engines.includes(engine.id);
                  const marks = [
                    engine.carousel_platforms.includes(platform.id) ? "carousel" : null,
                    engine.follow_up_platforms.includes(platform.id) ? "follow_up" : null,
                    engine.topic_platforms.includes(platform.id) ? "topic" : null,
                  ].filter(Boolean) as string[];
                  return (
                    <td key={engine.id} className="capability-cell">
                      {reaches ? (
                        <span className="capability-yes">
                          <Check size={13} aria-hidden="true" />
                          {marks.length > 0 && (
                            <span className="capability-marks">
                              {marks.map((mark) => {
                                const found = MARKS.find((item) => item.id === mark)!;
                                return (
                                  <b key={mark} title={`${found.label} on ${platform.label}`}>
                                    {found.glyph}
                                  </b>
                                );
                              })}
                            </span>
                          )}
                        </span>
                      ) : (
                        <Minus className="capability-no" size={13} aria-label="Not supported" />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="capability-legend">
        {MARKS.map((mark) => (
          <li key={mark.id}><b>{mark.glyph}</b> {mark.label}</li>
        ))}
        <li><Minus size={12} aria-hidden="true" /> Network not offered by that engine</li>
      </ul>

      {/* What an engine needs of the machine, which is not a per-network fact
          and so has no place in the grid above. */}
      <h3 className="capability-heading">What each engine needs</h3>
      <div className="capability-scroll">
        <table className="product-table capability-table">
          <thead>
            <tr>
              <th scope="col">Engine</th>
              <th scope="col" className="numeric">Networks</th>
              <th scope="col">Media</th>
              <th scope="col">Approval</th>
              <th scope="col">Notes</th>
            </tr>
          </thead>
          <tbody>
            {engines.map((engine) => (
              <tr key={engine.id}>
                <th scope="row">
                  <strong>{engine.label}</strong>
                  <small>{engine.tagline}</small>
                </th>
                <td className="numeric">{engine.platforms.length}</td>
                <td>
                  {engine.requires_public_media
                    ? "Must be hosted publicly"
                    : "Uploads the file directly"}
                  {!engine.ingests_media_url && (
                    <small>Cannot fetch a URL, so it always needs the file</small>
                  )}
                </td>
                <td>{engine.supports_approval ? "Yes" : "—"}</td>
                <td className="capability-note-cell">{engine.media_note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="capability-footnote">
        A thread runs to {matrix.limits.max_thread_parts} parts.
        {" "}Buffer&rsquo;s first comment also depends on the plan its account is on.
      </p>
    </div>
  );
}
