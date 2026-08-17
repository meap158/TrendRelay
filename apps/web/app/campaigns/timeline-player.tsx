"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The timeline's clip, read into memory and played from a blob.
 *
 * A plain `src` pointing at the API is a progressive `video/mp4` served with
 * range support, which is precisely what a download manager watches for: IDM
 * captures the clip the moment a row is expanded, with no click involved.
 * `controlsList="nodownload"` does not help - that governs the controls Chrome
 * draws, not what an extension does with the request underneath them.
 *
 * Reading the bytes with `fetch` and handing the player a blob keeps the media
 * out of the browser's download path entirely, so there is no request for a
 * grabber to see. The cost is the whole file before the first frame, which
 * against an API on loopback is a disk read rather than a transfer, and seeking
 * still works because the blob is complete once it arrives.
 *
 * Fetched only once the row is actually open. The content of a closed `details`
 * has no layout box, so an observer on the wrapper reports it as unseen - which
 * is what keeps a timeline of fifty rows from reading fifty videos off disk.
 */
export function TimelinePlayer({ src, title }: { src: string; title: string }) {
  const wrapper = useRef<HTMLDivElement>(null);
  const [wanted, setWanted] = useState(false);
  const [objectUrl, setObjectUrl] = useState("");
  const [problem, setProblem] = useState("");

  useEffect(() => {
    const element = wrapper.current;
    if (!element || wanted) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setWanted(true);
        observer.disconnect();
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [wanted]);

  useEffect(() => {
    if (!wanted) return;
    let cancelled = false;
    let created = "";
    // No credentials, matching what the media element sent before this: the
    // API grants a local identity to loopback callers, and asking for
    // credentials here would need the CORS exchange to allow them.
    fetch(src)
      .then((response) => {
        if (!response.ok) throw new Error(`The clip could not be read (${response.status}).`);
        return response.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch((reason) => {
        if (!cancelled) {
          setProblem(reason instanceof Error ? reason.message : "The clip could not be read.");
        }
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [src, wanted]);

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
