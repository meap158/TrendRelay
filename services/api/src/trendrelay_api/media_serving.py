"""How this app hands its own media bytes back to its own interface.

A download manager hooks the *request*, not the element that made it, and it
reads the response's media type to decide what it has caught. Anything served
as `video/mp4` over a plain GET is a file a grabber will take - whatever the
interface meant by showing it.

`OPAQUE_MEDIA_TYPE` is the escape hatch: the same bytes under a type nothing
recognises, for callers that read the response themselves (a `fetch`, then a
blob with the real type put back on this side of the wire). An element pointed
straight at an opaque response cannot play it, which is exactly the point - a
caller still doing that has not finished the job.

Kept off any one router because every surface that previews workspace media
needs the same answer: Library streams, publishing previews, thumbnails.
"""

from __future__ import annotations

OPAQUE_MEDIA_TYPE = "application/x-trendrelay-preview"
