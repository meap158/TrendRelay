"""Choosing which picture is on screen for which sentence.

The difference between a slideshow with a voice over it and something that
reads as an edit. Every assertion here is either about the picture suiting the
words, or about a rule that stops a good score producing a bad edit.
"""

from __future__ import annotations

from trendrelay_api.storytelling.match import Candidate, arrange, evidence_for


def picture(asset_id: str, shows: str, **kwargs) -> Candidate:
    return Candidate(asset_id=asset_id, evidence={"what it shows": shows}, **kwargs)


def chosen(lines, candidates, **kwargs) -> list[str]:
    return [item.asset_id for item in arrange(lines, candidates, **kwargs)]


def test_a_sentence_gets_a_picture_of_what_it_is_about() -> None:
    lines = ["The kitchen was spotless.", "Outside, the rain had not stopped."]
    pictures = [
        picture("rain", "heavy rain falling on a street at night"),
        picture("kitchen", "a clean modern kitchen with marble counters"),
    ]
    # Not the order they were given - the words decide.
    assert chosen(lines, pictures) == ["kitchen", "rain"]


def test_nothing_repeats_while_something_unused_is_waiting() -> None:
    """A picture that fits twice is still worse the second time.

    It has stopped being new, and a cut to something already seen is barely a
    cut. Two pictures that fit one sentence each beat one that fits both.
    """
    lines = ["The rain kept falling.", "Rain again the next morning.", "The kitchen was cold."]
    pictures = [
        picture("rain", "rain falling on a street"),
        picture("kitchen", "a cold empty kitchen"),
        picture("street", "a wet street in the rain at dawn"),
    ]
    picked = chosen(lines, pictures)
    assert len(set(picked)) == 3, picked


def test_the_same_picture_never_lands_twice_in_a_row() -> None:
    """Two neighbouring shots of one still is not a cut.

    It is a longer shot with a fake edge in it, and it is what a pure
    best-score assignment does when one picture wins every sentence.
    """
    lines = ["Rain.", "More rain.", "Still raining.", "Rain all week."]
    pictures = [picture("rain", "rain"), picture("other", "a kitchen")]
    picked = chosen(lines, pictures)
    assert all(a != b for a, b in zip(picked, picked[1:], strict=False)), picked


def test_a_clip_too_short_for_its_sentence_loses_a_close_contest() -> None:
    """Two seconds of b-roll under a nine-second sentence loops visibly.

    Penalised rather than refused: a short clip is still better than a picture
    about something else, and the still under the same sentence just moves.
    """
    lines = ["The rain kept falling all evening."]
    short = Candidate(
        asset_id="clip", media_kind="video", duration_seconds=2.0,
        evidence={"what it shows": "rain falling"},
    )
    still = picture("still", "rain falling")
    assert chosen(lines, [short, still], durations=[9.0]) == ["still"]
    # With time to fill it, the clip is not disadvantaged.
    assert chosen(lines, [short, still], durations=[1.5])[0] in {"clip", "still"}


def test_a_word_on_every_picture_cannot_choose_between_them() -> None:
    """Rarity, the correction the overlay matcher already needed.

    "video" on all three says nothing about which; "harbour" on one says
    everything. Counting them equally lets a sentence match all three equally
    well and therefore none of them usefully.
    """
    lines = ["A quiet harbour at dawn."]
    pictures = [
        picture("a", "video of a kitchen"),
        picture("b", "video of a harbour at dawn"),
        picture("c", "video of a street"),
    ]
    assert chosen(lines, pictures) == ["b"]


def test_evidence_about_the_picture_outranks_the_filename() -> None:
    # A title is often a download's filename; a reading of what the clip shows
    # is the thing actually being matched against a sentence.
    lines = ["Rain on the window."]
    misleading = Candidate(
        asset_id="misleading", evidence={"title": "rain rain rain 7231.mp4"},
    )
    real = Candidate(
        asset_id="real", evidence={"what it shows": "rain running down a window"},
    )
    assert chosen(lines, [misleading, real]) == ["real"]


def test_stock_b_roll_is_matched_on_what_it_was_searched_for() -> None:
    # It knows nothing else about itself, and what it was searched for is the
    # most direct thing it could know.
    lines = ["The city never slept."]
    stock = Candidate(asset_id="stock", evidence={"search words": "city at night skyline"})
    other = Candidate(asset_id="other", evidence={"what it shows": "a quiet field"})
    assert chosen(lines, [stock, other]) == ["stock"]


def test_words_that_say_nothing_fall_back_to_the_order_somebody_arranged() -> None:
    """A coin toss dressed as a match is worse than the arrangement given.

    With no evidence there is nothing to be right about, so the pictures play
    in the order they were chosen - which is what the operator decided.
    """
    lines = ["One.", "Two.", "Three."]
    blank = [Candidate(asset_id=f"p{n}") for n in range(3)]
    assert chosen(lines, blank) == ["p0", "p1", "p2"]


def test_every_line_is_answered_even_with_one_picture() -> None:
    # Repeating is the honest failure; leaving a sentence with no picture is a
    # hole in the video.
    lines = ["One.", "Two.", "Three."]
    picked = arrange(lines, [picture("only", "anything")])
    assert [item.line for item in picked] == [0, 1, 2]
    assert {item.asset_id for item in picked} == {"only"}


def test_a_match_says_which_words_it_was_made_on() -> None:
    # A suggestion nobody can see the reason for is one nobody trusts twice.
    found = arrange(
        ["A quiet harbour at dawn."],
        [picture("harbour", "a harbour at dawn, boats moored")],
    )
    assert "harbour" in found[0].matched
    assert found[0].score > 0


def test_nothing_to_say_or_nothing_to_show_assigns_nothing() -> None:
    assert arrange([], [picture("a", "x")]) == []
    assert arrange(["One."], []) == []


# --------------------------------------------------------------------------- #
# Reading a Library asset. The kinds are this library's own, and getting them
# wrong is silent: every candidate simply arrives with nothing to match on.
# --------------------------------------------------------------------------- #


class FakeAsset:
    def __init__(self, **kwargs) -> None:
        self.title = kwargs.get("title", "")
        self.caption = kwargs.get("caption", "")
        self.hashtags = kwargs.get("hashtags", [])
        self.creator = kwargs.get("creator", "")
        self.engagement = kwargs.get("engagement", {})


class FakeTranscript:
    def __init__(self, kind: str, text: str, status: str = "machine") -> None:
        self.kind, self.text, self.status = kind, text, status


def test_the_transcript_kinds_this_library_stores_are_the_ones_read() -> None:
    """`vision` / `ocr` / `speech`, not a guess at what they might be called.

    A label that matches nothing does not fail - it returns empty evidence,
    and the matcher falls back to filenames and the order things were picked,
    which looks exactly like the matcher not being very good.
    """
    evidence, machine = evidence_for(
        FakeAsset(title="clip.mp4"),
        [
            FakeTranscript("vision", "a harbour at dawn, boats moored"),
            FakeTranscript("ocr", "SUNRISE"),
            FakeTranscript("speech", "we set out before six"),
        ],
    )
    assert evidence["what it shows"] == "a harbour at dawn, boats moored"
    assert evidence["on-screen text"] == "SUNRISE"
    assert evidence["spoken words"] == "we set out before six"
    assert machine == {"what it shows", "on-screen text", "spoken words"}


def test_a_checked_reading_beats_a_machine_one_of_the_same_kind() -> None:
    evidence, machine = evidence_for(
        FakeAsset(),
        [
            FakeTranscript("vision", "checked reading", status="reviewed"),
            FakeTranscript("vision", "machine reading"),
        ],
    )
    assert evidence["what it shows"] == "checked reading"
    assert "what it shows" not in machine


def test_two_revisions_of_one_reading_do_not_crowd_out_the_others() -> None:
    evidence, _ = evidence_for(
        FakeAsset(),
        [
            FakeTranscript("speech", "first pass"),
            FakeTranscript("speech", "second pass"),
            FakeTranscript("vision", "a wet street"),
        ],
    )
    assert evidence["what it shows"] == "a wet street"


def test_a_checked_reading_outweighs_a_machine_one_on_the_same_words() -> None:
    # Both say "harbour"; one of them has been read by a person.
    lines = ["A quiet harbour at dawn."]
    guessed = Candidate(
        asset_id="guessed",
        evidence={"what it shows": "a harbour at dawn"},
        machine=frozenset({"what it shows"}),
    )
    checked = Candidate(
        asset_id="checked", evidence={"what it shows": "a harbour at dawn"},
    )
    assert chosen(lines, [guessed, checked]) == ["checked"]


def test_stock_broll_keeps_full_weight_for_what_it_was_searched_for() -> None:
    # Nobody machine-read it; the words are the ones somebody typed.
    evidence, machine = evidence_for(
        FakeAsset(engagement={"searched_for": "city at night skyline"}), [],
    )
    assert evidence["search words"] == "city at night skyline"
    assert machine == frozenset()
