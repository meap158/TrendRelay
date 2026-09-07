"""Executing a CutPlan into an MP4: pictures, motion, transitions, music.

The planner decides *what* happens when; this draws it. One ffmpeg graph per
render: each picture becomes a moving clip (the template's Ken Burns push
over its shot's duration), the clips are joined by the shot's transition
(hard cut, crossfade, whip, zoom), and the chosen track is laid under the
whole thing, trimmed to the video's length. Encoding goes through the shared
`encode_h264`, so AutoCut gets the same hardware-then-software fallback every
other render here uses.

Portrait 1080x1920 by default - the short-form frame the references use and
the one Publish expects. A picture of any shape is fitted into it by scaling
to cover and cropping, so a landscape photo fills the frame rather than
sitting letterboxed in it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from trendrelay_api.autocut.planner import CutPlan, Shot
from trendrelay_api.autocut.templates import Motion
from trendrelay_api.video_encoding import encode_h264

FRAME_W = 1080
FRAME_H = 1920
FPS = 30
#: The zoompan filter works in frames, so every duration becomes a frame
#: count at this rate; keeping it one constant stops the audio and video
#: clocks from drifting apart over a long video.


@dataclass(frozen=True)
class RenderRequest:
    """Everything the renderer needs that is not in the plan itself."""

    plan: CutPlan
    #: asset_id -> the picture file on disk, resolved by the caller.
    image_paths: dict[str, Path]
    #: The music file, already resolved (a template's default or an override).
    #: None renders a silent video rather than failing - a plan can be watched
    #: without a track, and the interface says when there is none.
    audio_path: Path | None
    destination: Path
    width: int = FRAME_W
    height: int = FRAME_H
    #: A preview trades pixels for speed: half the frame and the fastest
    #: preset, so the operator sees the actual arrangement in a few seconds
    #: rather than waiting out a full-quality encode to decide whether to keep
    #: it. The plan and the timing are identical - only the resolution differs.
    preview: bool = False


def _cover_and_move(shot: Shot, index: int, width: int, height: int) -> str:
    """The filter chain for one picture: cover the frame, then move slowly.

    zoompan does the Ken Burns work. It counts in frames, so the shot's
    seconds become a frame count, and the zoom ramps linearly across them.
    A pan is a slow drift of the crop centre; both are small on purpose - a
    still should breathe, not lurch.
    """
    frames = max(1, round(shot.duration * FPS))
    motion: Motion = shot.motion
    # Oversize first so zoompan has pixels to push into, then cover-crop.
    scaled = (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2}"
    )
    # zoom ramps 1.0 -> 1+zoom across the shot; the centre drifts by pan.
    zoom_expr = f"1+{motion.zoom:.4f}*on/{frames}"
    x_expr = f"iw/2-(iw/zoom/2)+({motion.pan_x:.4f}*iw*on/{frames})"
    y_expr = f"ih/2-(ih/zoom/2)+({motion.pan_y:.4f}*ih*on/{frames})"
    zoompan = (
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
        f":d={frames}:s={width}x{height}:fps={FPS}"
    )
    return (
        f"[{index}:v]{scaled},{zoompan},setsar=1,format=yuv420p[v{index}]"
    )


def _join(shots: tuple[Shot, ...], labels: list[str]) -> tuple[str, str]:
    """Chain the per-shot streams with each shot's transition.

    A hard cut is a plain concat. A crossfade/whip/zoom is an xfade of the
    transition's length, which overlaps the two clips - so the running
    timeline offset is the sum of durations minus the overlaps already spent.
    """
    if len(labels) == 1:
        return "", labels[0]
    graph: list[str] = []
    current = labels[0]
    offset = shots[0].duration
    for index in range(1, len(shots)):
        shot = shots[index]
        nxt = labels[index]
        out = f"x{index}"
        if shot.transition == "cut" or shot.transition_seconds <= 0:
            graph.append(f"[{current}][{nxt}]concat=n=2:v=1:a=0[{out}]")
            offset += shot.duration
        else:
            style = {
                "crossfade": "fade",
                "whip": "slideleft",
                "zoom": "smoothup",
            }.get(shot.transition, "fade")
            trans_at = round(offset - shot.transition_seconds, 4)
            graph.append(
                f"[{current}][{nxt}]xfade=transition={style}"
                f":duration={shot.transition_seconds}:offset={trans_at}[{out}]"
            )
            offset += shot.duration - shot.transition_seconds
        current = out
    return ";".join(graph), current


def build_filtergraph(request: RenderRequest) -> str:
    """The full -filter_complex string for this plan. Pure, so it is testable."""
    shots = request.plan.shots
    per_shot = [
        _cover_and_move(shot, index, request.width, request.height)
        for index, shot in enumerate(shots)
    ]
    labels = [f"v{index}" for index in range(len(shots))]
    join_graph, final = _join(shots, labels)
    parts = per_shot + ([join_graph] if join_graph else [])
    graph = ";".join(parts)
    # The final video stream is tagged [vout] for the encoder to map.
    return f"{graph};[{final}]copy[vout]"


def render(ffmpeg: Path, request: RenderRequest) -> Path:
    """Draw the plan to `destination` and return it.

    Raises RuntimeError with ffmpeg's own words on failure, so a job can put
    the real reason on screen rather than a generic "render failed".
    """
    shots = request.plan.shots
    if not shots:
        raise RuntimeError("This AutoCut plan has no shots to render.")

    inputs: list[str] = []
    for shot in shots:
        path = request.image_paths.get(shot.asset_id)
        if path is None or not path.is_file():
            raise RuntimeError(f"Missing picture for shot {shot.asset_id!r}.")
        # A single frame in: zoompan itself produces the shot's whole length
        # from it (`d` frames at `fps`). Looping the still into many frames
        # AND letting zoompan expand each one multiplied the two - a
        # four-second shot rendered as twenty-eight.
        inputs += ["-i", str(path)]

    graph = build_filtergraph(request)
    before = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", *inputs]
    after = ["-filter_complex", graph, "-map", "[vout]"]

    audio = request.audio_path
    if audio is not None and audio.is_file():
        before += ["-i", str(audio)]
        after += [
            "-map", f"{len(shots)}:a",
            # End with the video, however long the track is.
            "-shortest",
            "-c:a", "aac", "-b:a", "160k",
        ]
    after += ["-r", str(FPS), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]

    completed, _profile = encode_h264(
        ffmpeg, before, after, request.destination,
        # A preview is watched once and discarded, so speed beats quality:
        # the fastest preset and a looser quantiser cut the encode to a few
        # seconds. A full render keeps the shared defaults.
        preset="ultrafast" if request.preview else "veryfast",
        quality=30 if request.preview else 20,
        timeout=1800,
    )
    if completed.returncode != 0 or not request.destination.is_file():
        stderr = (completed.stderr or b"")
        message = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else str(stderr)
        raise RuntimeError(message.strip()[-1500:] or "ffmpeg produced no output.")
    return request.destination
