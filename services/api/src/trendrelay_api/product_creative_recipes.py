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

The later recipes are read back from published shop and creator posts: a
mannequin walkaround, a styled outfit flat lay as a photograph and as a
handheld reveal, a sleepwear lookbook that moves from the hanger to a hotel
suite, a garment held up to the lens, a recurring virtual model, and an
overhead outfit change hidden by a ceiling-fan blade. Each prompt describes
the scene, the camera, and the motion those posts share, never one shop's
own product, caption, or watermark.
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

#: Every wording below closes with the same promise about the product.
_KEEP = (
    "The clothing keeps its exact color, print, cut, trim, and construction "
    "from the attached product image."
)
_KEEP_EACH = (
    "Each garment keeps its own color, print, cut, trim, and construction "
    "exactly as in its own product image."
)

#: A handheld walkaround of the garment on a headless mannequin, the way a
#: boutique films a new arrival: wide, closer, a hand on the details, the
#: back, and wide again.
_WALKAROUND_SCENE_OFF = (
    "A vertical handheld phone video in a bright white boutique corner. A "
    "white headless mannequin with legs stands on a round black pedestal "
    "table beside a white fireplace mantel, with white plush toys and a small "
    "vase of white flowers nearby."
)
_WALKAROUND_SCENE_ON = (
    "A vertical handheld phone video set in the scene from the attached "
    "background image. A white headless mannequin with legs stands on a round "
    "black pedestal table in that scene."
)
_WALKAROUND_SINGLE = (
    " The mannequin wears the exact clothing from the attached product "
    "image.\n\n"
    "The video opens on a wide shot of the whole outfit, then slowly pushes "
    "in. A hand enters the frame and lightly touches the details: the "
    "neckline, the ties or buttons, the trim, the waistband, lifting one edge "
    "so the fabric catches the light. The camera arcs around to the side and "
    "the back, then pulls out to a wide shot of the back of the outfit. Soft, "
    "even daylight. No face appears, and there is no text or logo on "
    "screen. " + _KEEP
)
_WALKAROUND_TOGETHER = (
    " A row of these mannequins each wears one garment from the attached "
    "product images, so every attached product appears in this one video. "
    + _KEEP_EACH + "\n\n"
    "The video opens on a wide shot of the row, then moves from one mannequin "
    "to the next. At each one, a hand lightly touches the details: the "
    "neckline, the ties or buttons, the trim, the waistband. The camera ends "
    "on a wide shot of the whole row. Soft, even daylight. No face appears, "
    "and there is no text or logo on screen."
)
WALKAROUND_OFF = _WALKAROUND_SCENE_OFF + _WALKAROUND_SINGLE
WALKAROUND_ON = _WALKAROUND_SCENE_ON + _WALKAROUND_SINGLE
WALKAROUND_OFF_TOGETHER = _WALKAROUND_SCENE_OFF + _WALKAROUND_TOGETHER
WALKAROUND_ON_TOGETHER = _WALKAROUND_SCENE_ON + _WALKAROUND_TOGETHER

#: A complete outfit styled around the product, from above, on a round cream
#: boucle ottoman. Accessories complete the look and never compete with it.
_STYLED_SURFACE_OFF = (
    "laid out on a round cream boucle ottoman on a light wood floor, with a "
    "coffee-table book and a small vase of dried flowers at the edge of the "
    "frame. Warm, soft window light from one side, with gentle shadows."
)
_STYLED_SURFACE_ON = (
    "laid out in the scene from the attached background image. Match the "
    "lighting and setting of the attached background image."
)
_STYLED_SINGLE = (
    "The exact clothing from the attached product image is arranged as a "
    "complete outfit, sleeves placed naturally and lower pieces folded just "
    "below, styled with a structured handbag, clean sneakers or loafers, "
    "sunglasses, and a slim watch. If the product is one piece, complete the "
    "outfit with simple neutral pieces that do not compete with it. " + _KEEP
)
_STYLED_EACH = (
    "Every garment from the attached product images is arranged together as "
    "one styled outfit, side by side, so every attached product appears in "
    "this one shot, with a structured handbag, clean "
    "sneakers or loafers, sunglasses, and a slim watch. " + _KEEP_EACH
)
_STYLED_CARDS = (
    " When several cards are asked for, the first card shows the whole "
    "outfit and each later card is a closer crop of the same arrangement: "
    "the collar, the fabric texture, the details."
)
STYLED_FLAT_LAY_OFF = (
    "A top-down styled outfit photograph, " + _STYLED_SURFACE_OFF + " "
    + _STYLED_SINGLE + _STYLED_CARDS
)
STYLED_FLAT_LAY_ON = (
    "A top-down styled outfit photograph, " + _STYLED_SURFACE_ON + " "
    + _STYLED_SINGLE + _STYLED_CARDS
)
STYLED_FLAT_LAY_OFF_TOGETHER = (
    "A top-down styled outfit photograph, " + _STYLED_SURFACE_OFF + " "
    + _STYLED_EACH + _STYLED_CARDS
)
STYLED_FLAT_LAY_ON_TOGETHER = (
    "A top-down styled outfit photograph, " + _STYLED_SURFACE_ON + " "
    + _STYLED_EACH + _STYLED_CARDS
)

#: The same styled flat lay as a short handheld video: close on the fabric,
#: one smooth rise to the whole outfit, then a slow drift.
_REVEAL_MOTION = (
    "\n\nThe video opens close and slightly tilted on the collar and the "
    "fabric, then rises and pulls back in one smooth, slightly handheld move "
    "to reveal the whole outfit from directly above, and holds with a gentle "
    "drift. A hand briefly enters to smooth a sleeve. There is no text or "
    "logo on screen."
)
FLAT_LAY_REVEAL_OFF = (
    "A vertical handheld phone video looking down at a styled outfit flat "
    "lay " + _STYLED_SURFACE_OFF + " " + _STYLED_SINGLE + _REVEAL_MOTION
)
FLAT_LAY_REVEAL_ON = (
    "A vertical handheld phone video looking down at a styled outfit flat "
    "lay " + _STYLED_SURFACE_ON + " " + _STYLED_SINGLE + _REVEAL_MOTION
)
FLAT_LAY_REVEAL_OFF_TOGETHER = (
    "A vertical handheld phone video looking down at a styled outfit flat "
    "lay " + _STYLED_SURFACE_OFF + " " + _STYLED_EACH + _REVEAL_MOTION
)
FLAT_LAY_REVEAL_ON_TOGETHER = (
    "A vertical handheld phone video looking down at a styled outfit flat "
    "lay " + _STYLED_SURFACE_ON + " " + _STYLED_EACH + _REVEAL_MOTION
)

#: A sleepwear carousel in three beats: the hanger, the fabric, the model.
#: Only the model's room follows the background switch; the hanger and the
#: close-up stay against the white wall, where the garment reads truest.
def _lookbook_rack(garment: str) -> str:
    return (
        f"A carousel of vertical 3:4 photographs. Card 1: {garment} hangs on "
        "a cream wooden hanger from a dark wooden clothing rail against a "
        "plain white wall, beside a tall vase of dried pampas grass, in bright "
        "soft daylight, while a hand gently lifts the hem so the fabric "
        "drapes. Card 2: a close-up of the fabric, showing its sheen or print, "
        "the lace trim, the bow or the buttons, with a hand lifting one edge "
        "so it catches the light. Every later card: a young Vietnamese woman "
        "with a soft updo and fresh, natural makeup wears the garment "
    )
_LOOKBOOK_ROOM_OFF = (
    "in a warm, softly lit luxury hotel suite with wood-panelled walls, a "
    "vanity holding a vase of white hydrangeas, an upholstered bench, and a "
    "patterned rug."
)
_LOOKBOOK_ROOM_ON = (
    "in the scene from the attached background image, matching its lighting "
    "and setting."
)
_LOOKBOOK_POSES = (
    " Full-length, relaxed poses: standing, sitting on the bench, leaning by "
    "the vanity, a small clutch in her hands. Each later card uses a "
    "different pose and framing. There is no text or logo on any card. "
)
_LOOKBOOK_SINGLE = _lookbook_rack(
    "the exact sleepwear from the attached product image"
)
_LOOKBOOK_GROUP = _lookbook_rack("each garment")
SLEEPWEAR_LOOKBOOK_OFF = (
    _LOOKBOOK_SINGLE + _LOOKBOOK_ROOM_OFF + _LOOKBOOK_POSES + _KEEP
)
SLEEPWEAR_LOOKBOOK_ON = (
    _LOOKBOOK_SINGLE + _LOOKBOOK_ROOM_ON + _LOOKBOOK_POSES + _KEEP
)
_LOOKBOOK_EACH = (
    "This carousel features every attached product: the hanger and "
    "close-up cards take the products in order, and on each model card she "
    "wears one product, never several at once. " + _KEEP_EACH + " "
)
SLEEPWEAR_LOOKBOOK_OFF_TOGETHER = (
    _LOOKBOOK_EACH + _LOOKBOOK_GROUP + _LOOKBOOK_ROOM_OFF
    + _LOOKBOOK_POSES.rstrip()
)
SLEEPWEAR_LOOKBOOK_ON_TOGETHER = (
    _LOOKBOOK_EACH + _LOOKBOOK_GROUP + _LOOKBOOK_ROOM_ON
    + _LOOKBOOK_POSES.rstrip()
)

#: The garment held up to the lens by its shoulders, in a dim room under a
#: paper lantern: front, a closer look, the back, the front again.
_HELD_UP_SCENE_OFF = (
    "A vertical handheld phone video in a dim, moody room with dark wood "
    "furniture and a glowing round paper lantern hanging above, shelves and "
    "small objects softly out of focus behind. Warm lamp light from above, "
    "with natural shadows."
)
_HELD_UP_SCENE_ON = (
    "A vertical handheld phone video in the scene from the attached "
    "background image, matching its lighting and setting."
)
_HELD_UP_SINGLE = (
    " Two hands hold the exact garment from the attached product image up by "
    "its shoulders, facing the camera and filling the frame. The hands give "
    "it a small shake so the fabric settles, bring it closer to show the "
    "collar, the zipper or buttons, and the stitching, then turn it around to "
    "show the back, and turn it to the front again. The fabric shows its real "
    "weight and texture. No face appears, and there is no text on screen. "
    + _KEEP
)
_HELD_UP_TOGETHER = (
    " Two hands hold up each garment from the attached product images in "
    "turn, by its shoulders, facing the camera and filling the frame. Each "
    "garment gets a small shake, a closer look at its collar and fastenings, "
    "a turn to show the back, and a turn to the front, before the next one "
    "takes its place, so every attached product appears once. No face appears, "
    "and there is no text on screen. " + _KEEP_EACH
)
HELD_UP_OFF = _HELD_UP_SCENE_OFF + _HELD_UP_SINGLE
HELD_UP_ON = _HELD_UP_SCENE_ON + _HELD_UP_SINGLE
HELD_UP_OFF_TOGETHER = _HELD_UP_SCENE_OFF + _HELD_UP_TOGETHER
HELD_UP_ON_TOGETHER = _HELD_UP_SCENE_ON + _HELD_UP_TOGETHER

#: Hikaru, the recurring virtual model. Every Hikaru prompt carries this
#: profile word for word, so the same face, hair, and build come back in
#: every card and every draft. Change it here and every later draft follows.
HIKARU_PROFILE = (
    "Hikaru is a Japanese woman in her mid-twenties. She has long, straight, "
    "glossy dark-brown hair past her chest, with soft wispy bangs swept to "
    "one side; fair, clear skin with a natural flush; gentle almond-shaped "
    "dark-brown eyes; soft natural makeup with light pink lips; a warm, wide "
    "smile that shows her teeth; and a slim build. Her signature gestures "
    "are tucking her hair behind one ear, resting a hand lightly at her "
    "collar, and glancing back over her shoulder."
)
CHARACTERS: dict[str, str] = {"hikaru": HIKARU_PROFILE}

_HIKARU_SCENE_OFF = (
    "She stands on a quiet, tree-lined Tokyo side street beside a modern "
    "building with a white textured facade and glass doors, in soft overcast "
    "daylight."
)
_HIKARU_SCENE_ON = (
    "She stands in the scene from the attached background image, matching "
    "its lighting and setting."
)
_HIKARU_CAMERA = (
    " A relaxed, natural pose, smiling at the camera. Shot like a "
    "professional street-fashion photograph: an 85mm portrait lens, shallow "
    "depth of field, eye level, true-to-life color, a vertical 3:4 frame. "
    "When several cards are asked for, every card shows the same outfit in "
    "the same place, with a different pose and framing: full-length, "
    "three-quarter, and a close portrait. Her face, hair, and build stay the "
    "same in every card. "
)
HIKARU_OFF = (
    "A photorealistic photograph of Hikaru. " + HIKARU_PROFILE + " She wears "
    "the exact clothing from the attached product image. " + _HIKARU_SCENE_OFF
    + _HIKARU_CAMERA + _KEEP
)
HIKARU_ON = (
    "A photorealistic photograph of Hikaru. " + HIKARU_PROFILE + " She wears "
    "the exact clothing from the attached product image. " + _HIKARU_SCENE_ON
    + _HIKARU_CAMERA + _KEEP
)
#: Hikaru is one person, so a group shot is one product per card, not one
#: outfit made of every product.
_HIKARU_EACH = (
    " The set features every attached product: each card shows her wearing one "
    "product from the attached product images, and she never wears two "
    "attached products at once. " + _KEEP_EACH + " "
)
HIKARU_OFF_TOGETHER = (
    "A photorealistic photograph set of Hikaru. " + HIKARU_PROFILE
    + _HIKARU_EACH + _HIKARU_SCENE_OFF + _HIKARU_CAMERA.rstrip()
)
HIKARU_ON_TOGETHER = (
    "A photorealistic photograph set of Hikaru. " + HIKARU_PROFILE
    + _HIKARU_EACH + _HIKARU_SCENE_ON + _HIKARU_CAMERA.rstrip()
)

#: An overhead outfit change. The camera looks straight down past a turning
#: ceiling fan; each blade that crosses the lens hides a cut, and the outfit
#: underneath has changed when it clears. The pose barely moves.
_FAN_SCENE_OFF = (
    "A vertical video shot from directly overhead, as if from a camera fixed "
    "to the ceiling beside a slowly turning ceiling fan. A young woman lies "
    "on her back on a faded vintage patterned rug in a cosy living room, with "
    "the edge of a cream sofa at one side and warm afternoon sunlight "
    "falling across the floor in slatted stripes."
)
_FAN_SCENE_ON = (
    "A vertical video shot from directly overhead, as if from a camera fixed "
    "to the ceiling beside a slowly turning ceiling fan. A young woman lies "
    "on her back on the floor of the scene from the attached background "
    "image, matching its lighting and setting."
)
_FAN_POSE = (
    " She lies still and relaxed, arms bent with her hands near her "
    "shoulders, looking up into the camera."
)
_FAN_SINGLE = (
    " At first she wears a plain cream knit lounge set. A dark wooden fan "
    "blade sweeps across the frame and briefly covers it; as the blade "
    "clears, she is wearing the exact clothing from the attached product "
    "image, in the same pose. Two more blade passes follow, and after each "
    "one the outfit stays the same while her pose changes slightly: hands "
    "near her hair, ankles crossed, a small smile. The passing blade hides "
    "every cut, so each change feels seamless. " + _KEEP
)
_FAN_TOGETHER = (
    " At first she wears a plain cream knit lounge set. A dark wooden fan "
    "blade sweeps across the frame and briefly covers it; as each blade "
    "clears, she is wearing the next garment from the attached product "
    "images, in order, in nearly the same pose, so every attached product "
    "appears once and she never wears two at once. The passing blade hides "
    "every cut, so each change feels seamless. " + _KEEP_EACH
)
CEILING_FAN_OFF = _FAN_SCENE_OFF + _FAN_POSE + _FAN_SINGLE
CEILING_FAN_ON = _FAN_SCENE_ON + _FAN_POSE + _FAN_SINGLE
CEILING_FAN_OFF_TOGETHER = _FAN_SCENE_OFF + _FAN_POSE + _FAN_TOGETHER
CEILING_FAN_ON_TOGETHER = _FAN_SCENE_ON + _FAN_POSE + _FAN_TOGETHER

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
    "mannequin_walkaround": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
    },
    "styled_flat_lay": {
        "kinds": ("image", "carousel"),
        "background": "optional",
        "variants": (),
    },
    "flat_lay_reveal": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
    },
    #: Carousel only: the wording is three beats, which one image cannot hold.
    "sleepwear_lookbook": {
        "kinds": ("carousel",),
        "background": "optional",
        "variants": (),
    },
    "held_up_garment": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
    },
    "hikaru_lookbook": {
        "kinds": ("image", "carousel"),
        "background": "optional",
        "variants": (),
        "character": "hikaru",
    },
    "ceiling_fan_transition": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
    },
}

_PROMPTS: dict[tuple[str, bool, str | None], str] = {
    ("bed_flat_lay", False, None): BED_FLAT_LAY_OFF,
    ("bed_flat_lay", True, None): BED_FLAT_LAY_ON,
    ("mannequin_transition", False, None): MANNEQUIN_OFF,
    ("mannequin_transition", True, None): MANNEQUIN_ON,
    ("mirror_selfie", True, "female"): MIRROR_FEMALE,
    ("mirror_selfie", True, "male"): MIRROR_MALE,
    ("mannequin_walkaround", False, None): WALKAROUND_OFF,
    ("mannequin_walkaround", True, None): WALKAROUND_ON,
    ("styled_flat_lay", False, None): STYLED_FLAT_LAY_OFF,
    ("styled_flat_lay", True, None): STYLED_FLAT_LAY_ON,
    ("flat_lay_reveal", False, None): FLAT_LAY_REVEAL_OFF,
    ("flat_lay_reveal", True, None): FLAT_LAY_REVEAL_ON,
    ("sleepwear_lookbook", False, None): SLEEPWEAR_LOOKBOOK_OFF,
    ("sleepwear_lookbook", True, None): SLEEPWEAR_LOOKBOOK_ON,
    ("held_up_garment", False, None): HELD_UP_OFF,
    ("held_up_garment", True, None): HELD_UP_ON,
    ("hikaru_lookbook", False, None): HIKARU_OFF,
    ("hikaru_lookbook", True, None): HIKARU_ON,
    ("ceiling_fan_transition", False, None): CEILING_FAN_OFF,
    ("ceiling_fan_transition", True, None): CEILING_FAN_ON,
}

_TOGETHER: dict[tuple[str, bool, str | None], str] = {
    ("bed_flat_lay", False, None): BED_FLAT_LAY_OFF_TOGETHER,
    ("bed_flat_lay", True, None): BED_FLAT_LAY_ON_TOGETHER,
    ("mannequin_transition", False, None): MANNEQUIN_OFF_TOGETHER,
    ("mannequin_transition", True, None): MANNEQUIN_ON_TOGETHER,
    ("mirror_selfie", True, "female"): MIRROR_FEMALE_TOGETHER,
    ("mirror_selfie", True, "male"): MIRROR_MALE_TOGETHER,
    ("mannequin_walkaround", False, None): WALKAROUND_OFF_TOGETHER,
    ("mannequin_walkaround", True, None): WALKAROUND_ON_TOGETHER,
    ("styled_flat_lay", False, None): STYLED_FLAT_LAY_OFF_TOGETHER,
    ("styled_flat_lay", True, None): STYLED_FLAT_LAY_ON_TOGETHER,
    ("flat_lay_reveal", False, None): FLAT_LAY_REVEAL_OFF_TOGETHER,
    ("flat_lay_reveal", True, None): FLAT_LAY_REVEAL_ON_TOGETHER,
    ("sleepwear_lookbook", False, None): SLEEPWEAR_LOOKBOOK_OFF_TOGETHER,
    ("sleepwear_lookbook", True, None): SLEEPWEAR_LOOKBOOK_ON_TOGETHER,
    ("held_up_garment", False, None): HELD_UP_OFF_TOGETHER,
    ("held_up_garment", True, None): HELD_UP_ON_TOGETHER,
    ("hikaru_lookbook", False, None): HIKARU_OFF_TOGETHER,
    ("hikaru_lookbook", True, None): HIKARU_ON_TOGETHER,
    ("ceiling_fan_transition", False, None): CEILING_FAN_OFF_TOGETHER,
    ("ceiling_fan_transition", True, None): CEILING_FAN_ON_TOGETHER,
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
