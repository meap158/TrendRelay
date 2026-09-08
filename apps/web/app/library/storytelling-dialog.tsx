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

import { useCallback, useEffect, useMemo, useState } from "react";

import { ActionIcon } from "../ui/action-icons";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Select } from "../ui/select";
import { SegmentedControl } from "../ui/segmented";

export type StoryAsset = { id: string; title: string; media_kind: string };

type Template = { id: string; name: string; description: string };
type Voice = {
  voice_id: string;
  name: string;
  /** Language ids this voice is verified in - what the filter matches on. */
  languages: string[];
  accents: string[];
  category?: string | null;
  labels?: Record<string, string> | null;
};
/** A speech model and the languages it can say. Not every model speaks every
    language, which is the whole reason this is read rather than assumed. */
type Model = { model_id: string; languages: { language_id: string; name: string }[] };
type Tile = {
  id: string;
  kind: string;
  preview_url: string;
  credit: string;
  photographer_url: string;
};

/** A language code as a reader would recognise it, falling back to the code.
    `Intl.DisplayNames` is in every browser this runs in and knows far more
    languages than a table here would. */
function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

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
  const [models, setModels] = useState<Model[]>([]);
  /** The model configured in Tools, which wins whenever it can say the words. */
  const [defaultModel, setDefaultModel] = useState("");
  const [voiceId, setVoiceId] = useState("");
  /** Which language the script is read in. Empty means every voice is offered
      and the service is left to detect it from the words. */
  const [language, setLanguage] = useState("");
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
      setVoices(voiceBody?.voices ?? []);
      setModels(voiceBody?.models ?? []);
      setDefaultModel(voiceBody?.defaults?.model_id ?? "");
      // The language the operator configured, if any. The voice is chosen from
      // whatever that leaves rather than from the whole list, which is done
      // below so that changing the language re-chooses it the same way.
      setLanguage((current) => current || (voiceBody?.defaults?.language_code ?? ""));
    })();
    return () => { cancelled = true; };
  }, [apiFetch, base, open, workspaceId]);

  /** Every language some voice on this key is verified in, in order. */
  const languages = useMemo(
    () => [...new Set(voices.flatMap((voice) => voice.languages ?? []))].sort(),
    [voices],
  );

  /** The voices that can say it. Everything when no language is chosen. */
  const speakable = useMemo(
    () => (language
      ? voices.filter((voice) => (voice.languages ?? []).includes(language))
      : voices),
    [voices, language],
  );

  /**
   * Which model reads it, which is not a free choice.
   *
   * The configured model unless it cannot say the words. On this key
   * `eleven_multilingual_v2` is the default and reads twenty-nine languages;
   * Vietnamese is not one of them, and three other models on the same key do
   * speak it. Sending the default anyway is how a language the account can
   * speak comes back refused.
   *
   * Only then does another model win. Overriding a configured choice for a
   * language it handles perfectly well would be this fix causing its own kind
   * of surprise - the operator picked that model for a reason.
   */
  const modelId = useMemo(() => {
    if (!language) return "";
    const speaks = (model: Model | undefined) =>
      Boolean(model?.languages?.some((item) => item.language_id === language));
    const configured = models.find((model) => model.model_id === defaultModel);
    if (speaks(configured)) return configured!.model_id;
    return models.find(speaks)?.model_id ?? "";
  }, [models, language, defaultModel]);

  /**
   * The voice actually used: the chosen one while it can say the language,
   * and otherwise the first that can.
   *
   * Derived rather than corrected in an effect. Writing the correction back
   * into state means a render that immediately schedules another, and it also
   * loses the operator's choice permanently - this way, narrowing to a
   * language and widening again returns the voice they picked.
   */
  const effectiveVoiceId = useMemo(() => (
    voiceId && speakable.some((voice) => voice.voice_id === voiceId)
      ? voiceId
      : speakable[0]?.voice_id ?? ""
  ), [voiceId, speakable]);

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
          voice_id: effectiveVoiceId,
          // Both, because the language decides two different things: which
          // model can read it, and what the synthesiser is told it is reading.
          model_id: modelId || undefined,
          language_code: language || undefined,
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

  const ready = Boolean(body.trim() && lines.length && assets.length && effectiveVoiceId && !busy);
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
                  : !speakable.length ? "No voice on this key reads that language."
                    : !effectiveVoiceId ? "Choose a voice."
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
        <section className="story-write">
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

        <section className="story-settings">
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
            {/* Before the voice, because it decides which voices there are.
                A key with three hundred voices offers a handful in any one
                language, and scrolling past the other two hundred and ninety
                to find them is the whole problem. */}
            <label>Language
              <Select
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                aria-label="The language the script is read in"
              >
                <option value="">Any language</option>
                {languages.map((code) => (
                  <option key={code} value={code}>{languageName(code)}</option>
                ))}
              </Select>
            </label>
            <label>Voice
              <Select
                value={effectiveVoiceId}
                onChange={(event) => setVoiceId(event.target.value)}
                aria-label="The voice that reads the script"
              >
                {speakable.map((voice) => (
                  <option key={voice.voice_id} value={voice.voice_id}>
                    {voice.name}{voice.accents?.[0] ? ` · ${voice.accents[0]}` : ""}
                  </option>
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

        <section className="story-broll">
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
