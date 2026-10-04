/**
 * The autopilot's "Last run" note, split into what a person scans.
 *
 * The API stores the note as one paragraph: an outcome, then one sentence
 * per reason a slot went unused, some with a "(5 of 7 slots)" count, then
 * whatever the approval announcer added. Read as a block it runs three
 * destinations together, and two pages with near-identical names cannot be
 * told apart. Several writers produce it (the scheduler, the runner, the
 * approval announcer, the kill switch), so the stored text stays as it is
 * and this only gives it shape: the first sentence is the headline, each
 * other sentence is a row, and a row that opens with one of the campaign's
 * destinations is tied to that destination so its network can be shown.
 */

export type RunNoteSubject<P extends string = string> = { label: string; platform: P };

export type RunNoteLine<P extends string = string> = {
  /** The sentence without its destination and without its slot count. */
  text: string;
  /** The destination the sentence opens with, when it is one of the campaign's. */
  subject: RunNoteSubject<P> | null;
  /** A network named in the sentence when no destination opens it. */
  platform: P | null;
  /** How many of the run's slots this reason cost. */
  slots: { used: number; total: number } | null;
};

export type RunNote<P extends string = string> = {
  headline: string;
  lines: RunNoteLine<P>[];
};

const SLOTS = /\s*\((\d+) of (\d+) slots\)$/;

/**
 * Sentences, keeping a trailing "(N of M slots)" with the sentence it counts.
 *
 * A boundary is whitespace after sentence punctuation or after a slot count,
 * unless the next thing is that count. A period inside a name such as
 * "halcyonbooks.official" is not followed by whitespace, so it stays put.
 */
export function runNoteSentences(note: string): string[] {
  return note
    .trim()
    .split(/(?<=[.!?]|\(\d+ of \d+ slots\))\s+(?!\(\d+ of \d+ slots\))/u)
    .map((part) => part.trim())
    .filter(Boolean);
}

export function parseRunNote<P extends string>(
  note: string,
  destinations: readonly RunNoteSubject<P>[],
  networks: Readonly<Record<P, string>>,
): RunNote<P> {
  const [headline = "", ...rest] = runNoteSentences(note);
  // Longest first, so "Shop VN" is not read as "Shop" followed by "VN ...".
  const known = [...destinations]
    .filter((item) => item.label.trim())
    .sort((a, b) => b.label.length - a.label.length);
  const names = (Object.entries(networks) as [P, string][])
    .filter(([, name]) => name.trim());
  return {
    headline,
    lines: rest.map((sentence) => {
      const counted = SLOTS.exec(sentence);
      const body = counted ? sentence.slice(0, counted.index).trim() : sentence;
      const slots = counted
        ? { used: Number(counted[1]), total: Number(counted[2]) }
        : null;
      const subject = known.find((item) => body.startsWith(`${item.label} `)) ?? null;
      const text = subject ? body.slice(subject.label.length).trim() : body;
      const platform = subject
        ? null
        : names.find(([, name]) => new RegExp(`\\b${escape(name)}\\b`, "u").test(body))?.[0] ?? null;
      return { text, subject, platform, slots };
    }),
  };
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
