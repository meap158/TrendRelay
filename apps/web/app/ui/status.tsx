"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Transient status that does not move the page.
 *
 * Every console page announced the result of an action by rendering a banner
 * into the document flow. Showing one pushed everything below it down — 57px on
 * the downloads page — and hiding it pulled everything back up. An action that
 * cleared the old message before setting a new one did both in quick
 * succession, which is the flash: the row holding the button you just pressed
 * jumps out from under the cursor and back.
 *
 * These sit above the page instead. Nothing reflows, so an action can report
 * itself without the reader losing their place.
 *
 * Success messages retire on their own; failures do not. A failure the reader
 * blinked past is a failure they will hit again, so it stays until dismissed.
 */

export type StatusTone = "good" | "bad";

export type StatusMessage = {
  id: number;
  tone: StatusTone;
  text: string;
};

/** Long enough to read a sentence, short enough not to sit in the corner. */
const GOOD_MESSAGE_MS = 6000;

export function useStatus() {
  const [messages, setMessages] = useState<StatusMessage[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setMessages((current) => current.filter((item) => item.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback((tone: StatusTone, text: string | null | undefined) => {
    if (!text) return;
    const id = nextId.current++;
    // Replacing rather than stacking: these report one action's outcome, and a
    // column of them is the noise the notification drawer already collects.
    setMessages([{ id, tone, text }]);
    if (tone === "good") {
      const timer = setTimeout(() => {
        setMessages((current) => current.filter((item) => item.id !== id));
        timers.current.delete(id);
      }, GOOD_MESSAGE_MS);
      timers.current.set(id, timer);
    }
  }, []);

  const clear = useCallback(() => {
    timers.current.forEach((timer) => clearTimeout(timer));
    timers.current.clear();
    setMessages([]);
  }, []);

  const succeed = useCallback((text: string | null | undefined) => push("good", text), [push]);
  const fail = useCallback((text: string | null | undefined) => push("bad", text), [push]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((timer) => clearTimeout(timer));
      pending.clear();
    };
  }, []);

  return { messages, succeed, fail, dismiss, clear };
}

export function StatusToasts({
  messages,
  onDismiss,
}: {
  messages: StatusMessage[];
  onDismiss: (id: number) => void;
}) {
  if (!messages.length) return null;
  return (
    // aria-live rather than a role, so a screen reader hears the outcome
    // without the toast stealing focus from whatever was pressed.
    <div className="status-toasts" aria-live="polite">
      {messages.map((message) => (
        <div key={message.id} className={`status-toast status-toast-${message.tone}`}>
          <p>{message.text}</p>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => onDismiss(message.id)}
          >×</button>
        </div>
      ))}
    </div>
  );
}
