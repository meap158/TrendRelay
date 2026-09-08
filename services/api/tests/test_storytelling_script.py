"""Splitting a script into spoken lines.

One sentence is one shot, so this decides the whole rhythm of a narrated
video. The offsets matter as much as the text: the synthesiser's alignment is
measured in characters of the script it was handed, and a line that does not
know where it sits cannot be given a time.
"""

from __future__ import annotations

from trendrelay_api.storytelling import script


def texts(lines) -> list[str]:
    return [line.text for line in lines]


def test_a_sentence_is_a_line() -> None:
    found = script.split("The house was empty. Nobody had been there for weeks.")
    assert texts(found) == [
        "The house was empty.",
        "Nobody had been there for weeks.",
    ]


def test_every_line_indexes_the_script_it_came_from() -> None:
    """The join to the alignment, which is measured in characters.

    Asserted by reading the offsets back out of the original text rather than
    by comparing to numbers written down here: a test that agrees with itself
    about what character 21 is would pass with the offsets shifted by one.
    """
    source = "The house was empty. Nobody had been there for weeks."
    for line in script.split(source):
        assert source[line.start:line.end] == line.text


def test_a_paragraph_break_outranks_a_full_stop() -> None:
    # The writer put a beat there. Running the two into one line takes it away.
    found = script.split("He left at nine.\n\nThe car was still warm.")
    assert texts(found) == ["He left at nine.", "The car was still warm."]
    # And a paragraph break where there is no full stop is still a break.
    assert texts(script.split("A title with no stop\n\nThen the first line.")) == [
        "A title with no stop",
        "Then the first line.",
    ]


def test_a_cluster_of_marks_is_one_ending() -> None:
    assert texts(script.split("Was it him?! Nobody knew... Not for years.")) == [
        "Was it him?!",
        "Nobody knew...",
        "Not for years.",
    ]


def test_a_closing_quote_belongs_to_the_sentence_it_closes() -> None:
    found = script.split('"I never saw him." She was lying.')
    assert texts(found) == ['"I never saw him."', "She was lying."]


def test_a_full_stop_inside_something_is_not_an_ending() -> None:
    """A decimal and a domain both carry a stop that ends nothing.

    Told apart by what follows rather than by a list of exceptions: a sentence
    end is followed by a space or by the end of the script, and 3.5 is not.
    """
    assert texts(script.split("It cost 3.5 million. Paid on trendrelay.app that week.")) == [
        "It cost 3.5 million.",
        "Paid on trendrelay.app that week.",
    ]


def test_chinese_and_japanese_end_their_sentences_with_their_own_marks() -> None:
    """Without these the whole script is one line, and one enormous shot.

    Neither language uses `.`, and neither spaces its sentences apart - so the
    full-width marks are both the only signal and one that needs no space
    after it to count.
    """
    assert texts(script.split("房子是空的。已经好几个星期没人来过了。")) == [
        "房子是空的。",
        "已经好几个星期没人来过了。",
    ]
    assert texts(script.split("誰もいなかった。本当に？そうだ。")) == [
        "誰もいなかった。",
        "本当に？",
        "そうだ。",
    ]


def test_vietnamese_reads_as_the_latin_script_it_is() -> None:
    found = script.split("Căn nhà trống rỗng. Không ai đến đó suốt nhiều tuần.")
    assert texts(found) == [
        "Căn nhà trống rỗng.",
        "Không ai đến đó suốt nhiều tuần.",
    ]


def test_the_arabic_question_mark_is_its_own_character() -> None:
    # Not the Latin one, so a script that used it would otherwise never split.
    assert len(script.split("أين كان؟ لا أحد يعرف.")) == 2


def test_a_short_line_is_left_for_the_planner_to_judge() -> None:
    """"Nothing." is too brief to hold the screen, and this cannot know that.

    A character is not a unit of time, and it is a wildly different amount of
    speech between languages: six characters is a whole Chinese sentence and
    two English words. Merging on a character count merged the Chinese and it
    was the count that was wrong, not the sentence. The planner has each line's
    real duration, so it is the one that can say "too brief".
    """
    found = script.split("The room was searched twice. Nothing. They left before dawn.")
    assert texts(found) == [
        "The room was searched twice.",
        "Nothing.",
        "They left before dawn.",
    ]


def test_a_script_with_no_punctuation_at_all_is_still_one_line() -> None:
    assert texts(script.split("just some words with no ending")) == [
        "just some words with no ending",
    ]


def test_nothing_in_is_nothing_out() -> None:
    assert script.split("") == []
    assert script.split("   \n\n  \t ") == []


def test_windows_line_endings_are_paragraphs_too() -> None:
    # A script pasted from a document arrives with them, and a paragraph that
    # did not register would run two beats into one line.
    assert len(script.split("He left at nine.\r\n\r\nThe car was still warm.")) == 2
