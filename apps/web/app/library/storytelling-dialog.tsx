"use client";

/**
 * Storytelling, in the Library: a script and a voice cut to chosen pictures.
 *
 * Beside AutoCut and opened the same way, because it is the same kind of thing:
 * many pictures in, one new video out. AutoCut cuts them to a music track's
 * beats; this cuts them to the sentences of a narration, and the script's own
 * words become the subtitles.
 *
 * Pictures come from two places and the difference is deliberately thin: the
 * Library, browsed through the same shared loop every other picker reads with,
 * and stock b-roll, which is *filed into the Library* on import rather than
 * carried around as a second kind of picture. By the time anything is planned
 * there is one list, and nothing downstream knows where a picture came from.
 *
 * The shot list is where a narration stops being a slideshow. Each sentence is
 * a row with the picture it opens on: suggested by the matcher, on the words
 * the sentence and the picture share, and changed by hand from there - the
 * same bargain AutoCut makes with its timeline, for the same reason. The
 * machine arranges twenty tiles in a second and is wrong about two of them,
 * and the person fixing those two is faster than the person doing all twenty.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useLibraryAssets } from "../../lib/use-library-assets";
import { useLocale } from "../i18n-provider";
import { AssetThumbnail } from "../publish/composer";
import type { LibraryAsset } from "../publish/composer";
import { ActionIcon } from "../ui/action-icons";
import { AssetFilters } from "../ui/asset-filters";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Select } from "../ui/select";
import { SegmentedControl } from "../ui/segmented";

/** A superset of what the Library and Publish both hold, so either can pass
    its own rows and the shot list can still draw a thumbnail. */
export type StoryAsset = {
  id: string;
  title: string;
  media_kind: string;
  original_path?: string;
  duration_ms?: number | null;
  width?: number | null;
  height?: number | null;
  versions?: { kind: string }[];
};

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
/** A voice in ElevenLabs' shared library that this key has not got. */
type SharedVoice = {
  voice_id: string;
  public_owner_id: string;
  name: string;
  accent: string;
  description: string;
  /** What it was listed under, so a list fetched for one language is never
      shown under another. */
  language: string;
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
/** One sentence's picture, and the words it was suggested on. */
type Suggestion = { line: number; asset_id: string; score: number; matched: string[] };

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

const SOURCES = [
  { value: "library", label: "Library" },
  { value: "stock", label: "Stock" },
] as const;

/** The most pictures one narration takes, matched to the API's own ceiling so
    the modal never builds a set a render would refuse. */
const MAX_PICTURES = 200;

/** What a Library row has to become for the shared thumbnail to draw it. */
function forThumbnail(asset: StoryAsset): LibraryAsset {
  return {
    id: asset.id,
    title: asset.title,
    media_kind: asset.media_kind,
    original_path: asset.original_path ?? "",
    platform: null,
    creator: null,
    duration_ms: asset.duration_ms ?? null,
    width: asset.width ?? null,
    height: asset.height ?? null,
    versions: (asset.versions ?? []).map((version) => ({
      id: `${asset.id}-${version.kind}`, kind: version.kind,
    })),
  };
}

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
  /** The pictures chosen in the Library, in the order they were chosen. What
      the modal opens on; more can be added without leaving it. */
  assets: StoryAsset[];
  onClose: () => void;
  onQueued: (text: string) => void;
  onError: (text: string) => void;
}) {
  const { locale } = useLocale();
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
  /** Which language the script is read in. */
  const [language, setLanguage] = useState("");
  /** Offer every voice regardless of what it is checked in. Off by default -
      the voices of the chosen language are what was asked for - and reachable,
      because a model reads a language in any voice. */
  const [anyVoice, setAnyVoice] = useState(false);
  const [shared, setShared] = useState<SharedVoice[]>([]);
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);

  /** The pictures this video is made of, seeded from the Library selection and
      added to from either source without leaving the modal. */
  const [picked, setPicked] = useState<StoryAsset[]>(() => assets.slice(0, MAX_PICTURES));
  const [source, setSource] = useState<string>("library");
  /** One picture per sentence, by asset id. Empty until something arranges it. */
  const [assignments, setAssignments] = useState<string[]>([]);
  const [why, setWhy] = useState<Record<number, string[]>>({});
  const [arranging, setArranging] = useState(false);
  /** The row waiting to be given a picture, so a click in the strip lands
      somewhere specific instead of being a click on a picture. */
  const [focused, setFocused] = useState<number | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);

  const [brollReady, setBrollReady] = useState<boolean | null>(null);
  const [brollReason, setBrollReason] = useState("");
  const [query, setQuery] = useState("");
  const [brollKind, setBrollKind] = useState("image");
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [searching, setSearching] = useState(false);

  const base = workspaceId ? `/api/workspaces/${workspaceId}/storytelling` : "";

  // The Library selection is where this starts, not where it is confined: it
  // seeds the set, and adding or removing from either source is the same
  // gesture afterwards.
  //
  // Reset during render on a changed selection rather than in an effect - the
  // same way AutoCut's timeline tracks its own, and React's own answer to
  // resetting state on a prop change. An effect would paint one frame of the
  // last set's pictures first, and arriving at a shot list built from media
  // that is no longer chosen is worse than arriving at an empty one.
  const selectionKey = assets.map((asset) => asset.id).join(",");
  const [pickedKey, setPickedKey] = useState(selectionKey);
  if (pickedKey !== selectionKey) {
    setPickedKey(selectionKey);
    setPicked(assets.slice(0, MAX_PICTURES));
    // The arrangement was about the old pictures. Keeping it would leave rows
    // pointing at media this video no longer has.
    setAssignments([]);
    setWhy({});
    setFocused(null);
  }

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
      // What this workspace works in, unless ElevenLabs was configured for
      // something else. The interface language is the better of the two guesses
      // available for free: somebody writing a script in a workspace they run
      // in Vietnamese is writing it in Vietnamese, and opening on "Any
      // language" made them say so every time.
      //
      // Only when a model can read it. The interface speaks seven languages
      // and this key's models seventy-four, but they are not the same
      // seventy-four - defaulting to one that is missing would open the dialog
      // on a language nothing can say, with the picker showing a value that is
      // not among its own options.
      const spoken = new Set<string>((voiceBody?.models ?? []).flatMap(
        (model: Model) => (model.languages ?? []).map((item) => item.language_id),
      ));
      setLanguage((current) => (
        current
        || (voiceBody?.defaults?.language_code ?? "")
        || (spoken.has(locale) ? locale : "")
      ));
    })();
    return () => { cancelled = true; };
  }, [apiFetch, base, open, workspaceId, locale]);

  /**
   * Every language that can actually be read, which is the models' list.
   *
   * It used to be the voices' list, and that is a different thing: a voice's
   * `verified_languages` says somebody checked it sounds good in that
   * language, not that it can speak it. A multilingual model reads any
   * language it supports in any voice.
   *
   * Measured on this key: the voices are verified in eighteen languages and
   * the models speak seventy-four. Fifty-six were unreachable, Vietnamese
   * among them - in a workspace that runs in Vietnamese.
   */
  const languages = useMemo(
    () => [...new Set(models.flatMap(
      (model) => (model.languages ?? []).map((item) => item.language_id),
    ))].sort((left, right) => languageName(left).localeCompare(languageName(right))),
    [models],
  );

  /** The voices checked in the chosen language, and everything else. */
  const [verified, others] = useMemo(() => {
    if (!language) return [voices, [] as Voice[]];
    return [
      voices.filter((voice) => (voice.languages ?? []).includes(language)),
      voices.filter((voice) => !(voice.languages ?? []).includes(language)),
    ];
  }, [voices, language]);

  /**
   * The voices offered: the chosen language's own.
   *
   * Filtered, which is the point - a key with three hundred voices offers a
   * handful in any one language, and scrolling past the rest to find them is
   * the whole problem. The two escapes below exist because filtering alone
   * produced an empty picker: `anyVoice` says the rest can still read it,
   * which is true, and the shared library offers the ones that actually are
   * this language.
   */
  const speakable = useMemo(
    () => (!language || anyVoice ? [...verified, ...others] : verified),
    [language, anyVoice, verified, others],
  );

  /**
   * Which model reads it, which is not a free choice.
   *
   * The configured model unless it cannot say the words. On this key
   * `eleven_multilingual_v2` is the default and reads twenty-nine languages;
   * Vietnamese is not one of them, and three other models on the same key do
   * speak it. Sending the default anyway is how a language the account can
   * speak comes back refused.
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
   * The voice actually used: the chosen one while it is on offer, and
   * otherwise the first that is.
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

  // Nothing on this key reads the chosen language, so ask what does. A read,
  // and the answer to a question the picker could not otherwise answer: the
  // voices exist, they are simply not in this account yet.
  useEffect(() => {
    if (!open || !base || !language || verified.length) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await apiFetch(
          `${base}/voices/shared?language=${encodeURIComponent(language)}`,
        );
        const payload = await response.json();
        if (!cancelled) setShared(response.ok ? payload.voices ?? [] : []);
      } catch {
        if (!cancelled) setShared([]);
      }
    })();
    return () => { cancelled = true; };
  }, [apiFetch, base, open, language, verified.length]);

  /**
   * The shared voices to offer: this language's, and only when the key has
   * none of its own.
   *
   * Filtered here rather than cleared when the language changes. Clearing it
   * was a setState in an effect body - a cascading render, and one that runs
   * after a frame has already painted the last language's voices under the
   * new language's heading.
   */
  const offers = useMemo(
    () => (language && !verified.length
      ? shared.filter((voice) => voice.language === language)
      : []),
    [shared, language, verified.length],
  );

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

  // Browse the workspace's photos and videos through the shared library loop
  // every other picker reads with (ADR 0025) - it owns the debounce, paging,
  // facets and stale-response guard. Audio is dropped on arrival: a narration
  // plays pictures, and a sound file has nothing to show.
  const library = useLibraryAssets<LibraryAsset>({
    workspaceId,
    apiFetch,
    enabled: open && source === "library",
    keep: (asset) => asset.media_kind === "image" || asset.media_kind === "video",
  });

  const full = picked.length >= MAX_PICTURES;

  const addPicture = useCallback((asset: LibraryAsset) => {
    setPicked((current) => (
      current.some((item) => item.id === asset.id) || current.length >= MAX_PICTURES
        ? current
        : [...current, {
          id: asset.id,
          title: asset.title,
          media_kind: asset.media_kind,
          original_path: asset.original_path,
          duration_ms: asset.duration_ms,
          width: asset.width,
          height: asset.height,
          versions: asset.versions.map((version) => ({ kind: version.kind })),
        }]
    ));
  }, []);

  // Removing a picture takes it out of the arrangement too, rather than
  // leaving rows pointing at something that is no longer in the set. The
  // render would blank them anyway; doing it here is the difference between a
  // shot list that is true and one that only renders true.
  const removePicture = useCallback((assetId: string) => {
    setPicked((current) => current.filter((item) => item.id !== assetId));
    setAssignments((current) => current.map((id) => (id === assetId ? "" : id)));
  }, []);

  /** Ask which picture suits which sentence. Free and offline, and a
      suggestion: nothing is generated, and the answer is editable from here. */
  const arrange = useCallback(async () => {
    if (!base || !lines.length || !picked.length) return;
    setArranging(true);
    try {
      const response = await apiFetch(`${base}/arrange`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body, asset_ids: picked.map((asset) => asset.id) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That could not be arranged.");
      const found: Suggestion[] = payload.assignments ?? [];
      const next = Array<string>(payload.lines?.length ?? lines.length).fill("");
      const reasons: Record<number, string[]> = {};
      for (const item of found) {
        next[item.line] = item.asset_id;
        reasons[item.line] = item.matched ?? [];
      }
      setAssignments(next);
      setWhy(reasons);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That could not be arranged.");
    } finally {
      setArranging(false);
    }
  }, [apiFetch, base, body, lines.length, picked, onError]);

  /** Put a picture on one sentence by hand. The reason goes with it: it was
      the matcher's, and it is not any more. */
  const assign = useCallback((line: number, assetId: string) => {
    setAssignments((current) => {
      const next = [...current];
      while (next.length < lines.length) next.push("");
      next[line] = assetId;
      return next;
    });
    setWhy((current) => ({ ...current, [line]: [] }));
    setFocused(null);
  }, [lines.length]);

  /** Drag one row onto another to trade their pictures.
      A swap rather than an insert: there is one picture per sentence and the
      sentences themselves do not move - the script decides their order. */
  const swap = useCallback((from: number, to: number) => {
    if (from === to) return;
    setAssignments((current) => {
      const next = [...current];
      while (next.length < lines.length) next.push("");
      [next[from], next[to]] = [next[to], next[from]];
      return next;
    });
    setWhy((current) => ({ ...current, [from]: [], [to]: [] }));
  }, [lines.length]);

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

  /** Add one shared-library voice to the operator's own ElevenLabs account.
      Its own button because it is its own act: it changes what their
      subscription holds, so it happens when they ask for it. */
  async function addVoice(voice: SharedVoice) {
    setAdding(voice.voice_id);
    try {
      const response = await apiFetch(`${base}/voices/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          public_owner_id: voice.public_owner_id,
          voice_id: voice.voice_id,
          name: voice.name,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That voice could not be added.");
      // The id it is listed under is not the id it has once added, so the new
      // one is used rather than the library's.
      setVoices((current) => [...current, {
        voice_id: payload.voice_id,
        name: voice.name,
        languages: language ? [language] : [],
        accents: voice.accent ? [voice.accent] : [],
      }]);
      setVoiceId(payload.voice_id);
      onQueued(`${voice.name} is on your ElevenLabs key now.`);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That voice could not be added.");
    } finally {
      setAdding("");
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
          asset_ids: picked.map((asset) => asset.id),
          // The arrangement, when there is one. Absent means the order the
          // pictures were chosen in, which is what arranging them by hand in
          // the Library meant.
          assignments: assignments.some(Boolean) ? assignments : [],
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

  const byId = useMemo(() => new Map(picked.map((asset) => [asset.id, asset])), [picked]);
  const ready = Boolean(body.trim() && lines.length && picked.length && effectiveVoiceId && !busy);
  const short = lines.length > 0 && picked.length > 0 && picked.length < lines.length;

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
                : !picked.length ? "Add a picture for the narration to play over."
                  : language && !modelId ? "No model on this key reads that language."
                    : !effectiveVoiceId ? "Choose a voice."
                      : `${lines.length} ${lines.length === 1 ? "sentence" : "sentences"} over ${picked.length} ${picked.length === 1 ? "picture" : "pictures"}.`}
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
                  {short && ` You picked ${picked.length}, so some repeat.`}</>
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
            {/* Before the voice, because it decides which voices there are. */}
            <label>Language
              <Select
                value={language}
                onChange={(event) => { setLanguage(event.target.value); setAnyVoice(false); }}
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
                disabled={!speakable.length}
              >
                {speakable.map((voice) => (
                  <option key={voice.voice_id} value={voice.voice_id}>
                    {[voice.name, voice.accents?.[0]].filter(Boolean).join(" · ")}
                  </option>
                ))}
              </Select>
            </label>
            {/* The dead end this used to be, and the two ways out of it. The
                key's voices are checked in eighteen languages; the models read
                seventy-four. Asking for one of the other fifty-six emptied the
                picker and read as "not supported", when what is true is that
                the voices exist and are not in this account. */}
            {language && !verified.length && (
              <div className="story-voices-missing">
                <p className="story-note">
                  No voice on this key is checked in {languageName(language)}.
                </p>
                {offers.length > 0 && (
                  <ul className="story-shared">
                    {offers.slice(0, 6).map((voice) => (
                      <li key={voice.voice_id}>
                        <span>
                          <strong>{voice.name}</strong>
                          {voice.accent && <small>{voice.accent}</small>}
                        </span>
                        <Button
                          variant="secondary"
                          size="sm"
                          busy={adding === voice.voice_id}
                          onClick={() => void addVoice(voice)}
                          title={`Add ${voice.name} to your ElevenLabs voices`}
                        >Add</Button>
                      </li>
                    ))}
                  </ul>
                )}
                <label className="story-toggle">
                  <input
                    type="checkbox"
                    checked={anyVoice}
                    onChange={(event) => setAnyVoice(event.target.checked)}
                  />
                  <span>Use a voice from another language</span>
                  <small>
                    The model reads {languageName(language)} in any voice; nobody has
                    checked how this one sounds doing it.
                  </small>
                </label>
              </div>
            )}
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

        <section className="story-pictures">
          <div className="story-section-head">
            <h4>Pictures <span className="story-count">{picked.length}</span></h4>
            <SegmentedControl
              value={source}
              onChange={setSource}
              options={SOURCES}
              label="Where to add pictures from"
            />
          </div>

          {/* The set itself, above whichever source is open, so adding one is
              visibly adding to something rather than a search that ends
              nowhere. */}
          {picked.length > 0 && (
            <ul className="story-tiles story-tiles-picked">
              {picked.map((asset) => (
                <li key={asset.id}>
                  <button
                    type="button"
                    className={focused !== null ? "story-assignable" : ""}
                    title={focused !== null
                      ? `Put ${asset.title} on sentence ${focused + 1}`
                      : asset.title}
                    onClick={() => { if (focused !== null) assign(focused, asset.id); }}
                  >
                    <AssetThumbnail
                      asset={forThumbnail(asset)}
                      workspaceId={workspaceId}
                      apiFetch={apiFetch}
                    />
                  </button>
                  <button
                    type="button"
                    className="story-tile-remove"
                    aria-label={`Remove ${asset.title}`}
                    onPointerDown={(event) => {
                      event.stopPropagation(); removePicture(asset.id);
                    }}
                  >×</button>
                </li>
              ))}
            </ul>
          )}
          {full && <p className="story-note">Two hundred pictures is the most one narration takes.</p>}

          {source === "library" ? (
            <div className="story-library">
              <AssetFilters
                values={library.filters}
                facets={library.facets}
                fields={["query", "channel", "platform", "downloaded"]}
                cleared={{}}
                onChange={(next) => library.setFilters(next)}
              />
              {library.failure && <p className="console-error" role="alert">{library.failure}</p>}
              {library.loading === "list" && !library.assets.length ? (
                <p className="story-note">Loading…</p>
              ) : library.assets.length ? (
                <>
                  <ul className="story-tiles story-tiles-library">
                    {library.assets.map((asset) => {
                      const inUse = picked.some((item) => item.id === asset.id);
                      return (
                        <li key={asset.id}>
                          <button
                            type="button"
                            className={inUse ? "story-in-use" : ""}
                            disabled={inUse || full}
                            title={inUse ? "Already in this video" : asset.title}
                            onClick={() => addPicture(asset)}
                          >
                            <AssetThumbnail
                              asset={asset}
                              workspaceId={workspaceId}
                              apiFetch={apiFetch}
                            />
                            <small>{asset.title}</small>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  {library.canLoadMore && (
                    <Button
                      variant="secondary"
                      size="sm"
                      busy={Boolean(library.loading)}
                      onClick={() => library.loadMore()}
                    >Load more</Button>
                  )}
                </>
              ) : (
                <p className="story-note">No photos or videos match.</p>
              )}
            </div>
          ) : brollReady === false ? (
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

        {/* The shot list. Where a narration stops being a slideshow: which
            picture is on screen for which sentence, suggested and then
            corrected. */}
        {lines.length > 0 && picked.length > 0 && (
          <section className="story-shots">
            <div className="story-section-head">
              <h4>Shots</h4>
              <Button
                variant="secondary"
                size="sm"
                busy={arranging}
                onClick={() => void arrange()}
                title="Match each sentence to the picture it is about"
              ><ActionIcon name="effects" />Match to the script</Button>
            </div>
            <p className="story-note">
              {assignments.some(Boolean)
                ? "Drag a row onto another to trade their pictures, or pick a row and click a picture above."
                : "Unarranged, so the pictures play in the order you chose them."}
            </p>
            <ol className="story-shot-list">
              {lines.map((line, index) => {
                const assetId = assignments[index] ?? "";
                const asset = byId.get(assetId);
                const reasons = why[index] ?? [];
                return (
                  <li
                    key={`${index}-${line.slice(0, 24)}`}
                    className={[
                      "story-shot",
                      focused === index ? "story-focused" : "",
                      dragging === index ? "story-held" : "",
                    ].filter(Boolean).join(" ")}
                    draggable
                    onDragStart={() => setDragging(index)}
                    onDragEnd={() => setDragging(null)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => {
                      event.preventDefault();
                      if (dragging !== null) swap(dragging, index);
                      setDragging(null);
                    }}
                  >
                    <span className="story-shot-index">{index + 1}</span>
                    <button
                      type="button"
                      className="story-shot-picture"
                      aria-pressed={focused === index}
                      title={asset
                        ? `${asset.title} - click, then click a picture above to change it`
                        : "Click, then click a picture above"}
                      onClick={() => setFocused((current) => (current === index ? null : index))}
                    >
                      {asset
                        ? <AssetThumbnail
                          asset={forThumbnail(asset)}
                          workspaceId={workspaceId}
                          apiFetch={apiFetch}
                        />
                        : <span className="story-shot-empty">In order</span>}
                    </button>
                    <span className="story-shot-line">
                      {line}
                      {reasons.length > 0 && (
                        // Why it was suggested. A suggestion whose reason is
                        // invisible is one nobody trusts a second time.
                        <small className="story-shot-why">
                          matched {reasons.slice(0, 3).join(", ")}
                        </small>
                      )}
                    </span>
                  </li>
                );
              })}
            </ol>
          </section>
        )}
      </div>
    </Dialog>
  );
}
