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
    monkeypatch.setattr(face_swap, "FACES_DIR", tmp_path / "faces")
    monkeypatch.setattr(face_swap, "_SWAPPER", None)
    return tmp_path


def licensed(slot) -> None:
    record_licence("local-admin", reference="INV-2026-0042")


def with_model(slot, name: str = "inswapper-512-live.onnx") -> None:
    (slot / name).write_bytes(b"not a real model, but a file that exists")


def with_portrait(slot, name: str = "ada.jpg") -> None:
    """The third prerequisite: something to swap in."""
    (slot / "faces").mkdir(exist_ok=True)
    (slot / "faces" / name).write_bytes(b"pretend this is a JPEG")


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
    with_portrait(slot)
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
    with_portrait(slot)
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
    with_portrait(slot)
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


# --- the portrait being swapped in --------------------------------------------
#
# The model cannot run in a test, so what is exercised here is everything
# around it: which portraits are offered, which names resolve, and the pairing
# of faces to identities - which is the one piece whose failure swaps the wrong
# person rather than merely failing.


@pytest.fixture
def faces(slot):
    folder = slot / "faces"
    folder.mkdir()
    return folder


def portrait(faces, name: str = "ada.jpg") -> None:
    (faces / name).write_bytes(b"pretend this is a JPEG")


def test_portraits_dropped_in_the_folder_are_offered(faces) -> None:
    portrait(faces, "ada.jpg")
    portrait(faces, "grace_hopper.png")

    offered = face_swap.available_faces()

    assert [item["value"] for item in offered] == ["ada", "grace_hopper"]
    assert offered[1]["label"] == "grace hopper"


def test_files_that_are_not_pictures_are_not_offered(faces) -> None:
    (faces / "notes.txt").write_text("not a portrait", encoding="utf-8")

    assert face_swap.available_faces() == ()


def test_an_empty_folder_offers_nothing_rather_than_failing(faces) -> None:
    assert face_swap.available_faces() == ()


def test_a_name_reaching_outside_the_folder_selects_nothing(faces) -> None:
    """A recipe is stored and re-run, so its face name is untrusted input."""
    portrait(faces)
    (faces.parent / "licence.json").write_text("{}", encoding="utf-8")

    for attempt in ("../licence", "..\\licence", "/etc/passwd", "ada/../../licence"):
        assert face_swap.face_file(attempt) is None, attempt


def test_the_portrait_a_name_means_is_found(faces) -> None:
    portrait(faces)

    assert face_swap.face_file("ada").name == "ada.jpg"


def test_no_portrait_is_its_own_missing_step(faces) -> None:
    # Not "the model is broken". It is the one thing supplied per use rather
    # than once, so it gets said as itself.
    with_model(faces.parent)
    licensed(faces.parent)

    status = runtime_status()

    assert status["available"] is False
    assert "No portrait to swap in" in status["reason"]


def test_a_portrait_completes_the_gate(faces, monkeypatch) -> None:
    with_model(faces.parent)
    licensed(faces.parent)
    portrait(faces)
    monkeypatch.setattr(
        "trendrelay_api.integrations.face_identity.runtime_status",
        lambda: {"runtime_installed": True, "reason": None, "available": True,
                 "provider": "CPUExecutionProvider", "gpu_accelerated": False},
    )

    assert runtime_status()["available"] is True


def test_asking_for_a_portrait_that_is_not_there_names_what_is(faces) -> None:
    portrait(faces, "ada.jpg")

    with pytest.raises(FaceSwapUnavailable, match="ada"):
        face_swap.reference_face("someone-else")


# --- the settings the editor produces -----------------------------------------


def test_the_face_is_named_rather_than_pathed() -> None:
    """An absolute path in a stored recipe travels badly and reads widely."""
    settings = SwapSettings(source_face="ada")

    assert settings.source_face == "ada"
    assert "/" not in settings.source_face and "\\" not in settings.source_face


# --- who gets replaced --------------------------------------------------------
#
# The render itself needs the model and a real clip, so what is tested here is
# the decision it makes per face. It is worth isolating: every other failure in
# this module produces no output, while this one produces a finished clip with
# the wrong person's face on it.


def swap_targets(labels, subject, swap_subject):
    """The faces the renderer would replace, by index."""
    return [
        index for index, label in enumerate(labels)
        if (label == subject) == swap_subject
    ]


def test_only_the_subject_is_replaced_by_default() -> None:
    # Two people: the subject appears three times, the bystander twice.
    labels = [0, 1, 0, 1, 0]

    assert swap_targets(labels, subject=0, swap_subject=True) == [0, 2, 4]


def test_inverting_it_replaces_the_bystanders_instead() -> None:
    """The anonymise-the-crowd case, where the creator stays themselves."""
    labels = [0, 1, 0, 1, 0]

    assert swap_targets(labels, subject=0, swap_subject=False) == [1, 3]


def test_a_clip_with_one_person_replaces_only_them() -> None:
    labels = [0, 0, 0]

    assert swap_targets(labels, subject=0, swap_subject=True) == [0, 1, 2]
    assert swap_targets(labels, subject=0, swap_subject=False) == []


def test_no_identifiable_subject_replaces_nobody_rather_than_everybody() -> None:
    """An empty clip must not become a clip where everyone was replaced.

    `main_identity` returns None when there is nothing to choose, and None
    equals no label - so the comparison already declines rather than matching
    every face. Pinned because the opposite failure is unrecoverable.
    """
    labels = [0, 1, 2]

    assert swap_targets(labels, subject=None, swap_subject=True) == []


def test_the_faces_read_line_up_with_the_labels_they_were_given() -> None:
    """The pairing walk, which is where the wrong person would get swapped.

    Labels come back as one flat list over every face in the clip; the renderer
    re-walks the timeline to pair them up. Getting the cursor wrong by one
    silently shifts every face onto the next person's identity.
    """
    timeline = [["a"], [], ["b", "c"], ["d"]]
    labels = [0, 1, 0, 1]

    paired, cursor = [], 0
    for frame_faces in timeline:
        row = []
        for face in frame_faces:
            row.append((face, labels[cursor]))
            cursor += 1
        paired.append(row)

    assert paired == [[("a", 0)], [], [("b", 1), ("c", 0)], [("d", 1)]]
    assert cursor == len(labels), "every label was consumed exactly once"


# --- portraits from the library ---------------------------------------------------


@pytest.fixture
def portraits(tmp_path, monkeypatch):
    """An empty portraits folder, and a picture to put in it."""
    folder = tmp_path / "faces"
    folder.mkdir()
    monkeypatch.setattr(face_swap, "FACES_DIR", folder)
    # Unavailable, so importing does not try to load a model to verify a face.
    monkeypatch.setattr(
        face_swap, "runtime_status", lambda: {"available": False, "reason": "no model"}
    )
    picture = tmp_path / "holiday.jpg"
    picture.write_bytes(b"pretend jpeg")
    return folder, picture


def test_a_library_picture_becomes_a_choosable_portrait(portraits) -> None:
    """The library is where an operator's pictures already are.

    Making them copy one into a folder by hand made the swap feel like a
    different product from the rest of the editing suite.
    """
    folder, picture = portraits
    added = face_swap.import_portrait(picture, "Ada Lovelace")

    assert added["value"] == "ada-lovelace"
    assert (folder / "ada-lovelace.jpg").is_file()
    assert added["value"] in {item["value"] for item in face_swap.available_faces()}


def test_the_picture_is_copied_rather_than_referenced(portraits) -> None:
    """A recipe stores a portrait by name and is re-run later.

    Pointing at the library asset would break an edit the moment that asset was
    removed, and would put a workspace-scoped id into a value that is otherwise
    a filename.
    """
    folder, picture = portraits
    face_swap.import_portrait(picture, "Kept")
    picture.unlink()
    assert face_swap.face_file("kept") is not None


def test_two_pictures_with_one_name_do_not_collide(portraits) -> None:
    _folder, picture = portraits
    first = face_swap.import_portrait(picture, "Same Name")
    second = face_swap.import_portrait(picture, "Same Name")
    # Reusing the name would silently repoint an existing recipe at a different
    # person.
    assert first["value"] != second["value"]
    assert {first["value"], second["value"]} == {"same-name", "same-name-2"}


def test_a_name_cannot_escape_the_folder(portraits) -> None:
    folder, picture = portraits
    added = face_swap.import_portrait(picture, "../../etc/passwd")
    assert "/" not in added["value"] and ".." not in added["value"]
    assert face_swap.face_file(added["value"]).parent == folder


def test_a_title_of_nothing_usable_still_gets_a_name(portraits) -> None:
    _folder, picture = portraits
    assert face_swap.import_portrait(picture, "!!!")["value"] == "portrait"


def test_a_file_that_is_not_a_picture_is_refused(portraits, tmp_path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not a picture")
    with pytest.raises(face_swap.FaceSwapUnavailable, match="portrait has to be"):
        face_swap.import_portrait(clip, "A clip")


def test_a_portrait_can_be_taken_back_out(portraits) -> None:
    _folder, picture = portraits
    added = face_swap.import_portrait(picture, "Temporary")
    assert face_swap.remove_portrait(added["value"]) is True
    assert face_swap.face_file(added["value"]) is None
    # And removing one that is not there is not an error, it is a False.
    assert face_swap.remove_portrait(added["value"]) is False


def test_a_portrait_with_no_face_in_it_is_refused_at_import(portraits, monkeypatch) -> None:
    """Refused here where it can still be undone.

    Otherwise it fails at render time, which is minutes later and reads as the
    swap being broken rather than the picture being wrong.
    """
    folder, picture = portraits
    monkeypatch.setattr(face_swap, "runtime_status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(
        face_swap, "reference_face",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("no face found")),
    )
    with pytest.raises(face_swap.FaceSwapUnavailable, match="No usable face"):
        face_swap.import_portrait(picture, "Landscape")
    # And it does not leave the rejected copy behind.
    assert list(folder.iterdir()) == []


def test_the_folder_says_where_a_library_picture_can_be_sent(portraits) -> None:
    described = face_swap.faces_folder()
    assert described["import_from_library"] == "effects/face-swap/faces"
    assert ".jpg" in described["accepts"]
