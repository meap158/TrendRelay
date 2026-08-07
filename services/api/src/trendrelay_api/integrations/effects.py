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


@dataclass(frozen=True)
class Effect:
    id: str
    label: str
    summary: str
    stage: Stage
    params: tuple[EffectParam, ...] = ()
    media_kinds: frozenset[str] = frozenset({"video"})
    #: Stream effects only: the FFmpeg fragments this step contributes.
    video_filters: Callable[[dict[str, Any]], list[str]] | None = None
    audio_filters: Callable[[dict[str, Any]], list[str]] | None = None
    #: Changes duration, so anything already timed against the source — a clip
    #: plan, a blur timeline — no longer lines up. Ordering matters because of
    #: this, and the interface says so.
    retimes: bool = False
    #: How the output duration changes, for showing the result before rendering.
    duration_factor: Callable[[dict[str, Any]], float] = lambda values: 1.0
    availability: Callable[[], tuple[bool, str | None]] = lambda: (True, None)

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
            allowed = {option for option, _ in param.options}
            if str(given) not in allowed:
                raise EffectError(
                    f"{param.label} must be one of {', '.join(sorted(allowed))}."
                )
            values[param.id] = str(given)
    return values


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
    duration_factor=lambda values: 1.0 / float(values["rate"]),
    video_filters=_speed_video_filters,
    audio_filters=_speed_audio_filters,
)

#: Order is the order the interface offers them in: the cheap, predictable
#: transforms first, then anything that needs a model.
REGISTRY: dict[str, Effect] = {
    effect.id: effect for effect in (FLIP, ROTATE, COLOUR, SPEED)
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
                        "options": [
                            {"value": value, "label": label}
                            for value, label in param.options
                        ],
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
        result *= step.effect.duration_factor(step.values)
    return result


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

    command = [str(FFMPEG), "-y"]
    if preview_seconds:
        # Placed before the input so decoding stops early too, which is what
        # makes a preview quick rather than merely short.
        command += ["-t", f"{preview_seconds:.3f}"]
    command += ["-i", str(source)]
    if video:
        command += ["-vf", ",".join(video)]
    if audio:
        command += ["-af", ",".join(audio)]
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
