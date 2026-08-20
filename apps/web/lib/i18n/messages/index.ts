import type { Locale } from "../locales";

import { en, type Messages } from "./en";

/**
 * English is always in the bundle.
 *
 * It is both the default locale and the fallback every lookup falls through to,
 * so it has to be available synchronously - before any chosen language's chunk
 * has loaded, and on the server, where rendering cannot await an import. Every
 * `t()` can answer from here the instant the app mounts.
 */
export const EN: Messages = en;

/**
 * A locale's dictionary, from its own chunk.
 *
 * The six non-English dictionaries were about 450 KB of source that every page
 * shipped while only one locale ever read one of them. Each is its own dynamic
 * `import()` now, fetched when a language is actually chosen. English is already
 * present as the fallback, so the interface renders immediately in it and swaps
 * to the chosen language once its chunk arrives - a brief, one-time flash of
 * English on a non-English first visit, in exchange for a much lighter page.
 */
export async function loadMessages(locale: Locale): Promise<Messages> {
  switch (locale) {
    case "vi": return (await import("./vi")).vi;
    case "ja": return (await import("./ja")).ja;
    case "fr": return (await import("./fr")).fr;
    case "zh": return (await import("./zh")).zh;
    case "ru": return (await import("./ru")).ru;
    case "ar": return (await import("./ar")).ar;
    // English, and the safe answer for any locale that is not one of the above.
    default: return en;
  }
}

export type { Messages };
