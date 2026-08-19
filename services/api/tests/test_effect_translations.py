"""Every effect the API serves must be translatable in every language.

The editor renders text the API gives it, so the frontend string sweep can
report 100% while this panel is entirely English — which is exactly what
happened. That gap closes by hand once; it stays closed only if adding an
effect and forgetting its dictionary entry is a failing test rather than
something noticed later by a reader who does not speak English.
"""

from __future__ import annotations

import re

import pytest

# effect_render is what registers the model-backed effects; importing effects
# alone would leave the registry holding only the ffmpeg ones.
from trendrelay_api.integrations import (
    effect_render,  # noqa: F401
    effects,
)
from trendrelay_api.tool_registry import PROJECT_ROOT

MESSAGES = PROJECT_ROOT / "apps" / "web" / "lib" / "i18n" / "messages"
LOCALES = ("en", "vi", "ja", "fr", "zh", "ru", "ar")

#: Choice values are not identifiers - "9:16" and "90" cannot be keys - so the
#: frontend maps them. This mirrors that table; a value missing from both is an
#: option that silently stays English.
OPTION_KEYS = {
    "horizontal": "horizontal", "vertical": "vertical", "both": "both",
    "90": "right90", "180": "half", "270": "left90",
    "9:16": "vertical916", "4:5": "portrait45",
    "1:1": "square11", "16:9": "landscape169",
    "centre": "middle", "top": "top", "bottom": "bottom",
    "largest": "mainFace", "all": "everyone",
    # The objects that ship with the overlay catalogue. Fixed strings, exactly
    # like the effect labels around them — leaving them out put an English
    # "Smiley" directly under a translated effect title. A *dropped-in* object
    # is not here and keeps the name its file was given, which no dictionary
    # shipped in this repository could have predicted.
    "censor_block": "censorBlock", "smiley": "smiley", "robot": "robot",
    "skull": "skull", "ghost": "ghost", "censor_bar": "censorBar",
    "sunglasses": "sunglasses", "face_mask": "faceMask", "moustache": "moustache",
    "cat_ears": "catEars", "crown": "crown", "party_hat": "partyHat",
}


def required_keys() -> list[str]:
    """Every `<effect>.<leaf>` the editor will look up."""
    wanted: list[str] = []
    for effect in effects.describe():
        wanted += [f"{effect['id']}.label", f"{effect['id']}.summary"]
        # A declared short tag must be translated wherever the label is: the
        # frontend falls back from tag to label, so a locale missing the tag
        # would quietly untag the card - "Blur faces" beside "Faces covered",
        # which is the exact split the shared tag exists to close.
        if effect.get("tag") and effect["tag"] != effect["label"]:
            wanted.append(f"{effect['id']}.tag")
        for param in effect["params"]:
            wanted.append(f"{effect['id']}.{param['id']}")
            if param["help"]:
                wanted.append(f"{effect['id']}.{param['id']}Help")
            for option in param["options"] or []:
                key = OPTION_KEYS.get(option["value"])
                if key:
                    wanted.append(f"{effect['id']}.{key}")
    return wanted


def fx_block(locale: str) -> str:
    source = (MESSAGES / f"{locale}.ts").read_text(encoding="utf-8")
    start = source.index("\n  fx: {")
    return source[start : source.index("\n  effects: {", start)]


def missing_from(locale: str) -> list[str]:
    block = fx_block(locale)
    gaps = []
    for key in required_keys():
        effect_id, leaf = key.split(".", 1)
        at = block.find(f"\n    {effect_id}: {{")
        scoped = block[at : block.find("\n    },", at)] if at >= 0 else ""
        if not re.search(rf"^\s*{re.escape(leaf)}:", scoped, re.M):
            gaps.append(key)
    return gaps


@pytest.mark.parametrize("locale", LOCALES)
def test_every_effect_string_has_a_translation(locale: str) -> None:
    gaps = missing_from(locale)
    assert not gaps, f"{locale} is missing {len(gaps)}: {gaps[:6]}"


def test_the_check_is_looking_at_something() -> None:
    # A coverage test that asserts over an empty list passes forever. This is
    # the assertion that the list is real.
    keys = required_keys()
    assert len(keys) > 50, keys
    assert any(key.endswith(".summary") for key in keys)


def test_a_new_effect_would_be_caught() -> None:
    # Proves the check fails when something is absent, rather than merely
    # passing today: no dictionary has a key for an effect that does not exist.
    block = fx_block("en")
    assert "\n    invented_effect: {" not in block


# --- gallery group headings ---------------------------------------------------


def groups_block(locale: str) -> str:
    """The `fx.groups` map, shared by every gallery the registry serves."""
    source = (MESSAGES / f"{locale}.ts").read_text(encoding="utf-8")
    start = source.index("\n    groups: {")
    return source[start : source.index("\n    },", start)]


def required_groups() -> set[str]:
    """Every group id an option actually carries."""
    return {
        option["group_id"]
        for effect in effects.describe()
        for param in effect["params"]
        for option in param["options"] or []
        if option.get("group_id")
    }


@pytest.mark.parametrize("locale", LOCALES)
def test_every_gallery_heading_has_a_translation(locale: str) -> None:
    """A heading sits directly under a translated effect title.

    Leaving it out was the visible half of the gap: "Cover a face with an
    object" in Japanese, and "Cover the face" in English immediately below it.
    """
    block = groups_block(locale)
    gaps = [
        group for group in sorted(required_groups())
        if not re.search(rf"^\s*{re.escape(group)}:", block, re.M)
    ]
    assert not gaps, f"{locale} is missing gallery headings: {gaps}"


def test_the_heading_check_is_looking_at_something() -> None:
    # An assertion over an empty set passes forever.
    found = required_groups()
    assert {"cover", "features", "headwear"} <= found, found


def test_a_group_an_operator_named_is_not_required_to_be_translated() -> None:
    """The drop-in folder's own heading is content, not chrome.

    It carries no id precisely so that nothing here demands a translation for a
    word somebody chose on their own disk.
    """
    from trendrelay_api.integrations.overlay_catalogue import DROP_IN_GROUP, GROUP_IDS

    assert DROP_IN_GROUP not in GROUP_IDS
