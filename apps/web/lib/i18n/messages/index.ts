import type { Locale } from "../locales";

import { ar } from "./ar";
import { en, type Messages } from "./en";
import { fr } from "./fr";
import { ja } from "./ja";
import { ru } from "./ru";
import { vi } from "./vi";
import { zh } from "./zh";

/**
 * Every dictionary, keyed by locale.
 *
 * Typed as `Messages` so a key added to English and forgotten elsewhere is a
 * compile error rather than a blank space someone notices in production. The
 * runtime still falls back to English, because a type error is what stops it
 * shipping and the fallback is what stops it being ugly if one ever slips.
 */
export const MESSAGES: Record<Locale, Messages> = { en, vi, ja, fr, zh, ru, ar };

export type { Messages };
