"""The prompt switch is pure: background on and off are different wordings."""

from __future__ import annotations

import re

import pytest

from trendrelay_api.product_creative_recipes import (
    BED_FLAT_LAY_OFF,
    BED_FLAT_LAY_ON,
    MANNEQUIN_OFF,
    MANNEQUIN_ON,
    MIRROR_FEMALE,
    MIRROR_MALE,
    resolve_prompt,
)

_BED = re.compile(r"\bbed\b", re.IGNORECASE)


def test_the_bed_wording_names_a_bed_only_when_the_background_is_off() -> None:
    off = resolve_prompt("bed_flat_lay", background=False)
    on = resolve_prompt("bed_flat_lay", background=True)
    assert off == BED_FLAT_LAY_OFF
    assert on == BED_FLAT_LAY_ON
    assert _BED.search(off)
    assert not _BED.search(on)
    assert "hallway" not in off and "hallway" not in on


def test_the_hallway_is_only_the_mannequin_without_a_background() -> None:
    off = resolve_prompt("mannequin_transition", background=False)
    on = resolve_prompt("mannequin_transition", background=True)
    assert off == MANNEQUIN_OFF and "hallway" in off
    assert on == MANNEQUIN_ON and "hallway" not in on
    assert "attached product image" in on


def test_mirror_selfie_keeps_both_genders_and_has_no_wording_without_a_background() -> None:
    female = resolve_prompt("mirror_selfie", background=True, variant="female")
    male = resolve_prompt("mirror_selfie", background=True, variant="male")
    assert female == MIRROR_FEMALE and male == MIRROR_MALE
    assert female != male
    assert "completely obscure" in female and "completely obscure" in male
    assert "background reference" in female and "background reference" in male
    assert "hallway" not in female and "hallway" not in male
    with pytest.raises(ValueError, match="background"):
        resolve_prompt("mirror_selfie", background=False, variant="female")


def test_a_variant_on_the_bed_recipe_is_refused() -> None:
    with pytest.raises(ValueError, match="variant"):
        resolve_prompt("bed_flat_lay", background=False, variant="female")
