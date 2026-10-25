"""Every clothing prompt stays byte for byte what drafts already store.

Pending drafts keep the prompt they were queued with, and a reviewer compares a
new draft's prompt with the old ones. Teaching the recipes about products that
are not clothing must not move a single character of the clothing wording, so
this pins a hash of every wording: each recipe, background, group, character
and mirror variant. A deliberate wording change regenerates the file:

    TRENDRELAY_REPIN_PROMPTS=1 python -m pytest tests/test_recipe_prompts_unchanged.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from trendrelay_api.product_creative_recipes import RECIPES, resolve_prompt

PINNED = Path(__file__).with_name("fixtures") / "recipe_prompt_hashes.json"


def _wordings() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for recipe in sorted(RECIPES):
        spec = RECIPES[recipe]
        for variant in spec["variants"] or (None,):
            for background in (False, True):
                if spec["background"] == "required" and not background:
                    continue
                for together in (False, True):
                    for character in (False, True):
                        try:
                            prompt = resolve_prompt(
                                recipe, background=background, variant=variant,
                                together=together, character=character,
                            )
                        except ValueError:
                            continue
                        key = f"{recipe}|{variant}|bg={background}|together={together}|character={character}"
                        hashes[key] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return hashes


def test_no_clothing_wording_moves() -> None:
    current = _wordings()
    if os.environ.get("TRENDRELAY_REPIN_PROMPTS"):
        PINNED.parent.mkdir(exist_ok=True)
        PINNED.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    pinned = json.loads(PINNED.read_text(encoding="utf-8"))
    changed = sorted(key for key in pinned if current.get(key) != pinned[key])
    assert not changed, f"{len(changed)} pinned wordings changed or vanished: {changed[:5]}"
