/**
 * Wording why a track was suggested, in the reader's language.
 *
 * The API sends the parts - a kind, and whatever that kind is about - rather
 * than a sentence, for the same reason `i18n/effects.ts` exists: a phrase
 * composed on the server is English on every screen however well the panel
 * around it is translated. The kinds are the stable names the dictionary is
 * keyed by, so a kind added later shows up as nothing rather than as a raw
 * word, and gets its key when somebody writes it.
 */

type Translate = (path: string, values?: Record<string, string | number>) => string;

/** Why one track is worth offering, as the suggestions route sends it. */
export type MusicReason = {
  kind: string;
  words: string[];
  mood: string;
  bpm: number | null;
};

/**
 * Words joined the way the reader's language joins them.
 *
 * `type: "unit"` rather than "conjunction": these are search terms, not a
 * sentence, so English wants "ocean, small, boat" and not "ocean, small and
 * boat" - while Chinese still gets its own separator rather than a comma we
 * chose for it.
 */
function list(words: string[]): string {
  return new Intl.ListFormat(undefined, { style: "long", type: "unit" }).format(words);
}

/** A mood in the reader's language, or the registry's own word for it. */
function mood(t: Translate, name: string): string {
  const key = `music.moods.${name}`;
  const found = t(key);
  return found === key ? name : found;
}

/**
 * One reason as a line to show under a track, or "" when there is nothing to say.
 *
 * Empty rather than a placeholder: a row with no reason is a row that simply
 * does not explain itself, which is better than one explaining itself badly.
 */
export function reasonText(t: Translate, reason: MusicReason | null | undefined): string {
  if (!reason) return "";
  if (reason.kind === "pacing") {
    if (!reason.mood) return "";
    const named = mood(t, reason.mood);
    return reason.bpm
      ? t("music.becausePacingTempo", { mood: named, bpm: reason.bpm })
      : t("music.becausePacing", { mood: named });
  }
  const words = reason.words ?? [];
  if (!words.length) return "";
  if (reason.kind === "tags") {
    // A hashtag run, not a prose list: "#coffee #espresso" is how they were
    // written on the clip and how a reader expects to see them back.
    return t("music.becauseTags", { words: words.map((word) => `#${word}`).join(" ") });
  }
  if (reason.kind === "script") return t("music.becauseScript", { words: list(words) });
  if (reason.kind === "titles") return t("music.becauseTitles", { words: list(words) });
  if (reason.kind === "match") return t("music.becauseMatch", { words: list(words) });
  return "";
}
