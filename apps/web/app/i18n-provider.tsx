"use client";

/**
 * The chosen language, and the text that follows from it.
 *
 * A context rather than routed locales. This is a private dashboard behind
 * auth, so per-language URLs buy nothing, and switching here changes the whole
 * app in place with no navigation and no reload — which is what "easy to
 * switch" has to mean for someone comparing two phrasings.
 *
 * The choice is restored in a microtask rather than during render, for the same
 * reason every other stored preference here is: the server has no access to it,
 * and reading it synchronously would make the first client render disagree with
 * the server's and blank the page.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  DEFAULT_LOCALE,
  Direction,
  Locale,
  directionOf,
  intlTagOf,
  isLocale,
  preferredLocale,
} from "../lib/i18n/locales";
import { MESSAGES } from "../lib/i18n/messages";
import type { Messages } from "../lib/i18n/messages/en";

const STORAGE_KEY = "trendrelay.locale";

type Values = Record<string, string | number>;

type LocaleContextValue = {
  locale: Locale;
  dir: Direction;
  setLocale: (next: Locale) => void;
  t: (path: string, values?: Values) => string;
  format: {
    number: (value: number) => string;
    compact: (value: number) => string;
    date: (value: Date | string | number) => string;
    time: (value: Date | string | number) => string;
  };
};

const LocaleContext = createContext<LocaleContextValue | null>(null);

/** Walk a dotted key. Returns undefined rather than throwing on a bad path. */
function lookup(source: unknown, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (node, key) =>
        node && typeof node === "object" ? (node as Record<string, unknown>)[key] : undefined,
      source,
    );
}

/**
 * Fill `{name}` placeholders, and pick a plural form for
 * `{count, plural, one {# video} other {# videos}}`.
 *
 * Deliberately small. A full ICU implementation is a dependency and a parser;
 * what the interface actually uses is named substitution and the one/other
 * split, and the languages here that need more than one/other — Russian and
 * Arabic — are handled by `Intl.PluralRules` choosing the category, with the
 * dictionary supplying whichever categories that language uses.
 */
function interpolate(template: string, values: Values | undefined, tag: string): string {
  if (!values) return template;
  return template.replace(
    /\{(\w+)(?:,\s*plural,\s*([^}]*(?:\{[^}]*\}[^}]*)*))?\}/g,
    (whole, name: string, pluralBody: string | undefined) => {
      const value = values[name];
      if (value === undefined) return whole;
      if (!pluralBody) return String(value);

      const forms = new Map<string, string>();
      for (const match of pluralBody.matchAll(/(\w+)\s*\{([^}]*)\}/g)) {
        forms.set(match[1]!, match[2]!);
      }
      const count = Number(value);
      const category = new Intl.PluralRules(tag).select(count);
      const chosen =
        forms.get(`=${count}`) ?? forms.get(category) ?? forms.get("other") ?? "";
      return chosen.replace(/#/g, new Intl.NumberFormat(tag).format(count));
    },
  );
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);
  const restored = useRef(false);

  useEffect(() => {
    queueMicrotask(() => {
      try {
        const stored = window.localStorage.getItem(STORAGE_KEY);
        if (isLocale(stored)) {
          setLocaleState(stored);
        } else {
          // Only when nothing was ever chosen. A stored choice always wins:
          // someone who picked a language meant it, even if the browser says
          // otherwise.
          setLocaleState(preferredLocale(navigator.languages ?? [navigator.language]));
        }
      } catch {
        // A preference that cannot be read is not worth reporting; English is
        // always a usable answer.
      } finally {
        restored.current = true;
      }
    });
  }, []);

  const dir = directionOf(locale);

  useEffect(() => {
    // The document element, not a wrapper: `dir` has to be on <html> for form
    // controls, scrollbars and text selection to flip with it, and `lang` is
    // what a screen reader reads the page with.
    const root = document.documentElement;
    root.lang = locale;
    root.dir = dir;
  }, [locale, dir]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage can be full or blocked. Losing the preference is not worth
      // interrupting anyone over; the session still switches.
    }
  }, []);

  const value = useMemo<LocaleContextValue>(() => {
    const tag = intlTagOf(locale);
    const dictionary = MESSAGES[locale] as Messages;

    const t = (path: string, values?: Values): string => {
      const found = lookup(dictionary, path) ?? lookup(MESSAGES.en, path);
      if (typeof found !== "string") {
        // The key itself, so a gap is visible in the interface rather than
        // rendering an empty space nobody notices in review.
        return path;
      }
      return interpolate(found, values, tag);
    };

    return {
      locale,
      dir,
      setLocale,
      t,
      format: {
        number: (v) => new Intl.NumberFormat(tag).format(v),
        compact: (v) =>
          new Intl.NumberFormat(tag, {
            notation: "compact",
            maximumFractionDigits: 1,
          }).format(v),
        date: (v) => new Intl.DateTimeFormat(tag, { dateStyle: "medium" }).format(new Date(v)),
        time: (v) =>
          new Intl.DateTimeFormat(tag, { hour: "numeric", minute: "2-digit" }).format(
            new Date(v),
          ),
      },
    };
  }, [locale, dir, setLocale]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext);
  if (!value) {
    throw new Error("useLocale must be used inside a LocaleProvider.");
  }
  return value;
}

/** The common case: just the translator. */
export function useT() {
  return useLocale().t;
}
