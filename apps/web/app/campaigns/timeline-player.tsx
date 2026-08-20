"use client";

import { useEffect, useRef, useState } from "react";

import { mediaTypeFor, opaquePreviewUrl } from "../../lib/media-preview";

/**
 * The timeline's media, read as opaque bytes and shown from a blob.
 *
 * Two things have to be true at once, and the first attempt only managed one.
 *
 * **Nothing may point an element at the API.** A plain `src` on a `<video>` is
 * a progressive `video/mp4` with range support, which is exactly what a
 * download manager watches for: IDM captured the clip the moment a row was
 * expanded, with no click involved. `controlsList="nodownload"` governs the
 * controls Chrome draws, not what an extension does with the request
 * underneath them. So the bytes are read with `fetch` and played from a blob.
 *
 * **And the request itself must not look like a file.** This is the half that
 * was missing, and it is why the first fix did not work: a grabber hooks the
 * *request*, not the element that made it. Moving the fetch into JavaScript
 * changed who asked for the clip and not what came back - still `video/mp4`,
 * still grabbed. Worse, IDM cancels the browser's copy of a request it takes
 * over, so the fetch failed as well: the row showed "Failed to fetch" and the
 * download happened anyway.
 *
 * `opaque=true` asks the API for the same bytes under a media type nothing
 * recognises, so there is no video response on the wire to notice. The type is
 * put back here, where the blob is made, because that is the only place it is
 * needed - a `<video>` will not play `application/x-trendrelay-preview`.
 *
 * Fetched only once the row is actually open. The content of a closed
 * `details` has no layout box, so an observer on the wrapper reports it unseen
 * - which is what keeps a timeline of fifty rows from reading fifty files.
 */

function useOpaqueMedia(src: string, path: string, fallbackType: string, wanted: boolean) {
  const [objectUrl, setObjectUrl] = useState("");
  const [problem, setProblem] = useState("");

  useEffect(() => {
    if (!wanted) return;
    let cancelled = false;
    let created = "";
    // No credentials, matching what the media element sent before this: the
    // API grants a local identity to loopback callers, and asking for
    // credentials here would need the CORS exchange to allow them.
    fetch(opaquePreviewUrl(src))
      .then((response) => {
        if (!response.ok) throw new Error(`The media could not be read (${response.status}).`);
        // Not `.blob()`: that would carry the opaque type through to the
        // element, which then refuses to show it. The bytes are retyped here.
        return response.arrayBuffer();
      })
      .then((bytes) => {
        if (cancelled) return;
        created = URL.createObjectURL(
          new Blob([bytes], { type: mediaTypeFor(path, fallbackType) }),
        );
        setObjectUrl(created);
      })
      .catch((reason) => {
        if (!cancelled) {
          setProblem(reason instanceof Error ? reason.message : "The media could not be read.");
        }
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [src, path, fallbackType, wanted]);

  return { objectUrl, problem };
}

/** True once the wrapper has been on screen, which for a `details` means open. */
function useSeen(wrapper: React.RefObject<HTMLElement | null>) {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const element = wrapper.current;
    if (!element || seen) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setSeen(true);
        observer.disconnect();
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [wrapper, seen]);
  return seen;
}

export function TimelinePlayer({ src, title }: { src: string; title: string }) {
  const wrapper = useRef<HTMLDivElement>(null);
  const seen = useSeen(wrapper);
  const { objectUrl, problem } = useOpaqueMedia(src, title, "video/mp4", seen);

  return (
    <div ref={wrapper} className="timeline-media-frame">
      {problem && <p className="campaign-pipeline-reason">{problem}</p>}
      {!problem && !objectUrl && <p className="campaign-pipeline-reason">Loading the clip…</p>}
      {objectUrl && (
        <video
          className="timeline-media"
          controls
          // Kept even though the blob is the actual defence: it still removes
          // the save button Chrome would otherwise draw in its own controls.
          controlsList="nodownload"
          disablePictureInPicture
          src={objectUrl}
          title={title}
        />
      )}
    </div>
  );
}

/**
 * One picture of a carousel, read the same way.
 *
 * An `<img>` pointed at the API is the same shape of request as the video was,
 * and a grabber configured for pictures takes it for the same reason. There is
 * no argument for treating the two differently: both are this workspace's own
 * media, shown back to it.
 */
export function TimelineImage({ src, path }: { src: string; path: string }) {
  const wrapper = useRef<HTMLSpanElement>(null);
  const seen = useSeen(wrapper);
  const { objectUrl, problem } = useOpaqueMedia(src, path, "image/jpeg", seen);

  return (
    <span ref={wrapper} className="timeline-media-slot">
      {problem
        ? <span className="campaign-pipeline-reason">{problem}</span>
        // eslint-disable-next-line @next/next/no-img-element
        : objectUrl ? <img className="timeline-media" src={objectUrl} alt={path} /> : null}
    </span>
  );
}
