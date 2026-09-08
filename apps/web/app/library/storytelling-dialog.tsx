"use client";

/**
 * Storytelling, in the Library: a script and a voice cut to chosen pictures.
 *
 * Beside AutoCut and opened the same way, because it is the same kind of thing:
 * many pictures in, one new video out. AutoCut cuts them to a music track's
 * beats; this cuts them to the sentences of a narration, and the script's own
 * words become the subtitles.
 *
 * The pictures are the Library selection, in the order they were picked -
 * order is the story, and a set has none. B-roll searched here lands in the
 * Library like any other media, so it is chosen the same way as everything
 * else rather than being a second kind of picture the plan has to know about.
 */

import { useCallback, useEffect, useState } from "react";

import { ActionIcon } from "../ui/action-icons";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Select } from "../ui/select";
import { SegmentedControl } from "../ui/segmented";

export type StoryAsset = { id: string; title: string; media_kind: string };

type Template = { id: string; name: string; description: string };
type Voice = { voice_id: string; name: string };
type Tile = {
  id: string;
  kind: string;
  preview_url: string;
  credit: string;
  photographer_url: string;
};

/** How the finished video is shaped. Wide leads because a narrated piece is
    watched on a wide screen; the short-form frame is offered beside it. */
const ASPECTS = [
  { value: "16:9", label: "Wide" },
  { value: "9:16", label: "Tall" },
  { value: "1:1", label: "Square" },
] as const;

export function StorytellingDialog({
  open,
  workspaceId,
  apiFetch,
  assets,
  onClose,
  onQueued,
  onError,
}: {
  open: boolean;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  /** The chosen pictures, in the order they were chosen. */
  assets: StoryAsset[];
  onClose: () => void;
  onQueued: (text: string) => void;
  onError: (text: string) => void;
}) {
  const [body, setBody] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [reading, setReading] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("explainer");
  const [aspect, setAspect] = useState<string>("16:9");
  const [subtitles, setSubtitles] = useState(true);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [busy, setBusy] = useState(false);

  const [brollReady, setBrollReady] = useState<boolean | null>(null);
  const [brollReason, setBrollReason] = useState("");
  const [query, setQuery] = useState("");
  const [brollKind, setBrollKind] = useState("image");
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [searching, setSearching] = useState(false);

  const base = workspaceId ? `/api/workspaces/${workspaceId}/storytelling` : "";

  useEffect(() => {
    if (!open || !base) return;
    let cancelled = false;
    void (async () => {
      const [templateBody, statusBody, voiceBody] = await Promise.all([
        apiFetch(`${base}/templates`).then((r) => r.json()).catch(() => ({ templates: [] })),
        apiFetch(`${base}/broll/status`).then((r) => r.json()).catch(() => null),
        apiFetch(`/api/workspaces/${workspaceId}/media/library/voice/voices`)
          .then((r) => r.json()).catch(() => ({ voices: [] })),
      ]);
      if (cancelled) return;
      setTemplates(templateBody.templates ?? []);
      setBrollReady(statusBody ? Boolean(statusBody.configured) : false);
      setBrollReason(statusBody?.reason ?? "");
      const found: Voice[] = voiceBody?.voices ?? [];
      setVoices(found);
      setVoiceId((current) => current || found[0]?.voice_id || "");
    })();
    return () => { cancelled = true; };
  }, [apiFetch, base, open, workspaceId]);

  /**
   * How the script splits, read back from the server that will split it.
   *
   * Not counted here. The sentence rules are the renderer's, and a count this
   * dialog worked out for itself would be a second opinion that quietly
   * differs - on a decomposed accent, on a full-width stop, on an ellipsis.
   */
  const outline = useCallback(async () => {
    if (!base || !body.trim()) { setLines([]); return; }
    setReading(true);
    try {
      const response = await apiFetch(`${base}/outline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body }),
      });
      const payload = await response.json();
      setLines(response.ok ? payload.lines ?? [] : []);
    } catch {
      setLines([]);
    } finally {
      setReading(false);
    }
  }, [apiFetch, base, body]);

  useEffect(() => {
    const timer = window.setTimeout(() => void outline(), 400);
    return () => window.clearTimeout(timer);
  }, [outline]);

  async function searchBroll() {
    if (!base || !query.trim()) return;
    setSearching(true);
    try {
      const params = new URLSearchParams({
        q: query,
        kind: brollKind,
        // The shape the video is, so a candidate does not arrive to be cropped
        // in half.
        orientation: aspect === "9:16" ? "portrait" : aspect === "1:1" ? "square" : "landscape",
      });
      const response = await apiFetch(`${base}/broll/search?${params}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That search did not answer.");
      setTiles(payload.results ?? []);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That search did not answer.");
    } finally {
      setSearching(false);
    }
  }

  async function importTile(tile: Tile) {
    try {
      const response = await apiFetch(`${base}/broll/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // The id and nothing else. Where the file lives is resolved on the
        // server, so this request carries no url to be pointed elsewhere.
        body: JSON.stringify({ id: tile.id, kind: tile.kind, query }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That could not be brought in.");
      onQueued(`${tile.credit}. Pick it from the Library once it is filed.`);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That could not be brought in.");
    }
  }

  async function render() {
    setBusy(true);
    try {
      const response = await apiFetch(`${base}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          body,
          asset_ids: assets.map((asset) => asset.id),
          template_id: templateId,
          voice_id: voiceId,
          aspect,
          subtitles,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That could not be queued.");
      onQueued("Reading the script, then cutting the pictures to it. It lands in your Library.");
      onClose();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That could not be queued.");
    } finally {
      setBusy(false);
    }
  }

  const ready = Boolean(body.trim() && lines.length && assets.length && voiceId && !busy);
  const short = lines.length > 0 && assets.length > 0 && assets.length < lines.length;

  return (
    <Dialog
      open={open}
      title="Storytelling"
      description="A script and a voice, cut to your chosen pictures. The cuts land where the narrator stops."
      onClose={onClose}
      size="wide"
      footer={
        <>
          <span className="story-ready">
            {!body.trim() ? "Write the script."
              : !lines.length ? "Nothing in the script to read."
                : !assets.length ? "Choose pictures in the Library first."
                  : !voiceId ? "Choose a voice."
                    : `${lines.length} ${lines.length === 1 ? "sentence" : "sentences"} over ${assets.length} ${assets.length === 1 ? "picture" : "pictures"}.`}
          </span>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready} busy={busy} onClick={() => void render()}>
            <ActionIcon name="play" />Make the video
          </Button>
        </>
      }
    >
      <div className="story-dialog">
        <section>
          <h4>The script</h4>
          <textarea
            className="story-script"
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={9}
            placeholder="What the narrator says. One sentence becomes one shot; a blank line forces a beat."
          />
          <p className="story-outline" aria-live="polite">
            {reading ? "Reading…"
              : lines.length
                ? <>Splits into <strong>{lines.length}</strong>{" "}
                  {lines.length === 1 ? "shot" : "shots"}.
                  {short && ` You picked ${assets.length}, so some repeat.`}</>
                : "Nothing to read yet."}
          </p>
        </section>

        <section>
          <h4>Pacing</h4>
          <div className="story-templates">
            {templates.map((item) => (
              <button
                key={item.id}
                type="button"
                className={templateId === item.id ? "selected" : ""}
                aria-pressed={templateId === item.id}
                onClick={() => setTemplateId(item.id)}
              >
                <strong>{item.name}</strong>
                <small>{item.description}</small>
              </button>
            ))}
          </div>
          <div className="story-shape">
            <label>Shape
              <SegmentedControl
                value={aspect}
                onChange={setAspect}
                options={ASPECTS}
                label="The shape of the finished video"
              />
            </label>
            <label>Voice
              <Select
                value={voiceId}
                onChange={(event) => setVoiceId(event.target.value)}
                aria-label="The voice that reads the script"
              >
                {voices.map((voice) => (
                  <option key={voice.voice_id} value={voice.voice_id}>{voice.name}</option>
                ))}
              </Select>
            </label>
            <label className="story-toggle">
              <input
                type="checkbox"
                checked={subtitles}
                onChange={(event) => setSubtitles(event.target.checked)}
              />
              <span>Burn in subtitles</span>
              <small>The script&apos;s own words, so nothing is transcribed back.</small>
            </label>
          </div>
        </section>

        <section>
          <h4>More pictures</h4>
          {brollReady === false ? (
            <p className="story-note">{brollReason}</p>
          ) : (
            <>
              <form className="story-search" onSubmit={(event) => {
                event.preventDefault(); void searchBroll();
              }}>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search stock photos and clips"
                  aria-label="Search stock b-roll"
                />
                <Select
                  value={brollKind}
                  onChange={(event) => setBrollKind(event.target.value)}
                  aria-label="Photos or clips"
                >
                  <option value="image">Photos</option>
                  <option value="video">Clips</option>
                </Select>
                <Button variant="secondary" size="sm" busy={searching} type="submit">
                  <ActionIcon name="search" />Search
                </Button>
              </form>
              <ul className="story-tiles story-tiles-broll">
                {tiles.map((tile) => (
                  <li key={`${tile.kind}-${tile.id}`}>
                    <button type="button" onClick={() => void importTile(tile)}
                      title={`${tile.credit} - add to the Library`}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={tile.preview_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
                      {/* The credit is on the tile because the licence asks for
                          it wherever the media is shown, and this is one of the
                          places it is shown. */}
                      <small>{tile.credit}</small>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
    </Dialog>
  );
}
