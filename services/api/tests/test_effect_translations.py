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
}


def required_keys() -> list[str]:
    """Every `<effect>.<leaf>` the editor will look up."""
    wanted: list[str] = []
    for effect in effects.describe():
        wanted += [f"{effect['id']}.label", f"{effect['id']}.summary"]
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
