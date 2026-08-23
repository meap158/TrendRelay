/**
 * The languages a transcription can be told to expect.
 *
 * The speech model detects a language per clip when it is given none, and that
 * is the right default - naming one the model then disagrees with is worse than
 * letting it decide. This list is the override for when you already know the
 * answer and detection is unreliable (music over speech, a few seconds of
 * audio), so it is the set the model itself can transcribe rather than the seven
 * the interface is translated into.
 *
 * Codes are what the model takes (`media_ai` passes them straight through, with
 * an empty value meaning "detect"). Names are not hard-coded: `Intl.DisplayNames`
 * renders each code in the reader's own language, and falls back to the bare
 * code only for the few the platform cannot name.
 */

/** The speech model's supported languages, as the codes it accepts. */
export const TRANSCRIPTION_LANGUAGE_CODES = [
  "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs", "ca",
  "cs", "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi", "fo", "fr",
  "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht", "hu", "hy", "id", "is", "it",
  "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb", "ln", "lo", "lt", "lv",
  "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my", "ne", "nl", "nn", "no",
  "oc", "pa", "pl", "ps", "pt", "ro", "ru", "sa", "sd", "si", "sk", "sl", "sn",
  "so", "sq", "sr", "su", "sv", "sw", "ta", "te", "tg", "th", "tk", "tl", "tr",
  "tt", "uk", "ur", "uz", "vi", "yi", "yo", "yue", "zh",
] as const;

/** The platform's own names for a couple of codes `Intl.DisplayNames` misses. */
const NAME_OVERRIDES: Record<string, string> = {
  jw: "Javanese",
  haw: "Hawaiian",
  yue: "Cantonese",
};

/**
 * Each code paired with a readable name in the given locale, sorted by name.
 *
 * Built once per locale by the caller. A code the platform names only as itself
 * (it returns the code back) falls to any override and then to the upper-cased
 * code, so an unknown language is still a row rather than a gap.
 */
export function transcriptionLanguageOptions(
  locale: string,
): { value: string; label: string }[] {
  let display: Intl.DisplayNames | null = null;
  try {
    display = new Intl.DisplayNames([locale], { type: "language", fallback: "none" });
  } catch {
    display = null;
  }
  return TRANSCRIPTION_LANGUAGE_CODES.map((code) => {
    const named = display?.of(code);
    const label = named && named.toLowerCase() !== code
      ? named
      : NAME_OVERRIDES[code] ?? code.toUpperCase();
    return { value: code, label };
  }).sort((a, b) => a.label.localeCompare(b.label, locale));
}
