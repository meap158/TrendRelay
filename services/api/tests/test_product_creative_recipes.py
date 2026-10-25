"""The prompt switch is pure: background on and off are different wordings."""

from __future__ import annotations

import re

import pytest

from trendrelay_api.product_creative_recipes import (
    BED_FLAT_LAY_OFF,
    BED_FLAT_LAY_ON,
    CHARACTERS,
    HIKARU_PROFILE,
    MANNEQUIN_OFF,
    MANNEQUIN_ON,
    MIRROR_FEMALE,
    MIRROR_MALE,
    RECIPES,
    resolve_prompt,
)

_BED = re.compile(r"\bbed\b", re.IGNORECASE)


@pytest.mark.parametrize("recipe", ["character_editorial_carousel", "plush_armchair_lounge", "hikaru_walk"])
def test_character_acting_direction_preserves_identity_and_product_views(recipe: str) -> None:
    prompt = resolve_prompt(recipe, background=False, character=True)
    assert "same facial identity" in prompt
    assert "eyes, brows, cheeks and mouth" in prompt
    assert "views facing away" in prompt
    assert "expression explicitly required by the scene" in prompt
    assert len(prompt) <= 4000
    if "carousel" in RECIPES[recipe]["kinds"]:
        assert "Across cards with a visible face" in prompt


@pytest.mark.parametrize("recipe", ["bed_flat_lay", "styled_flat_lay", "draped_garment_still_life"])
def test_product_only_recipes_do_not_gain_facial_acting(recipe: str) -> None:
    assert "same facial identity" not in resolve_prompt(recipe, background=False)


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
    assert off.startswith(MANNEQUIN_OFF) and "hallway" in off
    assert on.startswith(MANNEQUIN_ON) and "hallway" not in on
    assert "attached product image" in on


def test_mirror_selfie_keeps_both_genders_with_and_without_a_background() -> None:
    female = resolve_prompt("mirror_selfie", background=True, variant="female")
    male = resolve_prompt("mirror_selfie", background=True, variant="male")
    assert female.startswith(MIRROR_FEMALE) and male.startswith(MIRROR_MALE)
    assert female != male
    assert "completely obscure" in female and "completely obscure" in male
    assert "background reference" in female and "background reference" in male
    assert "hallway" not in female and "hallway" not in male
    # Without a background it is its own room, not a refusal.
    for together in (False, True):
        for variant in ("female", "male"):
            off = resolve_prompt(
                "mirror_selfie", background=False, variant=variant, together=together,
            )
            assert "background" not in off
            assert "tall mirror in a bright, tidy bedroom" in off
            assert "completely obscure" in off
            assert "Nobody speaks" in off
    assert RECIPES["mirror_selfie"]["background"] == "optional"


def test_no_recipe_insists_on_a_background_or_refuses_a_character() -> None:
    for name, spec in RECIPES.items():
        assert spec["background"] == "optional", name
        assert "character" not in spec, name
        variant = spec["variants"][0] if spec["variants"] else None
        for kind_together in (False, True):
            resolve_prompt(name, background=False, variant=variant, together=kind_together)
            resolve_prompt(
                name, background=False, variant=variant, together=kind_together, character=True,
            )


def test_a_featured_character_replaces_the_person_a_recipe_describes() -> None:
    """One person per prompt: the character, never Hikaru or the mirror's woman too."""
    for background in (False, True):
        for together in (False, True):
            for recipe in ("hikaru_lookbook", "hikaru_walk"):
                cast = resolve_prompt(
                    recipe, background=background, together=together, character=True,
                )
                assert HIKARU_PROFILE not in cast
                assert "Japanese woman" not in cast
                assert "character described below" in cast.lower()
                assert "No nipples are visible" in cast and "not a doll" in cast
                assert " she " not in f" {cast.lower()} " and " her " not in f" {cast.lower()} "
                # Without a character it is still Hikaru, word for word.
                own = resolve_prompt(recipe, background=background, together=together)
                assert HIKARU_PROFILE in own
            mirror = resolve_prompt(
                "mirror_selfie", background=background, variant="female",
                together=together, character=True,
            )
            assert "character described below" in mirror.lower()
            assert "woman" not in mirror and "Several" not in mirror
            assert "completely obscure" in mirror
            held = resolve_prompt(
                "held_up_garment", background=background, together=together, character=True,
            )
            assert "face is visible" in held
            assert "face out of frame" not in held and "No face appears" not in held
            assert "face out of frame" in resolve_prompt(
                "held_up_garment", background=background, together=together,
            )
    # A recipe with nobody in particular keeps its own wording with a character.
    assert resolve_prompt("ceiling_fan_transition", background=False, character=True) == (
        resolve_prompt("ceiling_fan_transition", background=False)
    )


def test_a_variant_on_the_bed_recipe_is_refused() -> None:
    with pytest.raises(ValueError, match="variant"):
        resolve_prompt("bed_flat_lay", background=False, variant="female")


def test_together_is_a_sibling_and_leaves_the_single_product_scripts_alone() -> None:
    assert resolve_prompt("bed_flat_lay", background=False, together=False) == BED_FLAT_LAY_OFF
    assert resolve_prompt("bed_flat_lay", background=True, together=False) == BED_FLAT_LAY_ON
    assert resolve_prompt("mannequin_transition", background=False, together=False).startswith(MANNEQUIN_OFF)
    assert resolve_prompt("mannequin_transition", background=True, together=False).startswith(MANNEQUIN_ON)

    bed_off = resolve_prompt("bed_flat_lay", background=False, together=True)
    bed_on = resolve_prompt("bed_flat_lay", background=True, together=True)
    hallway = resolve_prompt("mannequin_transition", background=False, together=True)
    room = resolve_prompt("mannequin_transition", background=True, together=True)
    female = resolve_prompt("mirror_selfie", background=True, variant="female", together=True)
    male = resolve_prompt("mirror_selfie", background=True, variant="male", together=True)
    for prompt in (bed_off, bed_on, hallway, room, female, male):
        assert len(prompt) <= 4000
        assert "every attached product" in prompt
        assert "only this one garment" not in prompt
        assert prompt not in {
            BED_FLAT_LAY_OFF, BED_FLAT_LAY_ON, MANNEQUIN_OFF, MANNEQUIN_ON,
            MIRROR_FEMALE, MIRROR_MALE,
        }
    assert _BED.search(bed_off)
    assert not _BED.search(bed_on)
    assert "narrow beige hallway" in hallway
    assert "hallway" not in room
    assert "instantly changes" not in hallway and "instantly changes" not in room
    assert "completely obscure" in female and "completely obscure" in male
    assert female != male


_READ_BACK = (
    "mannequin_walkaround", "styled_flat_lay", "flat_lay_reveal",
    "sleepwear_lookbook", "held_up_garment", "hikaru_lookbook",
    "character_editorial_carousel", "ceiling_fan_transition",
    "hanger_rail_reveal", "hikaru_walk",
)


@pytest.mark.parametrize("recipe", _READ_BACK)
def test_every_read_back_recipe_has_four_wordings_that_keep_the_product(recipe: str) -> None:
    off = resolve_prompt(recipe, background=False)
    on = resolve_prompt(recipe, background=True)
    off_group = resolve_prompt(recipe, background=False, together=True)
    on_group = resolve_prompt(recipe, background=True, together=True)
    assert len({off, on, off_group, on_group}) == 4
    for prompt in (off, on, off_group, on_group):
        assert len(prompt) <= 4000
        assert "  " not in prompt
    for prompt in (off, on):
        assert "attached product image" in prompt
        assert "exact" in prompt
    for prompt in (off_group, on_group):
        assert "every attached product" in prompt
    assert "attached background image" in on and "attached background image" in on_group
    assert "attached background image" not in off and "attached background image" not in off_group
    with pytest.raises(ValueError, match="variant"):
        resolve_prompt(recipe, background=False, variant="female")


def test_the_read_back_recipes_offer_the_kinds_their_wording_can_hold() -> None:
    assert RECIPES["sleepwear_lookbook"]["kinds"] == ("carousel",)
    assert RECIPES["styled_flat_lay"]["kinds"] == ("image", "carousel")
    assert RECIPES["hikaru_lookbook"]["kinds"] == ("image", "carousel")
    assert RECIPES["character_editorial_carousel"]["kinds"] == ("carousel",)
    assert RECIPES["character_editorial_carousel"]["requires_character"] is True
    assert RECIPES["character_editorial_carousel"]["card_count"] == 5
    for recipe in ("mannequin_walkaround", "flat_lay_reveal", "held_up_garment",
                   "ceiling_fan_transition", "hanger_rail_reveal", "hikaru_walk"):
        assert RECIPES[recipe]["kinds"] == ("video",)
        assert "video" in resolve_prompt(recipe, background=False)


def test_every_hikaru_wording_carries_her_whole_profile() -> None:
    assert CHARACTERS["hikaru"] == HIKARU_PROFILE
    for background in (False, True):
        for together in (False, True):
            prompt = resolve_prompt("hikaru_lookbook", background=background, together=together)
            assert HIKARU_PROFILE in prompt
            assert "Her face, hair, and build stay the same in every card." in prompt
            assert "No nipples are visible" in prompt
            assert "camel toe" in prompt
            assert "not a doll" in prompt
    group = resolve_prompt("hikaru_lookbook", background=False, together=True)
    assert "never wears two attached products at once" in group
    for background in (False, True):
        for together in (False, True):
            walk = resolve_prompt("hikaru_walk", background=background, together=together)
            assert HIKARU_PROFILE in walk
            assert "No nipples are visible" in walk
            assert "camel toe" in walk
            assert "thick, matte" in walk
            assert "not a doll" in walk
            assert "phone-camera detail" in walk
            assert "looks into the camera with her mouth closed" in walk
            assert "smiles into the camera" not in walk
    walks = resolve_prompt("hikaru_walk", background=False, together=True)
    assert "never two attached products at once" in walks


def test_only_the_model_cards_of_the_lookbook_follow_the_background() -> None:
    off = resolve_prompt("sleepwear_lookbook", background=False)
    on = resolve_prompt("sleepwear_lookbook", background=True)
    for prompt in (off, on):
        assert "Card 1:" in prompt and "Card 2:" in prompt and "Every later card:" in prompt
        assert "dark wooden clothing rail against a plain white wall" in prompt
    assert "hotel suite" in off and "hotel suite" not in on


def test_every_video_wording_keeps_people_silent() -> None:
    phrase = "Nobody speaks or mouths words."
    for name, spec in RECIPES.items():
        if "video" not in spec["kinds"]:
            continue
        backgrounds = (True,) if spec["background"] == "required" else (False, True)
        variants = spec["variants"] or (None,)
        for background in backgrounds:
            for variant in variants:
                for together in (False, True):
                    prompt = resolve_prompt(
                        name, background=background, variant=variant, together=together,
                    )
                    assert prompt.count(phrase) == 1
                    assert "no dialogue, narration, or voice-over" in prompt
    for name, spec in RECIPES.items():
        if "video" in spec["kinds"]:
            continue
        backgrounds = (True,) if spec["background"] == "required" else (False, True)
        variants = spec["variants"] or (None,)
        for background in backgrounds:
            for variant in variants:
                for together in (False, True):
                    prompt = resolve_prompt(
                        name, background=background, variant=variant, together=together,
                    )
                    assert phrase not in prompt


def test_the_ceiling_fan_blade_hides_each_change() -> None:
    single = resolve_prompt("ceiling_fan_transition", background=False)
    group = resolve_prompt("ceiling_fan_transition", background=False, together=True)
    for prompt in (single, group):
        assert "directly overhead" in prompt
        assert "blade" in prompt and "hides every cut" in prompt
        assert "No nipples are visible" in prompt
        assert "camel toe" in prompt
        assert "thick, matte" in prompt
    assert "only one product, so this is the only pass" in single
    assert "until late in the clip" in single
    assert "stay where they were" in single
    assert "one new product per pass" in group
    assert "next garment from the attached product images, in order" in group
    for prompt in (single, group):
        assert "vertical centre line" in prompt
        assert "presses" in prompt and "remote" in prompt
        assert "covers her completely" in prompt
        assert "does not pan, tilt, or zoom" in prompt
        assert "No text appears on screen" in prompt
    assert "not across the bottom" in single
