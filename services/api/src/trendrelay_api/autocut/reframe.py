"""Where to keep the crop when an off-ratio photo has to lose its edges.

Cover-fill scales a photo to fill the frame and crops the overflow. Cropping to
the geometric centre is what cuts a face in half when the subject stood to one
side - the single most visible weakness of a montage of mixed-shape photos. This
finds the subject and hands the renderer a point to crop around instead.

A face is the subject that matters here, and OpenCV's frontal-face cascade ships
with cv2 - no model file, no download, no GPU - which is enough to place a crop:
the point does not have to be exact, only better than the middle. Everything
degrades to the centre: no cv2, no cascade, no readable image, or no face all
return nothing for that photo, and the renderer keeps its centred crop. So this
can only improve a frame, never break one.
"""

from __future__ import annotations

from pathlib import Path


def focus_points(image_paths: dict[str, Path]) -> dict[str, tuple[float, float]]:
    """A crop-around point per photo that has a clear subject, as (x, y) frame
    fractions in 0..1. Photos with no detectable face - and videos, whose paths
    are not images cv2 can read - are simply absent, and the renderer centres
    those as before.

    Best-effort by construction: any failure to load cv2, build the cascade, or
    read a file is swallowed for that photo, because a missing focus point is a
    centred crop, which is exactly the behaviour without this at all.
    """
    try:
        import cv2  # noqa: PLC0415 - optional, imported only when a render runs
    except Exception:
        return {}
    try:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    except Exception:
        return {}
    if cascade.empty():
        return {}

    points: dict[str, tuple[float, float]] = {}
    for asset_id, path in image_paths.items():
        try:
            image = cv2.imread(str(path))
            if image is None:  # a video, or an unreadable file - centre it
                continue
            height, width = image.shape[:2]
            if width <= 0 or height <= 0:
                continue
            grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            cv2.equalizeHist(grey, grey)
            faces = cascade.detectMultiScale(
                grey, scaleFactor=1.1, minNeighbors=5,
                minSize=(max(16, width // 12), max(16, height // 12)),
            )
            if len(faces) == 0:
                continue
            # The largest face is the subject; several faces average out around
            # the group, which is still better than the geometric centre.
            x, y, face_w, face_h = max(faces, key=lambda box: box[2] * box[3])
            focus_x = (x + face_w / 2) / width
            focus_y = (y + face_h / 2) / height
            # A hair over the very edge would clip the crop expression to the
            # rim anyway; keep it inside so the maths never argues with itself.
            points[asset_id] = (
                round(min(0.98, max(0.02, focus_x)), 4),
                round(min(0.98, max(0.02, focus_y)), 4),
            )
        except Exception:
            continue
    return points
