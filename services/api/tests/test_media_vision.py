"""What the content reader promises, without the models it runs on.

The CLIP halves are hundreds of megabytes and a test suite's business is the
reasoning around them: that the vocabulary is well-formed, that an edit to it
changes the reading's identity, and that per-frame tags aggregate into a
clip-level answer the way the docstrings claim.
"""

from __future__ import annotations

from trendrelay_api import media_vision


# --- the vocabulary -----------------------------------------------------------


def test_every_entry_answers_one_of_the_four_questions() -> None:
    """subject, scene, product or format - the campaign fields downstream."""
    allowed = {"subject", "scene", "product", "format"}
    for label, category in media_vision.VOCABULARY:
        assert category in allowed, f"{label!r} is filed under {category!r}"
        assert label == label.strip() and label, "an entry is blank or padded"


def test_no_entry_is_listed_twice() -> None:
    labels = [label for label, _category in media_vision.VOCABULARY]
    assert len(labels) == len(set(labels))


def test_editing_the_vocabulary_changes_the_readings_identity(monkeypatch) -> None:
    """The digest joins the enrichment job's signature: the same clip read
    against an extended list is a different draft, and must not content-address
    to the stale one."""
    before = media_vision.vocabulary_digest()
    monkeypatch.setattr(
        media_vision,
        "VOCABULARY",
        (*media_vision.VOCABULARY, ("a hot air balloon", "subject")),
    )
    assert media_vision.vocabulary_digest() != before


# --- aggregating per-frame tags into a clip-level answer ----------------------


def frame(timestamp_ms: int, *labels: tuple[str, float]) -> dict:
    return {
        "timestamp_ms": timestamp_ms,
        "labels": [
            {"label": label, "category": "subject", "score": score}
            for label, score in labels
        ],
    }


def test_presence_outranks_a_single_strong_frame() -> None:
    """Seen on every frame at 0.24 describes the clip better than seen once
    at 0.31."""
    tags = media_vision.aggregate_tags(
        [
            frame(0, ("a dog", 0.24), ("a cat", 0.31)),
            frame(2000, ("a dog", 0.23)),
            frame(4000, ("a dog", 0.22)),
        ]
    )

    assert [item["label"] for item in tags] == ["a dog", "a cat"]
    assert tags[0]["frames"] == 3


def test_a_tag_keeps_the_moment_it_first_appeared() -> None:
    """What lets a product tag point at the frame to check it against."""
    tags = media_vision.aggregate_tags(
        [frame(0, ("a dog", 0.25)), frame(2000, ("a cat", 0.3), ("a dog", 0.2))]
    )

    by_label = {item["label"]: item for item in tags}
    assert by_label["a dog"]["first_ms"] == 0
    assert by_label["a cat"]["first_ms"] == 2000


def test_a_tags_score_is_its_best_frame() -> None:
    tags = media_vision.aggregate_tags(
        [frame(0, ("a dog", 0.21)), frame(2000, ("a dog", 0.29))]
    )

    assert tags[0]["score"] == 0.29


def test_a_busy_clip_is_capped_rather_than_echoing_the_vocabulary() -> None:
    crowded = [
        frame(0, *[(f"thing {at}", 0.3 - at * 0.001) for at in range(40)])
    ]

    assert len(media_vision.aggregate_tags(crowded)) == media_vision.TAGS_PER_CLIP


def test_no_frames_is_no_tags_rather_than_an_error() -> None:
    assert media_vision.aggregate_tags([]) == []


# --- the wiring into the provider registry ------------------------------------


def test_the_reader_is_a_registered_provider() -> None:
    """One provider pattern, no special cases: a tool card gates it, a prepare
    step exists for it, and the enrichment job knows its module."""
    from trendrelay_api import media_ai

    assert media_ai.PROVIDER_TOOL["vision"] == "fastembed"
    assert "vision" in media_ai.PROVIDER_PREPARE
    assert "vision" in media_ai.PROVIDER_MODULES
    assert any(
        name.startswith("fastembed==") for name in media_ai.PROVIDER_PACKAGES["vision"]
    )


def test_the_tool_card_is_in_the_catalog_with_its_licence() -> None:
    from trendrelay_api.tool_registry import list_tools

    card = next((tool for tool in list_tools() if tool["id"] == "fastembed"), None)
    assert card is not None, "the fastembed card is missing from the catalog"
    assert card["license"] == "Apache-2.0"
    assert card["category"] == "Media intelligence"
