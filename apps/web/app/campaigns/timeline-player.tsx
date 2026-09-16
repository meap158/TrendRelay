"use client";

import { Maximize2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Lightbox } from "../ui/lightbox";
import { useOpaqueMedia } from "../../lib/media-preview";

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

/**
 * True once the wrapper has been on screen, which for a `details` means open.
 */
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
function TimelineImage({ src, path, label, onReady, onOpen }: {
  src: string;
  path: string;
  /** What this picture is - its alt text, and the lightbox's name for it. */
  label: string;
  /** Hands the read bytes up, so the full-size view can show them again. */
  onReady: (path: string, objectUrl: string) => void;
  onOpen: () => void;
}) {
  const wrapper = useRef<HTMLSpanElement>(null);
  const seen = useSeen(wrapper);
  const { objectUrl, problem } = useOpaqueMedia(src, path, "image/jpeg", seen);

  useEffect(() => {
    if (objectUrl) onReady(path, objectUrl);
  }, [objectUrl, path, onReady]);

  return (
    <span ref={wrapper} className="timeline-media-slot">
      {problem && <span className="campaign-pipeline-reason">{problem}</span>}
      {objectUrl && (
        // A button rather than a click handler on the picture, which is what
        // the Library's own thumbnail is: reachable by keyboard, and saying
        // what it does when it gets there.
        <button type="button" className="timeline-media-zoom"
          aria-label={`${label} - view full size`} title="View full size"
          onClick={onOpen}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="timeline-media" src={objectUrl} alt={label} />
          <span className="timeline-media-zoom-mark" aria-hidden="true">
            <Maximize2 size={12} />
          </span>
        </button>
      )}
    </span>
  );
}

/**
 * The pictures a carousel went out as, any one of which opens full size.
 *
 * Eight frames at 110px are eight thumbnails of the same shoot, and which one
 * led is not a question a thumbnail answers - which is the same question the
 * Library answers with a lightbox, so this answers it with that lightbox
 * rather than a second way of looking at a picture.
 *
 * The blobs stay with the frames that read them and are lent upward: these
 * bytes are already here, and reading the file a second time to fill the
 * dialog would be a second request for a picture the page is showing.
 */
export function TimelineCarousel({ images }: {
  images: { path: string; src: string }[];
}) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [openAt, setOpenAt] = useState<number | null>(null);

  // Keyed by path rather than by index, and bailing when it already holds the
  // URL: the frames report on every render of a strip that re-renders with the
  // timeline around it, and a new object each time would loop.
  const remember = useCallback((path: string, objectUrl: string) => {
    setUrls((known) => (known[path] === objectUrl ? known : { ...known, [path]: objectUrl }));
  }, []);

  const labelFor = (index: number) => `Picture ${index + 1} of ${images.length}`;

  // Arrows page through the set, the way they do over the Library's own
  // lightbox. Bound only while it is open, so the timeline underneath keeps
  // its own keys the rest of the time.
  useEffect(() => {
    if (openAt === null) return;
    function step(event: KeyboardEvent) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      setOpenAt((index) => {
        if (index === null) return index;
        const next = index + (event.key === "ArrowLeft" ? -1 : 1);
        return next < 0 || next >= images.length ? index : next;
      });
    }
    window.addEventListener("keydown", step);
    return () => window.removeEventListener("keydown", step);
  }, [openAt, images.length]);

  return (
    <div className="timeline-media-strip">
      {images.map((image, index) => (
        <TimelineImage
          key={`${image.path}-${index}`}
          src={image.src}
          path={image.path}
          label={labelFor(index)}
          onReady={remember}
          onOpen={() => setOpenAt(index)}
        />
      ))}
      {openAt !== null && (
        <Lightbox
          open
          /* Empty while a frame the strip has not read yet is opened - the
             dark stage holds and the picture joins it, which is the state the
             lightbox is already written for. */
          src={urls[images[openAt]?.path ?? ""] ?? ""}
          alt={labelFor(openAt)}
          onClose={() => setOpenAt(null)}
          onPrevious={openAt > 0 ? () => setOpenAt(openAt - 1) : undefined}
          onNext={openAt < images.length - 1 ? () => setOpenAt(openAt + 1) : undefined}
        />
      )}
    </div>
  );
}
