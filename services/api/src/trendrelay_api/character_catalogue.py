"""The built-in cast and the two prompts that make a character.

A character is an invented person kept as two pictures: a portrait, which
fixes the identity, and a consistency sheet made *from* that portrait, which
gives a generator every angle, expression and detail of the same face. The
portrait comes first because a sheet drawn from text alone drifts between
panels; drawn from one reference it holds.

Both prompts live here and nowhere else. `scripts/build_default_characters.py`
reads them to draw the built-in cast, and the in-app generator reads them to
draw a new one, so a character made later looks like it belongs to the same
set. The built-in pictures ship under `character_defaults/<slug>/` and are
copied into a workspace's Library the first time its characters are listed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Where the built-in portraits and sheets live, one folder per slug.
DEFAULTS_ROOT = Path(__file__).resolve().parent / "character_defaults"
PORTRAIT_FILE = "portrait.jpg"
SHEET_FILE = "sheet.jpg"


@dataclass(frozen=True)
class CharacterSpec:
    """One invented person, described well enough to draw a portrait."""

    slug: str
    name: str
    #: Who they are, in the words the portrait prompt uses.
    appearance: str
    #: What they wear in the portrait and on every full-body panel.
    outfit: str


DEFAULT_CHARACTERS: tuple[CharacterSpec, ...] = (
    CharacterSpec(
        "mai", "Mai",
        "a fictional Vietnamese woman aged twenty-five with long center-parted black hair, "
        "light-medium warm skin, a soft round-oval face and dark-brown eyes",
        "an ivory blouse and taupe trousers",
    ),
    CharacterSpec(
        "thao", "Thảo",
        "a fictional Vietnamese woman aged twenty-nine with a wavy shoulder-length "
        "dark-brown bob, medium tan skin, an athletic build and dark-brown eyes",
        "a pale-blue t-shirt and charcoal trousers",
    ),
    CharacterSpec(
        "an", "An",
        "a fictional Vietnamese woman aged thirty-four with black hair in a low ponytail, "
        "medium warm skin, a curvy build and dark-brown eyes",
        "a sage blouse, navy trousers and a small silver pendant",
    ),
    CharacterSpec(
        "aiko", "Aiko",
        "a Japanese woman in her mid-twenties with shoulder-length straight black hair "
        "and soft curtain bangs, fair skin with a natural flush, and dark-brown eyes",
        "a plain white crew-neck t-shirt, straight-leg blue jeans and white sneakers",
    ),
    CharacterSpec(
        "minji", "Min-ji",
        "a Korean woman in her early twenties with long dark-brown hair in loose waves, "
        "light skin, a soft oval face and dark-brown eyes",
        "a cream ribbed knit cardigan over a white camisole, a light-grey pleated midi "
        "skirt and white loafers",
    ),
    CharacterSpec(
        "wei", "Wei",
        "a Chinese man around thirty with a short textured black crop, light-medium "
        "skin, a defined jawline and dark-brown eyes",
        "a navy cotton overshirt over a white t-shirt, charcoal tapered trousers and "
        "black leather sneakers",
    ),
    CharacterSpec(
        "ploy", "Ploy",
        "a Thai woman in her late twenties with long black hair in a low ponytail, "
        "warm tan skin, high cheekbones and dark-brown eyes",
        "a loose oatmeal linen button-up shirt, wide-leg white linen trousers and "
        "tan leather sandals",
    ),
    CharacterSpec(
        "minh", "Minh",
        "a Vietnamese man in his mid-twenties with medium-length black hair swept back, "
        "light-tan skin, a slim face and dark-brown eyes",
        "a black heavyweight t-shirt, olive cargo trousers and white canvas sneakers",
    ),
    CharacterSpec(
        "bea", "Bea",
        "a Filipina woman in her early thirties with chest-length black hair in soft "
        "layers, warm golden-tan skin, a round face and dark-brown eyes",
        "a sage-green linen shirt dress with short sleeves, a thin tan belt and nude "
        "flats",
    ),
    CharacterSpec(
        "putri", "Putri",
        "an Indonesian woman in her late twenties wearing a smooth light-grey hijab "
        "that frames her face, warm medium-brown skin and dark-brown eyes",
        "the light-grey hijab, a long-sleeved beige tunic, loose cream trousers and "
        "white sneakers",
    ),
    CharacterSpec(
        "arjun", "Arjun",
        "an Indian man in his mid-thirties with short black hair, a neatly trimmed "
        "full beard, medium-brown skin and dark-brown eyes",
        "a white oxford shirt with the sleeves rolled, stone chinos and brown suede "
        "desert boots",
    ),
    CharacterSpec(
        "haruto", "Haruto",
        "a Japanese man in his mid-forties with short salt-and-pepper hair, light "
        "skin with fine lines at the eyes, and dark-brown eyes",
        "a charcoal knit polo shirt, dark-navy trousers and black leather loafers",
    ),
    CharacterSpec(
        "sofia", "Sofía",
        "a Latina woman around thirty with long curly dark-brown hair, olive skin, "
        "full brows and warm brown eyes",
        "a light-wash denim shirt tucked into black high-waisted trousers and black "
        "ankle boots",
    ),
    CharacterSpec(
        "marcus", "Marcus",
        "a Black man in his early thirties with a short taper fade, deep-brown skin, "
        "a short boxed beard and dark-brown eyes",
        "an olive bomber jacket over a grey t-shirt, black slim jeans and white "
        "leather sneakers",
    ),
    CharacterSpec(
        "emma", "Emma",
        "a white woman in her late thirties with shoulder-length auburn hair, fair "
        "skin with light freckles, and green eyes",
        "a camel trench coat over a black turtleneck, dark straight jeans and brown "
        "leather ankle boots",
    ),
)

DEFAULTS_BY_SLUG: dict[str, CharacterSpec] = {spec.slug: spec for spec in DEFAULT_CHARACTERS}


def portrait_prompt(appearance: str, outfit: str) -> str:
    """The identity shot every sheet is drawn from.

    A plain front-facing portrait on a seamless background: the face is
    large, evenly lit and unobstructed, which is what an edit model needs to
    hold an identity. Natural texture is asked for outright, because the
    default from every image model is a smoothed, retouched face.
    """
    return (
        f"Ultra-photorealistic head-and-shoulders studio portrait of an invented person: "
        f"{appearance.strip().rstrip('.')}. They wear {outfit.strip().rstrip('.')}, seen "
        "only at the collar and shoulders. Framed from mid-chest up, with the camera "
        "straight on at eye level and the face large in the upper half of the frame. "
        "They face the camera squarely with a neutral, relaxed expression and both "
        "eyes open. Neutral light-grey seamless studio background, soft even studio "
        "lighting, natural skin texture with visible pores and slight asymmetry, "
        "individual hair strands, sharp focus, 85mm portrait lens with no wide-angle "
        "distortion, true-to-life color. A real person photographed in a studio, not "
        "a render, with no heavy makeup, no beauty filter and no text."
    )


def sheet_prompt(outfit: str) -> str:
    """The consistency sheet, drawn by editing the portrait.

    The operator's character-bible brief, kept in its order (identity lock,
    layout, consistency, presentation, photorealism) but written as positive
    description with the panels grouped into rows: image models drop panels
    from a flat list of twenty-four, and they follow "do this" better than a
    long list of what not to do.
    """
    return (
        "Create a professional, ultra-photorealistic CHARACTER CONSISTENCY SHEET using "
        "the reference image as the ONLY source for the character's identity. Reproduce "
        "the EXACT SAME PERSON throughout the sheet: the same face, facial structure and "
        "proportions, eye shape and iris color, eyebrows, nose, lips, cheeks, jawline, "
        "chin, skin tone and natural skin texture, age, hairline, exact hair color, "
        "length, texture and hairstyle, and body proportions. Keep them exactly as they "
        "are, without redesigning, beautifying or reinterpreting them.\n\n"
        "Layout: one clean reference board on a neutral light-grey studio background, "
        "with thin borders between panels and a small minimal label under each view. "
        "Top row: a large front face close-up, then head-and-shoulders views FRONT, "
        "3/4 LEFT, 3/4 RIGHT, LEFT PROFILE, RIGHT PROFILE and BACK. Middle row: four "
        f"full-body views FRONT, 3/4, SIDE and BACK, wearing {outfit.strip().rstrip('.')}. "
        "Bottom row: expressions NEUTRAL, NATURAL SMILE, SOFT SMILE, LOOKING LEFT and "
        "LOOKING RIGHT, then detail close-ups EYES, EYEBROWS, NOSE, LIPS, HAIR and "
        "ACCESSORIES, and a small swatch strip for the skin tone and outfit colors. "
        "The panel labels are the only text: no title, name or caption.\n\n"
        "Where a view is not visible in the reference, reconstruct it faithfully from "
        "what is visible, adding no new distinguishing features.\n\n"
        "Presentation: a premium film-production character bible, clean editorial "
        "layout, soft realistic studio lighting, consistent camera perspective, sharp "
        "facial detail, natural skin pores, individual hair strands, realistic fabric "
        "and accessories, accurate anatomy. Ultra-photorealistic real human appearance "
        "with natural shadows and realistic eyes; real skin rather than a CGI, waxy or "
        "doll-like finish, and their own face rather than a beauty-filter face. Every "
        "panel shows the same person with one consistent identity."
    )


def draft_line(name: str, appearance: str) -> str:
    """What a creative draft's prompt gains when a character is switched on."""
    return (
        f"The person in this content is the character {name}: {appearance.strip().rstrip('.')}. "
        "The attached character portrait and consistency sheet are the identity "
        "reference; keep their face, hair, skin tone and build exactly the same, and "
        "dress them in the product shown rather than the outfit on the sheet."
    )


def default_paths(slug: str) -> tuple[Path, Path]:
    """The shipped portrait and sheet for one built-in character."""
    folder = DEFAULTS_ROOT / slug
    return folder / PORTRAIT_FILE, folder / SHEET_FILE


__all__ = [
    "DEFAULTS_BY_SLUG",
    "DEFAULTS_ROOT",
    "DEFAULT_CHARACTERS",
    "CharacterSpec",
    "default_paths",
    "draft_line",
    "portrait_prompt",
    "sheet_prompt",
]
