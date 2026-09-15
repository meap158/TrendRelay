"""What the music bed actually does under a voice, measured rather than asserted.

`test_autocut_renderer` covers the graph the renderer builds; a filter chain
that reads correctly can still be inaudible, or loud enough to bury the
narration. The ducking constants in `renderer.py` were chosen from first
principles and checked only against tones, which proves the command ran and
nothing about what came out of it.

So this renders a real four-second video through the real path and measures
the result in dB. The bed is a low tone and the voice a high one, so a lowpass
keeps the bed and drops the voice - and every level is read through the *same*
lowpass, so the filter's own shape cancels out of the comparisons.

It runs ffmpeg, which the rest of this suite mostly does not. It is the only
way to answer the question it asks, it uses the smallest frame that still
renders, and it skips itself where the pinned runtime is absent - the pattern
`test_effect_render` already uses for the same reason.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from trendrelay_api.autocut.beat_analysis import BeatGrid
from trendrelay_api.autocut.planner import plan_cuts
from trendrelay_api.autocut.renderer import MUSIC_BED_GAIN, RenderRequest, render
from trendrelay_api.autocut.templates import get_template
from trendrelay_api.integrations import effects

#: A bed well below the lowpass and a voice well above it, so the two separate
#: cleanly with a filter rather than with arithmetic.
MUSIC_HZ, VOICE_HZ, LOWPASS_HZ = 110, 1500, 300

#: Windows to read the level in, clear of the things that would confuse it: the
#: compressor's 20 ms attack and 400 ms release, and the bed's own fade over the
#: last 1.5 s of the video. The voice speaks for the first second and rests for
#: the second, so these sit inside one of each.
SPEAKING = (0.4, 0.9)
RESTING = (1.5, 1.9)

#: How far the bed must drop while the voice speaks. Measured at 9.6 dB on
#: 2026-09-15; the floor is set well under that, because the claim being kept
#: is "the music gets out of the way", not a particular number of decibels.
MIN_DUCK_DB = 6.0
#: And where the bed sits when nothing is being said, against the track itself.
#: `MUSIC_BED_GAIN` alone is -12 dB; the compressor is still letting go of the
#: last word, so the measured -15 dB is the honest figure. Bounded both ways:
#: too quiet is a bed nobody hears, too loud is one that buries the narration.
BED_UNDER_TRACK_DB = (-20.0, -10.0)


def _run(args: list[str]) -> str:
    done = subprocess.run(
        [str(effects.FFMPEG), "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True,
        timeout=300,
    )
    return done.stderr.decode("utf-8", "replace")


def _bed_level(source: Path, window: tuple[float, float]) -> float:
    """The mean level of everything under the lowpass, inside a window, in dBFS."""
    start, end = window
    stderr = _run([
        "-i", str(source),
        "-af", f"atrim=start={start}:end={end},lowpass=f={LOWPASS_HZ},volumedetect",
        "-f", "null", "-",
    ])
    found = re.search(r"mean_volume:\s*(-?[\d.]+) dB", stderr)
    assert found, f"volumedetect said nothing about the level:\n{stderr[-800:]}"
    return float(found.group(1))


@pytest.fixture
def narrated_over_music(tmp_path: Path) -> tuple[Path, Path]:
    """A rendered video whose voice speaks a second and rests a second, and the
    bed it was laid over, so the two can be compared."""
    if not effects.FFMPEG.is_file():
        pytest.skip("the pinned FFmpeg runtime is not installed")

    voice, music = tmp_path / "voice.wav", tmp_path / "music.wav"
    _run([
        "-f", "lavfi", "-i", f"sine=frequency={VOICE_HZ}:duration=4",
        "-af", "volume='if(lt(mod(t,2),1),1,0)':eval=frame", str(voice),
    ])
    _run(["-f", "lavfi", "-i", f"sine=frequency={MUSIC_HZ}:duration=4", str(music)])
    for name, colour in (("a", "red"), ("b", "blue")):
        _run([
            "-f", "lavfi", "-i", f"color=c={colour}:s=160x120:d=1",
            "-frames:v", "1", str(tmp_path / f"{name}.png"),
        ])

    grid = BeatGrid(bpm=120.0, beats=tuple(round(i * 0.5, 4) for i in range(12)), duration=4.0)
    plan = plan_cuts(get_template("breathe"), grid, ["a", "b"])
    assert plan.duration == 4.0, "the windows below assume a four-second video"

    rendered = tmp_path / "mixed.mp4"
    render(effects.FFMPEG, RenderRequest(
        plan=plan,
        image_paths={"a": tmp_path / "a.png", "b": tmp_path / "b.png"},
        audio_path=voice,
        music_path=music,
        destination=rendered,
        width=160, height=288, preview=True,
    ))
    assert rendered.is_file() and rendered.stat().st_size > 0
    return rendered, music


def test_the_bed_drops_while_the_voice_speaks_and_comes_back_after(
    narrated_over_music: tuple[Path, Path],
) -> None:
    rendered, music = narrated_over_music

    speaking = _bed_level(rendered, SPEAKING)
    resting = _bed_level(rendered, RESTING)

    duck = resting - speaking
    assert duck >= MIN_DUCK_DB, (
        f"the music only dropped {duck:.1f} dB under the voice, which is not "
        "getting out of its way"
    )
    # And it is a duck, not a mute: a bed nobody can hear between sentences is
    # the same video as one with no music in it.
    assert resting > -50.0, f"the bed is inaudible even in the pause ({resting:.1f} dB)"


def test_the_bed_sits_under_the_track_it_was_made_from(
    narrated_over_music: tuple[Path, Path],
) -> None:
    """A track is mastered to be listened to; a bed is not. Both bounds matter -
    too loud buries the narration, too quiet is a video that may as well be
    silent - so the level is held between them rather than merely turned down."""
    rendered, music = narrated_over_music

    under = _bed_level(rendered, RESTING) - _bed_level(music, SPEAKING)

    low, high = BED_UNDER_TRACK_DB
    assert low <= under <= high, (
        f"the bed sits {under:.1f} dB under its own track, outside the "
        f"{low:.0f} to {high:.0f} dB this was tuned for"
    )
    # The gain that does most of that work, named so a change to it lands here
    # rather than in somebody's ears six months later.
    assert pytest.approx(0.25) == MUSIC_BED_GAIN
