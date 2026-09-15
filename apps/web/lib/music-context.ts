/**
 * What is being made, reduced to what the suggestions route takes.
 *
 * Two small rules live here rather than inside the picker, because both
 * decide whether the network is touched and neither can be seen in a
 * component: whether a piece has anything to suggest from at all, and what
 * counts as the same piece between two renders. A dialog rebuilds its context
 * object on every keystroke; asking again for an object that says exactly what
 * the last one said is a request nobody needed.
 */

/** The piece, as a dialog knows it. Every field optional - AutoCut has a
    pacing and clips, Storytelling has a script and pictures. */
export type MusicContext = {
  text?: string;
  mood?: string;
  bpm?: number | null;
  assetIds?: string[];
};

/** The piece, as the route takes it: its own field names, normalised. */
export type SuggestionRequest = {
  text: string;
  mood: string;
  bpm: number | null;
  asset_ids: string[];
};

export function suggestionRequest(context?: MusicContext | null): SuggestionRequest {
  return {
    text: context?.text?.trim() ?? "",
    mood: context?.mood?.trim() ?? "",
    bpm: context?.bpm ?? null,
    asset_ids: context?.assetIds ?? [],
  };
}

/**
 * Whether there is anything here to suggest music from.
 *
 * The three things the server can build a search out of: the pacing's mood,
 * the script, and the clips - whose names and tags it reads for itself. A
 * tempo is deliberately not one of them. It sharpens a mood's search into a
 * fast or a slow one and says nothing on its own, so a dialog holding only a
 * bpm would otherwise offer a Suggested tab with nothing behind it.
 */
export function hasSomethingToSuggest(context?: MusicContext | null): boolean {
  const asked = suggestionRequest(context);
  return Boolean(asked.mood || asked.text || asked.asset_ids.length);
}

/**
 * One piece's identity, for an effect to watch.
 *
 * The request's own JSON, so what is watched and what is sent cannot drift
 * apart - and so the watcher can simply parse it back rather than closing
 * over an object that is new on every render.
 */
export function contextKey(context?: MusicContext | null): string {
  return JSON.stringify(suggestionRequest(context));
}
