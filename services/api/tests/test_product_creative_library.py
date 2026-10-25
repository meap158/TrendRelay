"""The mined recipe library: what every entry must carry, and how it reads."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from trendrelay_api.product_creative_library import (
    CAST,
    CLEAN,
    CLEAN_LABELLED,
    COVERED,
    DEFAULT_MODEL,
    KEEP,
    KEEP_ACCESSORY,
    KEEP_ACCESSORY_EACH,
    KEEP_EACH,
    KEEP_PRODUCT,
    KEEP_PRODUCT_EACH,
    LIBRARY_DIR,
    LOCALES,
    MODEST,
    SILENT,
    catalogue,
    library,
    vocabulary,
)
from trendrelay_api.product_creative_models import ProductCreativeDraft
from trendrelay_api.product_creative_recipes import HAND_WRITTEN, RECIPES, resolve_prompt

MINED = sorted(library())
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_the_library_loads_and_never_reuses_a_hand_written_id() -> None:
    assert len(MINED) >= 15
    assert not set(MINED) & set(HAND_WRITTEN)
    for recipe in MINED:
        assert re.fullmatch(r"[a-z][a-z0-9_]{2,63}", recipe)
        assert RECIPES[recipe]["library"] is True


#: What each product type is promised, alone and in a group.
PROMISES = {
    "apparel": (KEEP, KEEP_EACH),
    "accessory": (KEEP_ACCESSORY, KEEP_ACCESSORY_EACH),
    "beauty": (KEEP_PRODUCT, KEEP_PRODUCT_EACH),
    "goods": (KEEP_PRODUCT, KEEP_PRODUCT_EACH),
    "food": (KEEP_PRODUCT, KEEP_PRODUCT_EACH),
}


@pytest.mark.parametrize("recipe", MINED)
def test_every_wording_keeps_the_product_and_the_rules(recipe: str) -> None:
    entry = library()[recipe]
    kind = entry["kinds"][0]
    for product_type in RECIPES[recipe]["fits"]:
        worn = product_type == "apparel"
        for background in (False, True):
            for together in (False, True):
                for character in (False, True):
                    prompt = resolve_prompt(
                        recipe, background=background, together=together,
                        character=character, product_type=product_type,
                    )
                    assert len(prompt) <= 4000
                    assert "  " not in prompt
                    assert PROMISES[product_type][1 if together else 0] in prompt
                    assert ("attached background image" in prompt) is background
                    # A label is part of the product; only clothing bans all text.
                    assert (CLEAN if worn else CLEAN_LABELLED) in prompt
                    if entry["cast"] == "person":
                        assert (COVERED if worn else MODEST) in prompt
                        assert (COVERED in prompt) is worn
                        assert (CAST in prompt) is character
                        assert (DEFAULT_MODEL in prompt) is not character
                    if kind == "video":
                        assert SILENT in prompt
                    if kind == "carousel":
                        assert f"Create exactly {len(entry['beats'])} separate" in prompt
                        assert f"Card {len(entry['beats'])}:" in prompt
    # The background switch replaces the recipe's own set, not adds to it.
    assert entry["scene"] not in resolve_prompt(
        recipe, background=True, together=False, character=False
    )


def test_a_recipe_for_other_products_never_reads_as_clothing() -> None:
    """A type a recipe does not fit - unknown, or a mixed group - reads as one
    the recipe was written for, never as a garment to wear."""
    others = [r for r in MINED if "apparel" not in RECIPES[r]["fits"]]
    assert others
    for recipe in others:
        for product_type in ("unknown", "apparel"):
            prompt = resolve_prompt(
                recipe, background=False, together=False, character=False,
                product_type=product_type,
            )
            assert "clothing from the attached product image" not in prompt
            assert KEEP not in prompt and COVERED not in prompt
            fits = RECIPES[recipe]["fits"]
            fallback = "goods" if "goods" in fits else fits[0]
            assert PROMISES[fallback][0] in prompt


def test_the_card_count_and_product_ceiling_follow_the_entry() -> None:
    for recipe in MINED:
        entry, spec = library()[recipe], RECIPES[recipe]
        if entry["kinds"] == ["carousel"]:
            assert spec["card_count"] == len(entry["beats"])
            if entry["together"] == "card":
                assert spec["max_products"] == len(entry["beats"])
        assert spec["max_products"] <= 5 or entry["together"] == "card"


def test_a_product_only_recipe_carries_its_cast_so_a_character_is_refused() -> None:
    """The refusal itself is exercised through the API in test_characters.py."""
    product_only = [r for r in MINED if library()[r]["cast"] == "none"]
    assert product_only
    for recipe in product_only:
        assert RECIPES[recipe]["cast"] == "none"
        assert "no other people in the picture" in resolve_prompt(
            recipe, background=False, together=True, character=False
        )


def test_no_two_recipes_are_the_same_recipe_twice() -> None:
    """Distinct means a different kind, set, pose family, framing or card structure.

    Two entries with the same kind, settings, poses, framings and number of
    beats are one recipe written twice, whatever their names. Framing counts
    because a product-only picture has no poses: a macro detail and a whole
    catalogue front on the same set are different pictures."""
    seen: dict[tuple, str] = {}
    for recipe in MINED:
        entry = library()[recipe]
        key = (
            entry["kinds"][0],
            frozenset(entry["facets"].get("setting", [])),
            frozenset(entry["facets"].get("pose", [])),
            frozenset(entry["facets"].get("framing", [])),
            len(entry["beats"]),
        )
        assert key not in seen, f"{recipe} repeats {seen.get(key)}"
        seen[key] = recipe
    scenes = [library()[recipe]["scene"] for recipe in MINED]
    assert len(set(scenes)) == len(scenes)


def test_tags_and_names_come_from_the_vocabulary_in_every_language() -> None:
    words = vocabulary()
    for recipe in MINED:
        entry = library()[recipe]
        for field in ("label", "help"):
            assert all(entry[field][code].strip() for code in LOCALES)
        for facet, values in entry["facets"].items():
            assert set(values) <= set(words[facet]), (recipe, facet)
    rows = {row["id"]: row for row in catalogue()}
    assert set(rows) == set(MINED)
    for row in rows.values():
        assert row["facets"]["format"] == [row["kind"]]
        assert row["facets"]["cast"] in (["person"], ["none"])


def test_the_hand_written_recipes_are_tagged_from_the_same_vocabulary() -> None:
    """A chip narrows every recipe, so the thirteen carry tags too."""
    from trendrelay_api.product_creative_recipes import builtin_catalogue

    words = vocabulary()
    rows = builtin_catalogue()
    assert [row["id"] for row in rows] == list(HAND_WRITTEN)
    for row in rows:
        assert row["builtin"] is True
        for facet, values in row["facets"].items():
            assert set(values) <= set(words[facet]), (row["id"], facet)


def test_no_entry_names_an_account_or_a_platform_handle() -> None:
    for path in LIBRARY_DIR.glob("batch-*.json"):
        text = path.read_text(encoding="utf-8")
        assert "@" not in text
        assert "instagram" not in text.lower()
        assert not re.search(r"\b(she|her|he|his)\b", text), (
            "beats say 'they' or nothing: a chosen character records no gender"
        )


def test_the_database_check_and_its_migration_list_every_recipe() -> None:
    check = next(
        str(constraint.sqltext)
        for constraint in ProductCreativeDraft.__table__.constraints
        if getattr(constraint, "name", None) == "valid_product_creative_recipe"
    )
    for recipe in RECIPES:
        assert f"'{recipe}'" in check, recipe
    newest = sorted(MIGRATIONS.glob("*_mined_recipes_*.py"))[-1]
    spec = importlib.util.spec_from_file_location("mined_migration", newest)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert set(module._NEW) == set(RECIPES)


def test_the_vocabulary_file_is_complete() -> None:
    data = json.loads((LIBRARY_DIR / "vocabulary.json").read_text(encoding="utf-8"))
    for values in data["facets"].values():
        for texts in values.values():
            assert all(texts[code].strip() for code in LOCALES)
