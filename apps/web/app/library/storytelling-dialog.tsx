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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  assign as assignPicture,
  forRender,
  fromSuggestions,
  reasonsFrom,
  swap as swapPictures,
  withoutPicture,
  type Suggestion,
} from "../../lib/storytelling-shots";
import {
  blockedReason,
  effectiveVoice,
  isExternal,
  modelFor,
  offerVoices,
  openingLanguage,
  partitionVoices,
  readableLanguages,
  usable,
  type Model,
  type Voice,
} from "../../lib/elevenlabs-voices";
import { useLibraryAssets } from "../../lib/use-library-assets";
import { usePersistedState } from "../ui/use-persisted-state";
import { LOCALE_CODES } from "../../lib/i18n/locales";
import { useLocale, useT } from "../i18n-provider";
import { AssetThumbnail } from "../publish/composer";
import type { LibraryAsset } from "../publish/composer";
import { ActionIcon } from "../ui/action-icons";
import { AspectIcon } from "../ui/aspect-icon";
import { AssetFilters } from "../ui/asset-filters";
import { Badge } from "../ui/primitives";
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
  /** Whether this plan may actually take it, and what to say when it may not.
      Decided on the server, where the one set of rules about plans lives. */
  addable: boolean;
  reason: string;
};

/** What the ElevenLabs subscription allows. `known` is false when it could not
    be read, which is different from a plan that allows nothing. */
type Plan = {
  known: boolean;
  tier: string;
  voice_limit: number;
  voice_slots_used: number;
  voice_slots_left: number;
  /** Characters this plan has left to speak before it resets. A narration is
      charged by the character, so this is what the script is measured against. */
  characters_left: number;
  character_limit: number;
};
type Tile = {
  id: string;
  kind: string;
  preview_url: string;
  credit: string;
  photographer_url: string;
};
/** A language code as a reader would recognise it, falling back to the code.
    `Intl.DisplayNames` is in every browser this runs in and knows far more
    languages than a table here would. Built once and reused: it is called in
    the language and voice option maps and as `readableLanguages`' sort
    comparator, so a fresh instance per call was many allocations per render. */
let _languageDisplay: Intl.DisplayNames | null = null;
function languageName(code: string): string {
  try {
    if (!_languageDisplay) {
      _languageDisplay = new Intl.DisplayNames(undefined, { type: "language" });
    }
    return _languageDisplay.of(code) ?? code;
  } catch {
    return code;
  }
}

/** How the finished video is shaped. Wide leads because a narrated piece is
    watched on a wide screen; the short-form frame is offered beside it. */
const ASPECTS = [
  { value: "16:9", label: "16:9", title: "Landscape (16:9)", icon: <AspectIcon ratio="16:9" /> },
  { value: "9:16", label: "9:16", title: "Portrait (9:16)", icon: <AspectIcon ratio="9:16" /> },
  { value: "1:1", label: "1:1", title: "Square (1:1)", icon: <AspectIcon ratio="1:1" /> },
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
  initialDraftId,
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
  /** Open straight onto a saved draft, resuming it - how the Library's Drafts
      view reopens an unfinished story. */
  initialDraftId?: string;
}) {
  const { locale } = useLocale();
  const t = useT();
  /**
   * The script, kept where a closed dialog cannot take it.
   *
   * Everything else here is a choice that takes a second to make again - a
   * template, a shape, a language. The script is writing, and it was being
   * thrown away by Escape, by a misplaced click on the backdrop, and by
   * picking one more picture from the Library. Per workspace, because two
   * workspaces are two different pieces of work.
   */
  const [body, setBody] = usePersistedState<string>(
    `trendrelay.storytelling.script.${workspaceId || "none"}`,
    "",
    (value): value is string => typeof value === "string",
  );
  const [lines, setLines] = useState<string[]>([]);
  const [reading, setReading] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("explainer");
  const [aspect, setAspect] = useState<string>("16:9");
  const [subtitles, setSubtitles] = useState(true);
  //: The caption look - "" is the plain sentence subtitle; a preset id is an
  //  animated word-highlight style that lights each word as it is spoken.
  const [captionStyle, setCaptionStyle] = useState("");
  const [captionStyles, setCaptionStyles] = useState<
    { id: string; label: string; summary: string; needs_word_timings: boolean }[]
  >([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  /** The model configured in Tools, which wins whenever it can say the words. */
  const [defaultModel, setDefaultModel] = useState("");
  const [voiceId, setVoiceId] = useState("");
  /** Which language the script is read in. */
  const [language, setLanguage] = useState("");
  /**
   * Show voices from other languages as well as this one.
   *
   * Off by default: choosing a language narrows the picker to it, which is the
   * point of choosing one. Forced on when narrowing would leave nothing that
   * can be spoken with, and the control says so rather than sitting unchecked
   * over a list that plainly is not narrowed.
   */
  const [showAll, setShowAll] = useState(false);
  const [shared, setShared] = useState<SharedVoice[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);
  //: Which autonomous action is running, so its own button spins and the rest
  //  hold still: "fill" is fill-from-stock-for-review, "create" is the one-step
  //  build. Empty is neither.
  const [autoBusy, setAutoBusy] = useState<"" | "fill" | "create">("");

  /** The pictures this video is made of, seeded from the Library selection and
      added to from either source without leaving the modal. */
  const [picked, setPicked] = useState<StoryAsset[]>(() => assets.slice(0, MAX_PICTURES));
  const [source, setSource] = useState<string>("library");
  /**
   * Whether the browser is open, not which source it shows.
   *
   * Chips, six filters and a grid come to about three hundred and eighty
   * pixels, and they are useful while pictures are being gathered and dead
   * weight afterwards - gathering and arranging are different jobs and nobody
   * does both in the same second. AutoCut made this call already, for the same
   * reason; this section was the copy that forgot to.
   */
  const [browsing, setBrowsing] = useState(false);
  /** One picture per sentence, by asset id. Empty until something arranges it. */
  const [assignments, setAssignments] = useState<string[]>([]);
  const [why, setWhy] = useState<Record<number, string[]>>({});
  const [arranging, setArranging] = useState(false);
  /** The exact script the current arrangement was built against. The map is one
      picture per sentence *by position*, so a sentence added or removed shifts
      every picture after it onto the wrong line - and the render would draw it
      that way. When the script is edited past this, the arrangement is dropped
      rather than silently desynced. Null means nothing is arranged to protect. */
  const arrangedFor = useRef<string | null>(null);
  /** Whether the dialog is still open, read by the background polls so they
      stop the moment it closes instead of running their full three minutes and
      then setting state on a dialog nobody is looking at. */
  const openRef = useRef(open);
  useEffect(() => { openRef.current = open; }, [open]);
  /** The live script, for the by-hand placement handlers below: they are
      memoised without `body`, so they read the current one here rather than the
      one captured when they were made. */
  const bodyRef = useRef(body);
  useEffect(() => { bodyRef.current = body; });
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
  //: Stock tiles whose import is in flight, keyed "kind-id", so a tile spins
  //  and refuses a second click while its download runs.
  const [importing, setImporting] = useState<Record<string, boolean>>({});

  const base = workspaceId ? `/api/workspaces/${workspaceId}/storytelling` : "";
  const creationsBase = workspaceId ? `/api/workspaces/${workspaceId}/creations` : "";
  // The saved draft this story belongs to, once saved, and the list to resume.
  const [draftId, setDraftId] = useState<string | null>(null);
  const [savedDrafts, setSavedDrafts] = useState<
    { id: string; title: string; status: string; summary: { script_chars: number; pictures: number } }[]
  >([]);
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);

  // The Library selection is where this starts, not where it is confined: it
  // seeds the set, and adding or removing from either source is the same
  // gesture afterwards.
  //
  // Reset during render on a changed selection rather than in an effect - the
  // same way AutoCut's timeline tracks its own, and React's own answer to
  // resetting state on a prop change. An effect would paint one frame of the
  // last set's pictures first, and arriving at a shot list built from media
  // that is no longer chosen is worse than arriving at an empty one.
  const selectionKey = useMemo(() => assets.map((asset) => asset.id).join(","), [assets]);
  const [pickedKey, setPickedKey] = useState(selectionKey);
  if (pickedKey !== selectionKey) {
    setPickedKey(selectionKey);
    setPicked(assets.slice(0, MAX_PICTURES));
    // Open on an empty set, because then adding pictures is the only thing
    // there is to do; closed when the Library selection already brought some,
    // because then the next thing is the script.
    setBrowsing(assets.length === 0);
    // The arrangement was about the old pictures. Keeping it would leave rows
    // pointing at media this video no longer has.
    setAssignments([]);
    setWhy({});
    setFocused(null);
    setDraftId(null);
    setDraftsOpen(false);
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
      setPlan(voiceBody?.plan ?? null);
      // What this workspace works in, unless ElevenLabs was configured for
      // something else, and only ever a language something can read.
      setLanguage((current) => current || openingLanguage({
        configured: voiceBody?.defaults?.language_code ?? "",
        locale,
        models: voiceBody?.models ?? [],
      }));
    })();
    return () => { cancelled = true; };
  }, [apiFetch, base, open, workspaceId, locale]);

  // The caption looks on offer, from the shared caption catalogue.
  useEffect(() => {
    if (!open || !base) return;
    let live = true;
    void apiFetch(`${base}/caption-styles`)
      .then((res) => res.json())
      .then((data) => { if (live && Array.isArray(data.styles)) setCaptionStyles(data.styles); })
      .catch(() => { /* the plain subtitle is always available even if this fails */ });
    return () => { live = false; };
  }, [open, base, apiFetch]);

  // Which language, which model and which voice are three choices that look
  // like one, and each has an edge that made the picker wrong once. They live
  // in `lib/storytelling-voice`, where they can be exercised without rendering
  // a modal - see that file for what each rule is protecting against.
  // The workspace's own languages first. The models read seventy-four; this
  // workspace works in seven of them, and the one being reached for was
  // otherwise thirty rows down a list alphabetised for a stranger.
  const languages = useMemo(
    () => readableLanguages(models, languageName, LOCALE_CODES), [models],
  );
  /** How many of those lead because this workspace works in them - the
      count `readableLanguages` puts first, and so where the line goes. */
  const workspaceLanguages = useMemo(
    () => languages.filter((code) => (LOCALE_CODES as readonly string[]).includes(code)).length,
    [languages],
  );
  const { verified } = useMemo(
    () => partitionVoices(voices, language), [voices, language],
  );
  // `widened` when narrowing to the language left nothing that can be spoken
  // with. It is not an error state - it is how Vietnamese is narrated on this
  // plan - so it is explained rather than blocked.
  const { voices: offeredAll, widened } = useMemo(
    () => offerVoices(voices, language, showAll), [voices, language, showAll],
  );
  // Drop the voices this plan cannot narrate with: an option that can only fail
  // is clutter, not a choice. Kept only as a last resort - if filtering would
  // empty the picker - so it never becomes a dead box with nothing to pick.
  const speakable = useMemo(() => {
    const usableOnly = offeredAll.filter(usable);
    return usableOnly.length ? usableOnly : offeredAll;
  }, [offeredAll]);
  const modelId = useMemo(
    () => modelFor(models, language, defaultModel), [models, language, defaultModel],
  );
  const effectiveVoiceId = useMemo(
    () => effectiveVoice(speakable, voiceId), [speakable, voiceId],
  );
  /**
   * Why the chosen voice cannot narrate, when the plan will not let it.
   *
   * Checked here rather than discovered at synthesis. A voice added from the
   * shared library sits happily on a free key and is refused every time it is
   * spoken with, so the failure used to arrive after the pictures were
   * gathered, the script written and the job queued - and read as the story
   * failing rather than as the voice never having been allowed.
   */
  const voiceBlocked = useMemo(
    () => blockedReason(speakable, effectiveVoiceId), [speakable, effectiveVoiceId],
  );

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
        if (cancelled) return;
        setShared(response.ok ? payload.voices ?? [] : []);
        setPlan(response.ok ? payload.plan ?? null : null);
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
    // Only while the dialog is open: the script is restored from localStorage
    // on any library page load, and without this guard that alone would POST
    // /outline for a dialog the operator never opened.
    if (!open) return;
    const timer = window.setTimeout(() => void outline(), 400);
    return () => window.clearTimeout(timer);
  }, [open, outline]);

  // Drop the arrangement when the script is edited out from under it. Keyed on
  // the script the arrangement was built for, not on the sentence list, so the
  // arrangements that arrive with their own script - a resumed draft, a stock
  // fill - are not mistaken for an edit and cleared the instant they load.
  useEffect(() => {
    if (arrangedFor.current !== null && body !== arrangedFor.current) {
      arrangedFor.current = null;
      setAssignments((current) => (current.some(Boolean) ? [] : current));
      setWhy((current) => (Object.keys(current).length ? {} : current));
    }
  }, [body]);

  // Browse the workspace's photos and videos through the shared library loop
  // every other picker reads with (ADR 0025) - it owns the debounce, paging,
  // facets and stale-response guard. Audio is dropped on arrival: a narration
  // plays pictures, and a sound file has nothing to show.
  const library = useLibraryAssets<LibraryAsset>({
    workspaceId,
    apiFetch,
    enabled: open && browsing && source === "library",
    keep: (asset) => asset.media_kind === "image" || asset.media_kind === "video",
  });

  /**
   * How many photos and how many videos the current filter matches.
   *
   * From the facets rather than from the hook's `total`, which counts audio
   * too - this picker never offers a sound file, so a total that included
   * them would be a number nothing on screen adds up to.
   *
   * "All" is the two added together for the same reason. It is not the
   * server's total and is not meant to be.
   */
  const kindCount = useCallback(
    (kind: string) =>
      library.facets.media_kinds.find((facet) => facet.value === kind)?.count ?? 0,
    [library.facets],
  );
  const kindChips = useMemo(() => [
    { key: "", label: t("common.all"), count: kindCount("image") + kindCount("video") },
    { key: "image", label: t("library.images"), count: kindCount("image") },
    { key: "video", label: t("library.videos"), count: kindCount("video") },
  ], [t, kindCount]);

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
    // Any sentence that opened on this picture loses its match reasons too, so
    // the shot list does not keep showing "matched …" under a now-empty shot.
    const affected = assignments.flatMap((id, line) => (id === assetId ? [line] : []));
    setPicked((current) => current.filter((item) => item.id !== assetId));
    setAssignments((current) => withoutPicture(current, assetId));
    if (affected.length) {
      setWhy((reasons) => {
        const next = { ...reasons };
        for (const line of affected) delete next[line];
        return next;
      });
    }
  }, [assignments]);

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
      setAssignments(fromSuggestions(found, payload.lines?.length ?? lines.length));
      setWhy(reasonsFrom(found));
      arrangedFor.current = body;  // this arrangement belongs to this script
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That could not be arranged.");
    } finally {
      setArranging(false);
    }
  }, [apiFetch, base, body, lines.length, picked, onError]);

  /** Put a picture on one sentence by hand. The reason goes with it: it was
      the matcher's, and it is not any more. */
  const assign = useCallback((line: number, assetId: string) => {
    setAssignments((current) => assignPicture(current, line, assetId, lines.length));
    setWhy((current) => ({ ...current, [line]: [] }));
    setFocused(null);
    // A by-hand placement is an arrangement too, so a later script edit clears it.
    if (arrangedFor.current === null) arrangedFor.current = bodyRef.current;
  }, [lines.length]);

  /** Drag one row onto another to trade their pictures.
      A swap rather than an insert: there is one picture per sentence and the
      sentences themselves do not move - the script decides their order. */
  const swap = useCallback((from: number, to: number) => {
    if (from === to) return;
    setAssignments((current) => swapPictures(current, from, to, lines.length));
    setWhy((current) => ({ ...current, [from]: [], [to]: [] }));
    if (arrangedFor.current === null) arrangedFor.current = bodyRef.current;
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
    const key = `${tile.kind}-${tile.id}`;
    // A stock import is a download the click cannot see finish, so without a
    // guard a second click filed the same clip twice. One import per tile.
    if (importing[key]) return;
    setImporting((current) => ({ ...current, [key]: true }));
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
    } finally {
      setImporting((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
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
      // It is on the key now, so it is neither on offer nor free of charge:
      // the row stops being addable and the slot it took is spent. Both are
      // corrected here rather than by re-reading, so the list does not
      // silently keep offering a voice that has just been taken.
      setShared((current) => current.map((item) => (
        item.voice_id === voice.voice_id
          ? { ...item, addable: false, reason: "Already on your key" }
          : item
      )));
      setPlan((current) => (current && current.known ? {
        ...current,
        voice_slots_used: current.voice_slots_used + 1,
        voice_slots_left: Math.max(0, current.voice_slots_left - 1),
      } : current));
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
          assignments: forRender(assignments),
          template_id: templateId,
          voice_id: effectiveVoiceId,
          // Both, because the language decides two different things: which
          // model can read it, and what the synthesiser is told it is reading.
          model_id: modelId || undefined,
          language_code: language || undefined,
          aspect,
          subtitles,
          caption_style: captionStyle,
          // The draft this is the render of, so it stops being offered to
          // pick up once its video is in the Library.
          draft_id: draftId ?? undefined,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That could not be queued.");
      onQueued("Reading the script, then cutting the pictures to it. It lands in your Library.");
      // It has been said now. Held until this point on purpose: a render that
      // failed is precisely when the words are still wanted.
      setBody("");
      // The draft is the render's now; the next story saved here is a new one.
      setDraftId(null);
      onClose();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That could not be queued.");
    } finally {
      setBusy(false);
    }
  }

  /**
   * The autonomous path, in two shapes.
   *
   * Both post to the one /autocreate endpoint, which fills a sentence's stock,
   * arranges it with the same matcher, and - on the flag - renders. It is the
   * manual flow with the human choices filled in, so nothing new is queued:
   * the import is the Library's own ingest and the render is the same one
   * "Make the video" queues. `fill` stops for review and loads the shots back
   * into this dialog; `create` carries straight through and closes.
   */
  async function pollAutocreate(id: string): Promise<Record<string, unknown> | null> {
    // It runs in the worker; wait for it with a short poll, the way the rest of
    // the dialog waits on a job. Generous cap - a dozen downloads take a while.
    for (let attempt = 0; attempt < 120; attempt += 1) {
      // Give up the moment the dialog is closed: a poll that ran on for three
      // minutes after the operator left, then set state, is pure waste.
      if (!openRef.current) return null;
      const res = await apiFetch(`${base}/autocreate/${id}`);
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail ?? "The build could not be read.");
      if (payload.status === "succeeded") return payload;
      if (payload.status === "failed" || payload.status === "cancelled") {
        throw new Error(payload.error ?? "The build did not finish.");
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    throw new Error("The build is taking longer than expected; check the Library shortly.");
  }

  async function fillFromStock() {
    if (!base || !lines.length) return;
    setAutoBusy("fill");
    try {
      const response = await apiFetch(`${base}/autocreate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          body,
          asset_ids: picked.map((asset) => asset.id),
          voice_id: effectiveVoiceId || undefined,
          aspect,
          broll_kind: brollKind,
          // Stop for review: fill and arrange, but do not render.
          render: false,
        }),
      });
      const queued = await response.json();
      if (!response.ok) throw new Error(queued.detail ?? "That could not start.");
      const result = await pollAutocreate(queued.id);
      if (!result || !openRef.current) return;  // dialog closed while it ran
      const ids = (result.asset_ids as string[]) ?? [];
      let rows: StoryAsset[] = [];
      if (ids.length) {
        const assetsRes = await apiFetch(
          `/api/workspaces/${workspaceId}/media/library/assets?asset_ids=${ids.join(",")}`,
        );
        const assetsBody = await assetsRes.json();
        const found = new Map<string, StoryAsset>(
          (assetsBody.assets ?? []).map((asset: StoryAsset) => [asset.id, asset]),
        );
        // Keep the order the build settled on; drop any the fetch could not find.
        rows = ids
          .map((id) => found.get(id))
          .filter((asset): asset is StoryAsset => Boolean(asset))
          .map((asset) => ({
            id: asset.id, title: asset.title, media_kind: asset.media_kind,
            original_path: asset.original_path, duration_ms: asset.duration_ms ?? null,
            width: asset.width ?? null, height: asset.height ?? null,
            versions: (asset.versions ?? []).map((version) => ({ kind: version.kind })),
          }));
      }
      setPicked(rows.slice(0, MAX_PICTURES));
      setAssignments(Array.isArray(result.assignments) ? (result.assignments as string[]) : []);
      arrangedFor.current = body;  // filled for this script; an edit still clears it
      const nextWhy: Record<number, string[]> = {};
      for (const [line, words] of Object.entries((result.reasons as Record<string, string[]>) ?? {})) {
        nextWhy[Number(line)] = words;
      }
      setWhy(nextWhy);
      const imported = Number(result.imported ?? 0);
      onQueued(
        `Filled ${imported} clip${imported === 1 ? "" : "s"} from stock. `
        + "Review the shots, then make the video when you're happy.",
      );
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That could not be filled.");
    } finally {
      setAutoBusy("");
    }
  }

  async function autoCreate() {
    if (!base) return;
    setAutoBusy("create");
    try {
      const response = await apiFetch(`${base}/autocreate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          body,
          asset_ids: picked.map((asset) => asset.id),
          voice_id: effectiveVoiceId,
          model_id: modelId || undefined,
          language_code: language || undefined,
          template_id: templateId,
          aspect,
          subtitles,
          caption_style: captionStyle,
          broll_kind: brollKind,
          // Carry straight through to a queued render.
          render: true,
          draft_id: draftId ?? undefined,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That could not be queued.");
      onQueued("Finding b-roll for every sentence, then narrating and cutting it. It lands in your Library.");
      setBody("");
      setDraftId(null);
      onClose();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "That could not be queued.");
    } finally {
      setAutoBusy("");
    }
  }

  // The current story as a storytelling draft spec - the same fields a render
  // takes, so a saved draft renders to exactly what the dialog would now.
  function draftSpec() {
    return {
      body,
      asset_ids: picked.map((asset) => asset.id),
      assignments: forRender(assignments),
      template_id: templateId,
      voice_id: effectiveVoiceId || undefined,
      model_id: modelId || undefined,
      language_code: language || undefined,
      aspect,
      subtitles,
      caption_style: captionStyle,
    };
  }

  // Save (or re-save) the story as a resumable draft, so closing the dialog no
  // longer loses the script and its arrangement. Unnamed on purpose: the
  // server names a draft for its first sentence, and renames it as the
  // script changes, which a name sent from here would have frozen.
  async function saveDraft() {
    if (!body.trim() && !picked.length) return;
    setSavingDraft(true);
    try {
      const path = draftId ? `${creationsBase}/${draftId}` : creationsBase;
      const response = await apiFetch(path, {
        method: draftId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          draftId
            ? { spec: draftSpec() }
            : { kind: "storytelling", spec: draftSpec() },
        ),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Could not save the draft.");
      setDraftId(payload.id as string);
      setSavedDrafts((current) => [
        { id: payload.id, title: payload.title, status: payload.status, summary: payload.summary },
        ...current.filter((saved) => saved.id !== payload.id),
      ]);
      onQueued("Draft saved - reopen it any time from Saved drafts.");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not save the draft.");
    } finally {
      setSavingDraft(false);
    }
  }

  // Reopen a saved draft: pull its spec and its pictures (fetched by id, so it
  // resumes even when the dialog opened on a different selection) and set the
  // whole story from it.
  async function resumeDraft(id: string) {
    try {
      const response = await apiFetch(`${creationsBase}/${id}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Could not open the draft.");
      const spec = payload.spec ?? {};
      const ids: string[] = (spec.asset_ids ?? []).filter((assetId: string) => !assetId.startsWith("draft:"));
      let rows: StoryAsset[] = [];
      if (ids.length) {
        const assetsRes = await apiFetch(
          `/api/workspaces/${workspaceId}/media/library/assets?asset_ids=${ids.join(",")}`,
        );
        const assetsBody = await assetsRes.json();
        rows = (assetsBody.assets ?? []).map((asset: StoryAsset) => ({
          id: asset.id, title: asset.title, media_kind: asset.media_kind,
          original_path: asset.original_path, duration_ms: asset.duration_ms ?? null,
          width: asset.width ?? null, height: asset.height ?? null,
          versions: (asset.versions ?? []).map((v) => ({ kind: v.kind })),
        }));
      }
      setPicked(rows.slice(0, MAX_PICTURES));
      setBody(spec.body ?? "");
      // The restored arrangement belongs to the restored script, so the edit
      // guard above lets it stand instead of clearing it as the body lands.
      arrangedFor.current = spec.body ?? "";
      setAssignments(Array.isArray(spec.assignments) ? spec.assignments : []);
      setTemplateId(spec.template_id ?? "explainer");
      if (spec.voice_id) setVoiceId(spec.voice_id);
      setLanguage(spec.language_code ?? "");
      setAspect(spec.aspect ?? "16:9");
      setSubtitles(spec.subtitles !== false);
      setCaptionStyle(spec.caption_style ?? "");
      setDraftId(id);
      setDraftsOpen(false);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not open the draft.");
    }
  }

  // Offer the saved stories to resume whenever the dialog is open. Inline fetch,
  // setState in the async callback, like the reads above.
  useEffect(() => {
    if (!open || !creationsBase) return;
    let live = true;
    void apiFetch(`${creationsBase}?kind=storytelling&limit=50`)
      .then((res) => res.json())
      .then((payload) => { if (live && Array.isArray(payload.items)) setSavedDrafts(payload.items); })
      .catch(() => { /* a drafts list that will not load is not worth an error */ });
    return () => { live = false; };
  }, [open, creationsBase, apiFetch]);

  // Open straight onto a saved draft when the Library's Drafts view asked for
  // one, resumed once per open. Called through a ref so this effect does not
  // re-run on every render just because resumeDraft is a fresh function.
  const resumeRef = useRef(resumeDraft);
  useEffect(() => { resumeRef.current = resumeDraft; });
  const resumedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!open) {
      resumedFor.current = null;
      return;
    }
    if (initialDraftId && resumedFor.current !== initialDraftId) {
      resumedFor.current = initialDraftId;
      void resumeRef.current(initialDraftId);
    }
  }, [open, initialDraftId]);

  /**
   * Whether the plan has the characters to read this script.
   *
   * A narration is charged per character, and the free tier holds ten
   * thousand a month - about six minutes of speech. Over that, the render
   * does not refuse: it queues, spends the voice, and fails partway with the
   * audio half made. Counted from the script's own length, which is what the
   * synthesiser is sent.
   */
  const overBudget = Boolean(
    plan?.known && plan.character_limit > 0 && body.length > plan.characters_left
    // A voice from another service spends nothing on this plan, so the
    // allowance that would block it is not its allowance.
    && !isExternal(effectiveVoiceId),
  );

  const byId = useMemo(() => new Map(picked.map((asset) => [asset.id, asset])), [picked]);
  const ready = Boolean(
    body.trim() && lines.length && picked.length && effectiveVoiceId
    && !busy && !overBudget && !voiceBlocked,
  );
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
                : !picked.length ? (effectiveVoiceId
                  ? "No pictures yet - add some, or Auto-create to find them from stock."
                  : "Add a picture for the narration to play over.")
                  : language && !modelId && !isExternal(effectiveVoiceId)
                    ? "No model on this key reads that language."
                    : overBudget ? "The script is longer than this plan has characters left."
                      : voiceBlocked ? `${voiceBlocked}. Any other voice in the list will read it.`
                        : !effectiveVoiceId ? "Choose a voice."
                          : `${lines.length} ${lines.length === 1 ? "sentence" : "sentences"} over ${picked.length} ${picked.length === 1 ? "picture" : "pictures"}.`}
          </span>
          {/* Draft controls live in a body bar (below), not here: the footer
              is for the decision - cancel, or make the video - and AutoCut
              keeps its drafts in the body the same way. */}
          <Button
            variant="quiet"
            disabled={busy || autoBusy !== ""}
            onClick={onClose}
          >Cancel</Button>
          {/* The one-step build: fill the pictures from stock and render, from
              the script alone. No pictures required - finding them is the point
              - but a voice still is, the same as any render. */}
          <Button
            variant="secondary"
            busy={autoBusy === "create"}
            // Needs a voice like any render; and something to show - either
            // pictures already chosen or a stock provider to find them - so it
            // is not offered when it could only fail.
            disabled={!body.trim() || !lines.length || !effectiveVoiceId || overBudget || Boolean(voiceBlocked) || (brollReady === false && !picked.length) || autoBusy !== "" || busy}
            onClick={() => void autoCreate()}
            title="Fill the pictures from stock and render, in one step"
          >
            <ActionIcon name="effects" />Auto-create
          </Button>
          <Button variant="primary" disabled={!ready} busy={busy} onClick={() => void render()}>
            <ActionIcon name="play" />Make the video
          </Button>
        </>
      }
    >
      <div className="story-dialog">
        {/* Save the story to continue later, and reopen a saved one - in the
            body, so the footer stays the decision. Mirrors AutoCut's bar. */}
        <div className="story-drafts-bar">
          {savedDrafts.length > 0 && (
            <Button variant="quiet" size="sm" aria-expanded={draftsOpen} onClick={() => setDraftsOpen((v) => !v)}>
              {draftsOpen ? "Hide saved" : `Saved drafts · ${savedDrafts.length}`}
            </Button>
          )}
          <Button
            variant="secondary" size="sm" busy={savingDraft}
            disabled={savingDraft || (!body.trim() && !picked.length)}
            onClick={() => void saveDraft()}
          >{draftId ? "Update draft" : "Save draft"}</Button>
        </div>
        {draftsOpen && savedDrafts.length > 0 && (
          <div className="story-drafts-list" role="list" aria-label="Saved stories">
            {savedDrafts.map((saved) => (
              <button
                key={saved.id}
                type="button"
                aria-current={saved.id === draftId ? "true" : undefined}
                className="story-draft-row"
                onClick={() => void resumeDraft(saved.id)}
              >
                <span className="story-draft-title">{saved.title}</span>
                <span className="story-draft-meta">
                  {saved.summary?.pictures ?? 0} pictures · {saved.status}
                </span>
              </button>
            ))}
          </div>
        )}
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
                  {lines.length === 1 ? "sentence" : "sentences"}.
                  {short && ` You picked ${picked.length}, so some repeat.`}</>
                : "Nothing to read yet."}
            {/* What it will cost, on the line that already exists rather than
                a status row of its own. Before the voice is spent, not after:
                going over does not come back as a refusal, it comes back as a
                half-made recording. */}
            {plan?.known && plan.character_limit > 0 && body.length > 0 && (
              overBudget
                ? <><br /><strong>
                  {body.length.toLocaleString()} characters, and this plan has{" "}
                  {plan.characters_left.toLocaleString()} left of{" "}
                  {plan.character_limit.toLocaleString()}.
                </strong></>
                : ` ${body.length.toLocaleString()} of ${plan.characters_left.toLocaleString()} characters left this month.`
            )}
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
            <label>Aspect ratio
              <SegmentedControl
                value={aspect}
                onChange={setAspect}
                options={ASPECTS}
                label="The aspect ratio of the finished video"
              />
            </label>
            {/* Before the voice, because it decides which voices there are. */}
            <label>Language
              <Select
                value={language}
                onChange={(event) => { setLanguage(event.target.value); setShowAll(false); }}
                aria-label="The language the script is read in"
              >
                <option value="">Any language</option>
                {languages.map((code, index) => (
                  <option
                    key={code}
                    value={code}
                    // A line under the last one this workspace works in.
                    // Seven of them lead a list of seventy-four, and without
                    // a break the eighth reads as the eighth favourite
                    // rather than the first of the rest.
                    data-separator-after={index === workspaceLanguages - 1 || undefined}
                  >{languageName(code)}</option>
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
                    {[
                      voice.name,
                      // When the list has widened past the language's own
                      // voices, say the voice reads it - the multilingual model
                      // does - rather than showing an accent that reads as the
                      // wrong language. Otherwise the accent is the useful note.
                      widened && language ? `reads ${languageName(language)}` : voice.accents?.[0],
                      // Only ever seen in the last-resort case above, where an
                      // unusable voice is all there is; normally these are gone.
                      // No leading dash - the " · " separator already divides it.
                      usable(voice) ? "" : "needs a paid plan",
                    ].filter(Boolean).join(" · ")}
                  </option>
                ))}
              </Select>
            </label>
            {/* Whether the picker is narrowed, said where the narrowing is.
                Checked means "showing everything", always, so the box and the
                list cannot disagree - it sat unchecked over an unnarrowed list
                for every language whose only checked voice cannot be used,
                which is the one case anybody needed it to explain.

                Disabled when the narrowing is impossible rather than hidden:
                the reason is worth reading even when there is nothing to
                decide. */}
            {language && (
              <label className="story-toggle">
                <input
                  type="checkbox"
                  checked={showAll || widened}
                  disabled={widened}
                  onChange={(event) => setShowAll(event.target.checked)}
                />
                <span>Show voices from other languages</span>
                <small>
                  {widened
                    ? `No voice checked in ${languageName(language)} can be used on this plan, `
                      + "so every voice is offered. The multilingual model reads it in any of them."
                    : `Off, so only voices checked in ${languageName(language)} are offered.`}
                </small>
              </label>
            )}
            {/* The dead end this used to be, and the two ways out of it. The
                key's voices are checked in eighteen languages; the models read
                seventy-four. Asking for one of the other fifty-six emptied the
                picker and read as "not supported", when what is true is that
                the voices exist and are not in this account. */}
            {language && widened && (
              <div className="story-voices-missing">
                <p className="story-note">
                  {verified.length
                    ? `The one voice checked in ${languageName(language)} cannot be used on `
                      + "this plan. These can be added:"
                    : `No voice on this key is checked in ${languageName(language)}. `
                      + "These can be added:"}
                </p>
                {offers.length > 0 && (
                  <ul className="story-shared">
                    {offers.slice(0, 8).map((voice) => (
                      <li key={voice.voice_id}>
                        <span>
                          <strong>{voice.name}</strong>
                          <small>
                            <span className="story-voice-accent">{voice.accent}</span>
                            {/* Why this one cannot be taken, on the row it
                                cannot be taken from. The three refusals are
                                different problems - a plan, a full shelf, and
                                one that already happened - and a badge saying
                                which is the difference between knowing what
                                to do and clicking again. */}
                            {!voice.addable && voice.reason && (
                              <Badge
                                tone={voice.reason.startsWith("Already") ? "good" : "warn"}
                                title={voice.reason.startsWith("Already")
                                  ? "This voice is already on your ElevenLabs key"
                                  : `${voice.reason}. Your key is on the ${plan?.tier || "current"} plan.`}
                              >{voice.reason}</Badge>
                            )}
                          </small>
                        </span>
                        <Button
                          variant="secondary"
                          size="sm"
                          busy={adding === voice.voice_id}
                          disabled={!voice.addable}
                          onClick={() => void addVoice(voice)}
                          title={voice.addable
                            ? `Add ${voice.name} to your ElevenLabs voices`
                            : voice.reason}
                        >Add</Button>
                      </li>
                    ))}
                  </ul>
                )}
                {/* What the plan actually allows, said once under the list.
                    The free tier holds three voices and is closed to most of
                    the library, and neither of those is guessable from a row
                    of names. */}
                {plan?.known && (
                  <p className="story-note">
                    {plan.tier ? `${plan.tier} plan` : "This plan"}
                    {" · "}
                    {plan.voice_slots_left > 0
                      ? `${plan.voice_slots_left} of ${plan.voice_limit} voice slots free`
                      : `all ${plan.voice_limit} voice slots used`}
                    {offers.some((voice) => !voice.addable && voice.reason.includes("paid"))
                      && ", and some of these need a paid plan"}
                  </p>
                )}
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
            {subtitles && captionStyles.length > 0 && (
              <label>Caption style
                <Select
                  value={captionStyle}
                  onChange={(event) => setCaptionStyle(event.target.value)}
                  aria-label="The caption look"
                >
                  <option value="">Plain subtitle</option>
                  {captionStyles.map((style) => (
                    <option key={style.id} value={style.id}>
                      {style.label}{style.needs_word_timings ? " · animated" : ""}
                    </option>
                  ))}
                </Select>
                {/* An animated caption lights up a word at a time, and a
                    voice from another service reports sentences rather than
                    words. The renderer already falls back to plain subtitles
                    on its own; saying so is the difference between choosing
                    that and discovering it in the finished video. */}
                {isExternal(effectiveVoiceId)
                  && captionStyles.find((style) => style.id === captionStyle)?.needs_word_timings && (
                  <small className="story-note">
                    This voice times sentences, not words, so the captions will be
                    plain. An ElevenLabs voice animates them.
                  </small>
                )}
              </label>
            )}
          </div>
        </section>

        <section className="story-pictures">
          <div className="story-section-head">
            <h4>Pictures <span className="story-count">{picked.length}</span></h4>
            <span className="story-head-controls">
              {/* Only while it is open. A choice between two sources is not
                  worth a permanent control when the thing it steers is shut. */}
              {browsing && (
                <SegmentedControl
                  value={source}
                  onChange={setSource}
                  options={SOURCES}
                  label="Where to add pictures from"
                />
              )}
              {/* The autonomous fill: search stock for every sentence and
                  arrange it, so a script with no pictures becomes a shot list
                  to review rather than an empty set to fill by hand. */}
              <Button
                variant="secondary"
                size="sm"
                busy={autoBusy === "fill"}
                disabled={!lines.length || brollReady === false || autoBusy !== ""}
                onClick={() => void fillFromStock()}
                title={brollReady === false && brollReason
                  ? brollReason
                  : "Search stock for every sentence and arrange it for review"}
              >
                <ActionIcon name="search" />Fill from stock
              </Button>
              <Button
                variant="secondary"
                size="sm"
                aria-expanded={browsing}
                disabled={full && !browsing}
                onClick={() => setBrowsing((current) => !current)}
              >
                <ActionIcon name={browsing ? "confirm" : "add"} />
                {browsing ? "Done adding" : "Add pictures"}
              </Button>
            </span>
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
                    // Icon-only and small, so the name is worth showing on
                    // hover as well as to a screen reader.
                    title={`Remove ${asset.title}`}
                    onClick={(event) => {
                      // onClick, not onPointerDown, so Enter/Space work too; the
                      // stop keeps it off the assign click on the tile beneath.
                      event.stopPropagation(); removePicture(asset.id);
                    }}
                  >×</button>
                </li>
              ))}
            </ul>
          )}
          {full && <p className="story-note">Two hundred pictures is the most one narration takes.</p>}

          {!browsing ? null : source === "library" ? (
            <div className="story-library">
              {/* The Library's own category tabs, by the Library's own class.
                  The kind is raised above the rest of the row because it is
                  the filter a narration asks about constantly - stills to cut
                  on the sentences, clips where something has to move - and a
                  third select in a row of selects is not that.

                  Wearing the page's control rather than a lookalike: this is a
                  Library dialog reached from the row it is copying, and the
                  same question asked twice in one surface should not be asked
                  by two different-looking things. Nothing is restyled here, so
                  the day that row changes this changes with it.

                  Audio is left off rather than listed and refused. A narration
                  plays pictures; there is no third thing for a sound file to
                  become here, and `keep` on the hook drops them on arrival. */}
              <div
                className="library-category-tabs story-media-kinds"
                role="group"
                aria-label={t("filters.byMediaKind")}
              >
                {kindChips.map((chip) => {
                  const active = (library.filters.mediaKind ?? "") === chip.key;
                  return (
                    <button
                      key={chip.key || "all"}
                      type="button"
                      className={active ? "selected" : ""}
                      aria-pressed={active}
                      onClick={() => library.setFilters({
                        ...library.filters,
                        mediaKind: chip.key as "" | "image" | "video",
                      }, true)}
                    >{chip.label} <span>{chip.count.toLocaleString()}</span></button>
                  );
                })}
              </div>
              {/* Everything else the Library narrows by, through the one shared
                  control (ADR 0025). It was four fields, which made the same
                  library answer a smaller question here than on the page the
                  pictures were picked from - and "the clip from this channel
                  with a reading on it, under fifteen seconds" is exactly how
                  somebody finds a shot for a sentence.

                  Six, not seven. The fields are 128px at their narrowest, so a
                  seventh wraps to a row of its own with a thousand pixels of
                  nothing beside it. The one dropped is `effect`: a rendered
                  blur or overlay says nothing about whether a clip suits a
                  sentence, while `processing` says whether it has been read at
                  all - and a clip with no reading is one the matcher can only
                  guess about. */}
              <AssetFilters
                values={library.filters}
                facets={library.facets}
                fields={[
                  "query", "channel", "platform", "processing",
                  "length", "downloaded",
                ]}
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
                {tiles.map((tile) => {
                  const inFlight = Boolean(importing[`${tile.kind}-${tile.id}`]);
                  return (
                    <li key={`${tile.kind}-${tile.id}`}>
                      <button type="button" onClick={() => void importTile(tile)}
                        disabled={inFlight} aria-busy={inFlight}
                        title={inFlight ? "Adding to the Library…" : `${tile.credit} - add to the Library`}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={tile.preview_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
                        {/* The credit is on the tile because the licence asks for
                            it wherever the media is shown, and this is one of the
                            places it is shown. */}
                        <small>{inFlight ? "Adding…" : tile.credit}</small>
                      </button>
                    </li>
                  );
                })}
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
                ? "Drag a row onto another to trade their pictures, or use the arrows. Pick a row and click a picture above to change one."
                : "Unarranged, so the pictures play in the order you chose them."}
              {" "}
              {/* One row per sentence, and mostly one shot per row - but the
                  cut needs the voice to know where it lands, so a sentence too
                  brief to hold a shot joins the one before it. Said here
                  because the alternative is a list that numbers eleven rows
                  and quietly renders ten. */}
              A sentence too short to hold a shot shares the one before it.
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
                    {/* The same trade, by keyboard. Alt is deliberate: the
                        arrows alone belong to the list they are scrolling. */}
                    <span className="story-shot-move">
                      <button
                        type="button"
                        aria-label={`Move this picture up to sentence ${index}`}
                        title="Trade with the shot above (Alt+Up)"
                        disabled={index === 0}
                        onClick={() => swap(index, index - 1)}
                      >↑</button>
                      <button
                        type="button"
                        aria-label={`Move this picture down to sentence ${index + 2}`}
                        title="Trade with the shot below (Alt+Down)"
                        disabled={index === lines.length - 1}
                        onClick={() => swap(index, index + 1)}
                      >↓</button>
                    </span>
                    <button
                      type="button"
                      className="story-shot-picture"
                      aria-pressed={focused === index}
                      title={asset
                        ? `${asset.title} - click, then click a picture above to change it`
                        : "Click, then click a picture above"}
                      onClick={() => setFocused((current) => (current === index ? null : index))}
                      onKeyDown={(event) => {
                        if (!event.altKey) return;
                        if (event.key === "ArrowUp" && index > 0) {
                          event.preventDefault();
                          swap(index, index - 1);
                        }
                        if (event.key === "ArrowDown" && index < lines.length - 1) {
                          event.preventDefault();
                          swap(index, index + 1);
                        }
                      }}
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
