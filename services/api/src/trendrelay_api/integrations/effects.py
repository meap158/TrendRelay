"""The editing suite's effects, declared once and driven from that declaration.

Face blur was built as a feature: its own settings dialog, its own endpoint, its
own version kind. Adding flip, rotate, colour and speed the same way would mean
five more of each. So an effect is declared here instead — its parameters, how
it renders, when it is available — and the endpoint, the validation and the
interface all read that declaration. Adding an effect should be adding an entry.

Two kinds of effect, and the difference decides everything about cost:

*Stream* effects are things FFmpeg already does to a video stream — flipping,
rotating, grading, retiming. Any number of them compose into one filtergraph and
one pass, with no frame ever reaching Python. They are effectively free.

*Frame* effects need a model to look at each frame: blurring a face, replacing
one, changing a garment. They cost a decode, an inference and an encode.

Keeping them apart is what stops a flip from costing what a blur costs, and what
lets both sit in one ordered recipe without re-encoding between every step —
each re-encode is a generation of quality gone.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from trendrelay_api.tool_registry import PROJECT_ROOT

FFMPEG = (
    PROJECT_ROOT
    / "node_modules"
    / "ffmpeg-static"
    / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
)

ParamKind = Literal["number", "choice", "toggle"]
Stage = Literal["stream", "frame"]

#: FFmpeg's atempo filter only accepts a rate in this range, so anything beyond
#: it has to be reached by chaining instances.
ATEMPO_MIN = 0.5
ATEMPO_MAX = 2.0

#: Effects a photograph can take as well as a clip. Geometry and colour mean
#: the same thing on a still; anything measured in time does not, and saying so
#: here is what keeps a speed control off an image rather than letting it be
#: chosen and quietly do nothing.
STILL_AND_MOVING = frozenset({"video", "image"})


class EffectError(ValueError):
    """A recipe that cannot be rendered as asked."""


@dataclass(frozen=True)
class EffectParam:
    """One knob, described well enough that the interface needs no other source."""

    id: str
    label: str
    kind: ParamKind
    default: Any
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    #: (value, label) pairs for a choice. The values here are the *only* ones
    #: accepted, which is what keeps a hand-written request out of the command
    #: line FFmpeg is handed.
    options: tuple[tuple[str, str], ...] = ()
    unit: str = ""
    #: Options that are not known when the effect is declared, because they come
    #: from a catalogue an operator can add to. Called on every describe and
    #: every validation, so a file dropped in a folder becomes selectable
    #: without a restart — and, more to the point, so validation cannot fall
    #: behind the list the interface was offering.
    #:
    #: Returns full option dicts rather than pairs: a gallery needs a group and
    #: a thumbnail as well as a name, and those belong to the option.
    options_from: Callable[[], tuple[dict[str, Any], ...]] | None = None
    #: How the interface should offer the choice. A dozen picture-shaped options
    #: are a gallery; rendering them as a dropdown of names asks someone to pick
    #: a sticker by reading about it.
    presentation: Literal["control", "gallery"] = "control"
    #: Where an operator adds their own options, for a choice that is fed by a
    #: folder. Returns the directory and any files in it that did not become
    #: options. Declared here so the picker can name the folder and explain a
    #: file that failed without knowing which effect it is showing — a file
    #: that silently does not appear is the one case nobody can act on.
    folder_from: Callable[[], dict[str, Any]] | None = None

    def choices(self) -> tuple[dict[str, Any], ...]:
        """Every option this parameter accepts right now, in one shape."""
        if self.options_from is not None:
            return tuple(self.options_from())
        return tuple({"value": value, "label": label} for value, label in self.options)


@dataclass(frozen=True)
class Effect:
    id: str
    label: str
    summary: str
    stage: Stage
    #: What to call this on a chip, where there is room for a name and not for a
    #: sentence. The label is imperative because it sits in a menu of things to
    #: do — "Cover a face with an object" reads correctly there and truncates to
    #: "Cover a face wit…" on a library card, which names nothing. Most effects
    #: are already short enough and leave this empty to reuse the label.
    tag: str = ""
    params: tuple[EffectParam, ...] = ()
    media_kinds: frozenset[str] = frozenset({"video"})
    #: Stream effects only: the FFmpeg fragments this step contributes.
    video_filters: Callable[[dict[str, Any]], list[str]] | None = None
    audio_filters: Callable[[dict[str, Any]], list[str]] | None = None
    #: Changes duration, so anything already timed against the source — a clip
    #: plan, a blur timeline — no longer lines up. Ordering matters because of
    #: this, and the interface says so.
    retimes: bool = False
    #: The running time this step leaves behind, given what it started with.
    #: A function of the duration rather than a multiplier of it: speed scales
    #: the length, but a trim replaces it, and a factor cannot say that.
    duration_of: Callable[[dict[str, Any], float], float] = lambda values, seconds: seconds
    availability: Callable[[], tuple[bool, str | None]] = lambda: (True, None)
    #: Render one frame with this effect applied, for judging it before paying
    #: for a clip. Given the source, the step's values and where in the clip to
    #: look; returns `image` bytes, the `position` it landed on, the clip's
    #: `duration_seconds`, and a `note` saying what was found.
    #:
    #: Declared here rather than reached through an endpoint per effect, so one
    #: preview endpoint serves all of them and a new frame effect gets a
    #: preview by supplying this and nothing else.
    preview: Callable[[Path, dict[str, Any], float | None], dict[str, Any]] | None = None
    #: Apply this effect to a still, writing one image. Frame effects are
    #: already per-frame operations, so a photograph is the easy case and not a
    #: separate feature — what a blur or a swap does to frame 400 of a clip is
    #: exactly what it should do to a photograph of the same person.
    render_still: Callable[[Path, Path, dict[str, Any]], dict[str, Any]] | None = None
    #: Why a still cannot answer this effect's question, when it cannot. Said
    #: rather than left as a missing feature: for an effect that decides
    #: something over the whole clip, one frame is not a cheap preview, it is a
    #: misleading one.
    unpreviewable_reason: str = ""

    @property
    def chip(self) -> str:
        """The short name, falling back to the label when none was needed."""
        return self.tag or self.label

    def param(self, param_id: str) -> EffectParam | None:
        return next((item for item in self.params if item.id == param_id), None)


def _number(param: EffectParam, raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise EffectError(f"{param.label} must be a number.") from error
    if value != value or value in (float("inf"), float("-inf")):
        raise EffectError(f"{param.label} must be a real number.")
    if param.minimum is not None and value < param.minimum:
        raise EffectError(f"{param.label} cannot go below {param.minimum:g}.")
    if param.maximum is not None and value > param.maximum:
        raise EffectError(f"{param.label} cannot go above {param.maximum:g}.")
    return value


def coerce_params(effect: Effect, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Read a request's values against the effect's own declaration.

    Every value is either coerced to a number in range or matched against a
    fixed list. Nothing a caller sends reaches an FFmpeg argument as free text:
    these strings become a command line, and a filter description is a language
    with its own quoting and escaping.
    """
    supplied = raw or {}
    unknown = set(supplied) - {item.id for item in effect.params}
    if unknown:
        raise EffectError(
            f"{effect.label} has no setting called {sorted(unknown)[0]!r}."
        )

    values: dict[str, Any] = {}
    for param in effect.params:
        if param.id not in supplied or supplied[param.id] is None:
            values[param.id] = param.default
            continue
        given = supplied[param.id]
        if param.kind == "number":
            values[param.id] = _number(param, given)
        elif param.kind == "toggle":
            values[param.id] = bool(given)
        else:
            allowed = {str(option["value"]) for option in param.choices()}
            if str(given) not in allowed:
                raise EffectError(
                    f"{param.label} must be one of {_listed(allowed)}."
                )
            values[param.id] = str(given)
    return values


#: A catalogue is allowed to grow; an error message naming every entry is not.
NAMED_IN_ERRORS = 8


def _listed(allowed: set[str]) -> str:
    """Name the accepted values, without reciting a whole catalogue."""
    ordered = sorted(allowed)
    if len(ordered) <= NAMED_IN_ERRORS:
        return ", ".join(ordered)
    shown = ", ".join(ordered[:NAMED_IN_ERRORS])
    return f"{shown} and {len(ordered) - NAMED_IN_ERRORS} others"


# --------------------------------------------------------------------------- #
# Stream effects
# --------------------------------------------------------------------------- #


def _flip_filters(values: dict[str, Any]) -> list[str]:
    return {
        "horizontal": ["hflip"],
        "vertical": ["vflip"],
        "both": ["hflip", "vflip"],
    }[values["axis"]]


def _rotate_filters(values: dict[str, Any]) -> list[str]:
    # Quarter turns only. FFmpeg's transpose is exact and keeps the frame
    # rectangular; an arbitrary angle needs a fill colour and resamples every
    # pixel, which is a different feature rather than a wider version of this one.
    return {
        "90": ["transpose=1"],
        "180": ["transpose=1", "transpose=1"],
        "270": ["transpose=2"],
    }[values["turn"]]


def _colour_filters(values: dict[str, Any]) -> list[str]:
    parts = [
        f"contrast={values['contrast']:.4g}",
        f"brightness={values['brightness']:.4g}",
        f"saturation={values['saturation']:.4g}",
        f"gamma={values['gamma']:.4g}",
    ]
    return [f"eq={':'.join(parts)}"]


def atempo_chain(rate: float) -> list[str]:
    """Audio filters that reach `rate`, which one atempo often cannot.

    atempo is limited to 0.5-2.0 per instance, so quadruple speed is two
    doublings rather than one impossible filter. Getting this wrong does not
    fail loudly — FFmpeg clamps, and the audio quietly drifts out of sync with
    the picture over the length of the clip.
    """
    if rate <= 0:
        raise EffectError("Speed must be greater than zero.")
    factors: list[float] = []
    remaining = rate
    while remaining > ATEMPO_MAX:
        factors.append(ATEMPO_MAX)
        remaining /= ATEMPO_MAX
    while remaining < ATEMPO_MIN:
        factors.append(ATEMPO_MIN)
        remaining /= ATEMPO_MIN
    factors.append(remaining)
    return [f"atempo={factor:.6g}" for factor in factors]


def _speed_video_filters(values: dict[str, Any]) -> list[str]:
    # Dividing the presentation timestamps is what speeds a stream up; the frames
    # themselves are untouched, so nothing is resampled or lost.
    return [f"setpts=PTS/{values['rate']:.6g}"]


def _speed_audio_filters(values: dict[str, Any]) -> list[str]:
    return atempo_chain(float(values["rate"]))


FLIP = Effect(
    id="flip",
    label="Flip",
    summary="Mirror the picture. Often enough on its own to defeat a duplicate check.",
    stage="stream",
    params=(
        EffectParam(
            id="axis",
            label="Direction",
            kind="choice",
            default="horizontal",
            options=(
                ("horizontal", "Horizontal"),
                ("vertical", "Vertical"),
                ("both", "Both"),
            ),
            help="Horizontal reads as natural; vertical rarely does.",
        ),
    ),
    video_filters=_flip_filters,
    media_kinds=STILL_AND_MOVING,
)

ROTATE = Effect(
    id="rotate",
    label="Rotate",
    summary="Turn the picture in quarter steps, swapping width and height at 90 and 270.",
    stage="stream",
    params=(
        EffectParam(
            id="turn",
            label="Turn",
            kind="choice",
            default="90",
            options=(("90", "90° right"), ("180", "180°"), ("270", "90° left")),
        ),
    ),
    video_filters=_rotate_filters,
    media_kinds=STILL_AND_MOVING,
)

COLOUR = Effect(
    id="colour",
    label="Colour",
    summary="Contrast, brightness, saturation and gamma in one pass.",
    stage="stream",
    params=(
        EffectParam(
            id="contrast", label="Contrast", kind="number", default=1.0,
            minimum=0.0, maximum=3.0, step=0.05,
            help="1.0 leaves it alone.",
        ),
        EffectParam(
            id="brightness", label="Brightness", kind="number", default=0.0,
            minimum=-1.0, maximum=1.0, step=0.02,
            help="0 leaves it alone. This adds light rather than scaling it.",
        ),
        EffectParam(
            id="saturation", label="Saturation", kind="number", default=1.0,
            minimum=0.0, maximum=3.0, step=0.05,
            help="0 is greyscale.",
        ),
        EffectParam(
            id="gamma", label="Gamma", kind="number", default=1.0,
            minimum=0.1, maximum=3.0, step=0.05,
            help="Lifts the midtones without touching black or white.",
        ),
    ),
    video_filters=_colour_filters,
    media_kinds=STILL_AND_MOVING,
)

SPEED = Effect(
    id="speed",
    label="Speed",
    summary="Play faster or slower, keeping the audio in tune and in sync.",
    stage="stream",
    params=(
        EffectParam(
            id="rate", label="Rate", kind="number", default=1.0,
            minimum=0.25, maximum=4.0, step=0.05, unit="×",
            help="2 is twice as fast and half as long.",
        ),
    ),
    retimes=True,
    duration_of=lambda values, seconds: seconds / float(values["rate"]),
    video_filters=_speed_video_filters,
    audio_filters=_speed_audio_filters,
)


#: The shapes the networks actually want, as width over height.
ASPECT_RATIOS = {
    "9:16": 9 / 16,
    "4:5": 4 / 5,
    "1:1": 1.0,
    "16:9": 16 / 9,
}


def _aspect_filters(values: dict[str, Any]) -> list[str]:
    ratio = ASPECT_RATIOS[values["ratio"]]
    # The comma inside the expression belongs to min(), not to the filter list,
    # so it is escaped. Dimensions are trimmed to even numbers because yuv420p
    # halves the chroma planes and an odd one has nowhere to put the last line.
    width = rf"trunc(min(iw\,ih*{ratio:.6f})/2)*2"
    height = rf"trunc(min(ih\,iw/{ratio:.6f})/2)*2"
    x, y = {
        "centre": ("(iw-ow)/2", "(ih-oh)/2"),
        "top": ("(iw-ow)/2", "0"),
        "bottom": ("(iw-ow)/2", "ih-oh"),
    }[values["anchor"]]
    return [f"crop={width}:{height}:{x}:{y}"]


def _trim_video_filters(values: dict[str, Any]) -> list[str]:
    start = float(values["start"])
    length = float(values["length"])
    # Timestamps are rebased to zero. Without that the output keeps a gap where
    # the removed opening was, and players sit on a frozen first frame.
    if length <= 0:
        return [f"trim=start={start:.3f}", "setpts=PTS-STARTPTS"]
    return [f"trim=start={start:.3f}:duration={length:.3f}", "setpts=PTS-STARTPTS"]


def _trim_audio_filters(values: dict[str, Any]) -> list[str]:
    start = float(values["start"])
    length = float(values["length"])
    if length <= 0:
        return [f"atrim=start={start:.3f}", "asetpts=PTS-STARTPTS"]
    return [f"atrim=start={start:.3f}:duration={length:.3f}", "asetpts=PTS-STARTPTS"]


def _trim_duration(values: dict[str, Any], seconds: float) -> float:
    remaining = max(0.0, seconds - float(values["start"]))
    length = float(values["length"])
    return min(remaining, length) if length > 0 else remaining


def _volume_filters(values: dict[str, Any]) -> list[str]:
    if values["mute"]:
        return ["volume=0"]
    return [f"volume={float(values['gain']):.4g}"]


ASPECT = Effect(
    id="aspect",
    label="Aspect",
    summary="Crop to the shape a network wants, without stretching anything.",
    stage="stream",
    params=(
        EffectParam(
            id="ratio", label="Shape", kind="choice", default="9:16",
            options=(
                ("9:16", "9:16 vertical"),
                ("4:5", "4:5 portrait"),
                ("1:1", "1:1 square"),
                ("16:9", "16:9 landscape"),
            ),
            help="Cropped, never squeezed, so a face keeps the shape it had.",
        ),
        EffectParam(
            id="anchor", label="Keep", kind="choice", default="centre",
            options=(("centre", "Middle"), ("top", "Top"), ("bottom", "Bottom")),
            help="Which part survives when the frame has to lose height.",
        ),
    ),
    video_filters=_aspect_filters,
    media_kinds=STILL_AND_MOVING,
)

TRIM = Effect(
    id="trim",
    label="Trim",
    summary="Keep a stretch of the clip and drop the rest.",
    stage="stream",
    params=(
        EffectParam(
            id="start", label="Start at", kind="number", default=0.0,
            minimum=0.0, maximum=3600.0, step=0.1, unit="s",
            help="Everything before this is dropped.",
        ),
        EffectParam(
            id="length", label="Keep for", kind="number", default=0.0,
            minimum=0.0, maximum=3600.0, step=0.1, unit="s",
            help="0 keeps everything from the start point onwards.",
        ),
    ),
    retimes=True,
    duration_of=_trim_duration,
    video_filters=_trim_video_filters,
    audio_filters=_trim_audio_filters,
)

VOLUME = Effect(
    id="volume",
    label="Volume",
    summary="Lift, drop, or silence the clip's own audio.",
    stage="stream",
    params=(
        EffectParam(
            id="gain", label="Gain", kind="number", default=1.0,
            minimum=0.0, maximum=4.0, step=0.05, unit="x",
            help="1 leaves it alone. Ignored when muted.",
        ),
        EffectParam(
            id="mute", label="Mute", kind="toggle", default=False,
            help="Silence the source, for a clip that will carry its own sound.",
        ),
    ),
    audio_filters=_volume_filters,
)

#: Order is the order the interface offers them in: the cheap, predictable
#: transforms first, then anything that needs a model.
REGISTRY: dict[str, Effect] = {
    effect.id: effect for effect in (FLIP, ROTATE, ASPECT, COLOUR, SPEED, TRIM, VOLUME)
}


def register(effect: Effect) -> None:
    """Add an effect. Kept a function so a plug-in can call it on import."""
    REGISTRY[effect.id] = effect


def describe() -> list[dict[str, Any]]:
    """The registry as the interface consumes it — the only source of the form."""
    described = []
    for effect in REGISTRY.values():
        available, reason = effect.availability()
        described.append(
            {
                "id": effect.id,
                "label": effect.label,
                "summary": effect.summary,
                "stage": effect.stage,
                "retimes": effect.retimes,
                "media_kinds": sorted(effect.media_kinds),
                "available": available,
                "unavailable_reason": reason,
                # The interface offers a preview only where one exists, and
                # explains the gap where it does not.
                "previewable": effect.preview is not None,
                "unpreviewable_reason": effect.unpreviewable_reason,
                "params": [
                    {
                        "id": param.id,
                        "label": param.label,
                        "kind": param.kind,
                        "default": param.default,
                        "help": param.help,
                        "minimum": param.minimum,
                        "maximum": param.maximum,
                        "step": param.step,
                        "unit": param.unit,
                        "presentation": param.presentation,
                        "options": [dict(option) for option in param.choices()],
                        "folder": param.folder_from() if param.folder_from else None,
                    }
                    for param in effect.params
                ],
            }
        )
    return described


# --------------------------------------------------------------------------- #
# Recipes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RecipeStep:
    effect: Effect
    values: dict[str, Any] = field(default_factory=dict)


def read_recipe(steps: Sequence[dict[str, Any]] | None) -> list[RecipeStep]:
    """Turn a stored or submitted recipe into validated steps."""
    read: list[RecipeStep] = []
    for index, raw in enumerate(steps or [], start=1):
        effect_id = str(raw.get("effect") or raw.get("effect_id") or "").strip()
        effect = REGISTRY.get(effect_id)
        if effect is None:
            raise EffectError(f"Step {index} names an unknown effect: {effect_id!r}.")
        available, reason = effect.availability()
        if not available:
            raise EffectError(reason or f"{effect.label} is not available.")
        read.append(RecipeStep(effect=effect, values=coerce_params(effect, raw.get("values"))))
    return read


def build_filtergraph(steps: Sequence[RecipeStep]) -> tuple[list[str], list[str]]:
    """Collect every stream step into one video and one audio filter chain.

    In recipe order, because these do not commute: rotating then flipping is not
    flipping then rotating, and a viewer can see the difference.
    """
    video: list[str] = []
    audio: list[str] = []
    for step in steps:
        if step.effect.stage != "stream":
            continue
        if step.effect.video_filters:
            video.extend(step.effect.video_filters(step.values))
        if step.effect.audio_filters:
            audio.extend(step.effect.audio_filters(step.values))
    return video, audio


def duration_after(steps: Sequence[RecipeStep], seconds: float) -> float:
    """What the clip will run to once the recipe has been applied."""
    result = seconds
    for step in steps:
        result = max(0.0, step.effect.duration_of(step.values, result))
    return result


def render_stream_still(
    source: Path,
    destination: Path,
    steps: Sequence[RecipeStep],
    timeout: int = 300,
) -> dict[str, Any]:
    """Apply the stream effects to a photograph, in one pass.

    The same filtergraph the clip path builds, minus everything about time. A
    flip is a flip whatever it is applied to; what a still has no use for is
    the audio chain, the encoder settings and the timestamps — and the effects
    that only mean something over a duration are refused before they reach
    here, rather than silently doing nothing.
    """
    if not FFMPEG.is_file():
        raise EffectError("The pinned local ffmpeg runtime is missing. Run npm install.")
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")

    video, _audio = build_filtergraph(steps)
    if not video:
        raise EffectError("This recipe has nothing for ffmpeg to do.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(FFMPEG), "-y", "-i", str(source),
            "-vf", ",".join(video),
            # One picture out. Without it ffmpeg will happily treat a still as a
            # one-frame stream and write a video container with an image suffix.
            "-frames:v", "1",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0 or not destination.is_file():
        raise EffectError((completed.stderr or "ffmpeg failed without saying why.").strip()[-1500:])
    return {
        "video_filters": video,
        "output": str(destination),
        "size_bytes": destination.stat().st_size,
    }


def render_stream(
    source: Path,
    destination: Path,
    steps: Sequence[RecipeStep],
    *,
    preview_seconds: float | None = None,
    timeout: int = 1800,
) -> dict[str, Any]:
    """Apply every stream effect in one pass.

    One pass rather than one per effect: each encode costs a generation of
    quality, so a flip, a grade and a speed change done separately would be three
    of them for work FFmpeg can do in a single filtergraph.
    """
    if not FFMPEG.is_file():
        raise EffectError("The pinned local ffmpeg runtime is missing. Run npm install.")
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")

    video, audio = build_filtergraph(steps)
    if not video and not audio:
        raise EffectError("This recipe has nothing for ffmpeg to do.")

    command = [str(FFMPEG), "-y", "-i", str(source)]
    if video:
        command += ["-vf", ",".join(video)]
    if audio:
        command += ["-af", ",".join(audio)]
    if preview_seconds:
        # Cap the rendered timeline, not the source timeline. A trim may start
        # after this many source seconds and slow motion expands the source;
        # input-side `-t` made the former empty and the latter too long.
        command += ["-t", f"{preview_seconds:.3f}"]
    command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(destination),
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        # Decoded explicitly, as everywhere else that runs a media tool here.
        # This library holds clips whose metadata is not Latin-1, and the
        # console default would raise on ffmpeg's own error output — turning a
        # render that merely needed reporting into an unhandled crash.
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0 or not destination.is_file():
        # The tail, not the head: ffmpeg's banner is the first thing it prints
        # and the reason it stopped is the last.
        detail = (completed.stderr or "ffmpeg failed without saying why.").strip()[-1500:]
        raise EffectError(detail)

    return {
        "video_filters": video,
        "audio_filters": audio,
        "output": str(destination),
        "size_bytes": destination.stat().st_size,
    }
