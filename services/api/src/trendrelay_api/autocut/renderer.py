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

import tempfile
import textwrap
from dataclasses import dataclass, field
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
    #: How off-ratio media meets the canvas. "cover" fills the frame and crops
    #: the overflow - clean for media already near the shape. "blur" fits the
    #: whole clip inside, over a blurred, frame-filling copy of itself - the
    #: short-form look that keeps a landscape photo whole in a portrait video.
    fill: str = "cover"
    #: Where to keep the cover-crop for a photo with a subject off-centre, as
    #: asset_id -> (x, y) frame fractions (see ``reframe.focus_points``). Absent
    #: means the geometric centre, which is the crop without this at all.
    focus: dict[str, tuple[float, float]] = field(default_factory=dict)
    #: A hook line burned over the whole video, the way short-form leans on a
    #: caption to carry the opening. Empty draws none.
    caption: str = ""
    #: Where the caption sits: "top" or "bottom".
    caption_position: str = "bottom"
    #: Timed subtitles, as (start_ms, end_ms, text) in order.
    #:
    #: A hook caption is one line held over a whole video, which is what a
    #: montage wants. A narration wants the sentence being spoken right now,
    #: which is a different thing and cannot be expressed as one cue. Both go
    #: through the same subtitle formatter and come out with the same weight
    #: and outline; only the number of cues differs.
    #:
    #: Supplying these replaces the hook caption rather than joining it: two
    #: sets of words on one frame is a subtitle fighting a title.
    cues: tuple[tuple[int, int, str], ...] = ()
    #: The animated caption style, by subtitle-preset id ("word-pop", "karaoke",
    #: "one-word", …). Empty is the plain built-in look. A word-highlight preset
    #: needs per-word timings; ``caption_words`` supplies them.
    caption_style: str = ""
    #: Per-word timings, as (start_ms, end_ms, text), for a word-highlight style
    #: - the pop/karaoke look that lights each word as it is spoken.
    caption_words: tuple[tuple[int, int, str], ...] = ()

    @property
    def blurred(self) -> bool:
        return self.fill == "blur"

    @property
    def captioned(self) -> bool:
        return bool(self.cues) or bool(self.caption.strip()) or bool(self.caption_words)


#: How hard the fill background is blurred. Enough that it reads as a wash of
#: the clip's colour rather than a second, competing picture.
FILL_BLUR_SIGMA = 24


def _cover_and_move(
    shot: Shot, index: int, width: int, height: int,
    *, blurred: bool, focus: tuple[float, float] = (0.5, 0.5),
) -> str:
    """The filter chain for one clip: fit it to the canvas, and move a still.

    zoompan does the Ken Burns work on stills; a video carries its own motion.
    In cover mode the clip fills the frame and the overflow is cropped; in
    blur mode the whole clip is fitted inside, over a blurred copy of itself
    that fills the frame - so nothing off-ratio is cut off.
    """
    frames = max(1, round(shot.duration * FPS))
    src = f"[{index}:v]"

    if shot.media_kind == "video":
        # A video's own frames are the timeline; only normalise fps and trim
        # to the slot (the input is looped ahead of this so a short clip fills
        # its beats). A still is held for `frames` by zoompan below.
        prep = f"fps={FPS},trim=duration={shot.duration:.4f},setpts=PTS-STARTPTS"
    else:
        prep = None

    if not blurred:
        cover = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
        if shot.media_kind == "video":
            return f"{src}{prep},{cover},setsar=1,format=yuv420p[v{index}]"
        # Oversize so zoompan has pixels to push into, then cover-crop by zoom.
        # The crop keeps the subject, not the geometric centre: the scale is
        # uniform, so a face at fraction (fx, fy) of the photo is still at that
        # fraction of the scaled image, and the crop window is placed around it,
        # clamped to stay inside. Default (0.5, 0.5) is the centred crop exactly.
        motion: Motion = shot.motion
        focus_x, focus_y = focus
        scaled = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2}"
            f":x='clip(in_w*{focus_x:.4f}-out_w/2,0,in_w-out_w)'"
            f":y='clip(in_h*{focus_y:.4f}-out_h/2,0,in_h-out_h)'"
        )
        zoompan = (
            f"zoompan=z='1+{motion.zoom:.4f}*on/{frames}'"
            f":x='iw/2-(iw/zoom/2)+({motion.pan_x:.4f}*iw*on/{frames})'"
            f":y='ih/2-(ih/zoom/2)+({motion.pan_y:.4f}*ih*on/{frames})'"
            f":d={frames}:s={width}x{height}:fps={FPS}"
        )
        return f"{src}{scaled},{zoompan},setsar=1,format=yuv420p[v{index}]"

    # Blur-fit: one source split into a frame-filling blurred background and a
    # fully-contained foreground, overlaid centre. A still is held to `frames`
    # on each branch (zoom=1, so a static hold, not a Ken Burns) so the clip
    # has length; a video's branches inherit its own frames.
    hold = "" if shot.media_kind == "video" else f",zoompan=z=1:d={frames}:s={width}x{height}:fps={FPS}"
    pre = f"{src}{prep}," if prep else src
    bg = (
        f"[bgsrc{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}{hold},gblur=sigma={FILL_BLUR_SIGMA}[bg{index}]"
    )
    fg = (
        f"[fgsrc{index}]scale={width}:{height}:force_original_aspect_ratio=decrease{hold}[fg{index}]"
    )
    overlay = (
        f"[bg{index}][fg{index}]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v{index}]"
    )
    return f"{pre}split=2[bgsrc{index}][fgsrc{index}];{bg};{fg};{overlay}"


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


def _caption_ass(request: RenderRequest) -> str:
    """The subtitle file: one cue for a hook, or one a sentence for a narration.

    Built through the app's shared subtitle formatter, so an AutoCut caption
    reads with the same weight and outline as a burned-in subtitle elsewhere.
    Size, margin and outline scale with the frame height, so the caption keeps
    its proportion whether it is drawn into a full render or a half-size
    preview, a portrait or a square.
    """
    import dataclasses

    from trendrelay_api.subtitle_formats import PRESETS, Style, to_ass
    from trendrelay_api.subtitles import Cue, build_cues

    duration_ms = max(1, round(request.plan.duration * 1000))

    # Animated word-highlight captions - the pop/karaoke look that lights each
    # word as it is spoken. The style and its layout are a reviewed preset; the
    # words are timed by the narration. Sizes in the preset are drawn for a
    # 1080x1920 social frame, so they scale to this frame's height to keep their
    # proportion in a preview, a square, or a portrait.
    if request.caption_style in PRESETS and request.caption_words:
        base_style, layout = PRESETS[request.caption_style]
        scale = request.height / 1920
        style = dataclasses.replace(
            base_style,
            size=max(14, round(base_style.size * scale)),
            outline=round(base_style.outline * scale, 2),
            shadow=round(base_style.shadow * scale, 2),
            margin_v=max(10, round(base_style.margin_v * scale)),
            margin_h=max(8, round(base_style.margin_h * scale)),
        )
        segments = [{"words": [
            {"text": text, "start_ms": max(0, start_ms), "end_ms": max(start_ms + 1, end_ms)}
            for (start_ms, end_ms, text) in request.caption_words if text.strip()
        ]}]
        cues = build_cues(segments, layout=layout)
        return to_ass(cues, style, play_width=request.width, play_height=request.height)

    if request.cues:
        # One cue a sentence, each wrapped the same way the hook is - a
        # narration line is a sentence rather than a slogan, so it is the one
        # that actually needs the wrap.
        cues = [
            Cue(
                index=index + 1,
                start_ms=max(0, start_ms),
                end_ms=max(start_ms + 1, end_ms),
                lines=textwrap.wrap(text.strip(), width=26)[:4] or [text.strip()],
            )
            for index, (start_ms, end_ms, text) in enumerate(request.cues)
            if text.strip()
        ]
    else:
        cues = [Cue(
            index=1,
            start_ms=0,
            end_ms=duration_ms,
            lines=textwrap.wrap(request.caption.strip(), width=26)[:4]
            or [request.caption.strip()],
        )]
    style = Style(
        name="AutoCut",
        font="Arial",
        size=max(16, round(request.height * 0.045)),
        bold=True,
        outline=max(2.0, request.height * 0.004),
        alignment="top" if request.caption_position == "top" else "bottom",
        margin_v=max(20, round(request.height * 0.06)),
    )
    return to_ass(cues, style, play_width=request.width, play_height=request.height)


def build_filtergraph(request: RenderRequest, *, caption_file: str | None = None) -> str:
    """The full -filter_complex string for this plan. Pure, so it is testable.

    When ``caption_file`` is given (a bare filename resolved from ffmpeg's own
    working directory), the hook caption is burned onto the finished montage as
    the last step before the encoder maps it.
    """
    shots = request.plan.shots
    per_shot = [
        _cover_and_move(
            shot, index, request.width, request.height, blurred=request.blurred,
            focus=request.focus.get(shot.asset_id, (0.5, 0.5)),
        )
        for index, shot in enumerate(shots)
    ]
    labels = [f"v{index}" for index in range(len(shots))]
    join_graph, final = _join(shots, labels)
    parts = per_shot + ([join_graph] if join_graph else [])
    graph = ";".join(parts)
    # The final video stream is tagged [vout] for the encoder to map. A caption
    # is drawn on last, so it sits over every clip and transition.
    last = f"subtitles={caption_file}" if caption_file else "copy"
    return f"{graph};[{final}]{last}[vout]"


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
            raise RuntimeError(f"Missing file for shot {shot.asset_id!r}.")
        if shot.media_kind == "video":
            # Loop the source so a clip shorter than its beat-slot still fills
            # it, then let the filter trim to the exact duration. Its own audio
            # is dropped later - the template's track is the sound.
            inputs += ["-stream_loop", "-1", "-t", f"{shot.duration:.4f}", "-i", str(path)]
        else:
            # A single frame in: zoompan itself produces the shot's whole
            # length from it. Looping the still into many frames AND letting
            # zoompan expand each one multiplied the two - a four-second shot
            # rendered as twenty-eight.
            inputs += ["-i", str(path)]

    before = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", *inputs]

    audio = request.audio_path
    audio_tail: list[str] = []
    if audio is not None and audio.is_file():
        before += ["-i", str(audio)]
        audio_tail = [
            "-map", f"{len(shots)}:a",
            # End with the video, however long the track is.
            "-shortest",
            # Resample to 48 kHz. The template tracks are 96 kHz (they were
            # pulled from source videos at that rate), and a browser <video>
            # cannot decode AAC above 48 kHz - it stalls at readyState 0 with no
            # error, which left both the preview and the Library playback black.
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        ]

    def _encode(graph: str, cwd: Path | None, scratch: Path):
        # The graph goes to a file, never onto the command line.
        #
        # It grows about 420 characters per shot, and Windows refuses a command
        # line over 32,767 - so a plan of roughly seventy-seven shots was the
        # ceiling, and the failure is `CreateProcess` refusing rather than
        # ffmpeg saying anything useful. AutoCut's own templates ask for at
        # most twenty-four pictures and never came near it; a narrated video is
        # one shot per sentence and walks straight into it.
        #
        # `-filter_complex_script` takes a plain path and is not parsed the way
        # the subtitles filter's argument is, so an absolute one is safe here
        # even on Windows.
        script = scratch / "filtergraph.txt"
        script.write_text(graph, encoding="utf-8")
        after = ["-filter_complex_script", str(script), "-map", "[vout]", *audio_tail]
        # moov atom up front (faststart) for both the full render and the
        # preview. A browser <video> stalls at readyState 0 on a moov-at-end
        # MP4 even when the whole file is already in a blob - so the preview
        # needs faststart as much as the streamed Library render does. The
        # rewrite is a few milliseconds on a half-size clip; skipping it left
        # the preview pane black.
        after += ["-r", str(FPS), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        return encode_h264(
            ffmpeg, before, after, request.destination,
            # A preview is watched once and discarded, so speed beats quality:
            # the fastest preset and a looser quantiser cut the encode to a few
            # seconds. A full render keeps the shared defaults.
            preset="ultrafast" if request.preview else "veryfast",
            quality=30 if request.preview else 20,
            timeout=1800,
            cwd=cwd,
        )

    # One scratch directory for both paths now: the graph is written to a file
    # whether or not there are captions, so there is always something to write.
    with tempfile.TemporaryDirectory(prefix="trendrelay-autocut-") as scratch:
        work = Path(scratch)
        if request.captioned:
            # libass' subtitles filter parses its own argument, where a Windows
            # path's drive colon and backslashes are read as option separators
            # and escapes - so the .ass is written into the scratch directory
            # and ffmpeg is run from inside it, leaving the filter a bare
            # filename. The same trick the app's subtitle burn-in uses.
            (work / "caption.ass").write_text(_caption_ass(request), encoding="utf-8")
            graph = build_filtergraph(request, caption_file="caption.ass")
            completed, _profile = _encode(graph, work, work)
        else:
            completed, _profile = _encode(build_filtergraph(request), None, work)

    if completed.returncode != 0 or not request.destination.is_file():
        stderr = (completed.stderr or b"")
        message = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else str(stderr)
        raise RuntimeError(message.strip()[-1500:] or "ffmpeg produced no output.")
    return request.destination
