/**
 * Translating the effect catalogue, which the API owns.
 *
 * The editor renders text it is given rather than text it holds: the label,
 * summary, parameter names, help and choice options all arrive from
 * `/api/effects`, in English, because that is where the registry lives. So the
 * i18n sweep over `.tsx` files could reach 100% while the one panel where
 * somebody tunes a blur stayed entirely English.
 *
 * The fix is a lookup rather than more plumbing. The registry already has
 * stable ids — `face_blur`, `padding_ratio`, `9:16` — so the dictionary is
 * keyed by those and the API's own English is the fallback. That keeps the
 * locale in the browser with the rest of it, and means a newly registered
 * effect shows up in English immediately and gets translated later, instead of
 * showing up blank.
 */

/** Option values are not identifiers — `9:16` and `90` have to become keys. */
const OPTION_KEYS: Record<string, string> = {
  horizontal: "horizontal",
  vertical: "vertical",
  both: "both",
  "90": "right90",
  "180": "half",
  "270": "left90",
  "9:16": "vertical916",
  "4:5": "portrait45",
  "1:1": "square11",
  "16:9": "landscape169",
  centre: "middle",
  top: "top",
  bottom: "bottom",
  largest: "mainFace",
  all: "everyone",

  /**
   * The objects that ship with the overlay catalogue.
   *
   * These are chrome after all. The first reading was that a catalogue an
   * operator can add to is content and therefore stays as written — true of
   * what they add, and not of the dozen objects shipped here, which are fixed
   * strings exactly like every effect label above. Leaving them out put an
   * English "Cover the face" directly under a translated effect title.
   *
   * A dropped-in object is still content: its id is not in this table, so it
   * keeps the name its file was given.
   */
  censor_block: "censorBlock",
  smiley: "smiley",
  robot: "robot",
  skull: "skull",
  ghost: "ghost",
  censor_bar: "censorBar",
  sunglasses: "sunglasses",
  face_mask: "faceMask",
  moustache: "moustache",
  cat_ears: "catEars",
  crown: "crown",
  party_hat: "partyHat",
  pixel_mask: "pixelMask",
  alien: "alien",
  flower_face: "flowerFace",
  cloud_face: "cloudFace",
  heart_eyes: "heartEyes",
  star_glasses: "starGlasses",
  cyber_visor: "cyberVisor",
  dog_nose: "dogNose",
  halo: "halo",
  devil_horns: "devilHorns",
  graduation_cap: "graduationCap",
  headphones: "headphones",
  heart_bubble: "heartBubble",
  lightning: "lightning",
  sparkles: "sparkles",
  live_badge: "liveBadge",
  focus_frame: "focusFrame",
  comment_bubble: "commentBubble",
  tap_cursor: "tapCursor",
};

type Translate = (path: string, values?: Record<string, string | number>) => string;

/**
 * Look up a key, and fall back to what the API said.
 *
 * `t()` returns the path itself when a key is missing, which is what makes an
 * omission visible during a sweep. Here that would put `fx.face_blur.label` in
 * front of a user, so a miss falls back to the English the API already sent —
 * untranslated text beats a dotted path.
 */
function fromDictionary(t: Translate, key: string, apiText: string): string {
  const found = t(key);
  return found === key ? apiText : found;
}

export function effectLabel(t: Translate, id: string, apiLabel: string): string {
  return fromDictionary(t, `fx.${id}.label`, apiLabel);
}

export function effectSummary(t: Translate, id: string, apiSummary: string): string {
  return fromDictionary(t, `fx.${id}.summary`, apiSummary);
}

export function paramLabel(
  t: Translate, effectId: string, paramId: string, apiLabel: string,
): string {
  return fromDictionary(t, `fx.${effectId}.${paramId}`, apiLabel);
}

export function paramHelp(
  t: Translate, effectId: string, paramId: string, apiHelp: string,
): string {
  return fromDictionary(t, `fx.${effectId}.${paramId}Help`, apiHelp);
}

export function optionLabel(
  t: Translate, effectId: string, value: string, apiLabel: string,
): string {
  const key = OPTION_KEYS[value];
  return key ? fromDictionary(t, `fx.${effectId}.${key}`, apiLabel) : apiLabel;
}

/**
 * The heading a gallery section sits under.
 *
 * Keyed on the id the API sends rather than on the English heading itself:
 * keying on the wording would work right up until somebody improved it, at
 * which point six locales would quietly revert to English with nothing failing.
 * A group with no id is a folder an operator named, and keeps their name.
 */
export function optionGroup(
  t: Translate, groupId: string | undefined | null, apiName: string,
): string {
  return groupId ? fromDictionary(t, `fx.groups.${groupId}`, apiName) : apiName;
}
