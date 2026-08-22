"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * State that survives a reload.
 *
 * A user change is written immediately instead of waiting for a later effect.
 * That matters when a selection is followed straight away by navigation or a
 * closed tab: the preference has already reached storage before this component
 * can unmount. Restoring uses the internal state setter, so it never writes the
 * server-rendered default over the saved value.
 *
 * Restore happens in a microtask rather than during render because the stored
 * value is not available while the server renders the page, and reading it
 * synchronously would make the first client render disagree with the server's.
 */
export function usePersistedState<T>(
  key: string,
  initial: T,
  /** Guards against a stored value that no longer fits the option it drives. */
  isValid: (value: unknown) => value is T,
): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(initial);

  useEffect(() => {
    queueMicrotask(() => {
      try {
        const raw = window.localStorage.getItem(key);
        if (raw !== null) {
          const parsed: unknown = JSON.parse(raw);
          if (isValid(parsed)) setValue(parsed);
        }
      } catch {
        // A preference that cannot be read is not worth reporting; the default
        // is always a usable answer.
      }
    });
    // Restoring once per key is the whole point; isValid is a predicate whose
    // identity changes every render and would restart this forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const store = useCallback((next: T) => {
    setValue(next);
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // Storage can be full or blocked; losing a preference is not an error
      // worth interrupting anyone over.
    }
  }, [key]);

  return [value, store];
}

/** Accepts one of a fixed set of options, so a stale stored value cannot stick. */
export function oneOf<T extends string>(...allowed: T[]) {
  return (value: unknown): value is T =>
    typeof value === "string" && (allowed as string[]).includes(value);
}

/** Accepts a list drawn from a fixed set, used for multi-choice options. */
export function subsetOf<T extends string>(...allowed: T[]) {
  return (value: unknown): value is T[] =>
    Array.isArray(value)
    && value.length > 0
    && value.every((item) => typeof item === "string" && (allowed as string[]).includes(item));
}

/** Accepts one of a fixed set of numbers, such as a page-size preset. */
export function numberIn(...allowed: number[]) {
  return (value: unknown): value is number =>
    typeof value === "number" && allowed.includes(value);
}

/**
 * Fetched data that survives a reload, but not indefinitely.
 *
 * Different from `usePersistedState`, which stores a preference: a preference
 * is correct until changed, whereas a board of trending videos is correct for
 * about as long as it takes to make a cup of tea. Restoring a day-old hot list
 * as though it were current would be worse than an empty panel, because an
 * empty panel is obviously empty.
 *
 * So the value is stored with the time it was fetched and comes back only while
 * it is younger than `maxAgeMs`. The age comes back with it, which is what lets
 * the interface say when it last looked rather than implying it just did.
 *
 * Douyin's cover images are the reason the age is not merely cosmetic: they are
 * signed URLs with their own expiry, so a board restored hours later renders a
 * grid of broken thumbnails. Better to treat the whole payload as perishable.
 *
 * `ready` is what stops a caller fetching over its own cache. The restore
 * happens in a microtask, so an effect that runs on mount sees `null` and
 * cannot tell "nothing was stored" from "the restore has not run yet" - and
 * would refetch every time, which is the opposite of the point.
 */
export function usePersistedCache<T>(
  key: string,
  maxAgeMs: number,
  isValid: (value: unknown) => value is T,
): [T | null, (next: T | null) => void, number | null, boolean] {
  const [value, setValue] = useState<T | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    queueMicrotask(() => {
      try {
        const raw = window.localStorage.getItem(key);
        if (raw !== null) {
          const parsed = JSON.parse(raw) as { savedAt?: unknown; value?: unknown };
          const stamp = typeof parsed.savedAt === "number" ? parsed.savedAt : 0;
          if (Date.now() - stamp <= maxAgeMs && isValid(parsed.value)) {
            setValue(parsed.value);
            setSavedAt(stamp);
          } else {
            // Expired entries are removed rather than left to be re-read and
            // re-rejected on every visit.
            window.localStorage.removeItem(key);
          }
        }
      } catch {
        // Unreadable cache is not worth reporting; a refetch is always correct.
      } finally {
        setReady(true);
      }
    });
    // Restoring once per key is the point; isValid is a fresh function each
    // render and would restart this forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, maxAgeMs]);

  const store = useCallback((next: T | null) => {
    setValue(next);
    const stamp = next === null ? null : Date.now();
    setSavedAt(stamp);
    try {
      if (next === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, JSON.stringify({ savedAt: stamp, value: next }));
    } catch {
      // Storage can be full or blocked. The session still works; only the
      // survival across a reload is lost, which is the feature, not the app.
    }
  }, [key]);

  return [value, store, savedAt, ready];
}
