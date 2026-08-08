"""Telling faces apart, and the licence that gates it."""

from __future__ import annotations

import json

import numpy as np
import pytest

from trendrelay_api.integrations import effect_render, effects, face_identity  # noqa: F401
from trendrelay_api.integrations.face_identity import (
    IdentitySettings,
    cluster,
    identity_report,
    main_identity,
)


def unit(*values: float) -> np.ndarray:
    """A normalised embedding, so a dot product is the cosine similarity."""
    vector = np.array(values, dtype="float32")
    return vector / (np.linalg.norm(vector) or 1.0)


# --- the licence gate -----------------------------------------------------------


@pytest.fixture
def acknowledgement(tmp_path, monkeypatch):
    path = tmp_path / "licence.json"
    monkeypatch.setattr(face_identity, "ACKNOWLEDGEMENT_FILE", path)
    return path


def test_nothing_runs_until_the_model_licence_is_acknowledged(acknowledgement) -> None:
    # The whole reason this module is gated: the code is MIT and the models are
    # not, and this product is commercial use. An ungated capability would make
    # that decision silently on the operator's behalf.
    status = face_identity.runtime_status()
    assert status["available"] is False
    assert status["licence_acknowledged"] is False
    assert "non-commercial" in status["reason"]


def test_acknowledging_records_who_and_when(acknowledgement) -> None:
    face_identity.acknowledge_licence("local-admin")

    saved = json.loads(acknowledgement.read_text(encoding="utf-8"))
    assert saved["acknowledged"] is True
    assert saved["actor_user_id"] == "local-admin"
    assert saved["recorded_at"]
    # The terms are stored with the acceptance, so what was agreed to is a
    # matter of record rather than of whatever the source says today.
    assert "non-commercial" in saved["licence"]
    assert face_identity.licence_acknowledged() is True


def test_an_acknowledgement_can_be_withdrawn(acknowledgement) -> None:
    face_identity.acknowledge_licence("local-admin")
    face_identity.acknowledge_licence("local-admin", accepted=False)
    assert face_identity.licence_acknowledged() is False


def test_a_corrupt_acknowledgement_is_not_an_acceptance(acknowledgement) -> None:
    # Failing closed: a file that cannot be read is not consent.
    acknowledgement.write_text("{ not json", encoding="utf-8")
    assert face_identity.licence_acknowledged() is False


def test_rendering_refuses_without_the_acknowledgement(acknowledgement, tmp_path) -> None:
    with pytest.raises(face_identity.FaceIdentityUnavailable, match="non-commercial"):
        face_identity.render_selective_blur(tmp_path / "a.mp4", tmp_path / "b.mp4")


def test_the_effect_offers_itself_as_blocked_rather_than_missing(acknowledgement) -> None:
    # It stays in the catalogue and explains itself. Hiding it would leave an
    # operator unable to find out why a capability they read about is absent.
    available, reason = effects.REGISTRY["selective_face_blur"].availability()
    assert available is False
    assert "licence" in reason or "licensed" in reason


# --- picking an execution provider ----------------------------------------------


def offer(monkeypatch, *names: str) -> None:
    """Pretend onnxruntime reports these providers, in this order."""
    monkeypatch.setattr(face_identity, "available_providers",
                        lambda: [n for n in face_identity.PROVIDER_PREFERENCE if n in names])


def test_the_fastest_available_provider_wins(monkeypatch) -> None:
    # Measured on an RTX 2060: DirectML ran the detection network in 10.8ms
    # against the CPU's 58.1ms, and a whole render in 10.4s against 32.9s.
    offer(monkeypatch, "DmlExecutionProvider", "CPUExecutionProvider")
    assert face_identity.chosen_provider() == "DmlExecutionProvider"


def test_cuda_is_preferred_over_directml_when_present(monkeypatch) -> None:
    # Not installed by default - it wants about 3GB of CUDA and cuDNN wheels -
    # but adding it must not need a code change to take effect.
    offer(monkeypatch, "CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider")
    assert face_identity.chosen_provider() == "CUDAExecutionProvider"


def test_cpu_is_used_when_it_is_all_there_is(monkeypatch) -> None:
    offer(monkeypatch, "CPUExecutionProvider")
    assert face_identity.chosen_provider() == "CPUExecutionProvider"


def test_a_machine_offering_nothing_still_answers_cpu(monkeypatch) -> None:
    monkeypatch.setattr(face_identity, "available_providers", list)
    assert face_identity.chosen_provider() == "CPUExecutionProvider"


def test_cpu_can_be_demanded_explicitly(monkeypatch) -> None:
    # This is what the fallback uses after a GPU provider throws.
    offer(monkeypatch, "DmlExecutionProvider", "CPUExecutionProvider")
    assert face_identity.chosen_provider(force_cpu=True) == "CPUExecutionProvider"


def test_an_unrecognised_provider_is_not_selected(monkeypatch) -> None:
    # onnxruntime lists providers this code has never been measured against,
    # such as Azure's. Preferring one sight-unseen is not an optimisation.
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "get_available_providers",
                        lambda: ["AzureExecutionProvider", "CPUExecutionProvider"])
    assert face_identity.available_providers() == ["CPUExecutionProvider"]


def test_the_status_says_whether_the_gpu_is_in_use(monkeypatch, acknowledgement) -> None:
    offer(monkeypatch, "DmlExecutionProvider", "CPUExecutionProvider")
    status = face_identity.runtime_status()
    assert status["gpu_accelerated"] is True
    assert status["provider"] == "DmlExecutionProvider"


def test_only_the_models_this_feature_reads_are_loaded() -> None:
    # The pack also carries two landmark models and an age/gender classifier.
    # Nothing here looks at them, they cost 19% of the CPU path, and running an
    # age-and-gender classifier over passers-by is not a neutral default.
    assert face_identity.MODULES == ["detection", "recognition"]


# --- grouping faces by identity -------------------------------------------------


def test_the_same_face_across_frames_is_one_person() -> None:
    same = [unit(1, 0, 0), unit(0.99, 0.1, 0), unit(0.98, 0.15, 0)]
    assert len(set(cluster(same, 0.4))) == 1


def test_two_different_people_are_two_identities() -> None:
    faces = [unit(1, 0, 0), unit(1, 0.05, 0), unit(0, 1, 0), unit(0.05, 1, 0)]
    labels = cluster(faces, 0.4)
    assert len(set(labels)) == 2
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]


def test_strictness_decides_how_readily_faces_are_merged() -> None:
    # The control that matters when one person is being treated as several, or
    # two similar people as one.
    pair = [unit(1, 0, 0), unit(0.6, 0.8, 0)]  # similarity 0.6
    assert len(set(cluster(pair, 0.4))) == 1, "lenient: one person"
    assert len(set(cluster(pair, 0.8))) == 2, "strict: two people"


def test_an_empty_clip_has_no_identities() -> None:
    assert cluster([], 0.4) == []
    assert main_identity([]) is None
    assert identity_report([]) == []


def test_the_subject_is_whoever_appears_most() -> None:
    # A creator is in their own video more than anyone walking past them.
    labels = [0, 1, 1, 1, 2]
    assert main_identity(labels) == 1


def test_a_tie_goes_to_whoever_appeared_first() -> None:
    # Arbitrary either way, but the person the clip opens on is the better
    # guess than whichever label a dict happened to yield first.
    assert main_identity([0, 0, 1, 1]) == 0


def test_the_report_shows_the_share_each_identity_held() -> None:
    # This is what makes the subject guess checkable. A clip reported as one
    # identity when two people are plainly in it is a threshold problem, and
    # without the report the only symptom is the wrong face being blurred.
    report = identity_report([0, 1, 1, 1])
    assert report[0] == {"identity": 1, "appearances": 3, "share": 0.75}
    assert report[1] == {"identity": 0, "appearances": 1, "share": 0.25}


def test_clustering_survives_a_drifting_face() -> None:
    # A head slowly turning gives a chain of embeddings where each is close to
    # the last and the ends are far apart. Renormalising the running mean is
    # what stops the centre drifting off its own cluster.
    chain = [unit(1, step * 0.08, 0) for step in range(12)]
    assert len(set(cluster(chain, 0.4))) == 1


# --- how it is wired ------------------------------------------------------------


def test_an_unlicensed_recipe_is_refused_before_it_renders(acknowledgement) -> None:
    # The gate has to hold at the recipe layer, not only in the editor: a job
    # can be submitted straight to the API without the interface ever asking.
    with pytest.raises(effects.EffectError, match="non-commercial"):
        effects.read_recipe([{"effect": "selective_face_blur", "values": {}}])


def test_it_counts_as_a_privacy_render(acknowledgement) -> None:
    # The publish path and the Library filter both ask for `blurred` by name, so
    # a selective blur that registered as an ordinary edit would not reach them.
    face_identity.acknowledge_licence("tester")
    steps = effects.read_recipe([{"effect": "selective_face_blur", "values": {}}])
    assert effect_render.version_kind_for(steps) == "blurred"


def test_it_is_a_frame_effect_and_adds_no_filter(acknowledgement) -> None:
    face_identity.acknowledge_licence("tester")
    steps = effects.read_recipe([{"effect": "selective_face_blur", "values": {}}])
    assert effects.build_filtergraph(steps) == ([], [])
    assert effects.REGISTRY["selective_face_blur"].stage == "frame"


def test_the_default_keeps_the_subject_visible() -> None:
    assert IdentitySettings().keep_subject is True
