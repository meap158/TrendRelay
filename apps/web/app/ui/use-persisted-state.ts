"use client";

import { useEffect, useRef, useState } from "react";

/**
 * State that survives a reload.
 *
 * The save deliberately skips its first run. On mount the value is still the
 * default, and writing that would erase the stored one before the restore has
 * read it - the save would win the race against its own restore. That cost a
 * debugging round the first time it was written by hand, which is why every
 * persisted option now goes through here instead.
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
  const restored = useRef(false);

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
      } finally {
        restored.current = true;
      }
    });
    // Restoring once per key is the whole point; isValid is a predicate whose
    // identity changes every render and would restart this forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    if (!restored.current) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage can be full or blocked; losing a preference is not an error
      // worth interrupting anyone over.
    }
  }, [key, value]);

  return [value, setValue];
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
