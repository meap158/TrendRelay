"""Recipes read from mined reference posts, kept as data.

`product_creative_recipes` holds the hand-written recipes, each a set of
constants. The ones here come from studying batches of reference posts
(SOP/attribution/mine-reference-recipes.md), and a batch adds a dozen or more
at once, so each recipe is a JSON entry in `product_creative_library/`, one
file per batch, and its wording is assembled from the entry by one function
rather than written out four times by hand.

An entry says what to make and leaves the promises to the assembly: the
product image is the authority for the garment, a worn garment stays covered,
a video stays silent, a carousel is separate cards rather than a collage. Those
sentences are the same ones the hand-written recipes carry, so a mined recipe
cannot quietly drop one.

Nothing in an entry names an account, a person, or a brand. Each describes a
pattern that recurred across several of the mined posts, in our own words
(docs/reference-catalogue.md).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from trendrelay_api.product_types import PRODUCT_TYPES

LIBRARY_DIR = Path(__file__).with_name("product_creative_library")

#: The facets the recipe picker filters by, in the order its chips show. The
#: first three follow from the entry's own fields; an entry writes the rest.
DERIVED_FACETS = ("format", "cast", "products", "product_type")
FACETS = ("pose", "framing", "setting", "mood", "layout")
LOCALES = ("en", "vi", "ja", "zh", "ru", "fr", "ar")
KINDS = ("image", "carousel", "video")

#: The same promises the hand-written recipes make, word for word.
KEEP = (
    "The clothing keeps its exact color, print, cut, trim, and construction "
    "from the attached product image."
)
KEEP_EACH = (
    "Each garment keeps its own color, print, cut, trim, and construction "
    "exactly as in its own product image."
)
COVERED = (
    "The garment is thick, matte, and fully opaque in every frame, including "
    "the full-length view. No nipples are visible through the fabric, and the "
    "fit shows no camel toe."
)
SILENT = (
    "Nobody speaks or mouths words. The clip has no dialogue, narration, or "
    "voice-over."
)
CLEAN = (
    "There is no text, price, logo, watermark, or caption on screen, and no "
    "other people in the picture."
)
#: The same promises for a product that is not clothing (`product_types`),
#: by type. Clothing keeps the wording above, unchanged.
SHOWN = {
    "accessory": ("They wear the exact accessory", "It shows the exact accessory"),
    "beauty": ("They hold or use the exact product", "It shows the exact product"),
    "goods": ("They hold or use the exact product", "It shows the exact product"),
    "food": ("They hold or serve the exact food or drink", "It shows the exact food or drink"),
}
KEEP_ACCESSORY = (
    "The accessory keeps its exact color, material, finish, shape, size, and "
    "details from the attached product image."
)
KEEP_ACCESSORY_EACH = (
    "Each accessory keeps its own color, material, finish, shape, size, and "
    "details exactly as in its own product image."
)
KEEP_PRODUCT = (
    "The product keeps its exact shape, size, color, finish, packaging, and "
    "label artwork from the attached product image; text printed on it is "
    "never invented, rewritten, or removed."
)
KEEP_PRODUCT_EACH = (
    "Each product keeps its own shape, size, color, finish, packaging, and "
    "label artwork exactly as in its own product image; text printed on it "
    "is never invented, rewritten, or removed."
)
#: A product's own label is part of the product, so only added text is banned.
CLEAN_LABELLED = (
    "There is no added text, price, logo, watermark, or caption on screen "
    "beyond what is printed on the product itself, and no other people in "
    "the picture."
)
#: In place of `COVERED` when the person is not wearing the product.
MODEST = (
    "Their own clothing is plain, opaque, and modest, and does not draw the "
    "eye from the product."
)
REAL = (
    "They look like a real person on camera, with natural skin texture, "
    "pores, slight facial asymmetry, and ordinary camera detail, not a doll, "
    "a plastic render, or a beauty filter."
)


def expression_direction(moods: list[str], *, kind: str) -> str:
    """Acting for a visible face. The cast or character line keeps the identity.

    Kept short on purpose: it rides on every wording with a person, and the
    longest of those must still fit the stored prompt beside a character.
    """
    if "playful" in moods:
        arc = "an amused side glance, a small laugh, then a warm direct smile"
    elif any(mood in moods for mood in ("cozy", "candid")):
        arc = "a contented soft smile, an off-camera glance, then a quietly amused look"
    else:
        arc = "a relaxed attentive gaze, a thoughtful off-camera glance, then a subtle smile"
    if kind == "carousel":
        lead = f"Across cards with a visible face, let the expression follow the action: {arc}."
    elif kind == "video":
        lead = "Where the face is visible, let it change gently with the action: relaxed attentive eyes, then a subtle natural smile."
    else:
        lead = "Where the face is visible, give it an expression the action motivates: relaxed attentive eyes and a subtle natural smile."
    return (
        f"{lead} Keep the same facial identity and natural asymmetry, changing only the "
        "eyes, brows, cheeks and mouth; no frozen smile or exaggerated surprise. Keep any "
        "expression explicitly required by the scene, and never turn views facing away "
        "toward the camera to show one."
    )


#: Who a recipe with a person shows when no Library character is chosen.
#: Deliberately plain: an entry's own scene and poses carry the look, and a
#: character chosen in the dialog replaces this line entirely.
DEFAULT_MODEL = (
    "an adult woman model in her mid-twenties with a natural, friendly look "
    "and shoulder-length dark hair"
)
CAST = "the character described below"
BACKGROUND_SET = (
    "Use the attached background image as the set, matching its lighting, "
    "perspective, and setting; do not switch to another place."
)


class LibraryError(ValueError):
    """An entry in the library files is malformed."""


def _require(entry: dict[str, Any], key: str, kind: type, where: str) -> Any:
    value = entry.get(key)
    if not isinstance(value, kind) or (isinstance(value, str | list | dict) and not value):
        raise LibraryError(f"{where}: '{key}' is missing or empty.")
    return value


def _validate(entry: dict[str, Any], where: str) -> dict[str, Any]:
    recipe = _require(entry, "id", str, where)
    where = f"{where} {recipe}"
    kinds = tuple(_require(entry, "kinds", list, where))
    if any(kind not in KINDS for kind in kinds) or len(kinds) != 1:
        # One kind per entry: the beats of a carousel, the shot of an image and
        # the motion of a video are different texts, and a recipe that claims
        # two kinds would have to stretch one of them over the other.
        raise LibraryError(f"{where}: kinds must be exactly one of {KINDS}.")
    kind = kinds[0]
    if entry.get("cast") not in ("person", "none"):
        raise LibraryError(f"{where}: cast is 'person' or 'none'.")
    if entry.get("together") not in ("card", "frame"):
        raise LibraryError(f"{where}: together is 'card' (one product per card or "
                           "shot) or 'frame' (every product in one picture).")
    _require(entry, "scene", str, where)
    beats = _require(entry, "beats", list, where)
    if kind == "carousel" and not 2 <= len(beats) <= 10:
        raise LibraryError(f"{where}: a carousel has 2 to 10 card beats.")
    if kind != "carousel" and len(beats) != 1:
        raise LibraryError(f"{where}: an image or video has one beat.")
    for field in ("label", "help"):
        texts = _require(entry, field, dict, where)
        missing = [code for code in LOCALES if not str(texts.get(code) or "").strip()]
        if missing:
            raise LibraryError(f"{where}: {field} lacks {', '.join(missing)}.")
    fits = entry.get("fits", ["apparel"])
    if (not isinstance(fits, list) or not fits
            or any(kind not in PRODUCT_TYPES or kind == "service" for kind in fits)):
        # A voucher has nothing to photograph, so no recipe fits it.
        raise LibraryError(f"{where}: fits lists product types from "
                           f"{[k for k in PRODUCT_TYPES if k != 'service']}.")
    if "apparel" in fits and len(fits) > 1:
        # Clothing is worn and promised opaque; a wording that also suits a
        # bottle would have to drop that promise or make it about a bottle.
        raise LibraryError(f"{where}: a recipe fits clothing alone or other types alone.")
    facets = _require(entry, "facets", dict, where)
    unknown = [name for name in facets if name not in FACETS]
    if unknown:
        raise LibraryError(f"{where}: unknown facets {unknown}.")
    return entry


@cache
def vocabulary() -> dict[str, dict[str, dict[str, str]]]:
    """Facet -> value -> localized chip label, from `vocabulary.json`.

    The vocabulary is fixed before a batch is annotated and every entry's tags
    must come from it: tags each batch invented for itself would make the chips
    a list of near-synonyms that filter nothing."""
    data = json.loads((LIBRARY_DIR / "vocabulary.json").read_text(encoding="utf-8"))
    facets = data.get("facets") or {}
    for name in (*DERIVED_FACETS, *FACETS):
        values = facets.get(name)
        if not isinstance(values, dict) or not values:
            raise LibraryError(f"vocabulary.json: facet '{name}' has no values.")
        for value, texts in values.items():
            missing = [code for code in LOCALES if not str((texts or {}).get(code) or "").strip()]
            if missing:
                raise LibraryError(f"vocabulary.json: {name}.{value} lacks {', '.join(missing)}.")
    return facets


@cache
def library() -> dict[str, dict[str, Any]]:
    """Every mined recipe by id, from every batch file in the library folder."""
    entries: dict[str, dict[str, Any]] = {}
    batches = sorted(LIBRARY_DIR.glob("batch-*.json"))
    if not batches:
        return entries
    words = vocabulary()
    for path in batches:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("recipes") or []:
            entry = _validate(entry, path.name)
            if entry["id"] in entries:
                raise LibraryError(f"{path.name}: {entry['id']} is defined twice.")
            for name, values in entry["facets"].items():
                stray = [value for value in values if value not in words[name]]
                if stray:
                    raise LibraryError(
                        f"{path.name} {entry['id']}: {name} {stray} are not in vocabulary.json."
                    )
            entries[entry["id"]] = entry
    return entries


def spec(entry: dict[str, Any]) -> dict[str, Any]:
    """The entry in the shape `RECIPES` uses for the hand-written recipes."""
    kind = entry["kinds"][0]
    shaped: dict[str, Any] = {
        "kinds": (kind,),
        "background": "optional",
        "variants": (),
        "library": True,
        # "none" has no person to cast, so a chosen character is refused
        # rather than appended after "no other people in the picture".
        "cast": entry["cast"],
        # An entry written before recipes knew other products is about a
        # garment: it fits clothing.
        "fits": tuple(entry.get("fits") or ("apparel",)),
    }
    if kind == "carousel":
        shaped["card_count"] = len(entry["beats"])
        # One product per card cannot hold more products than there are cards.
        shaped["max_products"] = (
            len(entry["beats"]) if entry["together"] == "card" else 5
        )
    else:
        shaped["max_products"] = 5
    if entry.get("requires_character"):
        shaped["requires_character"] = True
    return shaped


def _subject(entry: dict[str, Any], character: bool) -> str:
    who = CAST if character else DEFAULT_MODEL
    return who[0].upper() + who[1:]


def resolve(
    recipe: str, *, background: bool, together: bool, character: bool,
    product_type: str = "apparel",
) -> str:
    """The wording for one mined recipe. Raises KeyError for an unknown id.

    `product_type` picks the promises among the types the entry fits. A type
    it does not fit - `unknown`, or a group of several types - reads as
    clothing on a clothing recipe, as a plain product on one that fits goods,
    and otherwise as the first type the entry fits."""
    entry = library()[recipe]
    kind = entry["kinds"][0]
    person = entry["cast"] == "person"
    fits = tuple(entry.get("fits") or ("apparel",))
    type_ = (product_type if product_type in fits
             else "apparel" if "apparel" in fits
             else "goods" if "goods" in fits else fits[0])
    worn = type_ == "apparel"
    beats = entry["beats"]
    parts: list[str] = []
    if kind == "carousel":
        layout = entry.get("layout_note")
        parts.append(
            f"Create exactly {len(beats)} separate, finished portrait 4:5 carousel "
            "images, one complete image per card. "
            + (layout if layout else
               "Never make a contact sheet, grid, collage, or one image that "
               "holds several cards.")
        )
    elif kind == "image":
        parts.append("One finished, photorealistic portrait 4:5 photograph.")
    else:
        parts.append("A photorealistic vertical 9:16 video.")
    if person:
        subject = _subject(entry, character)
        parts.append(
            f"The person in it is {subject[0].lower() + subject[1:]}. {REAL}"
            if kind != "carousel" else
            f"Every card that shows a person shows {subject[0].lower() + subject[1:]}, "
            f"the same face, hair, and build in each. {REAL}"
        )
    if worn:
        keep, keep_each, clean = KEEP, KEEP_EACH, CLEAN
    elif type_ == "accessory":
        keep, keep_each, clean = KEEP_ACCESSORY, KEEP_ACCESSORY_EACH, CLEAN_LABELLED
    else:
        keep, keep_each, clean = KEEP_PRODUCT, KEEP_PRODUCT_EACH, CLEAN_LABELLED
    if together:
        if entry["together"] == "card":
            parts.append(
                "This set features every attached product: each card or shot "
                "takes one product in the order provided, a person never "
                + ("wears" if worn else "holds")
                + " two attached products at once, and when there are more cards "
                "than products the later cards return to the first product in "
                "the same sequence. " + keep_each
            )
        else:
            parts.append(
                "Every attached product appears together in each picture, side "
                "by side, never merged into one "
                + ("garment" if worn else "product") + ". " + keep_each
            )
    elif worn:
        parts.append(
            ("They wear" if person else "It shows")
            + " the exact clothing from the attached product image. " + KEEP
        )
    else:
        parts.append(
            SHOWN[type_][0 if person else 1] + " from the attached product image. " + keep
        )
    parts.append(BACKGROUND_SET if background else entry["scene"])
    if entry.get("camera"):
        parts.append(entry["camera"])
    if kind == "carousel":
        parts.append(" ".join(f"Card {n}: {beat}" for n, beat in enumerate(beats, 1)))
        parts.append(
            "Keep the light, color grade, and set consistent across every card."
        )
    else:
        parts.append(beats[0])
    parts.append(clean)
    if person:
        parts.append(COVERED if worn else MODEST)
        parts.append(expression_direction(entry["facets"].get("mood", []), kind=kind))
    if kind == "video":
        parts.append(SILENT)
    return " ".join(part.strip() for part in parts if part.strip())


def catalogue() -> list[dict[str, Any]]:
    """What the recipe picker shows of each mined recipe."""
    rows = []
    for recipe, entry in library().items():
        shaped = spec(entry)
        rows.append({
            "id": recipe,
            "builtin": False,
            "kind": entry["kinds"][0],
            "kinds": [entry["kinds"][0]],
            "card_count": shaped.get("card_count"),
            "max_products": shaped["max_products"],
            "cast": entry["cast"],
            "together": entry["together"],
            "requires_character": bool(entry.get("requires_character")),
            "fits": list(shaped["fits"]),
            "facets": {
                "format": [entry["kinds"][0]],
                "cast": [entry["cast"]],
                "products": [entry["together"]],
                "product_type": list(shaped["fits"]),
                **{name: list(entry["facets"].get(name) or []) for name in FACETS},
            },
            "label": dict(entry["label"]),
            "help": dict(entry["help"]),
            "preview": entry.get("preview") or "",
        })
    return rows
