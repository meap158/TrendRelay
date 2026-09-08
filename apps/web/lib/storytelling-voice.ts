/**
 * Choosing the language, the model and the voice a narration is read in.
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
 * Kept out of the dialog because these are the rules with the edge cases in
 * them, and a rule that can only be exercised by rendering a modal is a rule
 * nobody exercises.
 */

/** A voice on the operator's own key. */
export type Voice = {
  voice_id: string;
  name: string;
  /** Language ids somebody has checked this voice in. Not what it can say. */
  languages?: string[];
  accents?: string[];
};

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
 */
export function readableLanguages(models: Model[], name: (code: string) => string): string[] {
  const codes = new Set<string>();
  for (const model of models) {
    for (const item of model.languages ?? []) codes.add(item.language_id);
  }
  return [...codes].sort((left, right) => name(left).localeCompare(name(right)));
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
export function partitionVoices(
  voices: Voice[], language: string,
): { verified: Voice[]; others: Voice[] } {
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
export function offeredVoices(
  voices: Voice[], language: string, anyVoice: boolean,
): Voice[] {
  const { verified, others } = partitionVoices(voices, language);
  return !language || anyVoice ? [...verified, ...others] : verified;
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
  return offered[0]?.voice_id ?? "";
}
