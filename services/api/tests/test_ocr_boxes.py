"""Where each line of on-screen text sits on the frame.

The detector has always returned corners and they were read and dropped. They
are the prerequisite for putting a translation over the original: a line can
only be covered and rewritten where its corners are known, and finding them
again at render time would mean a second OCR pass over every frame.
"""

from __future__ import annotations

from types import SimpleNamespace

from trendrelay_api.media_ai import _plain_boxes, _rapidocr_text


def test_the_corners_come_back_with_the_words() -> None:
    result = SimpleNamespace(
        txts=("足球记忆", "RedBull"),
        scores=(0.98, 0.71),
        boxes=[
            [[10, 20], [110, 20], [110, 60], [10, 60]],
            [[12, 90], [90, 90], [90, 120], [12, 120]],
        ],
    )

    texts, scores, boxes = _rapidocr_text(result)

    assert texts == ["足球记忆", "RedBull"]
    assert scores == [0.98, 0.71]
    assert boxes[0] == [[10, 20], [110, 20], [110, 60], [10, 60]]


def test_a_reading_with_no_boxes_still_reads() -> None:
    # An engine build that returns no geometry costs the overlay, not the text.
    texts, scores, boxes = _rapidocr_text(SimpleNamespace(txts=("hello",), scores=(0.9,)))

    assert texts == ["hello"]
    assert boxes == []


def test_boxes_arrive_from_the_json_shape_too() -> None:
    class Payload:
        txts: tuple[str, ...] = ()
        scores: tuple[float, ...] = ()

        def to_json(self) -> dict:
            return {
                "txts": ["one"],
                "scores": [0.8],
                "boxes": [[[0, 0], [5, 0], [5, 5], [0, 5]]],
            }

    texts, _scores, boxes = _rapidocr_text(Payload())

    assert texts == ["one"]
    assert boxes == [[[0, 0], [5, 0], [5, 5], [0, 5]]]


def test_a_box_that_is_not_a_quadrilateral_is_dropped() -> None:
    # Storing it would hand a renderer something it cannot draw, and the line's
    # text is still worth keeping.
    assert _plain_boxes([[[0, 0], [1, 1], [2, 2]]]) == []


def test_corners_are_rounded_to_whole_pixels() -> None:
    # Frame coordinates. A sub-pixel corner is precision the detector never had.
    assert _plain_boxes([[[0.4, 0.6], [9.5, 0.2], [9.7, 4.4], [0.1, 4.9]]]) == [
        [[0, 1], [10, 0], [10, 4], [0, 5]]
    ]


def test_no_boxes_at_all_is_not_an_error() -> None:
    assert _plain_boxes(None) == []
