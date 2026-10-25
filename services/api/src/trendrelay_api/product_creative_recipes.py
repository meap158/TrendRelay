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

#: A product clip stays silent. A smile was being read as a cue to speak.
_SILENT = (
    " Nobody speaks or mouths words. The clip has no dialogue, narration, "
    "or voice-over."
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
) + _SILENT

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
) + _SILENT

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
) + _SILENT

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
) + _SILENT

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
) + _SILENT

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
) + _SILENT

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
) + _SILENT

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
) + _SILENT

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
    "screen. " + _KEEP + _SILENT
)
_WALKAROUND_TOGETHER = (
    " A row of these mannequins each wears one garment from the attached "
    "product images, so every attached product appears in this one video. "
    + _KEEP_EACH + "\n\n"
    "The video opens on a wide shot of the row, then moves from one mannequin "
    "to the next. At each one, a hand lightly touches the details: the "
    "neckline, the ties or buttons, the trim, the waistband. The camera ends "
    "on a wide shot of the whole row. Soft, even daylight. No face appears, "
    "and there is no text or logo on screen." + _SILENT
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
    "logo on screen." + _SILENT
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

# A five-card apparel story based on the recurring poses in the supplied
# Instagram research: a clear hero, relaxed seated frame, construction detail,
# profile fit, and a full-length close. The selected Library character provides
# identity; the product gallery remains the authority for the garment.
_CHARACTER_EDITORIAL_COMMON = (
    "Create exactly five separate, finished portrait-format carousel images, "
    "one complete image per card. Never make a contact sheet, grid, collage, "
    "or image containing multiple cards. Use the selected Library character's "
    "portrait and consistency sheet as the identity reference; show the same "
    "adult character with the same face, hair, skin tone and build in every "
    "card. Use the attached product image or images as the sole authority for "
    "the product's exact cut, color, fabric, trims, fit and construction. "
    "The outfit on the character sheet is only an identity reference: replace "
    "it with the exact selected product and do not redesign or embellish it. "
    "Keep camera height, lens feel, color, lighting and set consistent across "
    "all five cards. Card 1: full-length three-quarter hero pose with the "
    "complete silhouette visible and relaxed natural hands. Card 2: seated "
    "three-quarter pose on a simple pale chair, torso on a gentle diagonal, "
    "one hand near the temple without obscuring the face or garment. Card 3: "
    "medium close-up that makes one visible product detail easy to inspect; "
    "a natural hand may rest beside the detail, never over it. Card 4: clean "
    "side or over-shoulder view showing fit and drape. Card 5: full-length "
    "front view in a distinct relaxed pose, showing the complete garment and "
    "hem. Use an eye-level fashion-camera perspective, realistic anatomy, "
    "natural skin texture, clear garment edges and crop-safe space. No text, "
    "price, claims, fake labels, logos, watermarks, extra people, or "
    "unrequested props. Keep the garment opaque and modestly presented."
)
CHARACTER_EDITORIAL_OFF = (
    _CHARACTER_EDITORIAL_COMMON + " The set is one uncluttered warm-grey studio "
    "with a soft seamless backdrop and broad diffused light; use the pale chair "
    "only for Card 2. Keep the background and light direction the same on all "
    "five cards."
)
CHARACTER_EDITORIAL_ON = (
    _CHARACTER_EDITORIAL_COMMON + " Use the attached background image as the "
    "same set for all five cards, matching its lighting and perspective. Do not "
    "switch rooms or add another scene."
)
_CHARACTER_EDITORIAL_EACH = (
    " This set features every attached product. Assign one product to each "
    "card in the order provided, one product per frame; do not combine "
    "garments or change a product's colorway. If fewer than five products are "
    "attached, use the remaining cards for alternate poses and details of the "
    "first product, preserving the same card sequence."
)
CHARACTER_EDITORIAL_OFF_TOGETHER = (
    _CHARACTER_EDITORIAL_COMMON + _CHARACTER_EDITORIAL_EACH
    + " The set is one uncluttered warm-grey studio with a soft seamless "
    "backdrop and broad diffused light; use the pale chair only for Card 2. "
    "Keep the background and light direction the same on all five cards."
)
CHARACTER_EDITORIAL_ON_TOGETHER = (
    _CHARACTER_EDITORIAL_COMMON + _CHARACTER_EDITORIAL_EACH
    + " Use the attached background image as the same set for all five cards, "
    "matching its lighting and perspective. Do not switch rooms or add another scene."
)

#: The sleepwear brand's own video: a set on hangers on a rail, a hand that
#: lifts the top away to show the bottoms, then the fabric in close-up.
_RAIL_SCENE_OFF = (
    "A vertical handheld phone video of a clothing rail made of dark wood "
    "against a plain white wall, beside a tall vase of dried pampas grass, in "
    "bright soft daylight."
)
_RAIL_SCENE_ON = (
    "A vertical handheld phone video of a dark wooden clothing rail in the "
    "scene from the attached background image, matching its lighting and "
    "setting."
)
_RAIL_SINGLE = (
    " The exact sleepwear set from the attached product image hangs on the "
    "rail, each piece on its own cream wooden or clear acrylic hanger, the "
    "top in front of the bottoms. The video opens on the whole set. A hand "
    "lifts the top's hanger forward and turns it so the straps, neckline, "
    "and hem show, then moves it aside to reveal the bottoms. The camera "
    "moves close as the hand gathers and gently squeezes the fabric to show "
    "its sheen, its lace, or its knit, then pulls back to the whole set. No "
    "face appears, and there is no text on screen. " + _KEEP + _SILENT
)
_RAIL_TOGETHER = (
    " Every garment from the attached product images hangs on the rail, side "
    "by side on cream wooden or clear acrylic hangers, so every attached "
    "product appears in this one video. The video opens on the full rail. A "
    "hand slides along it, lifting each hanger forward in turn so its "
    "straps, neckline, and hem show, and the camera moves close as the hand "
    "gathers each fabric to show its sheen, its lace, or its knit. It ends "
    "on the full rail. No face appears, and there is no text on screen. "
    + _KEEP_EACH + _SILENT
)
HANGER_RAIL_OFF = _RAIL_SCENE_OFF + _RAIL_SINGLE
HANGER_RAIL_ON = _RAIL_SCENE_ON + _RAIL_SINGLE
HANGER_RAIL_OFF_TOGETHER = _RAIL_SCENE_OFF + _RAIL_TOGETHER
HANGER_RAIL_ON_TOGETHER = _RAIL_SCENE_ON + _RAIL_TOGETHER

#: The garment held up to the lens by its shoulders against walnut panelling,
#: the holder's face out of frame: front, back, then hard-cut close-ups of
#: the collar, a sleeve, the cuff, and the hem, as the source post runs.
_HELD_UP_SCENE_OFF = (
    "A vertical handheld phone video in a warm room with dark vertical "
    "walnut wood panelling on the walls, a black mannequin stand and a small "
    "bookshelf of magazines softly out of focus behind, and a wooden floor. "
    "Warm indoor light, with natural shadows. Steady background music, no "
    "voice-over."
)
_HELD_UP_SCENE_ON = (
    "A vertical handheld phone video in the scene from the attached "
    "background image, matching its lighting and setting."
)
_HELD_UP_SINGLE = (
    " A person in a plain black T-shirt, face out of frame, holds the exact "
    "garment from the attached product image up by its shoulders at chest "
    "height, facing the camera and filling the frame, and gives it a small "
    "shake so the fabric settles. In about fifteen seconds of hard cuts: the "
    "garment is turned around to show the back; the camera moves close on "
    "the collar and its zipper or buttons; one sleeve is held out to show "
    "its pocket or seams; a hand pulls the cuff to show its snap, button, or "
    "elastic; and the hem is turned up to show its fastening. The fabric "
    "shows its real weight and texture. No face appears, and there is no "
    "text on screen. " + _KEEP + _SILENT
)
_HELD_UP_TOGETHER = (
    " A person in a plain black T-shirt, face out of frame, holds up each "
    "garment from the attached product images in turn, by its shoulders at "
    "chest height, facing the camera and filling the frame. Each "
    "garment gets a small shake, a turn to show the back, and hard-cut "
    "close-ups of its collar, a sleeve or cuff, and its hem, before the next "
    "one takes its place, so every attached product appears once. No face appears, "
    "and there is no text on screen. " + _KEEP_EACH + _SILENT
)
HELD_UP_OFF = _HELD_UP_SCENE_OFF + _HELD_UP_SINGLE
HELD_UP_ON = _HELD_UP_SCENE_ON + _HELD_UP_SINGLE
HELD_UP_OFF_TOGETHER = _HELD_UP_SCENE_OFF + _HELD_UP_TOGETHER
HELD_UP_ON_TOGETHER = _HELD_UP_SCENE_ON + _HELD_UP_TOGETHER

#: The recurring virtual model. Every one of her prompts carries this
#: profile word for word, so the same face, hair, and build come back in
#: every card and every draft. Change it here and every later draft follows.
#: She is "the model" in the wording. The recipe ids stay hikaru_*.
HIKARU_PROFILE = (
    "The model is a Japanese woman in her mid-twenties. She has long, straight, "
    "glossy dark-brown hair past her chest, with soft wispy bangs swept to "
    "one side; fair, clear skin with a natural flush; gentle almond-shaped "
    "dark-brown eyes; soft natural makeup with light pink lips; a warm, wide "
    "smile that shows her teeth; and a slim build. Her signature gestures "
    "are tucking her hair behind one ear, resting a hand lightly at her "
    "collar, and glancing back over her shoulder. She looks like a real "
    "person on camera, with natural skin texture, pores, slight facial "
    "asymmetry, and ordinary phone-camera detail, not a doll, a plastic "
    "render, or a beauty filter."
)
#: Worn sleepwear stays covered. Thin fabric was rendering nipples and a
#: camel toe; the sentence is part of every model wording and the fan.
_COVERED = (
    " The garment is thick, matte, and fully opaque in every frame, "
    "including the full-length view. No nipples are visible through the "
    "fabric, and the fit shows no camel toe."
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
    "A photorealistic photograph of the model. " + HIKARU_PROFILE + " She wears "
    "the exact clothing from the attached product image. " + _HIKARU_SCENE_OFF
    + _HIKARU_CAMERA.rstrip() + _COVERED + " " + _KEEP
)
HIKARU_ON = (
    "A photorealistic photograph of the model. " + HIKARU_PROFILE + " She wears "
    "the exact clothing from the attached product image. " + _HIKARU_SCENE_ON
    + _HIKARU_CAMERA.rstrip() + _COVERED + " " + _KEEP
)
#: The model is one person, so a group shot is one product per card, not one
#: outfit made of every product.
_HIKARU_EACH = (
    " The set features every attached product: each card shows her wearing one "
    "product from the attached product images, and she never wears two "
    "attached products at once. " + _KEEP_EACH + " "
)
HIKARU_OFF_TOGETHER = (
    "A photorealistic photograph set of the model. " + HIKARU_PROFILE
    + _HIKARU_EACH + _HIKARU_SCENE_OFF + _HIKARU_CAMERA.rstrip() + _COVERED
)
HIKARU_ON_TOGETHER = (
    "A photorealistic photograph set of the model. " + HIKARU_PROFILE
    + _HIKARU_EACH + _HIKARU_SCENE_ON + _HIKARU_CAMERA.rstrip() + _COVERED
)

#: The model on video: the walk toward a camera that backs away, then the stop.
#: She looks at the lens with her mouth closed. A smile at the lens was being
#: read as a cue to speak. The profile rides along word for word, as in the photos.
_HIKARU_WALK_SCENE_OFF = (
    "She walks along a Tokyo street at night, past glowing shop signs and "
    "neon, the wet pavement reflecting the lights, a handbag in one hand."
)
_HIKARU_WALK_SCENE_ON = (
    "She walks through the scene from the attached background image, "
    "matching its lighting and setting, a handbag in one hand."
)
_HIKARU_WALK_MOTION = (
    " The camera moves backward ahead of her in one smooth tracking shot as "
    "she walks toward it, glancing around and touching her hair. She slows, "
    "stops in a medium close-up, and looks into the camera with her mouth "
    "closed. Photorealistic, "
    "with a shallow depth of field and true-to-life color, in a vertical "
    "9:16 frame. Her face, hair, and build stay the same throughout. "
)
HIKARU_WALK_OFF = (
    "A vertical video of the model. " + HIKARU_PROFILE + " She wears the exact "
    "clothing from the attached product image. " + _HIKARU_WALK_SCENE_OFF
    + _HIKARU_WALK_MOTION.rstrip() + _COVERED + " " + _KEEP + _SILENT
)
HIKARU_WALK_ON = (
    "A vertical video of the model. " + HIKARU_PROFILE + " She wears the exact "
    "clothing from the attached product image. " + _HIKARU_WALK_SCENE_ON
    + _HIKARU_WALK_MOTION.rstrip() + _COVERED + " " + _KEEP + _SILENT
)
#: One person cannot wear every product at once: each one is its own walk,
#: cut together in order.
_HIKARU_WALK_EACH = (
    " The video features every attached product: it cuts between short walks "
    "in order, and in each walk she wears one product from the attached "
    "product images, never two attached products at once. " + _KEEP_EACH + " "
)
HIKARU_WALK_OFF_TOGETHER = (
    "A vertical video of the model. " + HIKARU_PROFILE + _HIKARU_WALK_EACH
    + _HIKARU_WALK_SCENE_OFF + _HIKARU_WALK_MOTION.rstrip() + _COVERED + _SILENT
)
HIKARU_WALK_ON_TOGETHER = (
    "A vertical video of the model. " + HIKARU_PROFILE + _HIKARU_WALK_EACH
    + _HIKARU_WALK_SCENE_ON + _HIKARU_WALK_MOTION.rstrip() + _COVERED + _SILENT
)

#: An overhead outfit change, read from the fan-wipe reels: a fixed camera,
#: her body on the centre line, a remote press, then one blade for one whole
#: outfit. With one product the blade waits until late in the clip. The blade
#: hides the cut. The frame stays free of captions, because this wording has
#: no product name to print and an invented label would be wrong.
_FAN_SCENE_OFF = (
    "A vertical video shot from directly overhead, as if from a camera fixed "
    "to the ceiling beside a slowly turning ceiling fan. The camera stays "
    "fixed: it does not pan, tilt, or zoom. A young woman lies on her back "
    "on a faded vintage patterned rug in a cosy living room, with the edge "
    "of a cream sofa along one side, not across the bottom of the frame, a "
    "TV remote on the rug beside her, and warm afternoon sunlight falling "
    "across the floor in slatted stripes."
)
_FAN_SCENE_ON = (
    "A vertical video shot from directly overhead, as if from a camera fixed "
    "to the ceiling beside a slowly turning ceiling fan. The camera stays "
    "fixed: it does not pan, tilt, or zoom. A young woman lies on her back "
    "on the floor of the scene from the attached background image, matching "
    "its lighting and setting, with a TV remote on the floor beside her."
)
_FAN_POSE = (
    " She lies straight along the vertical centre line, head toward the top "
    "of the frame and feet toward the bottom, not on a diagonal. Her arms "
    "are bent, her hands rest near her shoulders, and she looks up into the "
    "camera."
)
_FAN_SINGLE = (
    " At first she wears a plain cream knit lounge set, and she keeps "
    "wearing it while the fan turns slowly above her. No blade crosses the "
    "lens until late in the clip. She reaches to the remote, presses it "
    "once, and puts her hand back. One dark wooden fan blade then sweeps "
    "across the frame and covers her completely; as that blade clears, she "
    "is wearing the exact clothing from the attached product image, the "
    "whole outfit at once, in the same pose. Her arms, hands, legs, and "
    "head stay where they were. There is only one product, so this is the "
    "only pass, and the outfit does not change again. The passing blade "
    "hides every cut, so the change feels seamless. No text appears on "
    "screen." + _COVERED + " " + _KEEP + _SILENT
)
_FAN_TOGETHER = (
    " At first she wears a plain cream knit lounge set. She presses the "
    "remote once, and then each fan transition is a new product: a dark "
    "wooden fan blade sweeps across the frame and covers her completely, "
    "and as each blade clears she is wearing the next garment from the "
    "attached product images, in order, one new product per pass, the whole "
    "outfit at once, in nearly the same pose, so every attached product "
    "appears once and she never wears two at once. A pass never repeats a "
    "product, never mixes two products, and never changes only the pose. "
    "The passing blade hides every cut, so each change feels seamless. No "
    "text appears on screen." + _COVERED + " " + _KEEP_EACH + _SILENT
)
CEILING_FAN_OFF = _FAN_SCENE_OFF + _FAN_POSE + _FAN_SINGLE
CEILING_FAN_ON = _FAN_SCENE_ON + _FAN_POSE + _FAN_SINGLE
CEILING_FAN_OFF_TOGETHER = _FAN_SCENE_OFF + _FAN_POSE + _FAN_TOGETHER
CEILING_FAN_ON_TOGETHER = _FAN_SCENE_ON + _FAN_POSE + _FAN_TOGETHER

#: Mirror selfie without a background image: the same outfit check, in front
#: of a tall mirror in an ordinary bright bedroom. It used to be refused - the
#: reference post only had a with-room version - but the operator asked for
#: every recipe to work without a background, so this is the room it uses.
_MIRROR_ROOM_OFF = (
    "in front of a tall mirror in a bright, tidy bedroom with white walls, a "
    "neatly made bed, and soft daylight from a window"
)
MIRROR_FEMALE_OFF = (
    "A photorealistic mirror selfie video of a Vietnamese slender woman with "
    "long, voluminous wavy hair, holding a black smartphone directly in front "
    "of her face to completely obscure it. She is wearing the exact outfit "
    "from the provided subject reference image. She is standing "
    + _MIRROR_ROOM_OFF + ". The woman strikes subtle fashion poses, gently "
    "shifting her weight from side to side, lightly touching her waist, and "
    "taking a small step forward. Fixed camera angle from the mirror's "
    "perspective, in soft, natural daylight."
) + _SILENT
MIRROR_MALE_OFF = (
    "A photorealistic mirror selfie video of a slender Vietnamese man with a "
    "stylish, well-groomed hairstyle, holding a black smartphone directly in "
    "front of his face to completely obscure it. He is wearing the exact "
    "outfit from the provided subject reference image. He is standing "
    + _MIRROR_ROOM_OFF + ". The man strikes subtle, natural fashion poses, "
    "gently shifting his weight from side to side, briefly adjusting his shirt "
    "or waistband, lightly placing one hand near his pocket, and taking a small "
    "step forward. His movements are relaxed, confident, and understated, like "
    "a casual outfit-check video. Fixed camera angle from the mirror's "
    "perspective, in soft, natural daylight."
) + _SILENT
MIRROR_FEMALE_OFF_TOGETHER = (
    "A photorealistic mirror selfie video " + _MIRROR_ROOM_OFF + ". Several "
    "women stand in a row, each wearing one outfit from the attached product "
    "images, so every attached product appears in this one shot. Each woman "
    "keeps that one product's color, print, neckline, straps, and construction "
    "exactly as in its own product image, and wears no other attached product. "
    "Each holds a black smartphone directly in front of her face to completely "
    "obscure it. They strike subtle fashion poses, gently shifting their "
    "weight. Fixed camera angle from the mirror's perspective, in soft, "
    "natural daylight."
) + _SILENT
MIRROR_MALE_OFF_TOGETHER = (
    "A photorealistic mirror selfie video " + _MIRROR_ROOM_OFF + ". Several "
    "men stand in a row, each wearing one outfit from the attached product "
    "images, so every attached product appears in this one shot. Each man "
    "keeps that one product's color, print, neckline, straps, and construction "
    "exactly as in its own product image, and wears no other attached product. "
    "Each holds a black smartphone directly in front of his face to completely "
    "obscure it. They strike subtle, natural fashion poses, gently shifting "
    "their weight. Fixed camera angle from the mirror's perspective, in soft, "
    "natural daylight."
) + _SILENT

#: With a Library character switched on, the recipes that describe a person
#: of their own describe the character instead. Appending the character line
#: to them left two people in one prompt - Hikaru's whole profile, or the
#: mirror's slender woman, or a holder whose face is kept out of frame, and
#: then a different person to show. The character's own line still follows
#: (see `characters.draft_line`), so the wording points at it. Characters
#: record no gender, so these say "they". Without a character every recipe
#: keeps its own wording byte for byte; the other recipes describe nobody in
#: particular and still take the appended line alone.
_CAST = "the character described below"
_CAST_REAL = (
    " They look like a real person on camera, with natural skin texture, "
    "pores, slight facial asymmetry, and ordinary phone-camera detail, not a "
    "doll, a plastic render, or a beauty filter."
)

_MIRROR_CAST_ROOM_ON = (
    " They are standing inside the exact room from the provided background "
    "reference image."
)
_MIRROR_CAST_ROOM_OFF = f" They are standing {_MIRROR_ROOM_OFF}."
_MIRROR_CAST_LIGHT_ON = (
    " Fixed camera angle from the mirror's perspective, matching the lighting "
    "and ambiance of the background image."
)
_MIRROR_CAST_LIGHT_OFF = (
    " Fixed camera angle from the mirror's perspective, in soft, natural "
    "daylight."
)
_MIRROR_CAST_SINGLE = (
    f"A photorealistic mirror selfie video of {_CAST}, holding a black "
    "smartphone directly in front of their face to completely obscure it. They "
    "are wearing the exact outfit from the provided subject reference image."
)
_MIRROR_CAST_POSE = (
    " They strike subtle, natural fashion poses, gently shifting their weight "
    "from side to side and taking a small step forward."
)
#: One person cannot stand in a row with themselves: a group is short mirror
#: selfies cut together, one product each.
_MIRROR_CAST_TOGETHER = (
    "A photorealistic mirror selfie video that cuts between short mirror "
    f"selfies in order. In each one, {_CAST} wears one outfit from the attached "
    "product images, never two attached products at once, so every attached "
    "product appears once, and holds a black smartphone directly in front of "
    "their face to completely obscure it. " + _KEEP_EACH
)
MIRROR_CAST_ON = (
    _MIRROR_CAST_SINGLE + _MIRROR_CAST_ROOM_ON + _MIRROR_CAST_POSE + _MIRROR_CAST_LIGHT_ON
) + _SILENT
MIRROR_CAST_OFF = (
    _MIRROR_CAST_SINGLE + _MIRROR_CAST_ROOM_OFF + _MIRROR_CAST_POSE + _MIRROR_CAST_LIGHT_OFF
) + _SILENT
MIRROR_CAST_ON_TOGETHER = (
    _MIRROR_CAST_TOGETHER + _MIRROR_CAST_ROOM_ON + _MIRROR_CAST_POSE + _MIRROR_CAST_LIGHT_ON
) + _SILENT
MIRROR_CAST_OFF_TOGETHER = (
    _MIRROR_CAST_TOGETHER + _MIRROR_CAST_ROOM_OFF + _MIRROR_CAST_POSE + _MIRROR_CAST_LIGHT_OFF
) + _SILENT

#: The lookbook and the walk with the character in Hikaru's place: the same
#: street, camera, and covering, with nothing of her profile left in.
_LOOK_CAST_SCENE_OFF = (
    "They stand on a quiet, tree-lined Tokyo side street beside a modern "
    "building with a white textured facade and glass doors, in soft overcast "
    "daylight."
)
_LOOK_CAST_SCENE_ON = (
    "They stand in the scene from the attached background image, matching "
    "its lighting and setting."
)
_LOOK_CAST_CAMERA = (
    " A relaxed, natural pose, smiling at the camera. Shot like a "
    "professional street-fashion photograph: an 85mm portrait lens, shallow "
    "depth of field, eye level, true-to-life color, a vertical 3:4 frame. "
    "When several cards are asked for, every card shows the same outfit in "
    "the same place, with a different pose and framing: full-length, "
    "three-quarter, and a close portrait. Their face, hair, and build stay "
    "the same in every card."
)
_LOOK_CAST_EACH = (
    " The set features every attached product: each card shows them wearing "
    "one product from the attached product images, and they never wear two "
    "attached products at once. " + _KEEP_EACH + " "
)
_LOOK_CAST_WEARS = " They wear the exact clothing from the attached product image. "
LOOKBOOK_CAST_OFF = (
    f"A photorealistic photograph of {_CAST}." + _CAST_REAL + _LOOK_CAST_WEARS
    + _LOOK_CAST_SCENE_OFF + _LOOK_CAST_CAMERA + _COVERED + " " + _KEEP
)
LOOKBOOK_CAST_ON = (
    f"A photorealistic photograph of {_CAST}." + _CAST_REAL + _LOOK_CAST_WEARS
    + _LOOK_CAST_SCENE_ON + _LOOK_CAST_CAMERA + _COVERED + " " + _KEEP
)
LOOKBOOK_CAST_OFF_TOGETHER = (
    f"A photorealistic photograph set of {_CAST}." + _CAST_REAL + _LOOK_CAST_EACH
    + _LOOK_CAST_SCENE_OFF + _LOOK_CAST_CAMERA + _COVERED
)
LOOKBOOK_CAST_ON_TOGETHER = (
    f"A photorealistic photograph set of {_CAST}." + _CAST_REAL + _LOOK_CAST_EACH
    + _LOOK_CAST_SCENE_ON + _LOOK_CAST_CAMERA + _COVERED
)

_WALK_CAST_SCENE_OFF = (
    "They walk along a Tokyo street at night, past glowing shop signs and "
    "neon, the wet pavement reflecting the lights, a handbag in one hand."
)
_WALK_CAST_SCENE_ON = (
    "They walk through the scene from the attached background image, "
    "matching its lighting and setting, a handbag in one hand."
)
_WALK_CAST_MOTION = (
    " The camera moves backward ahead of them in one smooth tracking shot as "
    "they walk toward it, glancing around and touching their hair. They slow, "
    "stop in a medium close-up, and look into the camera with their mouth "
    "closed. Photorealistic, with a shallow depth of field and true-to-life "
    "color, in a vertical 9:16 frame. Their face, hair, and build stay the "
    "same throughout."
)
_WALK_CAST_EACH = (
    " The video features every attached product: it cuts between short walks "
    "in order, and in each walk they wear one product from the attached "
    "product images, never two attached products at once. " + _KEEP_EACH + " "
)
WALK_CAST_OFF = (
    f"A vertical video of {_CAST}." + _CAST_REAL + _LOOK_CAST_WEARS
    + _WALK_CAST_SCENE_OFF + _WALK_CAST_MOTION + _COVERED + " " + _KEEP + _SILENT
)
WALK_CAST_ON = (
    f"A vertical video of {_CAST}." + _CAST_REAL + _LOOK_CAST_WEARS
    + _WALK_CAST_SCENE_ON + _WALK_CAST_MOTION + _COVERED + " " + _KEEP + _SILENT
)
WALK_CAST_OFF_TOGETHER = (
    f"A vertical video of {_CAST}." + _CAST_REAL + _WALK_CAST_EACH
    + _WALK_CAST_SCENE_OFF + _WALK_CAST_MOTION + _COVERED + _SILENT
)
WALK_CAST_ON_TOGETHER = (
    f"A vertical video of {_CAST}." + _CAST_REAL + _WALK_CAST_EACH
    + _WALK_CAST_SCENE_ON + _WALK_CAST_MOTION + _COVERED + _SILENT
)

#: Held up by the character, who is the point of featuring one: framed from
#: the waist up so their face shows above the garment. The cuts are the same.
_HELD_UP_CAST_SINGLE = (
    f" {_CAST[0].upper()}{_CAST[1:]}, in a plain black T-shirt, holds the "
    "exact garment from the attached product image up by its shoulders at "
    "chest height, facing the camera, framed from the waist up so their face "
    "is visible above it, and gives it a small shake so the fabric settles. "
    "In about fifteen seconds of hard cuts: the garment is turned around to "
    "show the back; the camera moves close on the collar and its zipper or "
    "buttons; one sleeve is held out to show its pocket or seams; a hand "
    "pulls the cuff to show its snap, button, or elastic; and the hem is "
    "turned up to show its fastening. The fabric shows its real weight and "
    "texture. There is no text on screen. " + _KEEP + _SILENT
)
_HELD_UP_CAST_TOGETHER = (
    f" {_CAST[0].upper()}{_CAST[1:]}, in a plain black T-shirt, holds up each "
    "garment from the attached product images in turn, by its shoulders at "
    "chest height, facing the camera, framed from the waist up so their face "
    "is visible above it. Each garment gets a small shake, a turn to show the "
    "back, and hard-cut close-ups of its collar, a sleeve or cuff, and its "
    "hem, before the next one takes its place, so every attached product "
    "appears once. There is no text on screen. " + _KEEP_EACH + _SILENT
)
HELD_UP_CAST_OFF = _HELD_UP_SCENE_OFF + _HELD_UP_CAST_SINGLE
HELD_UP_CAST_ON = _HELD_UP_SCENE_ON + _HELD_UP_CAST_SINGLE
HELD_UP_CAST_OFF_TOGETHER = _HELD_UP_SCENE_OFF + _HELD_UP_CAST_TOGETHER
HELD_UP_CAST_ON_TOGETHER = _HELD_UP_SCENE_ON + _HELD_UP_CAST_TOGETHER

#: (recipe, background, together) -> the wording with a character in it.
_CAST_WORDINGS: dict[tuple[str, bool, bool], str] = {
    ("mirror_selfie", False, False): MIRROR_CAST_OFF,
    ("mirror_selfie", True, False): MIRROR_CAST_ON,
    ("mirror_selfie", False, True): MIRROR_CAST_OFF_TOGETHER,
    ("mirror_selfie", True, True): MIRROR_CAST_ON_TOGETHER,
    ("hikaru_lookbook", False, False): LOOKBOOK_CAST_OFF,
    ("hikaru_lookbook", True, False): LOOKBOOK_CAST_ON,
    ("hikaru_lookbook", False, True): LOOKBOOK_CAST_OFF_TOGETHER,
    ("hikaru_lookbook", True, True): LOOKBOOK_CAST_ON_TOGETHER,
    ("hikaru_walk", False, False): WALK_CAST_OFF,
    ("hikaru_walk", True, False): WALK_CAST_ON,
    ("hikaru_walk", False, True): WALK_CAST_OFF_TOGETHER,
    ("hikaru_walk", True, True): WALK_CAST_ON_TOGETHER,
    ("held_up_garment", False, False): HELD_UP_CAST_OFF,
    ("held_up_garment", True, False): HELD_UP_CAST_ON,
    ("held_up_garment", False, True): HELD_UP_CAST_OFF_TOGETHER,
    ("held_up_garment", True, True): HELD_UP_CAST_ON_TOGETHER,
}

#: What each recipe can be, and whether a second wording exists.
#: `background` is "optional" when the recipe has a wording with and without
#: a background image, and "required" when it only has the first. Every
#: recipe is optional now: the operator asked that none insist on one.
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
        "background": "optional",
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
    #: Hikaru is who these show when no Library character is chosen; with one
    #: chosen, that character takes her place (see `_CAST_WORDINGS`).
    "hikaru_lookbook": {
        "kinds": ("image", "carousel"),
        "background": "optional",
        "variants": (),
        "default_character": "hikaru",
    },
    "character_editorial_carousel": {
        "kinds": ("carousel",),
        "background": "optional",
        "variants": (),
        "requires_character": True,
        "card_count": 5,
        "max_products": 5,
    },
    "ceiling_fan_transition": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
    },
    "hanger_rail_reveal": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
    },
    "hikaru_walk": {
        "kinds": ("video",),
        "background": "optional",
        "variants": (),
        "default_character": "hikaru",
    },
}

#: The hand-written recipes above. Everything after them in RECIPES is mined.
HAND_WRITTEN: tuple[str, ...] = tuple(RECIPES)

#: Filter-chip tags for the hand-written recipes, from the same vocabulary the
#: mined ones use, so a chip narrows every recipe rather than hiding these.
#: Their names stay the dialog's own translated strings.
_BUILTIN_FACETS: dict[str, dict[str, Any]] = {
    "bed_flat_lay": {"cast": "none", "together": "frame", "framing": ["flat_lay"], "setting": ["bedroom"], "mood": ["clean_minimal"], "layout": ["single_hero"]},
    "styled_flat_lay": {"cast": "none", "together": "frame", "framing": ["flat_lay", "detail"], "setting": ["living_room"], "mood": ["cozy"], "layout": ["detail_to_full"]},
    "sleepwear_lookbook": {"cast": "person", "together": "card", "pose": ["standing", "seated"], "framing": ["detail", "full_length"], "setting": ["bedroom", "studio"], "mood": ["elegant"], "layout": ["story_sequence", "detail_to_full"]},
    "hikaru_lookbook": {"cast": "person", "together": "card", "pose": ["standing"], "framing": ["full_length", "half_body", "close_up"], "setting": ["street_day"], "mood": ["candid"], "layout": ["story_sequence"]},
    "character_editorial_carousel": {"cast": "person", "together": "card", "pose": ["standing", "seated", "over_shoulder"], "framing": ["full_length", "half_body", "detail"], "setting": ["studio"], "mood": ["clean_minimal"], "layout": ["story_sequence"]},
    "mannequin_transition": {"cast": "person", "together": "card", "pose": ["walking"], "framing": ["full_length"], "setting": ["corridor"], "mood": ["playful"], "layout": ["single_hero"]},
    "mirror_selfie": {"cast": "person", "together": "card", "pose": ["selfie"], "framing": ["full_length"], "setting": ["bedroom"], "mood": ["candid"], "layout": ["single_hero"]},
    "mannequin_walkaround": {"cast": "none", "together": "card", "framing": ["detail", "full_length"], "setting": ["studio"], "mood": ["clean_minimal"], "layout": ["detail_to_full"]},
    "flat_lay_reveal": {"cast": "none", "together": "frame", "framing": ["flat_lay", "detail"], "setting": ["living_room"], "mood": ["cozy"], "layout": ["detail_to_full"]},
    "held_up_garment": {"cast": "person", "together": "card", "pose": ["holding_prop"], "framing": ["detail"], "setting": ["studio"], "mood": ["candid"], "layout": ["detail_to_full"]},
    "ceiling_fan_transition": {"cast": "person", "together": "card", "pose": ["reclining"], "framing": ["full_length"], "setting": ["living_room"], "mood": ["playful"], "layout": ["single_hero"]},
    "hanger_rail_reveal": {"cast": "none", "together": "card", "framing": ["detail"], "setting": ["studio"], "mood": ["clean_minimal"], "layout": ["detail_to_full"]},
    "hikaru_walk": {"cast": "person", "together": "card", "pose": ["walking"], "framing": ["full_length", "half_body"], "setting": ["street_night"], "mood": ["cinematic"], "layout": ["single_hero"]},
}


def builtin_catalogue() -> list[dict[str, Any]]:
    """The hand-written recipes in the shape the picker filters, without labels."""
    from trendrelay_api.product_creative_library import FACETS

    rows = []
    for recipe in HAND_WRITTEN:
        spec, tags = RECIPES[recipe], _BUILTIN_FACETS[recipe]
        rows.append({
            "id": recipe,
            "builtin": True,
            "kind": spec["kinds"][0],
            "kinds": list(spec["kinds"]),
            "card_count": spec.get("card_count"),
            "max_products": spec.get("max_products") or 8,
            "cast": tags["cast"],
            "together": tags["together"],
            "requires_character": bool(spec.get("requires_character")),
            "facets": {
                "format": list(spec["kinds"]),
                "cast": [tags["cast"]],
                "products": [tags["together"]],
                **{name: list(tags.get(name) or []) for name in FACETS},
            },
            "label": {},
            "help": {},
            "preview": "",
        })
    return rows


def _merge_library() -> None:
    from trendrelay_api.product_creative_library import library, spec

    for recipe, entry in library().items():
        if recipe in RECIPES:
            raise ValueError(f"The mined recipe {recipe!r} reuses a hand-written id.")
        RECIPES[recipe] = spec(entry)


_merge_library()

_PROMPTS: dict[tuple[str, bool, str | None], str] = {
    ("bed_flat_lay", False, None): BED_FLAT_LAY_OFF,
    ("bed_flat_lay", True, None): BED_FLAT_LAY_ON,
    ("mannequin_transition", False, None): MANNEQUIN_OFF,
    ("mannequin_transition", True, None): MANNEQUIN_ON,
    ("mirror_selfie", True, "female"): MIRROR_FEMALE,
    ("mirror_selfie", True, "male"): MIRROR_MALE,
    ("mirror_selfie", False, "female"): MIRROR_FEMALE_OFF,
    ("mirror_selfie", False, "male"): MIRROR_MALE_OFF,
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
    ("character_editorial_carousel", False, None): CHARACTER_EDITORIAL_OFF,
    ("character_editorial_carousel", True, None): CHARACTER_EDITORIAL_ON,
    ("ceiling_fan_transition", False, None): CEILING_FAN_OFF,
    ("ceiling_fan_transition", True, None): CEILING_FAN_ON,
    ("hanger_rail_reveal", False, None): HANGER_RAIL_OFF,
    ("hanger_rail_reveal", True, None): HANGER_RAIL_ON,
    ("hikaru_walk", False, None): HIKARU_WALK_OFF,
    ("hikaru_walk", True, None): HIKARU_WALK_ON,
}

_TOGETHER: dict[tuple[str, bool, str | None], str] = {
    ("bed_flat_lay", False, None): BED_FLAT_LAY_OFF_TOGETHER,
    ("bed_flat_lay", True, None): BED_FLAT_LAY_ON_TOGETHER,
    ("mannequin_transition", False, None): MANNEQUIN_OFF_TOGETHER,
    ("mannequin_transition", True, None): MANNEQUIN_ON_TOGETHER,
    ("mirror_selfie", True, "female"): MIRROR_FEMALE_TOGETHER,
    ("mirror_selfie", True, "male"): MIRROR_MALE_TOGETHER,
    ("mirror_selfie", False, "female"): MIRROR_FEMALE_OFF_TOGETHER,
    ("mirror_selfie", False, "male"): MIRROR_MALE_OFF_TOGETHER,
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
    ("character_editorial_carousel", False, None): CHARACTER_EDITORIAL_OFF_TOGETHER,
    ("character_editorial_carousel", True, None): CHARACTER_EDITORIAL_ON_TOGETHER,
    ("ceiling_fan_transition", False, None): CEILING_FAN_OFF_TOGETHER,
    ("ceiling_fan_transition", True, None): CEILING_FAN_ON_TOGETHER,
    ("hanger_rail_reveal", False, None): HANGER_RAIL_OFF_TOGETHER,
    ("hanger_rail_reveal", True, None): HANGER_RAIL_ON_TOGETHER,
    ("hikaru_walk", False, None): HIKARU_WALK_OFF_TOGETHER,
    ("hikaru_walk", True, None): HIKARU_WALK_ON_TOGETHER,
}


def resolve_prompt(
    recipe: str,
    *,
    background: bool,
    variant: str | None = None,
    together: bool = False,
    character: bool = False,
) -> str:
    """The prompt a person reviews, for this recipe and this background switch.

    `together` selects the group wording. False returns the single-product
    script unchanged. `character` says a Library character is featured: the
    recipes that describe a person of their own then describe that character
    instead (`_CAST_WORDINGS`); every other recipe's wording is unchanged and
    the caller appends the character's line. Raises ValueError when the
    combination is not one of the wordings.
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
    if spec.get("library"):
        from trendrelay_api.product_creative_library import resolve

        return resolve(
            recipe, background=bool(background), together=bool(together),
            character=bool(character),
        )
    if character:
        cast = _CAST_WORDINGS.get((recipe, bool(background), bool(together)))
        if cast is not None:
            return _with_expression(cast, recipe, spec)
    key = (recipe, bool(background), variant if spec["variants"] else None)
    prompt = (_TOGETHER if together else _PROMPTS).get(key)
    if prompt is None:
        raise ValueError("That recipe has no wording for this background choice.")
    if _BUILTIN_FACETS[recipe]["cast"] == "person":
        return _with_expression(prompt, recipe, spec)
    return prompt


def _with_expression(prompt: str, recipe: str, spec: dict[str, Any]) -> str:
    """Append the acting line the mined recipes carry, so built-ins match them."""
    from trendrelay_api.product_creative_library import expression_direction

    kinds = spec["kinds"]
    kind = "carousel" if "carousel" in kinds else kinds[0]
    return prompt + " " + expression_direction(_BUILTIN_FACETS[recipe].get("mood", []), kind=kind)
