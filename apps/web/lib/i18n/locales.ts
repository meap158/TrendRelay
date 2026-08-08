/**
 * The languages TrendRelay speaks, and what each one needs from the layout.
 *
 * Order is deliberate: English first as the source language every other
 * dictionary is written against, then the rest in the order they were asked
 * for. The picker shows them in this order.
 *
 * Each label is written in its own language. A speaker looking for their
 * language scans for the word they would use for it, not for the English name
 * of it — "Tiếng Việt", not "Vietnamese".
 */

export const LOCALES = [
  { code: "en", label: "English", english: "English", dir: "ltr" },
  { code: "vi", label: "Tiếng Việt", english: "Vietnamese", dir: "ltr" },
  { code: "ja", label: "日本語", english: "Japanese", dir: "ltr" },
  { code: "fr", label: "Français", english: "French", dir: "ltr" },
  { code: "zh", label: "中文", english: "Mandarin Chinese", dir: "ltr" },
  { code: "ru", label: "Русский", english: "Russian", dir: "ltr" },
  { code: "ar", label: "العربية", english: "Standard Arabic", dir: "rtl" },
] as const;

export type Locale = (typeof LOCALES)[number]["code"];
export type Direction = "ltr" | "rtl";

export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_CODES = LOCALES.map((item) => item.code);

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALE_CODES as string[]).includes(value);
}

export function directionOf(locale: Locale): Direction {
  return (LOCALES.find((item) => item.code === locale)?.dir ?? "ltr") as Direction;
}

/**
 * The BCP 47 tag for formatting. Intl wants a real tag, and `zh` alone leaves
 * it to guess between simplified and traditional; the dictionaries here are
 * simplified, so the tag says so.
 */
export function intlTagOf(locale: Locale): string {
  return locale === "zh" ? "zh-Hans" : locale;
}

/**
 * The best match for what the browser asks for, or English.
 *
 * Only consulted the first time, before anyone has chosen: a stored choice
 * always wins, because a person who picked a language meant it even when their
 * browser disagrees.
 */
export function preferredLocale(languages: readonly string[]): Locale {
  for (const tag of languages) {
    const base = tag.toLowerCase().split("-")[0];
    if (isLocale(base)) return base;
  }
  return DEFAULT_LOCALE;
}
