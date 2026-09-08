/**
 * The arrangement: which picture is on screen for which sentence.
 *
 * One entry per sentence, by asset id, and an empty entry means "nothing has
 * said" - the render then falls back to playing the pictures in the order they
 * were chosen, which is what arranging them by hand in the Library meant.
 *
 * The array is deliberately allowed to be shorter than the script. A script is
 * typed and re-typed while pictures are being gathered, so the sentence count
 * moves under the arrangement constantly; every operation here grows it to fit
 * rather than assuming it already does. Reading past the end of a shorter array
 * is the one way this goes wrong silently - `undefined` where an id was
 * expected renders an empty shot rather than an error.
 */

/** One picture per sentence, by asset id. `""` means unarranged. */
export type Arrangement = string[];

/** What the matcher answered for one sentence. */
export type Suggestion = {
  line: number;
  asset_id: string;
  score: number;
  matched?: string[];
};

/** The arrangement grown to hold `length` sentences, without moving any. */
function fitted(current: Arrangement, length: number): Arrangement {
  const next = current.slice(0, length);
  while (next.length < length) next.push("");
  return next;
}

/**
 * The matcher's answer as an arrangement, one entry per sentence.
 *
 * Built by position rather than by order of arrival: the suggestions carry the
 * line they are for, and nothing promises they come back in that order.
 */
export function fromSuggestions(found: Suggestion[], lineCount: number): Arrangement {
  const next = Array<string>(lineCount).fill("");
  for (const item of found) {
    if (item.line >= 0 && item.line < lineCount) next[item.line] = item.asset_id;
  }
  return next;
}

/** The words each sentence was matched on, for showing why. */
export function reasonsFrom(found: Suggestion[]): Record<number, string[]> {
  const reasons: Record<number, string[]> = {};
  for (const item of found) reasons[item.line] = item.matched ?? [];
  return reasons;
}

/** Put one picture on one sentence. */
export function assign(
  current: Arrangement, line: number, assetId: string, lineCount: number,
): Arrangement {
  if (line < 0 || line >= lineCount) return current;
  const next = fitted(current, lineCount);
  next[line] = assetId;
  return next;
}

/**
 * Trade two sentences' pictures.
 *
 * A swap rather than an insert: there is one picture per sentence and the
 * sentences themselves do not move - the script decides their order, not the
 * editor. Dragging row three onto row one in a list of shots means "these two
 * are the wrong way round", not "renumber everything below".
 */
export function swap(
  current: Arrangement, from: number, to: number, lineCount: number,
): Arrangement {
  if (from === to) return current;
  if (from < 0 || to < 0 || from >= lineCount || to >= lineCount) return current;
  const next = fitted(current, lineCount);
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

/**
 * The arrangement with a removed picture taken out of it.
 *
 * Rather than left pointing at media the video no longer has. The render
 * blanks an unknown id anyway; doing it here is the difference between a shot
 * list that is true and one that only renders true.
 */
export function withoutPicture(current: Arrangement, assetId: string): Arrangement {
  return current.map((id) => (id === assetId ? "" : id));
}

/**
 * What to send with a render: the arrangement, or nothing at all.
 *
 * An array of empty strings is not an arrangement, and sending one would claim
 * every sentence had been given a picture and told to show nothing.
 */
export function forRender(current: Arrangement): Arrangement {
  return current.some(Boolean) ? current : [];
}
