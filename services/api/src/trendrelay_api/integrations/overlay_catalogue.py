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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from trendrelay_api.integrations import overlay_meshes as meshes
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
#: Skin for a drawn character rather than a person: warm, light, and plainly
#: illustrative, so nothing here is mistaken for a photograph of somebody.
CREAM: RGBA = (252, 224, 202, 255)
MILK_TEA: RGBA = (214, 178, 132, 255)
PEARL: RGBA = (58, 40, 34, 255)
SCREEN: RGBA = (108, 226, 214, 235)


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
    #: What kind of clip this object suits, for matching a suggestion to a
    #: video. Editorial rather than descriptive: not what the thing *is* - the
    #: label already says that - but the words a clip it belongs on would use.
    #:
    #: Multilingual on purpose, and the reason is measurable: this workspace's
    #: library is 2,540 Douyin clips whose captions are Chinese, so an
    #: English-only vocabulary would match nothing in it. See `OBJECT_KEYWORDS`.
    keywords: tuple[str, ...] = ()
    shapes: tuple[Shape, ...] = ()
    #: An object with depth, drawn instead of `shapes` when it is present.
    #:
    #: The two are alternatives rather than layers: a flat object is shapes in
    #: a unit square and a solid one is triangles in a unit cube, and mixing
    #: them would paste a drawing over a render at whatever depth the drawing
    #: is imagined to be at. See `overlay_meshes`.
    mesh: Any | None = None
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
#: The objects with depth, kept together rather than filed among the flat ones.
#:
#: They were first placed in the groups their flat cousins are in, on the
#: reasoning that somebody looking for a hat wants every hat in front of them.
#: That is true and it is not the question being asked here: turning with the
#: head is the reason to choose one of these at all, it is invisible in a
#: thumbnail of a face looking straight ahead, and a solid cap sitting between
#: two stickers reads as one more sticker.
SOLID = "Turns with the head"

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
    SOLID: "solid",
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


# --------------------------------------------------------------------------- #
# Character covers
# --------------------------------------------------------------------------- #
#
# The covers above are archetypes, and archetypes are interchangeable: choosing
# a smiley over a ghost says little beyond "not my face". These have a subject,
# so picking one is a decision about tone. Two are drawn from what this
# audience actually watches - a fox mask and a boba cup are ordinary sights in
# South-East Asian creator video, where a generic smiley reads as a placeholder.


_BABY_CHIBI = Overlay(
    id="baby_chibi", label="Baby chibi", group=COVER, anchor="face",
    width_in_faces=1.55, aspect=1.05, occludes=True,
    note="A big-eyed cartoon head. Warm rather than anonymous.",
    shapes=(
        # Hair first and wider than the head, so the head drawn over it leaves
        # a cap rather than needing a clip the renderer does not have.
        Shape("ellipse", fill=HAIR, centre=(.5, .48), size=(1.0, .96)),
        Shape("ellipse", fill=CREAM, centre=(.5, .58), size=(.88, .84)),
        Shape("ellipse", fill=HAIR, centre=(.5, .27), size=(.86, .38)),
        *_pair(Shape("ellipse", fill=INK, centre=(.33, .61), size=(.23, .29))),
        *_pair(Shape("ellipse", fill=WHITE, centre=(.28, .55), size=(.09, .11))),
        *_pair(Shape("ellipse", fill=WHITE, centre=(.37, .67), size=(.05, .06))),
        *_pair(Shape("ellipse", fill=SOFT_PINK, centre=(.18, .73), size=(.16, .09))),
        Shape("ellipse", stroke=INK, stroke_width=.028, centre=(.5, .77),
              size=(.11, .08), start_angle=20, end_angle=160),
    ),
)

_KITSUNE = Overlay(
    id="kitsune", label="Fox mask", group=COVER, anchor="face",
    width_in_faces=1.42, aspect=1.16, occludes=True,
    note="A painted fox mask. Covers the face and keeps a character.",
    shapes=(
        *_pair(Shape("polygon", fill=BONE,
                     points=((.13, .36), (.05, .01), (.36, .17)))),
        *_pair(Shape("polygon", fill=RED,
                     points=((.17, .30), (.12, .09), (.30, .19)))),
        Shape("ellipse", fill=BONE, centre=(.5, .58), size=(.84, .82)),
        # The snout, which is what stops this reading as a plain white oval.
        Shape("polygon", fill=BONE, points=((.29, .70), (.71, .70), (.5, 1.0))),
        *_pair(Shape("ellipse", fill=INK, centre=(.33, .57), size=(.21, .12),
                     rotation=-12)),
        *_pair(Shape("ellipse", stroke=RED, stroke_width=.026, centre=(.33, .45),
                     size=(.26, .12), rotation=-12,
                     start_angle=190, end_angle=350)),
        *_pair(Shape("polygon", fill=RED,
                     points=((.24, .74), (.40, .78), (.24, .80)))),
        Shape("ellipse", fill=INK, centre=(.5, .82), size=(.09, .06)),
    ),
)

_PANDA = Overlay(
    id="panda", label="Panda", group=COVER, anchor="face",
    width_in_faces=1.5, aspect=1.0, occludes=True,
    note="A round animal face that reads instantly at any size.",
    shapes=(
        *_pair(Shape("ellipse", fill=INK, centre=(.19, .15), size=(.31, .31))),
        Shape("ellipse", fill=WHITE, centre=(.5, .57), size=(.94, .86)),
        *_pair(Shape("ellipse", fill=INK, centre=(.32, .55), size=(.31, .37),
                     rotation=-18)),
        *_pair(Shape("ellipse", fill=WHITE, centre=(.33, .54), size=(.12, .14))),
        *_pair(Shape("ellipse", fill=INK, centre=(.34, .55), size=(.06, .07))),
        Shape("ellipse", fill=INK, centre=(.5, .72), size=(.13, .08)),
        Shape("ellipse", stroke=INK, stroke_width=.03, centre=(.5, .78),
              size=(.18, .11), start_angle=20, end_angle=160),
    ),
)

_BOBA = Overlay(
    id="boba", label="Bubble tea", group=COVER, anchor="face",
    width_in_faces=1.34, aspect=1.28, occludes=True,
    note="A boba cup with a face on it. Playful, and unmistakably itself.",
    shapes=(
        # A polygon rather than a rectangle: a cup tapers, and the taper is
        # most of what makes it read as a cup at thumbnail size.
        Shape("polygon", fill=PARTY,
              points=((.54, .01), (.64, .01), (.70, .30), (.60, .30))),
        Shape("polygon", fill=MILK_TEA,
              points=((.21, .27), (.79, .27), (.71, 1.0), (.29, 1.0))),
        Shape("rect", fill=WHITE, centre=(.5, .24), size=(.68, .11), radius=.03),
        Shape("ellipse", fill=PEARL, centre=(.39, .88), size=(.14, .12)),
        Shape("ellipse", fill=PEARL, centre=(.53, .90), size=(.14, .12)),
        Shape("ellipse", fill=PEARL, centre=(.63, .84), size=(.13, .11)),
        Shape("ellipse", fill=PEARL, centre=(.45, .78), size=(.13, .11)),
        *_pair(Shape("ellipse", fill=INK, centre=(.40, .49), size=(.09, .13))),
        *_pair(Shape("ellipse", fill=SOFT_PINK, centre=(.31, .60), size=(.11, .06))),
        Shape("ellipse", stroke=INK, stroke_width=.028, centre=(.5, .57),
              size=(.13, .09), start_angle=20, end_angle=160),
    ),
)

_CRT_HEAD = Overlay(
    id="crt_head", label="CRT head", group=COVER, anchor="face",
    width_in_faces=1.46, aspect=.96, occludes=True,
    note="A television for a head. Hides the face and looks deliberate.",
    shapes=(
        Shape("line", stroke=STEEL, stroke_width=.03,
              points=((.40, .20), (.24, .01))),
        Shape("line", stroke=STEEL, stroke_width=.03,
              points=((.60, .20), (.76, .01))),
        Shape("rect", fill=DARK_STEEL, centre=(.5, .61), size=(1.0, .76), radius=.09),
        Shape("rect", fill=SCREEN, centre=(.44, .60), size=(.70, .58), radius=.05),
        # Scanlines: what separates a television from a grey box.
        Shape("line", stroke=INK, stroke_width=.012, points=((.11, .48), (.77, .48))),
        Shape("line", stroke=INK, stroke_width=.012, points=((.11, .62), (.77, .62))),
        Shape("line", stroke=INK, stroke_width=.012, points=((.11, .76), (.77, .76))),
        # Knobs, written out rather than mirrored: they belong on one side.
        Shape("ellipse", fill=STEEL, centre=(.89, .48), size=(.11, .11)),
        Shape("ellipse", fill=STEEL, centre=(.89, .68), size=(.08, .08)),
        Shape("ellipse", fill=INK, centre=(.33, .55), size=(.08, .11)),
        Shape("ellipse", fill=INK, centre=(.55, .55), size=(.08, .11)),
        Shape("ellipse", stroke=INK, stroke_width=.026, centre=(.44, .66),
              size=(.16, .10), start_angle=20, end_angle=160),
    ),
)

# --------------------------------------------------------------------------- #
# Objects with depth
# --------------------------------------------------------------------------- #
#
# Built from `overlay_meshes` primitives in a unit cube, the same way the flat
# objects above are built from shapes in a unit square, and for the same three
# reasons: one renderer, any resolution, and a diff somebody can read.
#
# They sit in the groups their flat cousins are in rather than in a group of
# their own. Somebody looking for a hat wants every hat in front of them; that
# one of them turns with the head is a property of the hat, said on its tile,
# not a category of object.

#: The colours these are painted in. Named separately from the flat palette
#: above because a lit surface needs a base a shade darker than a drawn one -
#: the light adds up to 45% on top, and a base picked to look right flat comes
#: out washed once it is being shaded.
CAP_CLOTH: RGBA = (44, 62, 116, 255)
CAP_PEAK: RGBA = (32, 46, 88, 255)
FELT: RGBA = (28, 28, 34, 255)
BAND: RGBA = (176, 42, 58, 255)
GOLD_SOLID: RGBA = (196, 148, 38, 255)
JEWEL: RGBA = (188, 52, 74, 255)
LENS_SOLID: RGBA = (26, 30, 42, 255)
METAL: RGBA = (188, 194, 206, 255)
CONE_PARTY: RGBA = (206, 66, 118, 255)
POM: RGBA = (240, 226, 120, 255)


def _cap() -> Any:
    """A five-panel cap: a dome that fits a skull and a peak in front of it."""
    dome = meshes.painted(
        meshes.scaled(meshes.sphere(CAP_CLOTH, segments=24, rings=14), 0.86, 0.62, 0.86),
        CAP_CLOTH,
    )
    # The lower half of a sphere is inside the head, so it is lifted until only
    # the crown shows rather than being cut - a hemisphere would leave an open
    # edge, and an open edge lit from behind reads as a hole.
    dome = meshes.moved(dome, y=0.10)
    # Reaching further forward than it is wide, and angled down, so it reads as
    # a peak head-on instead of as a rim around the dome.
    peak = meshes.painted(
        meshes.moved(
            meshes.turned(
                meshes.scaled(meshes.cylinder(CAP_PEAK, sides=28), 0.78, 0.05, 0.98),
                x=16.0,
            ),
            y=-0.13, z=0.40,
        ),
        CAP_PEAK,
    )
    return dome + peak


def _top_hat() -> Any:
    """A crown and a brim, which is the whole of a top hat."""
    crown = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(FELT, sides=30), 0.58, 0.64, 0.58), y=0.16
        ),
        FELT,
    )
    brim = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(FELT, sides=34), 0.96, 0.05, 0.96), y=-0.17
        ),
        FELT,
    )
    band = meshes.painted(
        meshes.moved(meshes.scaled(meshes.cylinder(BAND, sides=30), 0.60, 0.14, 0.60), y=-0.08),
        BAND,
    )
    return brim + crown + band


def _party_cone() -> Any:
    """A cone and a bobble, leaning the way a party hat actually sits."""
    cone = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cone(CONE_PARTY, sides=26), 0.62, 0.80, 0.62), y=-0.05
        ),
        CONE_PARTY,
    )
    bobble = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.sphere(POM, segments=16, rings=10), 0.20, 0.20, 0.20),
            y=0.40,
        ),
        POM,
    )
    return meshes.turned(cone + bobble, x=-10.0)


def _solid_crown() -> Any:
    """A band with points on it, and a stone in the middle of the front."""
    band = meshes.painted(
        meshes.scaled(meshes.cylinder(GOLD_SOLID, sides=28, caps=False), 0.86, 0.34, 0.86),
        GOLD_SOLID,
    )
    points = meshes.Mesh()
    for index in range(7):
        angle = -70.0 + index * (140.0 / 6)
        radians = math.radians(angle)
        spike = meshes.painted(
            meshes.scaled(meshes.cone(GOLD_SOLID, sides=10), 0.20, 0.34, 0.20), GOLD_SOLID
        )
        points = points + meshes.moved(
            spike,
            x=math.sin(radians) * 0.40,
            y=0.32,
            z=math.cos(radians) * 0.40,
        )
    stone = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.sphere(JEWEL, segments=14, rings=9), 0.17, 0.17, 0.10),
            z=0.42,
        ),
        JEWEL,
    )
    return band + points + stone


def _solid_sunglasses() -> Any:
    """Two lenses, a bridge and the arms that go back past the ears.

    The arms are the reason this is worth having in three dimensions: face the
    camera and they are invisible, turn and they are most of what is seen.
    """
    # Lenses nearly touching, with a short bridge between them. Set wide apart
    # with a long bridge they read as two goggles rather than one pair - the
    # gap between the lenses of real shades is a few millimetres.
    lens = meshes.turned(
        meshes.scaled(meshes.cylinder(LENS_SOLID, sides=22), 0.38, 0.04, 0.30), x=90.0
    )
    left = meshes.painted(meshes.moved(lens, x=-0.21, z=0.20), LENS_SOLID)
    right = meshes.painted(meshes.moved(lens, x=0.21, z=0.20), LENS_SOLID)
    bridge = meshes.painted(
        meshes.moved(meshes.scaled(meshes.box(METAL), 0.09, 0.03, 0.05), y=0.05, z=0.21),
        METAL,
    )
    arms = meshes.Mesh()
    for side in (-1.0, 1.0):
        # Hinged at the top outer corner and running back, which is where an
        # arm actually joins and why it appears from behind the lens as the
        # head turns rather than sliding out of its middle.
        arm = meshes.painted(
            meshes.moved(
                meshes.scaled(meshes.box(METAL), 0.03, 0.03, 0.58),
                x=side * 0.38, y=0.09, z=-0.08,
            ),
            METAL,
        )
        arms = arms + arm
    return left + right + bridge + arms


_CAP_3D = Overlay(
    id="cap_3d",
    label="Cap",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.30,
    aspect=1.0,
    offset=(0.0, 0.14),
    note="Turns with the head — the peak swings round as the subject looks away.",
    mesh=_cap(),
)

_TOP_HAT_3D = Overlay(
    id="top_hat_3d",
    label="Top hat",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.20,
    aspect=1.0,
    offset=(0.0, 0.30),
    note="Turns with the head. The brim is a real disc, so it foreshortens.",
    mesh=_top_hat(),
)

_PARTY_CONE_3D = Overlay(
    id="party_cone_3d",
    label="Party cone",
    group=SOLID,
    anchor="forehead",
    width_in_faces=0.85,
    aspect=1.15,
    offset=(0.0, 0.34),
    note="Turns with the head, and leans the way one actually sits.",
    mesh=_party_cone(),
)

_CROWN_3D = Overlay(
    id="crown_3d",
    label="Solid crown",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.10,
    aspect=1.0,
    offset=(0.0, 0.16),
    note="Turns with the head, so the far points pass behind it.",
    mesh=_solid_crown(),
)

_SUNGLASSES_3D = Overlay(
    id="sunglasses_3d",
    label="Solid shades",
    group=SOLID,
    anchor="eyes",
    width_in_faces=1.24,
    aspect=1.0,
    offset=(0.0, 0.0),
    note="Turns with the head — the arms come into view as the subject turns.",
    mesh=_solid_sunglasses(),
)


# --------------------------------------------------------------------------- #
# The trend batch
# --------------------------------------------------------------------------- #
#
# Chosen from two kinds of evidence, because the first kind alone is thin.
#
# What the platforms are doing: butterflies around the head, star stickers on
# the face, halo and fairy looks, and anime styling come up repeatedly in
# reporting on TikTok and Douyin effects. That is trade-press evidence and it
# is soft, so it decides *which* of the durable prop families to draw next
# rather than being taken as a list.
#
# What this workspace is actually posting: 2,540 Douyin clips whose commonest
# tags are 变装 (transformation), 健身 / 腹肌 / 马甲线 (fitness, abs, core),
# 御姐, and 女摄. That is hard evidence, it is local, and it is why a sweatband
# and a visor are in this batch at all - nothing in the pack suited the second
# biggest thing this library posts about.
#
# See `docs/architecture/0026-adding-overlay-objects.md` for the process.

BUTTERFLY_WING: RGBA = (146, 112, 232, 255)
BUTTERFLY_EDGE: RGBA = (86, 62, 168, 255)
BLUSH: RGBA = (255, 148, 158, 210)
FLAME: RGBA = (250, 118, 44, 255)
FLAME_CORE: RGBA = (255, 208, 72, 255)
SWEAT: RGBA = (126, 200, 240, 240)
TERRY: RGBA = (238, 240, 244, 255)
SPORT_STRIPE: RGBA = (216, 62, 74, 255)
SANTA_RED: RGBA = (206, 54, 58, 255)
LEAF: RGBA = (86, 178, 122, 255)


def _butterfly(
    centre: UnitPoint, size: float, tilt: float, aspect: float
) -> tuple[Shape, ...]:
    """One butterfly: two wing pairs and a body, at a size and a lean.

    Written as a helper because the object is four of them at different sizes -
    hand-placing sixteen ellipses is how two of them end up subtly different
    shapes and nobody can find which.
    """
    x, y = centre
    # Both axes run 0 to 1 while the sprite itself is `aspect` times as tall as
    # it is wide, so a shape given the same number twice comes out squashed by
    # exactly that factor. Every vertical measurement here is divided by it.
    # Getting this backwards - multiplying - is what turned the first version
    # into a row of purple blobs, and it does not look like an aspect bug, it
    # looks like badly drawn wings.
    def tall(value: float) -> float:
        return value / aspect

    return (
        Shape("ellipse", fill=BUTTERFLY_WING, centre=(x - size * 0.26, y - tall(size * 0.16)),
              size=(size * 0.50, tall(size * 0.66)), rotation=tilt - 26),
        Shape("ellipse", fill=BUTTERFLY_WING, centre=(x + size * 0.26, y - tall(size * 0.16)),
              size=(size * 0.50, tall(size * 0.66)), rotation=-tilt + 26),
        Shape("ellipse", fill=BUTTERFLY_EDGE, centre=(x - size * 0.20, y + tall(size * 0.24)),
              size=(size * 0.34, tall(size * 0.38)), rotation=tilt - 12),
        Shape("ellipse", fill=BUTTERFLY_EDGE, centre=(x + size * 0.20, y + tall(size * 0.24)),
              size=(size * 0.34, tall(size * 0.38)), rotation=-tilt + 12),
        Shape("ellipse", fill=INK, centre=(x, y + tall(size * 0.04)),
              size=(size * 0.07, tall(size * 0.56)), rotation=tilt),
    )


def _star(centre: UnitPoint, size: float, colour: RGBA) -> Shape:
    """A five-pointed star, generated rather than typed.

    Ten points written by hand is ten chances to put one at the wrong radius,
    and a star with one short arm reads as a mistake rather than as a style.
    """
    x, y = centre
    points: list[UnitPoint] = []
    for index in range(10):
        angle = math.radians(-90 + index * 36)
        reach = size / 2 if index % 2 == 0 else size / 5
        points.append((x + math.cos(angle) * reach, y + math.sin(angle) * reach))
    return Shape("polygon", fill=colour, points=tuple(points))


_BUTTERFLIES = Overlay(
    id="butterflies",
    label="Butterflies",
    group=REACTIONS,
    anchor="forehead",
    width_in_faces=1.5,
    aspect=0.72,
    offset=(0.0, -0.10),
    note="Drifts around the head. Reads as dreamy rather than as a costume.",
    shapes=(
        *_butterfly((0.15, 0.60), 0.26, 14, 0.72),
        *_butterfly((0.50, 0.28), 0.32, -6, 0.72),
        *_butterfly((0.85, 0.56), 0.24, -18, 0.72),
        *_butterfly((0.33, 0.90), 0.16, 22, 0.72),
    ),
)

_STAR_FACE = Overlay(
    id="star_face",
    label="Face stars",
    group=FEATURES,
    anchor="eyes",
    width_in_faces=1.30,
    aspect=0.62,
    offset=(0.0, 0.10),
    note="Scattered across the cheekbones, not over the eyes.",
    shapes=(
        _star((0.15, 0.30), 0.30, YELLOW),
        _star((0.33, 0.66), 0.20, GOLD),
        _star((0.05, 0.72), 0.16, WHITE),
        _star((0.85, 0.30), 0.30, YELLOW),
        _star((0.67, 0.66), 0.20, GOLD),
        _star((0.95, 0.72), 0.16, WHITE),
    ),
)

_BUNNY_EARS = Overlay(
    id="bunny_ears",
    label="Bunny ears",
    group=HEADWEAR,
    anchor="forehead",
    width_in_faces=1.05,
    aspect=1.05,
    offset=(0.0, -0.44),
    shapes=(
        *_pair(Shape("ellipse", fill=BONE, centre=(0.30, 0.42),
                     size=(0.26, 0.82), rotation=-9)),
        *_pair(Shape("ellipse", fill=PINK, centre=(0.31, 0.44),
                     size=(0.13, 0.60), rotation=-9)),
    ),
)

_FLOWER_CROWN = Overlay(
    id="flower_crown",
    label="Flower crown",
    group=HEADWEAR,
    anchor="forehead",
    width_in_faces=1.34,
    # Half as tall again as the first attempt. At 0.40 the blooms were flatter
    # than they were wide and read as a smear of colour on a green line; a
    # flower needs room above the band it sits on.
    aspect=0.60,
    offset=(0.0, -0.10),
    note="A band of blooms rather than a full cover — the face stays visible.",
    shapes=(
        # A thin band of leaf behind them rather than a bed under them: at any
        # weight it competes with the blooms, which are the object.
        Shape("ellipse", fill=LEAF, centre=(0.50, 0.74), size=(0.94, 0.10)),
        *[
            shape
            for index, (x, size, colour) in enumerate((
                # Four, not five, and spaced by what one actually occupies: a
                # bloom reaches 1.34 times its own `size` across once its
                # petals are counted, so five of them at any readable size add
                # up to more than the sprite is wide and merge into one mass.
                # No white bloom. It reads perfectly on a face and disappears
                # on the picker's own light background, and the tile is where
                # somebody decides whether to use it at all.
                (0.13, 0.17, SOFT_PINK), (0.38, 0.19, BUTTERFLY_WING),
                (0.63, 0.19, YELLOW), (0.87, 0.17, SOFT_PINK),
            ))
            for shape in (
                # Five petals around a centre, which is what makes it a flower
                # rather than a dot: a single circle reads as a bead.
                *(
                    Shape(
                        "ellipse", fill=colour,
                        centre=(
                            x + math.cos(math.radians(-90 + petal * 72)) * size * 0.34,
                            0.44 + math.sin(math.radians(-90 + petal * 72))
                            * size * 0.34 / 0.60,
                        ),
                        # Divided by the aspect, not multiplied by it: the sprite
                        # is 0.60 as tall as it is wide, so a petal given equal
                        # numbers comes out flat. Multiplying squashed them into
                        # each other and five flowers became one smear.
                        size=(size * 0.66, size * 0.66 / 0.60),
                    )
                    for petal in range(5)
                ),
                Shape("ellipse", fill=GOLD, centre=(x, 0.44),
                      size=(size * 0.30, size * 0.30 / 0.60)),
            )
        ],
    ),
)

_ANIME_BLUSH = Overlay(
    id="anime_blush",
    label="Anime blush",
    group=FEATURES,
    anchor="nose",
    width_in_faces=1.10,
    aspect=0.36,
    offset=(0.0, 0.06),
    note="Cheek blush with the drawn-on lines, the way an edit marks a reaction.",
    shapes=(
        # The soft patch first and much larger, with three short strokes drawn
        # over it. The first version had full-height lines on a small patch, so
        # it read as a barcode rather than as a blush.
        *_pair(Shape("ellipse", fill=BLUSH, centre=(0.19, 0.50), size=(0.34, 0.70))),
        *_pair(Shape("line", stroke=SOFT_PINK, stroke_width=0.016,
                     points=((0.11, 0.36), (0.15, 0.64)))),
        *_pair(Shape("line", stroke=SOFT_PINK, stroke_width=0.016,
                     points=((0.19, 0.32), (0.23, 0.68)))),
        *_pair(Shape("line", stroke=SOFT_PINK, stroke_width=0.016,
                     points=((0.27, 0.36), (0.31, 0.64)))),
    ),
)

_SWEAT_DROP = Overlay(
    id="sweat_drop",
    label="Sweat drop",
    group=REACTIONS,
    anchor="forehead",
    width_in_faces=0.44,
    aspect=1.30,
    offset=(0.46, 0.02),
    follows_roll=False,
    note="The anime beat for awkwardness. Sits off to one side of the head.",
    shapes=(
        # A round belly with a point drawn on top of it, rather than a polygon
        # trying to be both - the polygon version came out a kite.
        Shape("ellipse", fill=SWEAT, centre=(0.50, 0.68), size=(0.78, 0.60)),
        Shape("polygon", fill=SWEAT, points=(
            (0.50, 0.04), (0.79, 0.72), (0.21, 0.72),
        )),
        Shape("ellipse", fill=WHITE, centre=(0.34, 0.66), size=(0.16, 0.20)),
    ),
)

_SWEATBAND = Overlay(
    id="sweatband",
    label="Sweatband",
    group=HEADWEAR,
    anchor="forehead",
    width_in_faces=1.24,
    aspect=0.30,
    offset=(0.0, 0.06),
    note="For gym and training clips — sits on the brow rather than the crown.",
    shapes=(
        # Full height, and the band itself carries the colour. The first version
        # was near-white terry with two thin stripes on it, which on a light
        # frame was three red lines floating over nothing.
        Shape("rect", fill=SPORT_STRIPE, centre=(0.5, 0.5), size=(1.0, 1.0), radius=0.06),
        Shape("rect", fill=TERRY, centre=(0.5, 0.5), size=(1.0, 0.44)),
        Shape("rect", fill=INK, centre=(0.5, 0.5), size=(0.16, 0.30), radius=0.03),
    ),
)

_SANTA_HAT = Overlay(
    id="santa_hat",
    label="Santa hat",
    group=HEADWEAR,
    anchor="forehead",
    width_in_faces=1.22,
    aspect=0.86,
    offset=(0.06, -0.26),
    shapes=(
        Shape("polygon", fill=SANTA_RED, points=(
            (0.06, 0.74), (0.44, 0.10), (0.86, 0.30), (0.62, 0.78),
        )),
        Shape("rect", fill=BONE, centre=(0.36, 0.82), size=(0.70, 0.24), radius=0.10),
        Shape("ellipse", fill=BONE, centre=(0.88, 0.30), size=(0.24, 0.24)),
    ),
)

_FIRE = Overlay(
    id="fire",
    label="Fire",
    group=REACTIONS,
    anchor="forehead",
    width_in_faces=0.70,
    aspect=1.10,
    offset=(0.0, -0.18),
    follows_roll=False,
    note="For a clip somebody is calling hot. Stays upright however the head leans.",
    shapes=(
        Shape("polygon", fill=FLAME, points=(
            (0.50, 0.02), (0.84, 0.44), (0.88, 0.74), (0.50, 0.98),
            (0.12, 0.74), (0.16, 0.44),
        )),
        Shape("polygon", fill=FLAME_CORE, points=(
            (0.50, 0.34), (0.70, 0.62), (0.50, 0.90), (0.30, 0.62),
        )),
    ),
)

_MUSIC_NOTES = Overlay(
    id="music_notes",
    label="Music notes",
    group=REACTIONS,
    anchor="forehead",
    width_in_faces=1.15,
    aspect=0.68,
    offset=(0.0, -0.08),
    follows_roll=False,
    note="For dance and sound clips. Floats beside the head rather than on it.",
    shapes=(
        Shape("ellipse", fill=INK, centre=(0.14, 0.74), size=(0.22, 0.17), rotation=-18),
        Shape("rect", fill=INK, centre=(0.24, 0.44), size=(0.045, 0.58)),
        Shape("polygon", fill=INK, points=(
            (0.24, 0.16), (0.44, 0.24), (0.44, 0.38), (0.24, 0.30),
        )),
        Shape("ellipse", fill=INK, centre=(0.66, 0.86), size=(0.19, 0.15), rotation=-18),
        Shape("rect", fill=INK, centre=(0.745, 0.60), size=(0.04, 0.50)),
        Shape("ellipse", fill=INK, centre=(0.90, 0.62), size=(0.19, 0.15), rotation=-18),
        Shape("rect", fill=INK, centre=(0.985, 0.36), size=(0.04, 0.50)),
        Shape("rect", fill=INK, centre=(0.865, 0.13), size=(0.28, 0.09)),
    ),
)


def _bucket_hat() -> Any:
    """A shallow crown and a brim that slopes down, which is the whole shape."""
    crown = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(BUCKET, sides=28), 0.62, 0.40, 0.62), y=0.22
        ),
        BUCKET,
    )
    # A short wide cylinder tipped forward, not an inverted cone: the cone came
    # to a point below the head and the whole hat read as a funnel.
    brim = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(BUCKET_SHADE, sides=30), 0.98, 0.13, 0.98),
            y=-0.08,
        ),
        BUCKET_SHADE,
    )
    return brim + crown


def _beanie() -> Any:
    """A dome with a folded cuff round the bottom."""
    dome = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.sphere(KNIT, segments=24, rings=14), 0.84, 0.70, 0.84),
            y=0.06,
        ),
        KNIT,
    )
    cuff = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(KNIT_CUFF, sides=28), 0.90, 0.26, 0.90),
            y=-0.20,
        ),
        KNIT_CUFF,
    )
    bobble = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.sphere(KNIT_CUFF, segments=14, rings=9), 0.20, 0.20, 0.20),
            y=0.39,
        ),
        KNIT_CUFF,
    )
    return dome + cuff + bobble


def _headphones() -> Any:
    """A band over the head and a cup on each side.

    The band is the reason this one wants depth: seen from the front it is an
    arc, and seen from the side it is the whole object.
    """
    band = meshes.Mesh()
    for step in range(15):
        angle = math.radians(-72 + step * (144 / 14))
        band = band + meshes.painted(
            meshes.moved(
                meshes.scaled(meshes.box(BAND_STEEL), 0.075, 0.075, 0.10),
                x=math.sin(angle) * 0.40,
                y=math.cos(angle) * 0.40 - 0.02,
            ),
            BAND_STEEL,
        )
    cups = meshes.Mesh()
    for side in (-1.0, 1.0):
        cup = meshes.painted(
            meshes.moved(
                meshes.turned(
                    meshes.scaled(meshes.cylinder(EARCUP, sides=20), 0.30, 0.16, 0.34),
                    z=90.0,
                ),
                x=side * 0.42, y=-0.16,
            ),
            EARCUP,
        )
        cups = cups + cup
    return band + cups


def _visor() -> Any:
    """A band and a peak, and nothing on top - which is the point of a visor."""
    band = meshes.painted(
        meshes.moved(
            meshes.scaled(
                meshes.cylinder(VISOR_BAND, sides=26, caps=False), 0.80, 0.26, 0.80
            ),
            y=0.14,
        ),
        VISOR_BAND,
    )
    # Wider and flatter than the cap's, and further forward: a visor is mostly
    # peak, and at the cap's proportions it read as a cap with the top cut off.
    peak = meshes.painted(
        meshes.moved(
            meshes.turned(
                meshes.scaled(meshes.cylinder(VISOR_PEAK, sides=26), 0.96, 0.06, 1.0),
                x=12.0,
            ),
            y=-0.02, z=0.44,
        ),
        VISOR_PEAK,
    )
    return band + peak


def _cowboy_hat() -> Any:
    """A crown and a wide brim that lifts at the sides."""
    crown = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(SUEDE, sides=26), 0.52, 0.40, 0.58), y=0.22
        ),
        SUEDE,
    )
    brim = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(SUEDE, sides=32), 0.98, 0.05, 0.74), y=-0.02
        ),
        SUEDE,
    )
    band = meshes.painted(
        meshes.moved(
            meshes.scaled(meshes.cylinder(SUEDE_BAND, sides=26), 0.54, 0.11, 0.60), y=0.06
        ),
        SUEDE_BAND,
    )
    return brim + crown + band


BUCKET: RGBA = (86, 116, 92, 255)
BUCKET_SHADE: RGBA = (70, 96, 76, 255)
KNIT: RGBA = (176, 84, 96, 255)
KNIT_CUFF: RGBA = (146, 64, 78, 255)
BAND_STEEL: RGBA = (54, 58, 68, 255)
EARCUP: RGBA = (34, 36, 44, 255)
VISOR_BAND: RGBA = (240, 242, 246, 255)
VISOR_PEAK: RGBA = (36, 122, 200, 255)
SUEDE: RGBA = (168, 122, 68, 255)
SUEDE_BAND: RGBA = (92, 66, 40, 255)


_BUCKET_HAT_3D = Overlay(
    id="bucket_hat_3d",
    label="Bucket hat",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.34,
    aspect=1.0,
    offset=(0.0, 0.16),
    note="Turns with the head — the brim slopes all the way round.",
    mesh=_bucket_hat(),
)

_BEANIE_3D = Overlay(
    id="beanie_3d",
    label="Beanie",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.24,
    aspect=1.0,
    offset=(0.0, 0.18),
    note="Turns with the head, cuff and all.",
    mesh=_beanie(),
)

_HEADPHONES_3D = Overlay(
    id="headphones_3d",
    label="Headphones",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.42,
    aspect=1.0,
    offset=(0.0, 0.30),
    note="The band is an arc from the front and the whole object from the side.",
    mesh=_headphones(),
)

_VISOR_3D = Overlay(
    id="visor_3d",
    label="Sun visor",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.28,
    aspect=1.0,
    offset=(0.0, 0.08),
    note="For training clips. Open on top, so the peak swings and the crown does not.",
    mesh=_visor(),
)

_COWBOY_HAT_3D = Overlay(
    id="cowboy_hat_3d",
    label="Cowboy hat",
    group=SOLID,
    anchor="forehead",
    width_in_faces=1.46,
    aspect=1.0,
    offset=(0.0, 0.22),
    note="Turns with the head, and the wide brim foreshortens as it does.",
    mesh=_cowboy_hat(),
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
    _CAP_3D,
    _TOP_HAT_3D,
    _PARTY_CONE_3D,
    _CROWN_3D,
    _SUNGLASSES_3D,
    _BUCKET_HAT_3D,
    _BEANIE_3D,
    _HEADPHONES_3D,
    _VISOR_3D,
    _COWBOY_HAT_3D,
    _BUTTERFLIES,
    _STAR_FACE,
    _BUNNY_EARS,
    _FLOWER_CROWN,
    _ANIME_BLUSH,
    _SWEAT_DROP,
    _SWEATBAND,
    _SANTA_HAT,
    _FIRE,
    _MUSIC_NOTES,
    _PIXEL_MASK,
    _ALIEN,
    _FLOWER_FACE,
    _CLOUD_FACE,
    _BABY_CHIBI,
    _KITSUNE,
    _PANDA,
    _BOBA,
    _CRT_HEAD,
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
# Second. Hiding a face still leads, because that is the job somebody most
# often opens this to do and the ordering is a privacy decision rather than a
# fashion; the solid objects sit directly behind it, where the newest thing in
# the pack is seen without displacing the reason the pack exists.
GROUP_ORDER = (COVER, SOLID, FEATURES, HEADWEAR, REACTIONS, CREATOR_UI)


#: What kind of clip each object suits, for suggesting one from what a video
#: says about itself.
#:
#: Kept in one table rather than spread across forty declarations because it is
#: *editorial* rather than structural: the geometry of a crown is settled and
#: the words a crown belongs on are a judgement somebody will want to revise
#: after watching the suggestions be wrong. One block is what makes that a
#: reviewable diff. `_apply_keywords` puts them onto the objects, and a test
#: fails if an object is added without an entry.
#:
#: Every entry carries Chinese as well as English, and that is not politeness.
#: This workspace's library is 2,540 Douyin clips; 2,434 of them have captions
#: and all of those captions are Chinese. An English-only vocabulary would
#: score zero against the entire library it is meant to serve.
OBJECT_KEYWORDS: dict[str, tuple[str, ...]] = {
    # --- covering a face -----------------------------------------------------
    "censor_block": (
        "anonymous", "privacy", "hide", "redact", "identity", "confidential",
        "匿名", "隐私", "遮挡", "打码", "保护",
    ),
    "pixel_mask": (
        "anonymous", "privacy", "censored", "pixelate", "hide", "leak",
        "匿名", "隐私", "马赛克", "打码", "遮脸",
    ),
    "smiley": (
        "happy", "friendly", "smile", "cheerful", "fun", "positive", "cute",
        "开心", "笑", "可爱", "搞笑", "快乐",
    ),
    "robot": (
        "robot", "tech", "ai", "gadget", "future", "machine", "automation",
        "机器人", "科技", "智能", "未来", "数码",
    ),
    "skull": (
        "skull", "halloween", "spooky", "dark", "metal", "edgy", "horror",
        "骷髅", "万圣节", "恐怖", "暗黑", "吓人",
    ),
    "ghost": (
        "ghost", "halloween", "spooky", "scary", "boo", "haunted",
        "鬼", "万圣节", "吓人", "灵异", "恐怖",
    ),
    "alien": (
        "alien", "ufo", "space", "weird", "sci-fi", "extraterrestrial",
        "外星人", "宇宙", "科幻", "飞碟", "奇怪",
    ),
    "flower_face": (
        "flower", "spring", "bloom", "floral", "garden", "fairy", "pretty",
        "花", "春天", "花朵", "仙女", "唯美", "变装",
    ),
    "cloud_face": (
        "cloud", "dream", "sky", "soft", "calm", "daydream", "weather",
        "云", "梦幻", "天空", "治愈", "温柔",
    ),
    "baby_chibi": (
        "cute", "baby", "chibi", "kawaii", "adorable", "child", "sweet",
        "可爱", "萌", "宝宝", "萌娃", "软萌",
    ),
    "kitsune": (
        "fox", "kitsune", "anime", "japan", "mask", "festival", "mythical",
        "狐狸", "面具", "动漫", "日系", "国风", "古风",
    ),
    "panda": (
        "panda", "cute", "animal", "china", "bamboo", "zoo",
        "熊猫", "可爱", "动物", "萌", "国宝",
    ),
    "boba": (
        "boba", "bubble tea", "drink", "milk tea", "cafe", "snack", "food",
        "奶茶", "喝的", "饮品", "美食", "探店",
    ),
    "crt_head": (
        "retro", "tv", "glitch", "vaporwave", "vintage", "static", "screen",
        "复古", "电视", "故障", "怀旧", "old school",
    ),
    # --- eyes and mouth ------------------------------------------------------
    "censor_bar": (
        "anonymous", "privacy", "eyes", "hide", "identity", "redact",
        "匿名", "隐私", "遮眼", "打码",
    ),
    "sunglasses": (
        "cool", "summer", "sunglasses", "beach", "confident", "swagger",
        "墨镜", "夏天", "酷", "帅", "御姐", "变装",
    ),
    "face_mask": (
        "mask", "health", "hospital", "clinic", "hygiene", "sick", "doctor",
        "口罩", "医生", "健康", "医院", "防护",
    ),
    "moustache": (
        "moustache", "funny", "disguise", "gentleman", "silly", "vintage",
        "胡子", "搞笑", "伪装", "绅士",
    ),
    "heart_eyes": (
        "love", "crush", "romance", "adore", "valentine", "swoon", "date",
        "爱心", "喜欢", "恋爱", "心动", "告白", "情人节",
    ),
    "star_glasses": (
        "party", "star", "celebrate", "disco", "fun", "shine", "festival",
        "星星", "派对", "闪耀", "庆祝", "开心",
    ),
    "cyber_visor": (
        "cyber", "tech", "gaming", "futuristic", "neon", "esports", "vr",
        "赛博", "科技", "游戏", "未来", "电竞",
    ),
    "dog_nose": (
        "dog", "puppy", "pet", "animal", "cute", "woof",
        "狗", "宠物", "可爱", "萌宠", "小狗",
    ),
    # --- on the head ---------------------------------------------------------
    "cat_ears": (
        "cat", "cute", "kitten", "pet", "kawaii", "neko", "anime",
        "猫耳", "可爱", "萌", "猫", "二次元", "变装",
    ),
    "crown": (
        "queen", "king", "royal", "winner", "birthday", "princess", "best",
        "皇冠", "女王", "公主", "第一", "生日", "御姐",
    ),
    "party_hat": (
        "birthday", "party", "celebrate", "anniversary", "congratulations",
        "生日", "派对", "庆祝", "纪念日", "开心",
    ),
    "halo": (
        "angel", "innocent", "pure", "sweet", "good", "heaven", "dreamy",
        "天使", "纯洁", "可爱", "梦幻", "圣洁",
    ),
    "devil_horns": (
        "devil", "naughty", "mischief", "evil", "halloween", "sassy", "bad",
        "恶魔", "调皮", "坏", "万圣节", "腹黑",
    ),
    "graduation_cap": (
        "graduation", "school", "student", "university", "exam", "learn",
        "毕业", "学生", "大学", "考试", "学习",
    ),
    "headphones": (
        "music", "dj", "song", "beat", "listen", "podcast", "audio", "dance",
        "音乐", "耳机", "歌", "跳舞", "节奏", "听歌",
    ),
    # --- reactions and accents -----------------------------------------------
    "heart_bubble": (
        "love", "like", "romance", "sweet", "crush", "affection", "thanks",
        "爱心", "喜欢", "心动", "甜", "宠粉",
    ),
    "lightning": (
        "energy", "power", "fast", "shock", "strong", "workout", "intense",
        "闪电", "力量", "爆发", "健身", "速度", "冲",
    ),
    "sparkles": (
        "glow", "shine", "magic", "pretty", "glam", "beauty", "transform",
        "闪", "发光", "变装", "美", "仙", "魔法",
    ),
    # --- creator UI ----------------------------------------------------------
    "live_badge": (
        "live", "stream", "broadcast", "online", "now", "streaming",
        "直播", "开播", "在线", "现场",
    ),
    "focus_frame": (
        "focus", "camera", "shot", "framing", "photography", "record",
        "对焦", "镜头", "摄影", "拍摄", "女摄",
    ),
    "comment_bubble": (
        "comment", "reply", "chat", "message", "question", "answer", "talk",
        "评论", "回复", "留言", "聊天", "提问",
    ),
    "tap_cursor": (
        "tap", "click", "tutorial", "how to", "guide", "demo", "step",
        "点击", "教程", "步骤", "演示", "操作",
    ),
    # --- objects with depth --------------------------------------------------
    "cap_3d": (
        "cap", "sport", "street", "casual", "gym", "athletic", "hat", "workout",
        "帽子", "运动", "健身", "街头", "潮流", "腹肌",
    ),
    "top_hat_3d": (
        "formal", "magic", "gentleman", "classy", "vintage", "magician", "show",
        "礼帽", "绅士", "魔术", "复古", "正式",
    ),
    "party_cone_3d": (
        "birthday", "party", "celebrate", "anniversary", "congratulations",
        "生日", "派对", "庆祝", "纪念日",
    ),
    "crown_3d": (
        "queen", "king", "royal", "winner", "champion", "best", "luxury",
        "皇冠", "女王", "冠军", "第一", "豪华", "御姐",
    ),
    "sunglasses_3d": (
        "cool", "summer", "sunglasses", "confident", "swagger", "style", "drip",
        "墨镜", "酷", "帅", "夏天", "御姐", "变装",
    ),
    # --- the trend batch -----------------------------------------------------
    "butterflies": (
        "butterfly", "dreamy", "fairy", "aesthetic", "spring", "pretty", "soft",
        "蝴蝶", "梦幻", "唯美", "仙", "变装", "氛围感",
    ),
    "star_face": (
        "star", "sparkle", "glam", "makeup", "pretty", "shine", "y2k",
        "星星", "闪", "妆", "美", "变装", "氛围感",
    ),
    "bunny_ears": (
        "bunny", "rabbit", "cute", "easter", "soft", "sweet", "kawaii",
        "兔子", "兔耳", "可爱", "萌", "变装",
    ),
    "flower_crown": (
        "flower", "fairy", "spring", "wedding", "festival", "bloom", "garden",
        "花环", "仙女", "春天", "花", "变装", "古风",
    ),
    "anime_blush": (
        "anime", "blush", "shy", "cute", "reaction", "manga", "flustered",
        "害羞", "脸红", "二次元", "动漫", "可爱", "萌",
    ),
    "sweat_drop": (
        "awkward", "nervous", "oops", "anime", "reaction", "embarrassed",
        "尴尬", "无语", "汗", "二次元", "紧张",
    ),
    "sweatband": (
        "gym", "workout", "training", "fitness", "run", "sport", "sweat", "abs",
        "健身", "运动", "训练", "腹肌", "马甲线", "跑步", "撸铁",
    ),
    "santa_hat": (
        "christmas", "santa", "winter", "holiday", "festive", "december", "gift",
        "圣诞", "圣诞节", "冬天", "节日", "过节",
    ),
    "fire": (
        "fire", "hot", "amazing", "lit", "impressive", "banger", "strong",
        "火", "厉害", "牛", "绝了", "燃", "热",
    ),
    "music_notes": (
        "music", "dance", "song", "sing", "beat", "rhythm", "sound", "audio",
        "音乐", "跳舞", "唱歌", "歌", "节奏", "舞蹈", "卡点",
    ),
    "bucket_hat_3d": (
        "bucket hat", "street", "summer", "casual", "festival", "fishing", "y2k",
        "渔夫帽", "街头", "夏天", "潮流", "休闲",
    ),
    "beanie_3d": (
        "beanie", "winter", "cold", "cosy", "knit", "autumn", "street",
        "毛线帽", "冬天", "保暖", "针织", "秋天", "潮流",
    ),
    "headphones_3d": (
        "music", "dj", "listen", "podcast", "gaming", "audio", "beat", "studio",
        "耳机", "音乐", "听歌", "游戏", "电台", "节奏",
    ),
    "visor_3d": (
        "gym", "training", "run", "tennis", "golf", "sport", "summer", "fitness",
        "运动", "健身", "训练", "跑步", "网球", "遮阳",
    ),
    "cowboy_hat_3d": (
        "cowboy", "western", "country", "ranch", "rodeo", "americana", "boots",
        "牛仔", "西部", "乡村", "牛仔帽",
    ),
}


def _with_keywords(objects: tuple[Overlay, ...]) -> tuple[Overlay, ...]:
    """The pack, each object carrying the words a clip it suits would use.

    Joined here rather than written into forty declarations, so the editorial
    judgement lives in one block somebody can read top to bottom and revise
    after watching the suggestions be wrong.
    """
    return tuple(
        replace(item, keywords=OBJECT_KEYWORDS.get(item.id, ()))
        for item in objects
    )


#: Rebound once the table above exists. The declarations stay about geometry
#: and the vocabulary stays in one block, and nothing downstream has to join
#: the two - by the time anything reads `BUILT_IN`, every object carries both.
BUILT_IN = _with_keywords(BUILT_IN)


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

#: The largest picture accepted through the dialog. Well above any real sticker
#: and far below what would fill a disk, and separate from `MAX_DROP_IN_SIDE`
#: because that one bounds a file already on the machine while this one bounds
#: what a request may carry.
MAX_IMPORT_BYTES = 12 * 1024 * 1024

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


class OverlayImportError(ValueError):
    """A picture that cannot become an overlay, with the reason to show."""


def _available_id(wanted: str) -> str:
    """A free id shaped like an id, derived from what the file was called.

    The stem becomes the object's id and reaches a URL and a filesystem path,
    so it is rebuilt from the allowed characters rather than sanitised in
    place - a name that is not this shape is replaced, never repaired into
    something that merely looks safe.
    """
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", wanted.strip().lower()).strip("-_")
    cleaned = re.sub(r"-{2,}", "-", cleaned)[:40].strip("-_")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"object-{cleaned}".strip("-_")[:40]
    taken = {item.id for item in catalogue()}
    if cleaned not in taken and not (OVERLAY_ROOT / f"{cleaned}.png").exists():
        return cleaned
    # A second picture called "logo" is a normal thing to add, and refusing it
    # would send somebody back to a file manager to rename a file.
    for suffix in range(2, 100):
        candidate = f"{cleaned[:36]}-{suffix}"
        if candidate not in taken and not (OVERLAY_ROOT / f"{candidate}.png").exists():
            return candidate
    raise OverlayImportError("Too many objects share that name already.")


def import_overlay(data: bytes, name: str) -> dict[str, Any]:
    """Add a picture to the drop-in folder, and hand back the option for it.

    The folder is the extension point and it works, but it is a folder: the
    picker used to name a path and leave somebody to open a file manager, copy
    a file in, and come back. This is that, done from the dialog they are
    already looking at.

    It stays a copy into the same folder rather than a new kind of storage. A
    recipe refers to an object by id and is re-run months later, and everything
    that reads the folder - the catalogue, the rejection report, the sprite
    endpoint - keeps working without being told about this.
    """
    if not data:
        raise OverlayImportError("That file is empty.")
    if len(data) > MAX_IMPORT_BYTES:
        raise OverlayImportError(
            f"That picture is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
        )
    if not data.startswith(PNG_SIGNATURE):
        # PNG only, because an overlay is pasted with an alpha channel and the
        # formats people reach for instead - JPEG above all - have none. A
        # JPEG accepted here would arrive as an opaque rectangle over a face,
        # which looks like the effect being broken rather than like a choice.
        raise OverlayImportError(
            "An object has to be a PNG with a transparent background. A JPEG "
            "has no transparency, so it would cover the face with a rectangle."
        )
    OVERLAY_ROOT.mkdir(parents=True, exist_ok=True)
    stem = _available_id(Path(name).stem or "object")
    destination = OVERLAY_ROOT / f"{stem}.png"
    destination.write_bytes(data)
    # Validated after it is written, because the reasons a file is refused are
    # already written down for the folder and asking them twice in two places
    # is how the two answers start to differ. A file that fails is removed
    # rather than left to appear in the rejection list.
    refusal = _rejection(destination)
    if refusal:
        destination.unlink(missing_ok=True)
        raise OverlayImportError(refusal)
    added = _drop_in(destination)
    if added is None:
        destination.unlink(missing_ok=True)
        raise OverlayImportError("That picture could not be read as an object.")
    return {
        "value": added.id,
        "label": added.label,
        "group": added.group,
        "group_id": GROUP_IDS.get(added.group),
        "occludes": added.occludes,
        "dimensional": False,
        "note": added.note,
        "preview": f"face-overlay/objects/{added.id}/sprite",
        "custom": True,
    }


def folder() -> dict[str, Any]:
    """Where an operator adds their own objects, and what did not load.

    Handed to the picker through the effect's own declaration, so the gallery
    can name the folder and explain a rejected file without knowing that it is
    showing overlays rather than something else.
    """
    return {
        "directory": str(OVERLAY_ROOT),
        "skipped": rejected_drop_ins(),
        # Where a picture can be sent to become an object, relative to the
        # media-library base. Declared so the picker can offer both ways in
        # without knowing which effect it is showing - the same contract the
        # face-swap folder already uses for its portraits.
        "import_from_library": "face-overlay/objects",
        "upload": "face-overlay/objects/upload",
        "accepts": [".png"],
    }


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
            # Said on the tile. Whether a prop turns with the head is the
            # difference somebody is choosing between two hats for, and it is
            # invisible in a thumbnail of a face looking straight ahead.
            "dimensional": item.mesh is not None,
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


def _shape_bounds(shape: Shape, width: int, height: int) -> tuple[int, int, int, int] | None:
    """The rectangle of the canvas a shape can possibly touch, or None.

    An eye is a fiftieth of a face and used to cost the same as one, because
    every shape was premultiplied and blended across the whole canvas. This says
    where it actually lands so that work happens over a few thousand pixels
    instead of half a million.

    Deliberately generous. It pads for the stroke straddling the outline and for
    OpenCV's own rounding rather than reproducing that arithmetic exactly: a box
    a pixel too large costs nothing measurable, and one a pixel too small
    silently clips an edge off the sticker.
    """
    pad = max(1, int(round(shape.stroke_width * width))) + 2

    if shape.kind == "ellipse":
        centre_x, centre_y = shape.centre[0] * width, shape.centre[1] * height
        # Rotation can swing either axis into either direction, so the longer
        # one bounds both. The extra pixel covers `_draw` refusing a zero axis.
        reach = max(shape.size[0] * width, shape.size[1] * height) / 2 + 1
        left, top, right, bottom = (
            centre_x - reach, centre_y - reach, centre_x + reach, centre_y + reach,
        )
    else:
        points = (
            _rounded_rect_points(shape.centre, shape.size, shape.radius)
            if shape.kind == "rect"
            else list(shape.points)
        )
        # The same "nothing to draw" case `_draw` returns on.
        if len(points) < 2:
            return None
        horizontal = [point[0] * width for point in points]
        vertical = [point[1] * height for point in points]
        left, top = min(horizontal), min(vertical)
        right, bottom = max(horizontal), max(vertical)

    box = (
        max(0, math.floor(left) - pad),
        max(0, math.floor(top) - pad),
        min(width, math.ceil(right) + pad),
        min(height, math.ceil(bottom) + pad),
    )
    # Entirely off the canvas.
    if box[0] >= box[2] or box[1] >= box[3]:
        return None
    return box


def premultiply(np: Any, image: Any) -> Any:
    """A straight-alpha BGRA image as premultiplied float, 0 to 1.

    Every resample and every blend below happens in this space. Averaging two
    straight-alpha pixels is simply wrong — half of an opaque white next to half
    of a transparent pixel is not grey — and the artefact it produces is a dark
    halo that looks like a badly cut-out sticker, which is exactly what this
    feature must not look like.

    Written in place on the one array the conversion already had to allocate.
    Stated the obvious way — divide, slice, multiply, concatenate — it builds
    four full-size float arrays to produce one, and this runs once per shape per
    sprite, which made it the single most expensive thing in the gallery.
    """
    values = image.astype(np.float32)
    values /= 255.0
    values[:, :, :3] *= values[:, :, 3:4]
    return values


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


#: How a solid object is turned for a thumbnail, when there is no face to take
#: an angle from. Three-quarters rather than square on: it is the view that
#: says "this one has depth" without a caption, and a gallery of solid objects
#: drawn face-on is a gallery that looks exactly like the flat one.
GALLERY_YAW = -28.0
GALLERY_PITCH = 10.0


def render_sprite(
    cv2: Any,
    np: Any,
    overlay: Overlay,
    width: int,
    pose: tuple[float, float, float] | None = None,
) -> Any:
    """The overlay as a straight-alpha BGRA image of the given width.

    A drop-in is read from its PNG and resized; a flat built-in is drawn; a
    solid one is rasterised at `pose`, which is the yaw, pitch and roll of the
    head it is going on. All three come back in the same form, so nothing
    downstream has to know which it got.

    `pose` is ignored by everything without a mesh, and a solid object with no
    pose is drawn at the gallery's three-quarter view - there is no face to
    take an angle from when the picker is showing a catalogue.
    """
    width = max(8, min(int(width), MAX_SPRITE_WIDTH))
    height = max(8, int(round(width * overlay.aspect)))

    if overlay.image is not None:
        return _read_png(cv2, np, overlay.image, width, height)

    if overlay.mesh is not None:
        yaw, pitch, roll = pose if pose is not None else (GALLERY_YAW, GALLERY_PITCH, 0.0)
        return meshes.render_sprite(
            cv2, np, overlay.mesh, width, height, yaw=yaw, pitch=pitch, roll=roll
        )

    factor = max(1, min(SUPERSAMPLE, MAX_DRAW_WIDTH // width))
    big_width, big_height = width * factor, height * factor
    canvas = np.zeros((big_height, big_width, 4), dtype=np.float32)
    # One scratch layer for all of them rather than one each, and the blend runs
    # only over the rectangle the shape reaches. Cleared whole between shapes
    # rather than only within that rectangle: if a bound were ever short of what
    # was drawn, the stray pixel would otherwise turn up inside a later shape's
    # rectangle wearing the wrong colour, and a memset is far too cheap to trade
    # that risk away for.
    layer = np.zeros((big_height, big_width, 4), dtype=np.uint8)
    for shape in overlay.shapes:
        box = _shape_bounds(shape, big_width, big_height)
        if box is None:
            continue
        left, top, right, bottom = box
        _draw(cv2, np, layer, shape, big_width, big_height)
        _over(
            canvas[top:bottom, left:right],
            premultiply(np, layer[top:bottom, left:right]),
        )
        layer.fill(0)
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


#: Encoded built-in sprites, keyed by id and width. A built-in is declared in
#: this file, so its bytes at a given width cannot change while the process
#: runs, and the gallery asks for the whole pack every time it opens — once per
#: operator per page load, all thirty-six at once. A drop-in is never kept: it
#: is a file on somebody's disk that they may be editing, and handing them a
#: stale copy of their own work is the one failure this feature must not have.
_SPRITE_CACHE: dict[tuple[str, int], bytes] = {}
#: Sprites are a few kilobytes each and only a handful of widths are ever asked
#: for, so this is a guard against a pathological caller rather than a budget.
_SPRITE_CACHE_LIMIT = 512


def sprite_png(overlay: Overlay, width: int = 256) -> bytes:
    """The overlay as PNG bytes, for the picker.

    The same renderer the clip uses, so what the gallery shows is what gets
    burned in — not a separate drawing of it that can quietly disagree.
    """
    from trendrelay_api.integrations.face_blur import _load_opencv

    # Clamped here as well as in the renderer so two widths that come out the
    # same size share one cache entry rather than rendering twice.
    width = max(8, min(int(width), MAX_SPRITE_WIDTH))
    key = (overlay.id, width)
    reusable = overlay.image is None
    if reusable:
        cached = _SPRITE_CACHE.get(key)
        if cached is not None:
            return cached

    cv2 = _load_opencv()
    import numpy as np

    sprite = render_sprite(cv2, np, overlay, width)
    encoded, buffer = cv2.imencode(".png", sprite)
    if not encoded:
        raise ValueError(f"{overlay.id} could not be encoded.")
    image = bytes(buffer)
    if reusable:
        if len(_SPRITE_CACHE) >= _SPRITE_CACHE_LIMIT:
            # Oldest first, which insertion order gives for free.
            del _SPRITE_CACHE[next(iter(_SPRITE_CACHE))]
        _SPRITE_CACHE[key] = image
    return image
