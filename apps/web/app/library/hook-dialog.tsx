"use client";

/**
 * Hook text, in the Library: the plain on-screen line a short video opens with, made for a whole
 * selection at once.
 *
 * Draft first. Suggestions and edits stay in this dialog as unapproved drafts; only a draft the
 * rules accept can be approved, and only an approved one is burned in (the server refuses the rest
 * too). Each render is a normal job that files its result in the Library as a new video.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { SegmentedControl } from "../ui/segmented";
import { canApprove, msToSeconds, secondsToMs, settledState } from "../../lib/hook-overlay";
import type { HookLine } from "../../lib/hook-overlay";

type Rules = {
  anchors: string[];
  presets: Record<string, unknown>;
  default_preset: string;
  max_lines: number;
  max_words_per_line: number;
};
type DraftView = { lines: HookLine[]; problems: string[]; notes: string[] };
type Phase = "idle" | "suggesting" | "queued" | "done" | "failed";

type Target = { id: string; title: string };
type Item = {
  id: string;
  title: string;
  topic: string;
  instruction: string;
  options: DraftView[];
  lines: HookLine[];
  problems: string[];
  notes: string[];
  approved: boolean;
  phase: Phase;
  message: string;
};

const PRESET_LABELS: Record<string, string> = { stroke: "Outline", box: "Box", pop: "Pop" };
const ANCHOR_LABELS: Record<string, string> = { top: "Top", middle: "Middle" };

function fresh(target: Target): Item {
  return {
    id: target.id,
    title: target.title,
    topic: target.title,
    instruction: "",
    options: [],
    lines: [],
    problems: [],
    notes: [],
    approved: false,
    phase: "idle",
    message: "",
  };
}

export function HookDialog({
  open,
  workspaceId,
  targets,
  apiFetch,
  onClose,
  onRendered,
  onError,
}: {
  open: boolean;
  workspaceId: string;
  targets: Target[];
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onRendered: () => void;
  onError: (message: string) => void;
}) {
  const base = `/api/workspaces/${workspaceId}/hook-overlays`;
  const [rules, setRules] = useState<Rules | null>(null);
  const [items, setItems] = useState<Item[]>(() => targets.map(fresh));
  const [preset, setPreset] = useState("stroke");
  const [anchor, setAnchor] = useState("top");
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!open || rules) return;
    void apiFetch(`${base}/rules`)
      .then((response) => (response.ok ? response.json() : null))
      .then((next: Rules | null) => {
        if (!alive.current || !next) return;
        setRules(next);
        setPreset(next.default_preset);
      })
      .catch(() => undefined);
  }, [open, rules, apiFetch, base]);

  const patch = useCallback((id: string, change: Partial<Item>) => {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...change } : item)));
  }, []);

  const fail = useCallback(
    (reason: unknown, fallback: string) => onError(reason instanceof Error ? reason.message : fallback),
    [onError],
  );

  async function post<T>(path: string, body: unknown): Promise<T> {
    const response = await apiFetch(`${base}${path}`, { method: "POST", body: JSON.stringify(body) });
    const data = (await response.json()) as T & { detail?: string };
    if (!response.ok) throw new Error(data.detail ?? "The request failed.");
    return data;
  }

  /** Ask for options for one video, or a revision of its current hook when there is an instruction. */
  async function suggest(item: Item) {
    patch(item.id, { phase: "suggesting", message: "" });
    try {
      const existing =
        item.instruction.trim() && item.lines.length
          ? { asset_id: item.id, lines: item.lines, preset, anchor }
          : undefined;
      const { options } = await post<{ options: DraftView[] }>("/propose", {
        asset_id: item.id,
        topic: item.topic.trim() || item.title,
        options: 3,
        preset,
        anchor,
        existing,
        instruction: existing ? item.instruction.trim() : "",
      });
      if (!alive.current) return;
      const first = options[0];
      patch(item.id, {
        options,
        lines: first?.lines ?? [],
        problems: first?.problems ?? [],
        notes: first?.notes ?? [],
        approved: false,
        phase: "idle",
        message: first ? "" : "No suggestion passed the short-form rules. Try a clearer topic.",
      });
    } catch (reason) {
      patch(item.id, { phase: "idle" });
      fail(reason, "Could not get suggestions.");
    }
  }

  async function suggestAll() {
    setBusy(true);
    for (const item of items) {
      if (!alive.current) break;
      await suggest(item);
    }
    if (alive.current) setBusy(false);
  }

  /** Judge the lines against the rules whenever they are edited. Editing withdraws approval. */
  async function edit(item: Item, lines: HookLine[]) {
    patch(item.id, { lines, approved: false });
    try {
      const view = await post<DraftView>("/check", { asset_id: item.id, lines, preset, anchor });
      if (alive.current) patch(item.id, { problems: view.problems, notes: view.notes });
    } catch {
      // The next edit or the render re-checks; the server is the judge either way.
    }
  }

  function setLine(item: Item, index: number, change: Partial<HookLine>) {
    void edit(
      item,
      item.lines.map((line, at) => (at === index ? { ...line, ...change } : line)),
    );
  }

  async function renderApproved() {
    const chosen = items.filter((item) => item.approved && item.phase !== "queued");
    if (!chosen.length) return;
    setBusy(true);
    await Promise.all(
      chosen.map(async (item) => {
        try {
          patch(item.id, { phase: "queued", message: "Rendering..." });
          const queued = await post<{ id: string }>("/render", {
            draft: { asset_id: item.id, lines: item.lines, preset, anchor, approved: true, source: "manual" },
            title: `${item.title} (hook)`,
          });
          for (let attempt = 0; attempt < 150 && alive.current; attempt++) {
            await new Promise((resolve) => window.setTimeout(resolve, 2000));
            const response = await apiFetch(`${base}/jobs/${queued.id}`);
            if (!response.ok) continue;
            const state = settledState(await response.json());
            if (state === "done") {
              patch(item.id, { phase: "done", message: "Filed in the Library as a new video." });
              onRendered();
              return;
            }
            if (state === "failed") throw new Error("The render failed.");
          }
          if (alive.current) throw new Error("The render is taking too long; check the Library later.");
        } catch (reason) {
          patch(item.id, { phase: "failed", message: reason instanceof Error ? reason.message : "Render failed." });
        }
      }),
    );
    if (alive.current) setBusy(false);
  }

  const presets = Object.keys(rules?.presets ?? { stroke: 1, box: 1, pop: 1 }).map((id) => ({
    value: id,
    label: PRESET_LABELS[id] ?? id,
  }));
  const anchors = (rules?.anchors ?? ["top", "middle"]).map((id) => ({ value: id, label: ANCHOR_LABELS[id] ?? id }));
  const approvedCount = items.filter((item) => item.approved).length;

  return (
    <Dialog
      open={open}
      title="Hook text"
      description="The short line a video opens with. Suggest, edit, approve, then render. Nothing is burned in until you approve it."
      onClose={onClose}
      size="wide"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button variant="secondary" disabled={busy || !items.length} onClick={() => void suggestAll()}>
            Suggest for all {items.length}
          </Button>
          <Button variant="primary" disabled={busy || approvedCount === 0} onClick={() => void renderApproved()}>
            Render {approvedCount} approved
          </Button>
        </>
      }
    >
      <div style={{ display: "grid", gap: 14 }}>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <SegmentedControl label="Look" value={preset} options={presets} onChange={setPreset} />
          <SegmentedControl label="Position" value={anchor} options={anchors} onChange={setAnchor} />
        </div>
        {items.map((item) => (
          <section key={item.id} style={{ display: "grid", gap: 8, borderTop: "1px solid var(--border, #ddd)", paddingTop: 10 }}>
            <strong>{item.title}</strong>
            <label>
              What the video is about
              <input
                value={item.topic}
                maxLength={300}
                onChange={(event) => patch(item.id, { topic: event.target.value })}
              />
            </label>
            <div>
              <Button
                variant="secondary"
                size="sm"
                disabled={busy || item.phase === "suggesting"}
                onClick={() => void suggest(item)}
              >
                {item.phase === "suggesting" ? "Thinking..." : item.lines.length ? "New suggestions" : "Suggest"}
              </Button>
              {item.options.length > 1 &&
                item.options.map((option, at) => (
                  <Button
                    key={at}
                    variant="secondary"
                    size="sm"
                    onClick={() =>
                      patch(item.id, {
                        lines: option.lines,
                        problems: option.problems,
                        notes: option.notes,
                        approved: false,
                      })
                    }
                  >
                    Option {at + 1}
                  </Button>
                ))}
            </div>
            {item.lines.map((line, index) => (
              <div key={`${index}:${line.start_ms}:${line.end_ms}`} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
                <label style={{ flex: "1 1 240px" }}>
                  Line {index + 1}
                  <input
                    value={line.text}
                    maxLength={200}
                    onChange={(event) => setLine(item, index, { text: event.target.value })}
                  />
                </label>
                <label>
                  From (s)
                  <input
                    style={{ width: 70 }}
                    defaultValue={msToSeconds(line.start_ms)}
                    inputMode="decimal"
                    onBlur={(event) => setLine(item, index, { start_ms: secondsToMs(event.target.value) })}
                  />
                </label>
                <label>
                  To (s)
                  <input
                    style={{ width: 70 }}
                    defaultValue={msToSeconds(line.end_ms)}
                    inputMode="decimal"
                    onBlur={(event) => setLine(item, index, { end_ms: secondsToMs(event.target.value) })}
                  />
                </label>
              </div>
            ))}
            {item.problems.length > 0 && (
              <ul role="alert" style={{ margin: 0, paddingLeft: 18 }}>
                {item.problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            )}
            {item.notes.map((note) => (
              <small key={note}>{note}</small>
            ))}
            {item.lines.length > 0 && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
                <label style={{ flex: "1 1 240px" }}>
                  Ask the AI to change it
                  <input
                    value={item.instruction}
                    maxLength={300}
                    placeholder="Shorter, more curious, mention the price..."
                    onChange={(event) => patch(item.id, { instruction: event.target.value })}
                  />
                </label>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy || !item.instruction.trim() || item.phase === "suggesting"}
                  onClick={() => void suggest(item)}
                >
                  Revise
                </Button>
                <label>
                  <input
                    type="checkbox"
                    checked={item.approved}
                    disabled={!canApprove(item.lines, item.problems) || item.phase === "queued" || item.phase === "done"}
                    onChange={(event) => patch(item.id, { approved: event.target.checked })}
                  />{" "}
                  Approve
                </label>
              </div>
            )}
            {item.message && <small role="status">{item.message}</small>}
          </section>
        ))}
        {!items.length && <p>Select one or more videos in the Library first.</p>}
      </div>
    </Dialog>
  );
}
