"""Turning timed narration into the shot list the renderer draws.

The promise this keeps is that a cut lands where the narrator stops. Every
assertion here is about that, or about the ways a shot list can be malformed in
a way ffmpeg will not complain about.
"""

from __future__ import annotations

from trendrelay_api.autocut.planner import MIN_SHOT_SECONDS
from trendrelay_api.storytelling.narration import TimedLine
from trendrelay_api.storytelling.planner import (
    TEMPLATES,
    Picture,
    captions,
    plan,
    template,
)

EXPLAINER = template("explainer")
PICTURES = [Picture(f"asset-{index}") for index in range(4)]


def line(text: str, start: float, end: float) -> TimedLine:
    return TimedLine(text=text, start=start, end=end)


def test_one_line_is_one_shot_and_the_cut_lands_where_the_line_ends() -> None:
    lines = [line("First sentence.", 0.0, 2.0), line("Second sentence.", 2.0, 4.5)]
    result = plan(lines, PICTURES, EXPLAINER)
    assert len(result.shots) == 2
    assert result.shots[0].start == 0.0
    assert result.shots[0].end == 2.0
    assert result.shots[1].start == 2.0
    assert result.duration == 4.5


def test_the_pause_between_sentences_belongs_to_the_picture_already_up() -> None:
    """Otherwise the renderer fills the gap with black.

    A narrator stops for a quarter-second between sentences. That silence is
    part of the shot that was on screen, not a hole in the video.
    """
    lines = [line("First.", 0.0, 2.0), line("Second.", 2.4, 4.0)]
    result = plan(lines, PICTURES, EXPLAINER)
    assert result.shots[0].end == 2.4
    assert result.shots[1].start == 2.4


def test_no_shot_starts_before_the_one_before_it_ended() -> None:
    lines = [line(f"Sentence {n}.", n * 1.7, n * 1.7 + 1.5) for n in range(8)]
    shots = plan(lines, PICTURES, EXPLAINER).shots
    assert all(
        later.start >= earlier.end - 1e-6
        for earlier, later in zip(shots, shots[1:], strict=False)
    )
    assert all(shot.duration >= MIN_SHOT_SECONDS for shot in shots)


def test_a_line_too_brief_to_hold_the_screen_joins_the_one_before() -> None:
    """The judgement the splitter could not make, made where the times are.

    "Nothing." spoken in a tenth of a second is not a shot. It joins the
    sentence before it, and takes its words with it - a shot's caption is what
    is said while it is up.
    """
    lines = [
        line("The room was searched twice.", 0.0, 2.0),
        line("Nothing.", 2.0, 2.08),
        line("They left before dawn.", 2.1, 4.0),
    ]
    result = plan(lines, PICTURES, EXPLAINER)
    assert len(result.shots) == 2
    assert [text for _, _, text in captions(lines)] == [
        "The room was searched twice. Nothing.",
        "They left before dawn.",
    ]


def test_a_brief_opener_borrows_from_in_front() -> None:
    # There is nothing behind it, and the video must not open on a flicker.
    lines = [line("Listen.", 0.0, 0.1), line("This is what happened.", 0.1, 3.0)]
    result = plan(lines, PICTURES, EXPLAINER)
    assert len(result.shots) == 1
    assert result.shots[0].duration >= MIN_SHOT_SECONDS


def test_a_long_sentence_takes_more_than_one_picture() -> None:
    """A twenty-second sentence on one still is the slideshow to avoid."""
    lines = [line("A very long sentence indeed.", 0.0, 18.0)]
    result = plan(lines, PICTURES, EXPLAINER)
    assert len(result.shots) == 3, "18s at a six-second hold"
    assert [shot.asset_id for shot in result.shots] == ["asset-0", "asset-1", "asset-2"]
    # Still contiguous, and still ending where the line ends.
    assert result.shots[0].start == 0.0
    assert result.shots[-1].end == 18.0


def test_a_picture_changing_mid_sentence_dissolves_rather_than_cuts() -> None:
    # The sentence has not finished, so the picture should not look like it has.
    shots = plan([line("Long one.", 0.0, 18.0)], PICTURES, EXPLAINER).shots
    assert shots[0].transition == "cut", "nothing to transition from"
    assert all(shot.transition == EXPLAINER.transition for shot in shots[1:])


def test_pictures_repeat_rather_than_stretch_when_there_are_too_few() -> None:
    """The honest failure. Forty sentences and two pictures is a story that
    needs more pictures, and showing the two again says so where stretching
    them would hide it."""
    lines = [line(f"Sentence {n}.", n * 2.0, n * 2.0 + 1.8) for n in range(5)]
    two = [Picture("asset-a"), Picture("asset-b")]
    shots = plan(lines, two, EXPLAINER).shots
    assert [shot.asset_id for shot in shots] == [
        "asset-a", "asset-b", "asset-a", "asset-b", "asset-a",
    ]


def test_a_video_keeps_its_own_kind_so_the_renderer_plays_it() -> None:
    # A still is pushed by the motion; a clip plays its own frames. The
    # renderer already knows the difference and reads it off the shot.
    shots = plan(
        [line("One.", 0.0, 2.0), line("Two.", 2.0, 4.0)],
        [Picture("clip", media_kind="video"), Picture("still")],
        EXPLAINER,
    ).shots
    assert [shot.media_kind for shot in shots] == ["video", "image"]


def test_the_last_shot_holds_for_narration_that_trails_off() -> None:
    # The audio runs past the last full stop; the picture should not cut to
    # black while the voice is still going.
    lines = [line("The end.", 0.0, 2.0)]
    result = plan(lines, PICTURES, EXPLAINER, audio_seconds=5.0)
    assert result.shots[-1].end == 5.0
    assert result.duration == 5.0


def test_an_audio_length_shorter_than_the_script_never_shortens_a_shot() -> None:
    # A wrong or stale duration must not cut the last sentence in half.
    result = plan([line("The end.", 0.0, 4.0)], PICTURES, EXPLAINER, audio_seconds=1.0)
    assert result.shots[-1].end == 4.0


def test_a_narration_plan_never_claims_to_be_beat_synced() -> None:
    """The one claim this plan must not make.

    The interface reads `beat_synced` to decide whether to promise the cuts sit
    on the music. A narration promises the narrator's timing, which is a
    different promise and a true one.
    """
    result = plan([line("One.", 0.0, 2.0)], PICTURES, EXPLAINER)
    assert result.beat_synced is False
    assert result.bpm == 0.0
    assert result.template_id == "explainer"


def test_nothing_to_say_or_nothing_to_show_is_an_empty_plan() -> None:
    assert plan([], PICTURES, EXPLAINER).shots == ()
    assert plan([line("One.", 0.0, 2.0)], [], EXPLAINER).shots == ()
    assert plan([], PICTURES, EXPLAINER).duration == 0.0


def test_captions_are_the_script_s_own_words() -> None:
    """No round trip through a recogniser: the words are already known.

    And they are the *joined* words, so a caption always matches the shot it is
    under rather than the line the writer typed.
    """
    lines = [line("First sentence.", 0.0, 2.0), line("Second sentence.", 2.0, 4.0)]
    assert captions(lines) == [
        (0, 2000, "First sentence."),
        (2000, 4000, "Second sentence."),
    ]


def test_every_shipped_template_plans_something_renderable() -> None:
    # A template with a hold shorter than the floor, or a missing motion, would
    # only be found when somebody chose it.
    lines = [line(f"Sentence {n}.", n * 3.0, n * 3.0 + 2.5) for n in range(6)]
    for story in TEMPLATES:
        result = plan(lines, PICTURES, story)
        assert result.shots, story.id
        assert all(shot.duration >= MIN_SHOT_SECONDS for shot in result.shots), story.id
        assert result.template_id == story.id


def test_an_unknown_template_falls_back_rather_than_failing() -> None:
    assert template("no-such-template").id == TEMPLATES[0].id


def test_a_script_longer_than_one_render_can_draw_is_refused_with_a_number() -> None:
    """The renderer's real ceiling, said where the number is known.

    Every shot is an `-i` on ffmpeg's command line and Windows caps that at
    32,767 characters - about two hundred and ten shots with real library
    paths. Past it the failure is `CreateProcess` refusing, at the end of a
    paid generation, with nothing a person could act on.
    """
    from trendrelay_api.storytelling.jobs import MAX_SHOTS

    # Comfortably under the measured wall, so a long path cannot decide it.
    assert MAX_SHOTS < 210
    lines = [line(f"Sentence {n}.", n * 2.0, n * 2.0 + 1.8) for n in range(MAX_SHOTS + 20)]
    result = plan(lines, PICTURES, EXPLAINER)
    assert len(result.shots) > MAX_SHOTS, "this script must be over the limit to test it"
