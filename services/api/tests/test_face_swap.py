"""The gate in front of face swapping.

Everything here tests a refusal. That is the point: the module's job until a
licensed model exists is to not run, and to say why in a way that does not send
someone to a mirror.
"""

from __future__ import annotations

import json

import pytest

from trendrelay_api.integrations import face_swap
from trendrelay_api.integrations.face_swap import (
    FaceSwapUnavailable,
    SwapSettings,
    record_licence,
    runtime_status,
)


@pytest.fixture
def slot(tmp_path, monkeypatch):
    """An empty model directory, so no test touches the real one."""
    monkeypatch.setattr(face_swap, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(face_swap, "LICENCE_FILE", tmp_path / "licence.json")
    monkeypatch.setattr(face_swap, "_SWAPPER", None)
    return tmp_path


def licensed(slot) -> None:
    record_licence("local-admin", reference="INV-2026-0042")


def with_model(slot, name: str = "inswapper-512-live.onnx") -> None:
    (slot / name).write_bytes(b"not a real model, but a file that exists")


# --- what it refuses --------------------------------------------------------


def test_nothing_runs_without_a_recorded_licence(slot) -> None:
    with_model(slot)
    status = runtime_status()
    assert status["available"] is False
    # Both footings named, because a research project and a paying one have
    # different answers and the message must not assume either.
    assert "commercial licence" in status["reason"]
    assert "research" in status["reason"]


def test_nothing_runs_without_a_model(slot) -> None:
    licensed(slot)
    status = runtime_status()
    assert status["available"] is False
    assert "No swap model is present" in status["reason"]


def test_the_refusal_does_not_point_at_a_mirror(slot) -> None:
    # The whole reason this module exists as a gate. Someone reading the error
    # must not come away thinking the fix is to find a copy somewhere.
    licensed(slot)
    reason = runtime_status()["reason"]
    assert "withdrawn" in reason
    assert "does not download" in reason
    assert "ships no mirror" in reason or "does not ship a mirror" in reason


def test_loading_refuses_before_both_are_present(slot) -> None:
    with pytest.raises(FaceSwapUnavailable):
        face_swap.swapper()


def test_a_licence_needs_a_reference_not_just_a_claim(slot) -> None:
    # "We have a licence" is not a record. An order number is.
    with pytest.raises(ValueError, match="rests on"):
        record_licence("local-admin", reference="   ")


# --- the two footings --------------------------------------------------------


def test_research_use_is_a_footing_of_its_own(slot) -> None:
    """A non-profit research project is not a company that skipped paying.

    The research licence permits exactly this, so recording it as a commercial
    licence nobody bought would make the record false in the direction that
    matters.
    """
    with_model(slot)
    record_licence("local-admin", reference="Grant 41/2026", basis="research")

    status = runtime_status()
    assert status["available"] is True
    assert status["licence_basis"] == "research"
    assert "research and evaluation only" in status["licence_terms"]


def test_the_research_footing_says_what_would_end_it(slot) -> None:
    # The condition it depends on is the thing that changes silently.
    record_licence("local-admin", reference="Grant 41/2026", basis="research")

    terms = runtime_status()["licence_terms"]
    assert "starts earning" in terms
    assert "commercial licence is required" in terms


def test_research_use_still_needs_naming_what_it_is(slot) -> None:
    """"We are a research project" with nothing behind it is not a record."""
    with pytest.raises(ValueError, match="rests on"):
        record_licence("local-admin", reference="  ", basis="research")


def test_an_invented_footing_is_refused(slot) -> None:
    with pytest.raises(ValueError, match="Unknown licence basis"):
        record_licence("local-admin", reference="whatever", basis="fair-use")


def test_a_withdrawn_licence_closes_the_gate_again(slot) -> None:
    with_model(slot)
    licensed(slot)
    assert runtime_status()["available"] is True
    record_licence("local-admin", reference="INV-2026-0042", licensed=False)
    assert runtime_status()["available"] is False


# --- what it records --------------------------------------------------------


def test_the_licence_record_names_who_what_and_when(slot) -> None:
    record_licence("local-admin", reference="INV-2026-0042")
    saved = json.loads((slot / "licence.json").read_text(encoding="utf-8"))
    assert saved["licensed"] is True
    assert saved["reference"] == "INV-2026-0042"
    assert saved["actor_user_id"] == "local-admin"
    assert "insightface" in saved["supplier"].lower()
    assert saved["recorded_at"]


def test_the_model_in_use_is_named(slot) -> None:
    # Which model is running is part of what the licence covers, so the status
    # says it rather than leaving it to whatever happens to be in the folder.
    with_model(slot, "inswapper_128.onnx")
    licensed(slot)
    assert runtime_status()["model"] == "inswapper_128.onnx"


def test_the_newer_model_wins_when_both_are_present(slot) -> None:
    with_model(slot, "inswapper_128.onnx")
    with_model(slot, "inswapper-512-live.onnx")
    licensed(slot)
    assert runtime_status()["model"] == "inswapper-512-live.onnx"


def test_consent_is_stated_separately_from_the_licence(slot) -> None:
    # A licence settles whether the model may be run. It does not settle
    # whether the footage may be published, and conflating the two is how a
    # legal question gets mistaken for a solved one.
    with_model(slot)
    licensed(slot)
    status = runtime_status()
    assert status["available"] is True
    assert "synthetic media of a real" in status["consent_note"]


def test_the_default_replaces_the_subject_rather_than_the_crowd(slot) -> None:
    assert SwapSettings().swap_subject is True
    # Shared with the identity blur, so "the subject" is the same person in both.
    from trendrelay_api.integrations.face_identity import IdentitySettings

    assert SwapSettings().match_threshold == IdentitySettings().match_threshold


# --- download mirrors, which are a different question ------------------------


def test_a_download_mirror_is_not_a_licence(tmp_path, monkeypatch) -> None:
    """Changing where a file comes from changes nothing about permission.

    hf-mirror.com and ModelScope serve the same weights from the same
    publishers, which makes them a delivery choice. A withdrawn or
    non-commercially licensed model stays withdrawn or non-commercial whichever
    host answers, and the gates are what say so - not the endpoint.
    """
    from trendrelay_api.integrations import face_anon

    monkeypatch.setattr(face_anon, "ENDPOINT_FILE", tmp_path / "endpoint")
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")
    assert face_anon.hf_endpoint() == "https://hf-mirror.com"

    monkeypatch.setattr(face_swap, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(face_swap, "LICENCE_FILE", tmp_path / "licence.json")
    # A mirror is configured and the swap gate is unmoved by it.
    assert runtime_status()["available"] is False


def test_a_mirror_must_be_https(tmp_path, monkeypatch) -> None:
    # Weights fetched over plain HTTP can be altered in transit, and a tampered
    # model fails silently rather than loudly.
    from trendrelay_api.integrations import face_anon

    monkeypatch.setattr(face_anon, "ENDPOINT_FILE", tmp_path / "endpoint")
    with pytest.raises(ValueError, match="https"):
        face_anon.save_hf_endpoint("http://mirror.example")
