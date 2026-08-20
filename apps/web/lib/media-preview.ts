/**
 * Asking the API for a workspace's own media without it looking like a file.
 *
 * A download manager hooks the *request*, not the element that made it. That
 * is the part the first attempt at this missed: moving the timeline's clip
 * from a `<video src>` to a `fetch` changed who asked for it and not what came
 * back - still `video/mp4`, still grabbed by IDM the moment a row was
 * expanded. Worse, IDM cancels the browser's copy of a request it takes over,
 * so the row also showed "Failed to fetch" while the download went ahead.
 *
 * So the request asks for the bytes under a type nothing recognises, and the
 * real type is put back on this side, where the blob is made. Both halves are
 * needed: an opaque response an element points at cannot play, and a
 * recognisable response is taken before it arrives.
 *
 * Kept here rather than in the component so it can be tested. The mode existed
 * on the API for a while with nothing calling it, which is exactly the sort of
 * thing a test on the caller notices and a test on the endpoint does not.
 */

/**
 * What to tell the blob it is, by extension.
 *
 * The server deliberately is not saying, so this has to. Read from the path
 * rather than sniffed: these are files this workspace published, so the
 * extension is the one the engine already accepted.
 */
const MEDIA_TYPES: Record<string, string> = {
  mp4: "video/mp4",
  m4v: "video/mp4",
  mov: "video/quicktime",
  webm: "video/webm",
  mkv: "video/x-matroska",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  gif: "image/gif",
};

export function mediaTypeFor(path: string, fallback: string): string {
  // The query string goes first: these paths arrive as URLs as often as
  // filenames, and "clip.mp4?v=2" has no extension by a naive reading.
  const name = path.split(/[?#]/)[0] ?? "";
  const dot = name.lastIndexOf(".");
  if (dot < 0) return fallback;
  return MEDIA_TYPES[name.slice(dot + 1).toLowerCase()] ?? fallback;
}

/**
 * The same preview URL, asking for bytes rather than for a recognisable file.
 *
 * Every preview request in the interface goes through here. A caller that
 * builds the URL itself is a caller that can forget, which is how this came
 * back the first time.
 */
export function opaquePreviewUrl(src: string): string {
  if (/[?&]opaque=true(&|$)/.test(src)) return src;
  return `${src}${src.includes("?") ? "&" : "?"}opaque=true`;
}
