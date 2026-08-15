"""The things that can be stuck on a face, and how a sprite is made of one.

An overlay pack is normally a folder of PNGs. That works and it is what the
drop-in half of this module accepts, but it makes the *built-in* pack a pile of
binary blobs: unreviewable in a diff, fixed at whatever resolution someone
exported, and fuzzy the moment a 4K close-up asks for a sticker larger than the
file. So the built-ins are declared as shapes in a unit square instead — a few
ellipses, polygons and rectangles per object — and rasterised on demand at
exactly the size the frame needs.

That buys three things worth more than the drawing code costs:

*One renderer.* The picker in the browser shows a PNG produced by this same
function, at the same proportions, so the gallery is the render rather than an
artist's impression of it. There is no second implementation to drift.

*Any resolution.* A sticker on a face forty pixels wide and a sticker on a face
a thousand pixels wide are both drawn at their own size, so neither is soft.

*A reviewable diff.* Moving an eye two percent to the left is a visible line in
a changeset.

Anything the built-ins cannot express is a drop-in: an RGBA PNG in
``.data/overlays`` with a small JSON sidecar saying where on a face it hangs.
That is the extension point, and it needs no code at all.
"""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from trendrelay_api.integrations.face_landmarks import AnchorName
from trendrelay_api.tool_registry import PROJECT_ROOT

#: Where an operator drops their own overlays. One RGBA PNG per object, with an
#: optional JSON sidecar of the same name describing where it sits.
OVERLAY_ROOT = PROJECT_ROOT / ".data" / "overlays"

#: Ids reach a filesystem path and a URL, so they are restricted rather than
#: sanitised. A name that is not this shape is skipped, not repaired.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")

#: Sprites are drawn oversized and shrunk, which antialiases every edge in one
#: step instead of relying on each drawing call to do it. Four is where the
#: improvement stops being visible.
SUPERSAMPLE = 4
#: The widest the supersampled canvas may get. A face can be most of a 4K frame,
#: and four times that in float32 is gigabytes per shape. Past this the
#: supersampling is dropped rather than the sprite shrunk: an edge drawn at two
#: thousand pixels is already smoother than one drawn at two hundred and
#: smoothed, and the sticker has to come out at the size the face asked for.
MAX_DRAW_WIDTH = 2048
#: A hard ceiling on the sprite itself, past which it is scaled up at the paste.
MAX_SPRITE_WIDTH = 4096

RGBA = tuple[int, int, int, int]
UnitPoint = tuple[float, float]

# The pack's palette, named so a change is one line rather than a hunt.
BLACK: RGBA = (18, 18, 20, 255)
INK: RGBA = (30, 32, 40, 255)
REDACT: RGBA = (16, 17, 20, 245)
BONE: RGBA = (238, 238, 232, 255)
WHITE: RGBA = (250, 250, 252, 255)
YELLOW: RGBA = (255, 206, 42, 255)
GOLD: RGBA = (232, 178, 46, 255)
DEEP_GOLD: RGBA = (196, 142, 26, 255)
PINK: RGBA = (240, 160, 176, 255)
FUR: RGBA = (58, 56, 62, 255)
LENS: RGBA = (26, 28, 34, 226)
FRAME: RGBA = (18, 20, 24, 245)
SURGICAL: RGBA = (140, 190, 215, 250)
SURGICAL_FOLD: RGBA = (108, 158, 186, 200)
LOOP: RGBA = (206, 214, 222, 230)
STEEL: RGBA = (150, 158, 168, 255)
DARK_STEEL: RGBA = (60, 66, 74, 255)
CYAN: RGBA = (86, 216, 226, 255)
RED: RGBA = (226, 74, 78, 255)
PARTY: RGBA = (232, 74, 95, 255)
HAIR: RGBA = (62, 44, 34, 255)
BLUE: RGBA = (56, 112, 230, 255)
PURPLE: RGBA = (132, 86, 214, 255)
GREEN: RGBA = (50, 190, 132, 255)
ORANGE: RGBA = (245, 136, 52, 255)
SOFT_PINK: RGBA = (255, 118, 168, 235)
GLASS: RGBA = (88, 214, 236, 175)


@dataclass(frozen=True)
class Shape:
    """One drawing instruction, in a unit square the sprite is scaled from.

    Coordinates run 0 to 1 across the sprite in both directions, so an object is
    a proportion rather than a size and the same declaration draws a thumbnail
    and a 4K sticker.
    """

    kind: Literal["ellipse", "rect", "polygon", "line"]
    fill: RGBA | None = None
    stroke: RGBA | None = None
    #: Stroke thickness, as a fraction of the sprite's width.
    stroke_width: float = 0.0
    centre: UnitPoint = (0.5, 0.5)
    #: Full width and height, not radii — the way a designer states a box.
    size: UnitPoint = (1.0, 1.0)
    #: Degrees clockwise, for an ellipse that is not axis-aligned.
    rotation: float = 0.0
    #: Ellipses only: the wedge to draw, which is how an arc becomes a smile.
    start_angle: float = 0.0
    end_angle: float = 360.0
    #: Rectangles only: corner rounding as a fraction of the sprite's width.
    radius: float = 0.0
    points: tuple[UnitPoint, ...] = ()


@dataclass(frozen=True)
class Overlay:
    """An object, and everything needed to hang it on a face."""

    id: str
    label: str
    group: str
    #: Which part of the face it attaches to.
    anchor: AnchorName = "face"
    #: How wide it is drawn, in face widths. The single most important number
    #: here: it is what makes a sticker the size of a head rather than the size
    #: of the frame, at any distance from the camera.
    width_in_faces: float = 1.4
    #: Sprite height over sprite width. Declared rather than measured so a
    #: drop-in PNG and a drawn object are placed by the same rule.
    aspect: float = 1.0
    #: Nudge from the anchor point, in face widths, along the head's own axes.
    #: Positive y is downwards on an upright head.
    offset: UnitPoint = (0.0, 0.0)
    #: Whether it turns with a tilted head. False for anything that should stay
    #: level with the world — very little does.
    follows_roll: bool = True
    #: Whether the face is actually hidden by this. Drives how the finished
    #: render is filed, so it is a claim about privacy and not about size: a bar
    #: across the eyes is smaller than a face and does not cover one.
    occludes: bool = False
    #: Said in the picker, where somebody is deciding whether this is enough.
    note: str = ""
    shapes: tuple[Shape, ...] = ()
    #: Drop-ins only: the PNG this is read from.
    image: Path | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def _mirror(shape: Shape) -> Shape:
    """The same shape on the other side of the sprite.

    Half the pack is symmetrical, and a hand-written mirror is where a
    two-percent asymmetry creeps in that nobody can find afterwards.
    """
    return Shape(
        kind=shape.kind,
        fill=shape.fill,
        stroke=shape.stroke,
        stroke_width=shape.stroke_width,
        centre=(1.0 - shape.centre[0], shape.centre[1]),
        size=shape.size,
        rotation=-shape.rotation,
        start_angle=180.0 - shape.end_angle,
        end_angle=180.0 - shape.start_angle,
        radius=shape.radius,
        points=tuple((1.0 - x, y) for x, y in shape.points),
    )


def _pair(shape: Shape) -> tuple[Shape, Shape]:
    return (shape, _mirror(shape))


# --------------------------------------------------------------------------- #
# The built-in pack
# --------------------------------------------------------------------------- #

COVER = "Cover the face"
FEATURES = "Eyes and mouth"
HEADWEAR = "On the head"
REACTIONS = "Reactions and accents"
CREATOR_UI = "Creator UI"

#: A stable id per group, alongside the English name.
#:
#: The interface translates from the id and falls back to the name, the way it
#: already does for every other label the registry serves. Keying a dictionary
#: on the English string itself would work until somebody improved the wording,
#: at which point six locales would silently revert to English.
GROUP_IDS = {
    COVER: "cover",
    FEATURES: "features",
    HEADWEAR: "headwear",
    REACTIONS: "reactions",
    CREATOR_UI: "creator_ui",
}


_CENSOR_BLOCK = Overlay(
    id="censor_block",
    label="Censor block",
    group=COVER,
    anchor="face",
    width_in_faces=1.35,
    aspect=1.25,
    occludes=True,
    note="A flat cover. Nothing of the face survives it.",
    shapes=(
        Shape("rect", fill=REDACT, size=(1.0, 1.0), radius=0.10),
    ),
)

_SMILEY = Overlay(
    id="smiley",
    label="Smiley",
    group=COVER,
    anchor="face",
    width_in_faces=1.45,
    aspect=1.0,
    occludes=True,
    note="Hides the whole face and reads as a choice rather than a redaction.",
    shapes=(
        Shape("ellipse", fill=YELLOW, size=(1.0, 1.0)),
        *_pair(Shape("ellipse", fill=BLACK, centre=(0.34, 0.38), size=(0.13, 0.19))),
        Shape(
            "ellipse", stroke=BLACK, stroke_width=0.065,
            centre=(0.5, 0.50), size=(0.56, 0.50), start_angle=25, end_angle=155,
        ),
    ),
)

_ROBOT = Overlay(
    id="robot",
    label="Robot head",
    group=COVER,
    anchor="face",
    width_in_faces=1.4,
    aspect=1.2,
    offset=(0.0, 0.04),
    occludes=True,
    note="Hides the whole face.",
    shapes=(
        Shape("rect", fill=STEEL, centre=(0.5, 0.14), size=(0.05, 0.18)),
        Shape("ellipse", fill=RED, centre=(0.5, 0.055), size=(0.16, 0.11)),
        Shape("rect", fill=STEEL, centre=(0.5, 0.62), size=(0.92, 0.72), radius=0.10),
        *_pair(Shape("rect", fill=CYAN, centre=(0.32, 0.50), size=(0.22, 0.17), radius=0.03)),
        Shape("rect", fill=DARK_STEEL, centre=(0.5, 0.81), size=(0.52, 0.20), radius=0.03),
        *_pair(Shape("rect", fill=STEEL, centre=(0.38, 0.81), size=(0.03, 0.18))),
    ),
)

_SKULL = Overlay(
    id="skull",
    label="Skull",
    group=COVER,
    anchor="face",
    width_in_faces=1.35,
    aspect=1.22,
    occludes=True,
    note="Hides the whole face.",
    shapes=(
        Shape("ellipse", fill=BONE, centre=(0.5, 0.40), size=(0.94, 0.74)),
        Shape("rect", fill=BONE, centre=(0.5, 0.76), size=(0.56, 0.42), radius=0.14),
        *_pair(Shape("ellipse", fill=INK, centre=(0.31, 0.42), size=(0.28, 0.32))),
        Shape("polygon", fill=INK, points=((0.50, 0.53), (0.575, 0.68), (0.425, 0.68))),
        # A dark mouth with bone between the teeth, rather than lines drawn on
        # the jaw: lines alone read as scratches, not as a skull.
        Shape("rect", fill=INK, centre=(0.5, 0.85), size=(0.44, 0.17), radius=0.03),
        *_pair(Shape("line", stroke=BONE, stroke_width=0.022,
                     points=((0.40, 0.77), (0.40, 0.94)))),
        Shape("line", stroke=BONE, stroke_width=0.022, points=((0.50, 0.77), (0.50, 0.94))),
    ),
)

_GHOST = Overlay(
    id="ghost",
    label="Ghost",
    group=COVER,
    anchor="face",
    width_in_faces=1.45,
    aspect=1.3,
    occludes=True,
    note="Hides the whole face.",
    shapes=(
        Shape("ellipse", fill=WHITE, centre=(0.5, 0.38), size=(0.92, 0.72)),
        Shape("rect", fill=WHITE, centre=(0.5, 0.58), size=(0.92, 0.40)),
        Shape(
            "polygon", fill=WHITE,
            points=(
                (0.04, 0.70), (0.96, 0.70), (0.96, 0.82), (0.84, 0.97),
                (0.70, 0.82), (0.56, 0.97), (0.42, 0.82), (0.28, 0.97), (0.14, 0.82),
                (0.04, 0.82),
            ),
        ),
        *_pair(Shape("ellipse", fill=INK, centre=(0.34, 0.36), size=(0.16, 0.22))),
        Shape("ellipse", fill=INK, centre=(0.5, 0.58), size=(0.18, 0.24)),
    ),
)

_CENSOR_BAR = Overlay(
    id="censor_bar",
    label="Eye bar",
    group=FEATURES,
    anchor="eyes",
    width_in_faces=1.15,
    aspect=0.26,
    occludes=False,
    note=(
        "Covers the eyes only. The traditional redaction, and not a reliable "
        "one: the jaw, hairline and ears are still there to be recognised."
    ),
    shapes=(Shape("rect", fill=REDACT, size=(1.0, 1.0), radius=0.03),),
)

_SUNGLASSES = Overlay(
    id="sunglasses",
    label="Sunglasses",
    group=FEATURES,
    anchor="eyes",
    width_in_faces=1.2,
    aspect=0.40,
    shapes=(
        Shape("rect", fill=FRAME, centre=(0.5, 0.34), size=(0.20, 0.10), radius=0.02),
        *_pair(Shape("rect", fill=LENS, centre=(0.255, 0.50), size=(0.43, 0.80), radius=0.14)),
        *_pair(Shape("rect", fill=FRAME, centre=(0.025, 0.28), size=(0.05, 0.12))),
    ),
)

_FACE_MASK = Overlay(
    id="face_mask",
    label="Face mask",
    group=FEATURES,
    anchor="mouth",
    width_in_faces=1.02,
    aspect=0.76,
    offset=(0.0, 0.04),
    note="Covers the nose and mouth. The eyes and brow are still visible.",
    shapes=(
        # Drawn first and reaching past the mask, so the loops read as going
        # behind it towards an ear rather than being painted on it.
        *_pair(Shape("ellipse", stroke=LOOP, stroke_width=0.022,
                     centre=(0.06, 0.50), size=(0.34, 0.66),
                     start_angle=100, end_angle=260)),
        Shape("rect", fill=SURGICAL, centre=(0.5, 0.50), size=(0.80, 0.94), radius=0.20),
        Shape("line", stroke=SURGICAL_FOLD, stroke_width=0.018,
              points=((0.14, 0.42), (0.86, 0.42))),
        Shape("line", stroke=SURGICAL_FOLD, stroke_width=0.018,
              points=((0.14, 0.58), (0.86, 0.58))),
        Shape("line", stroke=SURGICAL_FOLD, stroke_width=0.018,
              points=((0.16, 0.74), (0.84, 0.74))),
    ),
)

_MOUSTACHE = Overlay(
    id="moustache",
    label="Moustache",
    group=FEATURES,
    anchor="mouth",
    width_in_faces=0.82,
    aspect=0.38,
    offset=(0.0, -0.11),
    shapes=(
        Shape("rect", fill=HAIR, centre=(0.5, 0.60), size=(0.18, 0.42), radius=0.04),
        *_pair(Shape("ellipse", fill=HAIR, centre=(0.29, 0.52), size=(0.58, 0.56),
                     rotation=-18)),
    ),
)

_CAT_EARS = Overlay(
    id="cat_ears",
    label="Cat ears",
    group=HEADWEAR,
    anchor="forehead",
    width_in_faces=1.3,
    aspect=0.45,
    # Ears stand on the skull rather than in front of it, so the sprite is
    # lifted by half its own height from the anchor at the top of the head.
    offset=(0.0, -0.24),
    shapes=(
        *_pair(Shape("polygon", fill=FUR,
                     points=((0.02, 1.00), (0.20, 0.02), (0.43, 0.94)))),
        *_pair(Shape("polygon", fill=PINK,
                     points=((0.13, 0.86), (0.21, 0.26), (0.33, 0.82)))),
    ),
)

_CROWN = Overlay(
    id="crown",
    label="Crown",
    group=HEADWEAR,
    anchor="forehead",
    width_in_faces=1.15,
    aspect=0.66,
    offset=(0.0, -0.04),
    shapes=(
        Shape(
            "polygon", fill=GOLD,
            points=(
                (0.03, 0.98), (0.03, 0.30), (0.20, 0.60), (0.36, 0.08),
                (0.50, 0.44), (0.64, 0.08), (0.80, 0.60), (0.97, 0.30), (0.97, 0.98),
            ),
        ),
        Shape("rect", fill=DEEP_GOLD, centre=(0.5, 0.86), size=(0.94, 0.22)),
        *_pair(Shape("ellipse", fill=RED, centre=(0.22, 0.86), size=(0.10, 0.14))),
        Shape("ellipse", fill=CYAN, centre=(0.5, 0.86), size=(0.10, 0.14)),
    ),
)

_PARTY_HAT = Overlay(
    id="party_hat",
    label="Party hat",
    group=HEADWEAR,
    anchor="forehead",
    width_in_faces=0.8,
    aspect=1.2,
    # A cone balances on the head. Its base is the bottom of the sprite, so the
    # whole of it lifts above the anchor rather than hanging over the face.
    offset=(0.0, -0.46),
    shapes=(
        Shape("polygon", fill=PARTY, points=((0.50, 0.06), (0.95, 0.90), (0.05, 0.90))),
        Shape("line", stroke=WHITE, stroke_width=0.05, points=((0.29, 0.44), (0.71, 0.44))),
        Shape("line", stroke=WHITE, stroke_width=0.05, points=((0.19, 0.68), (0.81, 0.68))),
        Shape("rect", fill=WHITE, centre=(0.5, 0.93), size=(1.0, 0.11), radius=0.04),
        Shape("ellipse", fill=YELLOW, centre=(0.5, 0.06), size=(0.22, 0.14)),
    ),
)

# A second, more editorial pack. These are deliberately graphic rather than
# pseudo-photographic: clean shapes survive motion, scale well from thumbnails
# to 4K, and look intentional over footage instead of like low-resolution clip
# art pasted into it.
_PIXEL_MASK = Overlay(
    id="pixel_mask", label="Pixel mosaic", group=COVER, anchor="face",
    width_in_faces=1.38, aspect=1.15, occludes=True,
    note="A full-face graphic mosaic; suitable for identity coverage.",
    shapes=tuple(
        Shape("rect", fill=colour, centre=((column + .5) / 4, (row + .5) / 5),
              size=(.255, .205), radius=.015)
        for row in range(5) for column in range(4)
        for colour in ((INK, BLUE, PURPLE, CYAN)[(row * 3 + column) % 4],)
    ),
)

_ALIEN = Overlay(
    id="alien", label="Alien", group=COVER, anchor="face",
    width_in_faces=1.36, aspect=1.22, occludes=True,
    note="A full-face character mask.",
    shapes=(
        Shape("ellipse", fill=GREEN, centre=(.5, .5), size=(.9, 1.0)),
        *_pair(Shape("ellipse", fill=INK, centre=(.31, .43), size=(.25, .38), rotation=-14)),
        Shape("ellipse", stroke=INK, stroke_width=.035, centre=(.5, .7),
              size=(.28, .16), start_angle=205, end_angle=335),
    ),
)

_FLOWER_FACE = Overlay(
    id="flower_face", label="Flower bloom", group=COVER, anchor="face",
    width_in_faces=1.48, aspect=1.0, occludes=True,
    note="A bold full-face cover with a softer editorial feel.",
    shapes=(
        *tuple(Shape("ellipse", fill=SOFT_PINK,
                     centre=(.5 + .31 * math.cos(angle), .5 + .31 * math.sin(angle)),
                     size=(.43, .43)) for angle in (0, math.pi / 3, 2 * math.pi / 3,
                                                   math.pi, 4 * math.pi / 3, 5 * math.pi / 3)),
        Shape("ellipse", fill=YELLOW, centre=(.5, .5), size=(.62, .62)),
        *_pair(Shape("ellipse", fill=INK, centre=(.39, .45), size=(.07, .1))),
        Shape("ellipse", stroke=INK, stroke_width=.035, centre=(.5, .53),
              size=(.26, .2), start_angle=25, end_angle=155),
    ),
)

_CLOUD_FACE = Overlay(
    id="cloud_face", label="Cloud", group=COVER, anchor="face",
    width_in_faces=1.5, aspect=.82, occludes=True,
    note="A clean full-face cover for softer compositions.",
    shapes=(
        Shape("ellipse", fill=WHITE, centre=(.25, .58), size=(.46, .52)),
        Shape("ellipse", fill=WHITE, centre=(.48, .39), size=(.56, .66)),
        Shape("ellipse", fill=WHITE, centre=(.75, .57), size=(.46, .52)),
        Shape("rect", fill=WHITE, centre=(.5, .68), size=(.76, .34), radius=.15),
    ),
)

_HEART_EYES = Overlay(
    id="heart_eyes", label="Heart eyes", group=FEATURES, anchor="eyes",
    width_in_faces=1.12, aspect=.42,
    shapes=(
        *tuple(shape for x in (.27, .73) for shape in (
            Shape("ellipse", fill=SOFT_PINK, centre=(x - .06, .35), size=(.22, .42)),
            Shape("ellipse", fill=SOFT_PINK, centre=(x + .06, .35), size=(.22, .42)),
            Shape("polygon", fill=SOFT_PINK,
                  points=((x - .16, .38), (x + .16, .38), (x, .96))),
        )),
    ),
)

_STAR_GLASSES = Overlay(
    id="star_glasses", label="Star glasses", group=FEATURES, anchor="eyes",
    width_in_faces=1.24, aspect=.43,
    shapes=(
        Shape("rect", fill=FRAME, centre=(.5, .48), size=(.24, .09), radius=.02),
        *tuple(Shape("polygon", fill=GOLD, stroke=FRAME, stroke_width=.018,
                    points=tuple((x + radius * math.cos(-math.pi / 2 + index * math.pi / 5),
                                  .5 + radius * math.sin(-math.pi / 2 + index * math.pi / 5))
                                 for index, radius in enumerate((.2, .09) * 5)))
               for x in (.25, .75)),
    ),
)

_CYBER_VISOR = Overlay(
    id="cyber_visor", label="Cyber visor", group=FEATURES, anchor="eyes",
    width_in_faces=1.22, aspect=.34,
    shapes=(
        Shape("polygon", fill=GLASS, stroke=CYAN, stroke_width=.025,
              points=((.02, .18), (.98, .18), (.86, .86), (.14, .86))),
        Shape("line", stroke=WHITE, stroke_width=.018, points=((.12, .33), (.88, .33))),
        Shape("ellipse", fill=RED, centre=(.82, .58), size=(.06, .14)),
    ),
)

_DOG_NOSE = Overlay(
    id="dog_nose", label="Dog nose", group=FEATURES, anchor="nose",
    width_in_faces=.52, aspect=.64,
    shapes=(
        Shape("ellipse", fill=INK, centre=(.5, .35), size=(.54, .38)),
        Shape("line", stroke=INK, stroke_width=.055, points=((.5, .48), (.5, .7))),
        Shape("ellipse", stroke=INK, stroke_width=.05, centre=(.36, .68),
              size=(.34, .28), start_angle=0, end_angle=115),
        Shape("ellipse", stroke=INK, stroke_width=.05, centre=(.64, .68),
              size=(.34, .28), start_angle=65, end_angle=180),
    ),
)

_HALO = Overlay(
    id="halo", label="Halo", group=HEADWEAR, anchor="forehead",
    width_in_faces=1.05, aspect=.34, offset=(0, -.38), follows_roll=False,
    shapes=(Shape("ellipse", stroke=GOLD, stroke_width=.085, size=(.9, .52)),),
)

_DEVIL_HORNS = Overlay(
    id="devil_horns", label="Devil horns", group=HEADWEAR, anchor="forehead",
    width_in_faces=1.2, aspect=.55, offset=(0, -.27),
    shapes=(*_pair(Shape("polygon", fill=RED,
                         points=((.06, .98), (.17, .08), (.44, .92)))),),
)

_GRAD_CAP = Overlay(
    id="graduation_cap", label="Graduation cap", group=HEADWEAR, anchor="forehead",
    width_in_faces=1.35, aspect=.64, offset=(0, -.25), follows_roll=False,
    shapes=(
        Shape("polygon", fill=INK, points=((.04, .35), (.5, .08), (.96, .35), (.5, .62))),
        Shape("rect", fill=INK, centre=(.5, .68), size=(.58, .3), radius=.05),
        Shape("line", stroke=GOLD, stroke_width=.025, points=((.5, .2), (.84, .55), (.84, .88))),
        Shape("ellipse", fill=GOLD, centre=(.84, .9), size=(.09, .14)),
    ),
)

_HEADPHONES = Overlay(
    id="headphones", label="Headphones", group=HEADWEAR, anchor="forehead",
    width_in_faces=1.36, aspect=1.05, offset=(0, .27),
    shapes=(
        Shape("ellipse", stroke=INK, stroke_width=.09, centre=(.5, .43), size=(.82, .82),
              start_angle=180, end_angle=360),
        *_pair(Shape("rect", fill=INK, centre=(.1, .64), size=(.18, .46), radius=.06)),
        *_pair(Shape("rect", fill=BLUE, centre=(.1, .64), size=(.09, .3), radius=.03)),
    ),
)

_HEART_BUBBLE = Overlay(
    id="heart_bubble", label="Heart reaction", group=REACTIONS, anchor="forehead",
    width_in_faces=.68, aspect=.86, offset=(.52, -.22), follows_roll=False,
    shapes=(
        Shape("ellipse", fill=WHITE, stroke=SOFT_PINK, stroke_width=.025,
              centre=(.5, .43), size=(.9, .76)),
        Shape("polygon", fill=WHITE, points=((.34, .72), (.45, .98), (.58, .74))),
        Shape("ellipse", fill=SOFT_PINK, centre=(.42, .4), size=(.28, .3)),
        Shape("ellipse", fill=SOFT_PINK, centre=(.58, .4), size=(.28, .3)),
        Shape("polygon", fill=SOFT_PINK, points=((.3, .43), (.7, .43), (.5, .7))),
    ),
)

_LIGHTNING = Overlay(
    id="lightning", label="Lightning accent", group=REACTIONS, anchor="forehead",
    width_in_faces=.55, aspect=1.25, offset=(.58, -.08), follows_roll=False,
    shapes=(Shape("polygon", fill=YELLOW, stroke=ORANGE, stroke_width=.025,
                  points=((.58, .02), (.15, .58), (.48, .56),
                          (.34, .98), (.86, .36), (.53, .39))),),
)

_SPARKLES = Overlay(
    id="sparkles", label="Sparkles", group=REACTIONS, anchor="forehead",
    width_in_faces=1.28, aspect=.8, offset=(0, -.12), follows_roll=False,
    shapes=(
        *tuple(Shape("polygon", fill=colour,
                     points=((x, y - size), (x + size * .28, y - size * .28),
                             (x + size, y), (x + size * .28, y + size * .28),
                             (x, y + size), (x - size * .28, y + size * .28),
                             (x - size, y), (x - size * .28, y - size * .28)))
               for x, y, size, colour in ((.18, .58, .16, GOLD), (.78, .28, .2, CYAN),
                                           (.87, .72, .1, SOFT_PINK))),
    ),
)

_LIVE_BADGE = Overlay(
    id="live_badge", label="Live badge", group=CREATOR_UI, anchor="forehead",
    width_in_faces=.72, aspect=.42, offset=(.58, -.28), follows_roll=False,
    shapes=(
        Shape("rect", fill=RED, size=(1, .78), radius=.18),
        Shape("ellipse", fill=WHITE, centre=(.25, .5), size=(.17, .28)),
        Shape("line", stroke=WHITE, stroke_width=.06, points=((.43, .34), (.43, .66))),
        Shape("line", stroke=WHITE, stroke_width=.06, points=((.58, .34), (.58, .66))),
        Shape("line", stroke=WHITE, stroke_width=.06, points=((.73, .34), (.73, .66))),
    ),
)

_FOCUS_FRAME = Overlay(
    id="focus_frame", label="Focus frame", group=CREATOR_UI, anchor="face",
    width_in_faces=1.5, aspect=1.18,
    shapes=(
        Shape("line", stroke=WHITE, stroke_width=.028, points=((.04, .28), (.04, .04), (.28, .04))),
        Shape("line", stroke=WHITE, stroke_width=.028, points=((.72, .04), (.96, .04), (.96, .28))),
        Shape("line", stroke=WHITE, stroke_width=.028, points=((.96, .72), (.96, .96), (.72, .96))),
        Shape("line", stroke=WHITE, stroke_width=.028, points=((.28, .96), (.04, .96), (.04, .72))),
        Shape("ellipse", fill=RED, centre=(.92, .1), size=(.07, .09)),
    ),
)

_COMMENT_BUBBLE = Overlay(
    id="comment_bubble", label="Comment bubble", group=CREATOR_UI, anchor="forehead",
    width_in_faces=.84, aspect=.66, offset=(.58, -.2), follows_roll=False,
    shapes=(
        Shape("rect", fill=WHITE, stroke=INK, stroke_width=.025,
              centre=(.5, .42), size=(.94, .7), radius=.16),
        Shape("polygon", fill=WHITE, stroke=INK, stroke_width=.02,
              points=((.28, .72), (.36, .98), (.53, .73))),
        *tuple(Shape("ellipse", fill=INK, centre=(x, .42), size=(.09, .12))
               for x in (.3, .5, .7)),
    ),
)

_TAP_CURSOR = Overlay(
    id="tap_cursor", label="Tap cursor", group=CREATOR_UI, anchor="face",
    width_in_faces=.52, aspect=1.15, offset=(.62, .22), follows_roll=False,
    shapes=(
        Shape("polygon", fill=WHITE, stroke=INK, stroke_width=.035,
              points=((.14, .04), (.14, .84), (.38, .63), (.55, .98),
                      (.72, .88), (.55, .57), (.92, .55))),
        Shape("ellipse", stroke=BLUE, stroke_width=.035, centre=(.18, .08), size=(.32, .28)),
    ),
)

BUILT_IN: tuple[Overlay, ...] = (
    _CENSOR_BLOCK,
    _SMILEY,
    _ROBOT,
    _SKULL,
    _GHOST,
    _CENSOR_BAR,
    _SUNGLASSES,
    _FACE_MASK,
    _MOUSTACHE,
    _CAT_EARS,
    _CROWN,
    _PARTY_HAT,
    _PIXEL_MASK,
    _ALIEN,
    _FLOWER_FACE,
    _CLOUD_FACE,
    _HEART_EYES,
    _STAR_GLASSES,
    _CYBER_VISOR,
    _DOG_NOSE,
    _HALO,
    _DEVIL_HORNS,
    _GRAD_CAP,
    _HEADPHONES,
    _HEART_BUBBLE,
    _LIGHTNING,
    _SPARKLES,
    _LIVE_BADGE,
    _FOCUS_FRAME,
    _COMMENT_BUBBLE,
    _TAP_CURSOR,
)

#: The order groups appear in the picker: the ones that hide a face first,
#: because hiding a face is what someone opens this to do.
GROUP_ORDER = (COVER, FEATURES, HEADWEAR, REACTIONS, CREATOR_UI)


# --------------------------------------------------------------------------- #
# Drop-in overlays
# --------------------------------------------------------------------------- #


DROP_IN_GROUP = "Your own"

#: What a sidecar may set. Anything else is ignored rather than rejected, so a
#: sidecar written against a later version still loads.
SIDECAR_NUMBERS = {"width_in_faces": (0.1, 6.0), "aspect": (0.05, 8.0)}

#: The narrowest an object may be and still be said to cover a face. Anything
#: under about a face width is sitting on one, not hiding it.
MIN_COVER_WIDTH = 1.2

#: Sidecar text goes into an API response and onto a tile. An operator's own
#: file is not hostile, but nothing here needs a paragraph.
MAX_SIDECAR_TEXT = 200

#: The largest drop-in worth decoding, per side. A sprite is never drawn above
#: `MAX_SPRITE_WIDTH`, so a larger source buys nothing and costs four bytes a
#: pixel to read — an 8000-square PNG is a quarter of a gigabyte to load and
#: throw away.
MAX_DROP_IN_SIDE = 4096

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(path: Path) -> tuple[int, int] | None:
    """A PNG's dimensions, read from its header rather than by decoding it.

    Twenty-four bytes instead of the whole file, and — the reason it is done
    this way — without OpenCV. The catalogue is served by an endpoint and read
    during validation, neither of which should need the vision runtime just to
    find out how wide a picture is.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    # Signature, then a length, then the IHDR chunk whose first eight bytes are
    # the dimensions. Fixed by the format, so the offsets are safe.
    if len(header) < 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return (width, height) if width > 0 and height > 0 else None


def _rejection(png: Path) -> str | None:
    """Why this file cannot become an overlay, or None if it can."""
    if not ID_PATTERN.match(png.stem):
        return (
            "The name has to be lower-case letters, digits, dashes or "
            "underscores — it becomes the object's id."
        )
    size = png_size(png)
    if size is None:
        return "This is not a readable PNG."
    if max(size) > MAX_DROP_IN_SIDE:
        return (
            f"{size[0]}x{size[1]} is larger than the {MAX_DROP_IN_SIDE}px limit. "
            "Sprites are never drawn bigger than that, so the extra is only cost."
        )
    return None


def _read_sidecar(png: Path) -> dict[str, Any]:
    """The JSON beside a drop-in PNG, or sensible defaults if there is none.

    A pack that arrives as bare PNGs still works: an object with no sidecar is
    treated as a full-face cover, which is both the commonest case and the safe
    reading of "somebody put a picture in the overlays folder".
    """
    sidecar = png.with_suffix(".json")
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return raw


def _drop_in(png: Path) -> Overlay | None:
    if _rejection(png) is not None:
        return None
    raw = _read_sidecar(png)
    numbers: dict[str, float] = {}
    for name, (low, high) in SIDECAR_NUMBERS.items():
        try:
            value = float(raw[name])
        except (KeyError, TypeError, ValueError):
            continue
        numbers[name] = max(low, min(high, value))
    anchor = str(raw.get("anchor", "face"))
    if anchor not in {"eyes", "face", "mouth", "nose", "forehead", "chin"}:
        anchor = "face"
    offset = raw.get("offset")
    if not (isinstance(offset, list | tuple) and len(offset) == 2):
        offset = (0.0, 0.0)
    # A bare PNG with no sidecar keeps its own proportions. Defaulting to a
    # square instead would take a wide pair of sunglasses and squash it into
    # one, which looks like a bug in the compositor rather than a missing file.
    measured = png_size(png)
    shape = measured[1] / measured[0] if measured else 1.0
    width = numbers.get("width_in_faces", 1.4)

    # "This covers a face" decides whether a render is filed as a privacy cut,
    # and here it arrives from a JSON file next to a picture. The claim is
    # checked against the object rather than taken: something narrower than a
    # face, or hung off the mouth, is sitting on a face and not hiding one.
    claimed = bool(raw.get("occludes", False))
    covers = claimed and width >= MIN_COVER_WIDTH and anchor == "face"
    note = _text(raw.get("note"))
    if claimed and not covers:
        note = (
            f"{note} Its sidecar says it covers a face, but at {width:g} face "
            f"widths on the {anchor} it cannot, so it is not filed as one."
        ).strip()

    return Overlay(
        id=png.stem,
        label=_text(raw.get("label")) or png.stem.replace("_", " ").replace("-", " ").title(),
        group=_text(raw.get("group")) or DROP_IN_GROUP,
        anchor=anchor,  # type: ignore[arg-type]
        width_in_faces=width,
        aspect=numbers.get("aspect", shape),
        offset=(float(offset[0]), float(offset[1])),
        follows_roll=bool(raw.get("follows_roll", True)),
        occludes=covers,
        note=note,
        image=png,
    )


def _text(value: Any) -> str:
    """A sidecar string, trimmed to something that fits on a tile."""
    return "" if value is None else " ".join(str(value).split())[:MAX_SIDECAR_TEXT]


def catalogue() -> list[Overlay]:
    """Every overlay available right now, built-ins first.

    Read from disk on every call rather than cached. The folder is a live
    extension point, and a catalogue that needed a restart to notice a new file
    is a catalogue that is wrong exactly when someone is looking at it. The cost
    is a directory listing.
    """
    found = list(BUILT_IN)
    taken = {overlay.id for overlay in found}
    try:
        files = sorted(OVERLAY_ROOT.glob("*.png"))
    except OSError:
        files = []
    for png in files:
        overlay = _drop_in(png)
        # A drop-in never shadows a built-in: the recipe stored against an id
        # has to keep meaning what it meant when it was saved.
        if overlay is not None and overlay.id not in taken:
            found.append(overlay)
            taken.add(overlay.id)
    return found


def rejected_drop_ins() -> list[dict[str, str]]:
    """Files in the overlays folder that did not become objects, and why.

    A file that is simply missing from the gallery is the worst outcome here:
    somebody put it there on purpose, and silence gives them nothing to act on.
    Reported alongside the catalogue so the folder can explain itself.
    """
    try:
        files = sorted(OVERLAY_ROOT.glob("*.png"))
    except OSError:
        return []
    taken = {overlay.id for overlay in BUILT_IN}
    problems = []
    for png in files:
        reason = _rejection(png)
        if reason is None and png.stem in taken:
            reason = f"{png.stem!r} is the id of a built-in object, which it cannot replace."
        if reason is not None:
            problems.append({"file": png.name, "reason": reason})
    return problems


def folder() -> dict[str, Any]:
    """Where an operator adds their own objects, and what did not load.

    Handed to the picker through the effect's own declaration, so the gallery
    can name the folder and explain a rejected file without knowing that it is
    showing overlays rather than something else.
    """
    return {"directory": str(OVERLAY_ROOT), "skipped": rejected_drop_ins()}


def get(overlay_id: str) -> Overlay | None:
    return next((item for item in catalogue() if item.id == overlay_id), None)


def occludes(overlay_id: str) -> bool:
    """Whether choosing this actually hides a face. Unknown ids do not."""
    overlay = get(overlay_id)
    return bool(overlay and overlay.occludes)


def options() -> tuple[dict[str, Any], ...]:
    """The catalogue as the effect registry's choice options.

    Everything the picker needs travels with the option, so the gallery is built
    from one request and a newly dropped-in object appears with a name, a group
    and a preview without a frontend change.
    """
    ordered = sorted(
        catalogue(),
        key=lambda item: (
            GROUP_ORDER.index(item.group) if item.group in GROUP_ORDER else len(GROUP_ORDER),
            item.group,
        ),
    )
    return tuple(
        {
            "value": item.id,
            "label": item.label,
            "group": item.group,
            # Absent for a drop-in's own group, which no shipped dictionary
            # could name — those keep the folder's English heading.
            "group_id": GROUP_IDS.get(item.group),
            "occludes": item.occludes,
            "note": item.note,
            # Where to get a thumbnail, relative to the media-library base. The
            # option carries it so the gallery does not have to know what kind
            # of thing it is showing — the same gallery draws face-swap
            # portraits from a different path.
            "preview": f"face-overlay/objects/{item.id}/sprite",
            # Whether it came from the drop-in folder. The picker uses this to
            # decide what it may cache: a file somebody is editing must not be
            # held for the session.
            "custom": item.image is not None,
        }
        for item in ordered
    )


# --------------------------------------------------------------------------- #
# Rasterising one
# --------------------------------------------------------------------------- #


def _bgra(colour: RGBA) -> tuple[int, int, int, int]:
    """A declared RGBA colour in the order OpenCV writes channels."""
    red, green, blue, alpha = colour
    return (blue, green, red, alpha)


def _rounded_rect_points(
    centre: UnitPoint, size: UnitPoint, radius: float, steps: int = 6
) -> list[UnitPoint]:
    """A rounded rectangle as a polygon.

    Drawn as one polygon rather than a rectangle plus four discs so it can be
    filled in a single pass. Overlapping fills of a semi-transparent colour
    double up where they meet, and the seams show.
    """
    half_width, half_height = size[0] / 2, size[1] / 2
    limit = min(half_width, half_height)
    corner = max(0.0, min(radius, limit))
    left, right = centre[0] - half_width, centre[0] + half_width
    top, bottom = centre[1] - half_height, centre[1] + half_height
    if corner <= 0:
        return [(left, top), (right, top), (right, bottom), (left, bottom)]

    points: list[UnitPoint] = []
    corners = (
        ((right - corner, top + corner), 270.0),
        ((right - corner, bottom - corner), 0.0),
        ((left + corner, bottom - corner), 90.0),
        ((left + corner, top + corner), 180.0),
    )
    for (pivot_x, pivot_y), start in corners:
        for step in range(steps + 1):
            angle = math.radians(start + 90.0 * step / steps)
            points.append((pivot_x + corner * math.cos(angle), pivot_y + corner * math.sin(angle)))
    return points


def _draw(cv2: Any, np: Any, layer: Any, shape: Shape, width: int, height: int) -> None:
    """One shape onto its own transparent layer, in pixels.

    Deliberately not antialiased. Every edge here is softened by the downscale
    at the end instead, and OpenCV's own antialiasing would blend a shape's
    colour towards the transparent black underneath it — which is a dark rim
    around everything once the alpha channel is taken seriously.
    """

    def to_pixels(point: UnitPoint) -> tuple[int, int]:
        return (int(round(point[0] * width)), int(round(point[1] * height)))

    # Thickness is a fraction of the width so a stroke keeps its weight at any
    # sprite size; below one pixel OpenCV would treat it as "filled".
    thickness = max(1, int(round(shape.stroke_width * width)))
    fill = _bgra(shape.fill) if shape.fill else None
    stroke = _bgra(shape.stroke) if shape.stroke else None

    if shape.kind == "ellipse":
        centre = to_pixels(shape.centre)
        axes = (
            max(1, int(round(shape.size[0] * width / 2))),
            max(1, int(round(shape.size[1] * height / 2))),
        )
        if fill:
            cv2.ellipse(
                layer, centre, axes, shape.rotation,
                shape.start_angle, shape.end_angle, fill, -1,
            )
        if stroke:
            cv2.ellipse(
                layer, centre, axes, shape.rotation,
                shape.start_angle, shape.end_angle, stroke, thickness,
            )
        return

    points = (
        _rounded_rect_points(shape.centre, shape.size, shape.radius)
        if shape.kind == "rect"
        else list(shape.points)
    )
    if len(points) < 2:
        return
    polygon = np.array([to_pixels(point) for point in points], dtype=np.int32)
    if shape.kind == "line":
        if stroke:
            cv2.polylines(layer, [polygon], False, stroke, thickness)
        return
    if fill:
        cv2.fillPoly(layer, [polygon], fill)
    if stroke:
        cv2.polylines(layer, [polygon], True, stroke, thickness)


def premultiply(np: Any, image: Any) -> Any:
    """A straight-alpha BGRA image as premultiplied float, 0 to 1.

    Every resample and every blend below happens in this space. Averaging two
    straight-alpha pixels is simply wrong — half of an opaque white next to half
    of a transparent pixel is not grey — and the artefact it produces is a dark
    halo that looks like a badly cut-out sticker, which is exactly what this
    feature must not look like.
    """
    values = image.astype(np.float32) / 255.0
    alpha = values[:, :, 3:4]
    return np.concatenate([values[:, :, :3] * alpha, alpha], axis=2)


def unpremultiply(np: Any, image: Any) -> Any:
    """Premultiplied float back to a straight-alpha 8-bit BGRA image."""
    alpha = image[:, :, 3:4]
    # Where nothing was drawn the colour is meaningless, so the divide is
    # guarded rather than allowed to produce infinities.
    safe = np.where(alpha > 1e-6, alpha, 1.0)
    colour = np.clip(image[:, :, :3] / safe, 0.0, 1.0)
    joined = np.concatenate([colour, np.clip(alpha, 0.0, 1.0)], axis=2)
    return np.rint(joined * 255.0).astype(np.uint8)


def _over(base: Any, layer: Any) -> None:
    """Composite one premultiplied layer onto another, in place.

    Shapes are drawn one layer at a time rather than straight onto the sprite.
    Drawn directly, OpenCV writes the alpha channel as just another number: two
    overlapping fills at 90% would leave 90% rather than 99%, and a stroke over
    a fill would punch its own alpha through whatever was under it.
    """
    inverse = 1.0 - layer[:, :, 3:4]
    base *= inverse
    base += layer


def render_sprite(cv2: Any, np: Any, overlay: Overlay, width: int) -> Any:
    """The overlay as a straight-alpha BGRA image of the given width.

    A drop-in is read from its PNG and resized; a built-in is drawn. Both come
    back in the same form, so nothing downstream has to know which it got.
    """
    width = max(8, min(int(width), MAX_SPRITE_WIDTH))
    height = max(8, int(round(width * overlay.aspect)))

    if overlay.image is not None:
        return _read_png(cv2, np, overlay.image, width, height)

    factor = max(1, min(SUPERSAMPLE, MAX_DRAW_WIDTH // width))
    big_width, big_height = width * factor, height * factor
    canvas = np.zeros((big_height, big_width, 4), dtype=np.float32)
    for shape in overlay.shapes:
        layer = np.zeros((big_height, big_width, 4), dtype=np.uint8)
        _draw(cv2, np, layer, shape, big_width, big_height)
        _over(canvas, premultiply(np, layer))
    # INTER_AREA averages the block each output pixel came from, which is both
    # the antialiasing and, in premultiplied space, the correct average.
    return unpremultiply(np, cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA))


def _read_png(cv2: Any, np: Any, path: Path, width: int, height: int) -> Any:
    """A drop-in PNG as BGRA at the size asked for."""
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"{path.name} could not be read as an image.")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        # No alpha channel at all. Treated as fully opaque rather than refused:
        # a rectangular sticker is a legitimate thing to want.
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def sprite_png(overlay: Overlay, width: int = 256) -> bytes:
    """The overlay as PNG bytes, for the picker.

    The same renderer the clip uses, so what the gallery shows is what gets
    burned in — not a separate drawing of it that can quietly disagree.
    """
    from trendrelay_api.integrations.face_blur import _load_opencv

    cv2 = _load_opencv()
    import numpy as np

    sprite = render_sprite(cv2, np, overlay, width)
    encoded, buffer = cv2.imencode(".png", sprite)
    if not encoded:
        raise ValueError(f"{overlay.id} could not be encoded.")
    return bytes(buffer)
