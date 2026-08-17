"""The app must install and start without any of the model runtimes.

Every capability that needs a model - OpenCV for the blur, InsightFace for
identities, torch and an AGPL checkout for anonymisation - is an add-on. A
machine that only wants to download, plan and publish should not carry
gigabytes of native wheels to boot the API, and a missing one must show up as a
capability that explains itself, never as a crash on startup.

These tests simulate the bare install by making the heavy imports fail, which is
what a fresh `pip install -e services/api` actually looks like.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest

#: Everything the optional extras provide. `torch` belongs to the AGPL
#: anonymiser's own environment and should never be importable from here at all.
OPTIONAL = ("cv2", "insightface", "onnxruntime", "torch", "diffusers", "mediapipe")


@pytest.fixture
def bare_install(monkeypatch):
    """Make every optional runtime unimportable, and put them back afterwards."""
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in OPTIONAL:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    saved = {n: sys.modules[n] for n in list(sys.modules) if n.split(".")[0] in OPTIONAL}
    for name in saved:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    yield
    # monkeypatch restores __import__ and the deleted entries on its own.


def test_the_api_imports_with_no_model_runtime_installed(bare_install) -> None:
    module = importlib.import_module("trendrelay_api.main")
    assert module.app is not None


def test_importing_the_api_does_not_load_a_model_runtime(monkeypatch) -> None:
    # The cost this guards is startup time and memory on every machine that
    # never edits a video, which is most of them. Measured at import: 1.4s and
    # none of these loaded.
    #
    # Removed through monkeypatch so they come back, the way `bare_install`
    # above does it. Popping them outright left them gone for the rest of the
    # session: a later test importing insightface got a fresh import that then
    # failed on "Unable to import dependency onnxruntime", because the module
    # it wanted had been taken out from under a partly-initialised extension.
    # That surfaced as recolour previews and still-effects failing in a full
    # run and passing on their own - the shape of a pollution bug, which is
    # exactly what it was.
    for name in OPTIONAL:
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.reload(importlib.import_module("trendrelay_api.main"))
    assert [name for name in OPTIONAL if name in sys.modules] == []


def test_the_editor_still_lists_its_effects(bare_install) -> None:
    effects = importlib.import_module("trendrelay_api.integrations.effects")
    importlib.import_module("trendrelay_api.integrations.effect_render")
    described = effects.describe()
    assert described, "an effect list that empties itself is a broken editor"


def test_the_deterministic_effects_need_nothing_extra(bare_install) -> None:
    # Flip, colour, speed and the rest are ffmpeg filter strings. They must keep
    # working on a machine with no model runtime at all, or the base install
    # would have no editing to offer.
    effects = importlib.import_module("trendrelay_api.integrations.effects")
    importlib.import_module("trendrelay_api.integrations.effect_render")
    available = {item["id"] for item in effects.describe() if item["available"]}
    assert {"flip", "rotate", "colour", "speed", "trim", "volume"} <= available


def test_a_missing_runtime_explains_itself_rather_than_crashing(bare_install) -> None:
    effects = importlib.import_module("trendrelay_api.integrations.effects")
    importlib.import_module("trendrelay_api.integrations.effect_render")
    described = {item["id"]: item for item in effects.describe()}
    for effect_id in ("face_blur", "garment_recolour", "selective_face_blur"):
        entry = described[effect_id]
        assert entry["available"] is False
        assert entry["unavailable_reason"], f"{effect_id} must say what is missing"


def test_a_recipe_naming_an_unavailable_effect_is_refused(bare_install) -> None:
    effects = importlib.import_module("trendrelay_api.integrations.effects")
    importlib.import_module("trendrelay_api.integrations.effect_render")
    with pytest.raises(effects.EffectError):
        effects.read_recipe([{"effect": "face_blur", "values": {}}])


def test_the_anonymiser_is_reachable_without_torch_in_this_environment(bare_install) -> None:
    # It runs in its own virtualenv as a subprocess, so this package importing
    # it must not require any of its dependencies. If this ever fails, the AGPL
    # boundary has been crossed by an import.
    face_anon = importlib.import_module("trendrelay_api.integrations.face_anon")
    status = face_anon.runtime_status()
    assert status["isolation"] == "subprocess"
    assert isinstance(status["available"], bool)


def test_the_tool_catalogue_is_readable_without_any_runtime(bare_install) -> None:
    registry = importlib.import_module("trendrelay_api.tool_registry")
    assert registry.list_tools(), "the Tools page must survive a bare install"
