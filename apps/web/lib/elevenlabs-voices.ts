/**
 * Choosing the language, the model and the voice ElevenLabs speaks with.
 *
 * Three choices that look like one and are not, which is what made the picker
 * wrong in three different ways before this was written down.
 *
 * A voice's `verified_languages` says somebody checked it sounds good in that
 * language. It does not say what it can speak: a multilingual model reads any
 * language it supports in any voice. Measured on one real key, the voices are
 * verified in eighteen languages and the models speak seventy-four - so
 * offering the voices' list as the languages made fifty-six of them
 * unreachable, Vietnamese among them, in a workspace that runs in Vietnamese.
 *
 * The model is not a free choice either. `eleven_multilingual_v2` is the usual
 * default and reads twenty-nine languages; Vietnamese is not one, while three
 * other models on the same key do speak it. Sending the configured default
 * regardless is how a language the account can speak comes back refused.
 *
 * Kept out of the dialogs because these are the rules with the edge cases in
 * them, and a rule that can only be exercised by rendering a modal is a rule
 * nobody exercises. Read by the Storytelling picker and by the voiceover
 * editor, which asked the same question and got it wrong the same way.
 */

/** A voice on the operator's own key. */
export type Voice = {
  voice_id: string;
  name: string;
  /** Language ids somebody has checked this voice in. Not what it can say. */
  languages?: string[];
  accents?: string[];
  /**
   * Whether this plan may narrate with it, decided by the server.
   *
   * Having a voice and being allowed to speak with it are different things: a
   * free key can hold a voice added from the shared library and is refused at
   * synthesis every time. Absent means unknown, which is treated as usable -
   * the same permissive default the server uses when it cannot read the plan.
   */
  usable?: boolean;
  unusable_reason?: string;
};

/** Whether a voice may be narrated with. Unknown counts as yes. */
export function usable(voice: Voice | undefined): boolean {
  return voice ? voice.usable !== false : false;
}

/** A speech model, and the languages it can actually read. */
export type Model = {
  model_id: string;
  languages?: { language_id: string; name: string }[];
};

/** Whether a model can read a given language. */
function speaks(model: Model | undefined, language: string): boolean {
  return Boolean(model?.languages?.some((item) => item.language_id === language));
}

/**
 * Every language something on this key can read, from the models.
 *
 * Sorted by the caller's own name for each code rather than by the code, so
 * the list reads alphabetically to a person rather than to a computer.
 *
 * `preferred` comes first - the languages this workspace actually works in.
 * The models read seventy-four and a workspace uses a handful of them, so the
 * one being reached for is otherwise thirty rows down a list sorted for a
 * stranger. Preferred codes no model can read are dropped rather than offered:
 * being the workspace's language does not make it speakable.
 */
export function readableLanguages(
  models: Model[], name: (code: string) => string, preferred: readonly string[] = [],
): string[] {
  const codes = new Set<string>();
  for (const model of models) {
    for (const item of model.languages ?? []) codes.add(item.language_id);
  }
  const byName = (left: string, right: string) => name(left).localeCompare(name(right));
  const first = preferred.filter((code) => codes.has(code));
  const rest = [...codes].filter((code) => !first.includes(code)).sort(byName);
  return [...[...first].sort(byName), ...rest];
}

/**
 * What the dialog should open on.
 *
 * The language configured for ElevenLabs if there is one, otherwise the
 * language this workspace is being used in - somebody writing a script in a
 * workspace they run in Vietnamese is writing it in Vietnamese, and opening on
 * "any language" made them say so every time.
 *
 * Only ever a language something can read. The interface speaks seven and this
 * key's models seventy-four, and they are not the same seventy-four; defaulting
 * to one that is missing opens the dialog on a language nothing can say, with
 * a picker showing a value that is not among its own options.
 */
export function openingLanguage(
  { configured, locale, models }: { configured: string; locale: string; models: Model[] },
): string {
  if (configured) return configured;
  return models.some((model) => speaks(model, locale)) ? locale : "";
}

/**
 * The voices split into the ones checked in this language and the rest.
 *
 * With no language chosen there is nothing to check against, so everything is
 * "checked" and nothing is left over.
 */
export function partitionVoices<T extends Voice>(
  voices: T[], language: string,
): { verified: T[]; others: T[] } {
  if (!language) return { verified: voices, others: [] };
  return {
    verified: voices.filter((voice) => (voice.languages ?? []).includes(language)),
    others: voices.filter((voice) => !(voice.languages ?? []).includes(language)),
  };
}

/**
 * The voices to offer for a language.
 *
 * Filtered to the language's own, which is the point: a key with three hundred
 * voices offers a handful in any one language and scrolling past the rest is
 * the whole problem. `anyVoice` is the escape, because the filter can empty the
 * list entirely - the model reads the language in any voice, nobody has just
 * checked how that one sounds doing it.
 */
/** Usable voices first; the rest kept, marked, in their original order. */
function speakableFirst<T extends Voice>(voices: T[]): T[] {
  return [...voices.filter(usable), ...voices.filter((voice) => !usable(voice))];
}

/** What a language offers, and whether its filter had to be let go of. */
export type Offering<T extends Voice = Voice> = {
  voices: T[];
  /**
   * True when narrowing to the language left nothing that could be spoken
   * with, so every usable voice is offered instead.
   */
  widened: boolean;
};

/**
 * The voices to offer for a language, and never a dead end.
 *
 * Narrowing to the language is the point - a key with three hundred voices
 * offers a handful in any one, and scrolling past the rest is the whole
 * problem. But `verified_languages` is a quality label, not a capability: it
 * says somebody checked how a voice sounds in a language, not that it is the
 * only voice that can say it. A multilingual model reads any language it
 * supports in any voice.
 *
 * So when the filter leaves nothing that can actually be used, it is let go of
 * rather than honoured into a wall. That state is ordinary, not exotic: this
 * key has one voice verified in Vietnamese, it is a library voice, and a free
 * plan cannot narrate with library voices - so asking for Vietnamese offered
 * exactly one voice and no way to use it, while twenty-one premade voices that
 * read Vietnamese perfectly well sat one filter away.
 *
 * `widened` is returned rather than inferred so the interface can say why the
 * list is not what was asked for. A list that quietly ignores the filter is
 * its own kind of wrong.
 */
export function offerVoices<T extends Voice>(
  voices: T[], language: string, anyVoice: boolean,
): Offering<T> {
  if (!language) return { voices: speakableFirst(voices), widened: false };
  const { verified } = partitionVoices(voices, language);
  if (!anyVoice && verified.some(usable)) {
    return { voices: speakableFirst(verified), widened: false };
  }
  // Everything, because narrowing would leave nothing to speak with. Ordered
  // so the list agrees with the choice made from it: usable before unusable,
  // and checked in this language before not. A default that lands on the first
  // voice somebody can actually use, sitting third in the list, reads as the
  // wrong voice being chosen.
  const checked = new Set(verified.map((voice) => voice.voice_id));
  const rank = (voice: T) =>
    (usable(voice) ? 0 : 2) + (checked.has(voice.voice_id) ? 0 : 1);
  return {
    voices: [...voices].sort((left, right) => rank(left) - rank(right)),
    widened: !anyVoice,
  };
}

/** The offered voices alone, for callers with nothing to say about widening. */
export function offeredVoices<T extends Voice>(
  voices: T[], language: string, anyVoice: boolean,
): T[] {
  return offerVoices(voices, language, anyVoice).voices;
}

/**
 * Which model reads it: the configured one unless it cannot say the words.
 *
 * Another model only wins when the configured one is not an answer at all.
 * Overriding a working choice would be this fix causing its own kind of
 * surprise - the operator picked that model for a reason.
 */
export function modelFor(models: Model[], language: string, configuredId: string): string {
  if (!language) return "";
  const configured = models.find((model) => model.model_id === configuredId);
  if (speaks(configured, language)) return configured!.model_id;
  return models.find((model) => speaks(model, language))?.model_id ?? "";
}

/**
 * The voice actually used: the chosen one while it is still on offer, and
 * otherwise the first that is.
 *
 * Derived rather than written back over the choice. Correcting the stored
 * value loses it permanently, so narrowing to a language and widening again
 * would not return the voice somebody picked - and in React it also means a
 * render that immediately schedules another.
 */
export function effectiveVoice(offered: Voice[], chosenId: string): string {
  if (chosenId && offered.some((voice) => voice.voice_id === chosenId)) return chosenId;
  // Defaults to one that works. Falling back to the first of a list whose head
  // is unusable would arm the render with a voice the plan refuses.
  return (offered.find(usable) ?? offered[0])?.voice_id ?? "";
}

/**
 * Why this narration cannot be made with the voice chosen, if it cannot.
 *
 * The check belongs before the render rather than inside its failure: a
 * refusal at synthesis arrives after the pictures are gathered, the script is
 * written and the job is queued, and reads as the story failing rather than as
 * the voice never having been allowed.
 */
export function blockedReason(offered: Voice[], chosenId: string): string {
  const voice = offered.find((item) => item.voice_id === chosenId);
  if (!voice || usable(voice)) return "";
  return voice.unusable_reason || "This plan cannot narrate with that voice";
}
