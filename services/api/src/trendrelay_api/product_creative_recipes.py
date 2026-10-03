"""The kickstart prompts for an Attribution product creative.

The server is the only place a prompt is chosen. The modal and MCP both show
the string this module returns, and that same string is what a draft stores.
Nothing here reads a file or a database: the background switch is a pure
function of the recipe and the flag.

The two video scripts are the reference texts in substance. The bed photograph
has no script, so that prompt is written to match it: one garment, the
selected product's own, on the bed in that room. A sheet of unrelated tops is
the picture's layout, not the recipe.

`together=True` is a sibling wording for one shot of several products. It
keeps the same scene and does not replace the single-product constants.
"""

from __future__ import annotations

from typing import Any

#: Top-down flat lay of the selected product's own garment, in the room of
#: `References/Products/Products On Bed.jpg`. One garment, not the collage.
BED_FLAT_LAY_OFF = (
    "A top-down product photograph of the exact garment from the attached "
    "product image, laid flat on a neatly made bed. The bed has white sheets "
    "with soft folds, a black headboard, two large white pillows, and one long "
    "charcoal lumbar pillow in front of them. To the left, a black nightstand "
    "holds a white modern lamp. A framed black-and-white picture hangs above "
    "the headboard. Soft, even daylight. Place only this one garment — the "
    "clothing from the attached product image — on the bed. Do not add other "
    "tops, sets, or a collage of unrelated clothing. Keep the garment's color, "
    "print, neckline, straps, and construction exactly as in the product image."
)

#: The same flat lay, with the attached picture replacing that bedroom. The
#: word "bed" stays out of this wording: the switch is the wording, not a flag
#: beside an unchanged sentence.
BED_FLAT_LAY_ON = (
    "A top-down product photograph of the exact garment from the attached "
    "product image, laid flat in the scene from the attached background image. "
    "Place only this one garment — the clothing from the attached product "
    "image — in that scene. Do not add other tops, sets, or a collage of "
    "unrelated clothing. Keep the garment's color, print, neckline, straps, "
    "and construction exactly as in the product image. Match the lighting and "
    "setting of the attached background image."
)

#: Mannequin to Model Transition, without a background: the narrow beige hallway.
MANNEQUIN_OFF = (
    "A vertical video shot in a narrow beige hallway with white tile floors. "
    "On the left, a smiling female clerk in a black uniform stands behind a "
    "white headless mannequin torso. The mannequin displays the exact clothing "
    "item from the attached product image.\n\n"
    "A young woman with long black hair and silver heels, carrying two white "
    "shopping bags, walks forward toward the camera. She pauses, turns her "
    "attention to the mannequin, smiles, points approvingly at the displayed "
    "clothing with her right index finger, and begins walking backward.\n\n"
    "As she steps backward, a seamless jump-cut occurs. The woman's outfit "
    "instantly changes, and she is now wearing the exact clothing from the "
    "attached product image. She continues walking backward out of the frame."
)

#: The same transition against the attached background. The hallway sentence is
#: gone; the rest of the reference script is unchanged.
MANNEQUIN_ON = (
    "A vertical video set against the background from the attached background "
    "image. On the left, a smiling female clerk in a black uniform stands "
    "behind a white headless mannequin torso. The mannequin displays the exact "
    "clothing item from the attached product image.\n\n"
    "A young woman with long black hair and silver heels, carrying two white "
    "shopping bags, walks forward toward the camera. She pauses, turns her "
    "attention to the mannequin, smiles, points approvingly at the displayed "
    "clothing with her right index finger, and begins walking backward.\n\n"
    "As she steps backward, a seamless jump-cut occurs. The woman's outfit "
    "instantly changes, and she is now wearing the exact clothing from the "
    "attached product image. She continues walking backward out of the frame."
)

MIRROR_FEMALE = (
    "A photorealistic mirror selfie video of a Vietnamese slender woman with "
    "long, voluminous wavy hair, holding a black smartphone directly in front "
    "of her face to completely obscure it. She is wearing the exact outfit "
    "from the provided subject reference image. She is standing inside the "
    "exact room from the provided background reference image. The woman "
    "strikes subtle fashion poses, gently shifting her weight from side to "
    "side, lightly touching her waist, and taking a small step forward. Fixed "
    "camera angle from the mirror's perspective, matching the lighting and "
    "ambiance of the background image."
)

MIRROR_MALE = (
    "A photorealistic mirror selfie video of a slender Vietnamese man with a "
    "stylish, well-groomed hairstyle, holding a black smartphone directly in "
    "front of his face to completely obscure it. He is wearing the exact "
    "outfit from the provided subject reference image. He is standing inside "
    "the exact room from the provided background reference image. The man "
    "strikes subtle, natural fashion poses, gently shifting his weight from "
    "side to side, briefly adjusting his shirt or waistband, lightly placing "
    "one hand near his pocket, and taking a small step forward. His movements "
    "are relaxed, confident, and understated, like a casual outfit-check "
    "video. Fixed camera angle from the mirror's perspective, matching the "
    "lighting, reflections, framing, and ambiance of the background image."
)

#: The same bedroom, with every attached product in the one shot. Each garment
#: keeps its own look. Nobody wears the set.
BED_FLAT_LAY_OFF_TOGETHER = (
    "A top-down product photograph of every garment from the attached product "
    "images, each laid flat on a neatly made bed. The bed has white sheets "
    "with soft folds, a black headboard, two large white pillows, and one long "
    "charcoal lumbar pillow in front of them. To the left, a black nightstand "
    "holds a white modern lamp. A framed black-and-white picture hangs above "
    "the headboard. Soft, even daylight. Place every attached product in this "
    "one shot, side by side, each garment keeping its own color, print, "
    "neckline, straps, and construction exactly as in its own product image. "
    "Do not merge the products into one outfit, and do not dress one person "
    "in every garment."
)

#: The same group flat lay in the attached scene. The word "bed" stays out.
BED_FLAT_LAY_ON_TOGETHER = (
    "A top-down product photograph of every garment from the attached product "
    "images, each laid flat in the scene from the attached background image. "
    "Place every attached product in this one shot, side by side, in that "
    "scene. Each garment keeps its own color, print, neckline, straps, and "
    "construction exactly as in its own product image. Do not merge the "
    "products into one outfit, and do not dress one person in every garment. "
    "Match the lighting and setting of the attached background image."
)

#: Several mannequins in the hallway, one garment each. The shopper's clothes
#: stay as they are: the jump-cut that dresses one person is the single-product
#: script, and it does not fit a group.
MANNEQUIN_OFF_TOGETHER = (
    "A vertical video shot in a narrow beige hallway with white tile floors. "
    "On the left, a smiling female clerk in a black uniform stands behind a "
    "row of white headless mannequin torsos. Each mannequin displays one "
    "garment from the attached product images, so every attached product "
    "appears in this one shot. Each garment keeps its own color, print, "
    "neckline, straps, and construction exactly as in its own product image.\n\n"
    "A young woman with long black hair and silver heels, carrying two white "
    "shopping bags, walks forward toward the camera. She pauses, turns her "
    "attention to the mannequins, smiles, and points approvingly at the "
    "displayed clothing with her right index finger. Her own clothes stay as "
    "they are. She continues walking backward out of the frame. The mannequins "
    "keep each product separate."
)

MANNEQUIN_ON_TOGETHER = (
    "A vertical video set against the background from the attached background "
    "image. On the left, a smiling female clerk in a black uniform stands "
    "behind a row of white headless mannequin torsos. Each mannequin displays "
    "one garment from the attached product images, so every attached product "
    "appears in this one shot. Each garment keeps its own color, print, "
    "neckline, straps, and construction exactly as in its own product image.\n\n"
    "A young woman with long black hair and silver heels, carrying two white "
    "shopping bags, walks forward toward the camera. She pauses, turns her "
    "attention to the mannequins, smiles, and points approvingly at the "
    "displayed clothing with her right index finger. Her own clothes stay as "
    "they are. She continues walking backward out of the frame. The mannequins "
    "keep each product separate."
)

MIRROR_FEMALE_TOGETHER = (
    "A photorealistic mirror selfie video in the exact room from the provided "
    "background reference image. Several women stand in a row, each wearing "
    "one outfit from the attached product images, so every attached product "
    "appears in this one shot. Each woman keeps that one product's color, "
    "print, neckline, straps, and construction exactly as in its own product "
    "image, and wears no other attached product. Each holds a black smartphone "
    "directly in front of her face to completely obscure it. They strike "
    "subtle fashion poses, gently shifting their weight. Fixed camera angle "
    "from the mirror's perspective, matching the lighting and ambiance of the "
    "background image."
)

MIRROR_MALE_TOGETHER = (
    "A photorealistic mirror selfie video in the exact room from the provided "
    "background reference image. Several men stand in a row, each wearing one "
    "outfit from the attached product images, so every attached product "
    "appears in this one shot. Each man keeps that one product's color, print, "
    "neckline, straps, and construction exactly as in its own product image, "
    "and wears no other attached product. Each holds a black smartphone "
    "directly in front of his face to completely obscure it. They strike "
    "subtle, natural fashion poses, gently shifting their weight. Fixed "
    "camera angle from the mirror's perspective, matching the lighting, "
    "reflections, framing, and ambiance of the background image."
)

#: What each recipe can be, and whether a second wording exists.
#: `background` is "optional" when the reference has a with-background script,
#: and "required" when it does not have a version without one. Mirror selfie
#: is required: inventing a no-background wording would be a different film.
RECIPES: dict[str, dict[str, Any]] = {
    "bed_flat_lay": {
        "kinds": ("image", "carousel"),
        "background": "optional",
        "variants": (),
    },
    "mannequin_transition": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
    },
    "mirror_selfie": {
        "kinds": ("video",),
        "background": "required",
        "variants": ("female", "male"),
    },
}

_PROMPTS: dict[tuple[str, bool, str | None], str] = {
    ("bed_flat_lay", False, None): BED_FLAT_LAY_OFF,
    ("bed_flat_lay", True, None): BED_FLAT_LAY_ON,
    ("mannequin_transition", False, None): MANNEQUIN_OFF,
    ("mannequin_transition", True, None): MANNEQUIN_ON,
    ("mirror_selfie", True, "female"): MIRROR_FEMALE,
    ("mirror_selfie", True, "male"): MIRROR_MALE,
}

_TOGETHER: dict[tuple[str, bool, str | None], str] = {
    ("bed_flat_lay", False, None): BED_FLAT_LAY_OFF_TOGETHER,
    ("bed_flat_lay", True, None): BED_FLAT_LAY_ON_TOGETHER,
    ("mannequin_transition", False, None): MANNEQUIN_OFF_TOGETHER,
    ("mannequin_transition", True, None): MANNEQUIN_ON_TOGETHER,
    ("mirror_selfie", True, "female"): MIRROR_FEMALE_TOGETHER,
    ("mirror_selfie", True, "male"): MIRROR_MALE_TOGETHER,
}


def resolve_prompt(
    recipe: str,
    *,
    background: bool,
    variant: str | None = None,
    together: bool = False,
) -> str:
    """The prompt a person reviews, for this recipe and this background switch.

    `together` selects the group wording. False returns the single-product
    script unchanged. Raises ValueError when the combination is not one of
    the reference wordings. Mirror selfie has no wording without a background,
    so asking for one is refused rather than invented.
    """
    spec = RECIPES.get(recipe)
    if spec is None:
        known = ", ".join(sorted(RECIPES))
        raise ValueError(f"Unknown recipe {recipe!r}. Known recipes: {known}.")
    if spec["background"] == "required" and not background:
        raise ValueError(
            "This recipe needs a background image. There is no wording without one."
        )
    if spec["variants"]:
        if variant not in spec["variants"]:
            raise ValueError("Choose female or male for the mirror selfie.")
    elif variant:
        raise ValueError("This recipe has no subject variant.")
    key = (recipe, bool(background), variant if spec["variants"] else None)
    prompt = (_TOGETHER if together else _PROMPTS).get(key)
    if prompt is None:
        raise ValueError("That recipe has no wording for this background choice.")
    return prompt
