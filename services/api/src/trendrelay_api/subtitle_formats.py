"""Writing cues out as subtitle files, styled or plain.

Three formats, because they answer three different questions. SRT is what every
editor and platform will accept and carries no styling at all. WebVTT is what a
browser plays natively, which is what makes a preview possible without
rendering a video. ASS is the one that carries the look - fonts, outlines,
shadows, position, and per-word highlighting - and is what FFmpeg's libass
filter burns into a frame.

The styling lives here rather than in the cue engine on purpose: where a line
breaks is a fact about the speech, and whether it is yellow is not.

Two details worth stating because both are silent-corruption bugs otherwise.
ASS colours are `&HAABBGGRR` - the channels run backwards from RGB, and the
first byte is *transparency*, so `00` is opaque and `FF` is invisible. And
braces open an override block in ASS, so a brace inside somebody's speech has
to be escaped or the rest of the line vanishes from the screen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from unicodedata import east_asian_width

from trendrelay_api.subtitles import Cue, Layout

#: Where a subtitle sits, on the numeric keypad layout ASS uses: 1-3 are the
#: bottom row, 4-6 the middle, 7-9 the top.
ALIGNMENT = {
    "bottom-left": 1, "bottom": 2, "bottom-right": 3,
    "left": 4, "middle": 5, "right": 6,
    "top-left": 7, "top": 8, "top-right": 9,
}

#: 1 draws an outline and a shadow; 3 draws an opaque box behind the text.
BORDER_OUTLINE = 1
BORDER_BOX = 3


@dataclass(frozen=True)
class Style:
    """How a subtitle looks. Everything a caller may reasonably want to change."""

    name: str = "TrendRelay"
    font: str = "Arial"
    #: In points against `play_height`, not pixels, so a style keeps its
    #: proportions whichever resolution it is burned into.
    size: int = 48
    bold: bool = True
    italic: bool = False
    #: `#RRGGBB`. Converted to ASS's reversed byte order on the way out.
    colour: str = "#FFFFFF"
    #: Draws the outline - and, with `border=BORDER_BOX`, fills the box. That
    #: is the format's own quirk rather than a choice made here: an opaque box
    #: is painted in the *outline* colour and sized by the *outline* width, so
    #: a boxed style with `outline=0` draws no box at all.
    outline_colour: str = "#000000"
    #: 0-255 of transparency on the outline, and so on the box.
    outline_alpha: int = 0
    #: The shadow's colour. Not the box - see `outline_colour`.
    back_colour: str = "#000000"
    #: The colour a word takes while it is being spoken. Only used by the
    #: word-highlight styles, which need `Layout.max_words` set to match.
    highlight_colour: str = "#FFD400"
    outline: float = 3.0
    shadow: float = 0.0
    border: int = BORDER_OUTLINE
    #: 0-255 of transparency on the box or shadow behind the text.
    back_alpha: int = 0
    alignment: str = "bottom"
    margin_h: int = 60
    margin_v: int = 80
    #: Extra space between characters, which is how most social captions get
    #: their look as much as the font does.
    spacing: float = 0.0
    uppercase: bool = False
    #: Highlight the word currently being spoken rather than showing the cue
    #: as one static block. Needs word timings to mean anything.
    highlight_active_word: bool = False


#: Ready-made looks. The first is the broadcast default; the rest are the
#: social-video shapes, which differ from it in kind and not just in colour -
#: they show fewer words for longer and lean on the highlight to carry the
#: timing, so each one carries the `Layout` it needs alongside the `Style`.
PRESETS: dict[str, tuple[Style, Layout]] = {
    "broadcast": (
        Style(name="Broadcast", font="Arial", size=42, bold=False, outline=2.0, shadow=1.0),
        Layout(),
    ),
    "word-pop": (
        Style(
            name="WordPop", font="Arial Black", size=64, outline=5.0,
            alignment="middle", highlight_colour="#FFD400", highlight_active_word=True,
        ),
        Layout(max_words=3, break_on_sentence=False, min_duration_ms=200, max_cps=99.0),
    ),
    "one-word": (
        Style(
            name="OneWord", font="Arial Black", size=78, outline=6.0,
            alignment="middle", uppercase=True,
            # Nothing to pick out: the cue *is* the word being spoken, so a
            # highlight colour would be the only colour on screen.
            highlight_active_word=False,
        ),
        # The timing is the whole trick, and every field here is load-bearing.
        #
        # `min_gap_ms=0` so one word replaces the next with no blank frame
        # between them. At the default 84ms that is two or three black frames
        # per word at 30fps - a strobe rather than a caption.
        #
        # `min_duration_ms` equal to `max_duration_ms` makes each word reach
        # for the next one and stop where it starts: `_fit_timings` extends a
        # short cue up to the following cue's start, then caps whatever is
        # left. So a word holds until the next is spoken, and in a silence it
        # holds for a beat and leaves rather than hanging there.
        #
        # `max_cps` is off because reading speed is a two-line-of-prose idea.
        # One word is read at a glance, and enforcing 17 characters a second
        # would stretch "extraordinarily" over the three words after it.
        Layout(
            max_words=1, min_gap_ms=0, min_duration_ms=1200, max_duration_ms=1200,
            max_cps=99.0, break_on_sentence=False,
        ),
    ),
    "karaoke": (
        Style(
            name="Karaoke", font="Arial Black", size=54, outline=4.0,
            highlight_colour="#38E07B", highlight_active_word=True,
        ),
        Layout(max_words=6, min_duration_ms=300, max_cps=99.0),
    ),
    "boxed": (
        Style(
            name="Boxed", font="Arial", size=44, bold=False, border=BORDER_BOX,
            # The box is the outline: its colour and its padding both come from
            # the outline fields, which is why this is not simply `outline=0`.
            outline_colour="#000000", outline_alpha=60, outline=10.0, shadow=0.0,
        ),
        Layout(),
    ),
    "bold-outline": (
        Style(name="BoldOutline", font="Impact", size=60, outline=6.0, spacing=1.0,
              uppercase=True),
        Layout(max_chars_per_line=28, max_lines=2),
    ),
    "minimal": (
        Style(name="Minimal", font="Helvetica", size=40, bold=False, outline=0.0,
              shadow=2.0, margin_v=60),
        Layout(),
    ),
    # The two shapes the VEED reference sheet shows most: dark type on a solid
    # light panel (its Social swatches), and the warm italic serif with a hard
    # edge (its Retro row). Both are reachable with the fields the format
    # already has - the box is the outline, exactly as `boxed` documents.
    "social-box": (
        Style(
            name="SocialBox", font="Arial Black", size=46, colour="#111111",
            border=BORDER_BOX, outline_colour="#FFFFFF", outline_alpha=0,
            outline=12.0, shadow=0.0,
        ),
        Layout(max_chars_per_line=26, max_lines=2),
    ),
    "retro-pop": (
        Style(
            name="RetroPop", font="Georgia", size=52, italic=True,
            colour="#FFE83D", outline_colour="#000000", outline=4.0, shadow=3.0,
        ),
        Layout(max_chars_per_line=30, max_lines=2),
    ),
}


# --- timestamps ---------------------------------------------------------------


def srt_time(ms: int) -> str:
    hours, minutes, seconds, milli = _parts(ms)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milli:03d}"


def vtt_time(ms: int) -> str:
    hours, minutes, seconds, milli = _parts(ms)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milli:03d}"


def ass_time(ms: int) -> str:
    """ASS keeps centiseconds and a single-digit hour."""
    hours, minutes, seconds, milli = _parts(ms)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{milli // 10:02d}"


def _parts(ms: int) -> tuple[int, int, int, int]:
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, milli = divmod(rest, 1000)
    return hours, minutes, seconds, milli


# --- colours ------------------------------------------------------------------


def ass_colour(value: str, alpha: int = 0) -> str:
    """`#RRGGBB` to `&HAABBGGRR&`, which reverses the channels.

    `alpha` is transparency rather than opacity: 0 is solid, 255 is invisible.
    """
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise ValueError(f"a colour must be #RRGGBB, not {value!r}")
    try:
        red, green, blue = (int(text[at:at + 2], 16) for at in (0, 2, 4))
    except ValueError as error:
        raise ValueError(f"a colour must be #RRGGBB, not {value!r}") from error
    return f"&H{max(0, min(255, alpha)):02X}{blue:02X}{green:02X}{red:02X}&"


# --- SRT and WebVTT -----------------------------------------------------------


def _payload_lines(cue: Cue) -> list[str]:
    """A cue's text as lines that cannot end the cue early.

    A blank line is the record separator in both of these formats, so one
    inside a cue's own text splits it: everything after the blank becomes a
    block with no index and no timing, and a parser either drops the rest of
    the file or reads it as garbage. Cues built by the engine never carry one -
    `wrap_lines` drops empties - but these two functions are also handed
    transcripts somebody pasted in and cues a translator rewrote, and neither
    of those has been through it.
    """
    return [line for line in cue.text.split("\n") if line.strip()]


def to_srt(cues: Sequence[Cue]) -> str:
    """The universal format. No styling survives here, by design.

    Numbering stays contiguous across a cue with nothing to say, because an
    index that skips is a malformed file rather than a gap.
    """
    blocks = []
    for cue in cues:
        lines = _payload_lines(cue)
        if not lines:
            continue
        position = len(blocks) + 1
        timing = f"{srt_time(cue.start_ms)} --> {srt_time(cue.end_ms)}"
        blocks.append(f"{position}\n{timing}\n" + "\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def escape_vtt(text: str) -> str:
    """Make text safe to put in a WebVTT cue.

    A cue's payload is parsed as markup, not as plain text: `<` opens a tag and
    `&` opens an entity, so a caption reading `Marks & Spencer` or `5 < 10`
    renders wrong or disappears. Ampersand goes first, or the escapes this adds
    would themselves be escaped.

    Escaping `>` is what also protects the arrow: a payload line containing
    `-->` looks exactly like a timing row to a parser, and `--&gt;` cannot.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_vtt(cues: Sequence[Cue]) -> str:
    """What a browser plays without a plugin, which is what makes previewing cheap."""
    blocks = []
    for cue in cues:
        lines = _payload_lines(cue)
        if not lines:
            continue
        timing = f"{vtt_time(cue.start_ms)} --> {vtt_time(cue.end_ms)}"
        blocks.append(f"{timing}\n" + "\n".join(escape_vtt(line) for line in lines))
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")


# --- ASS ----------------------------------------------------------------------


def escape_ass(text: str) -> str:
    """Make text safe to put in a Dialogue line.

    Braces open an override block, so an unescaped one swallows everything
    after it. Newlines become the explicit break ASS understands, and leading
    spaces are protected because ASS discards them otherwise.
    """
    cleaned = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
    return cleaned.replace("  ", " \\h")


#: How wide a character is, in ems, for the two cases worth telling apart.
#:
#: There is no way to measure a glyph without the font, and libass will not be
#: asked until the render. This is the estimate that decides whether a
#: replacement line fits the box it has to sit in - deliberately generous, since
#: text that overflows its cover is worse than text a little smaller than it
#: needed to be.
WIDE_EM = 1.0
NARROW_EM = 0.52

#: Never shrink a replacement past this share of its box's height.
#:
#: Below it the text is unreadable and the honest outcome is a line that
#: overflows a little, which somebody can see and fix, rather than one that
#: technically fits and cannot be read.
MIN_FITTED_SHARE = 0.35


def em_width(text: str) -> float:
    """Roughly how many ems wide a string is.

    Full-width scripts get a whole em because they occupy one; everything else
    gets about half, which is the usual average advance for Latin and Cyrillic
    text. Wrong for a line of capital Ws and wrong the safe way.
    """
    total = 0.0
    for character in text:
        total += WIDE_EM if east_asian_width(character) in ("W", "F") else NARROW_EM
    return max(total, NARROW_EM)


def fitted_size(text: str, box: tuple[float, float, float, float],
                width: int, height: int) -> int:
    """The largest font size that keeps `text` inside `box`.

    Two limits, whichever bites first: the box's height, because a line taller
    than its cover sticks out above and below it, and the box's width, because
    a line wider than its cover is the original text showing at both ends.
    """
    box_height = max(1.0, box[3] * height)
    box_width = max(1.0, box[2] * width)
    by_height = box_height * 0.82
    by_width = box_width / em_width(text)
    return max(int(box_height * MIN_FITTED_SHARE), int(min(by_height, by_width)))


def placement(cue: Cue, width: int, height: int) -> str:
    r"""The override block that puts a cue over the words it replaces.

    `\an5` centres the line on a point rather than aligning it to a corner,
    so it sits in the middle of its box whichever way it over- or
    under-fills it.
    """
    if cue.place is None:
        return ""
    x, y, box_width, box_height = cue.place
    centre_x = round((x + box_width / 2) * width)
    centre_y = round((y + box_height / 2) * height)
    size = fitted_size(cue.text, cue.place, width, height)
    return rf"{{\an5\pos({centre_x},{centre_y})\fs{size}}}"


#: How far the backdrop reaches past the measured box, as a share of the box's
#: own height on every side. The OCR box hugs the glyphs, and antialiased edges
#: and drop shadows sit just outside it - the same six per cent the cover
#: effect learned to pad by, for the same reason.
BACKDROP_PAD = 0.06


def backdrop(cue: Cue, look: Style, width: int, height: int) -> str | None:
    r"""A filled rectangle over the whole box a placed cue replaces.

    The blocking half of the replacement. The text is fitted to the box and a
    translation is rarely the same width as its original, so the text alone
    leaves the original showing at whichever end it underfills - a translated
    line floating over still-legible source text, which is worse than either
    alone. The backdrop spans the measured box (plus the pad), so what the
    original occupied is covered whatever the replacement's width came to.

    Drawn from the style's `back_colour` and `back_alpha`, which is what those
    fields mean on a placed cue: not a shadow, the patch the line sits on. It
    is a vector drawing rather than `BorderStyle=3`, because the format's own
    box hugs the *text*, and the job here is to hide the *original*.
    """
    if cue.place is None:
        return None
    x, y, box_width, box_height = cue.place
    pad = box_height * BACKDROP_PAD
    left = max(0.0, x - pad)
    top = max(0.0, y - pad)
    right = min(1.0, x + box_width + pad)
    bottom = min(1.0, y + box_height + pad)
    span_x = max(1, round((right - left) * width))
    span_y = max(1, round((bottom - top) * height))
    # `\1c` carries the colour and `\1a` the transparency, separately - the
    # combined 8-digit form is a Styles-section shape, not an override one.
    colour = ass_colour(look.back_colour)
    return (
        rf"{{\an7\pos({round(left * width)},{round(top * height)})"
        rf"\1c{colour}\1a&H{max(0, min(255, look.back_alpha)):02X}&"
        rf"\bord0\shad0\p1}}"
        rf"m 0 0 l {span_x} 0 {span_x} {span_y} 0 {span_y}{{\p0}}"
    )


def to_ass(
    cues: Sequence[Cue],
    style: Style | None = None,
    *,
    play_width: int = 1080,
    play_height: int = 1920,
) -> str:
    """A full ASS file: the look, then the cues.

    `play_width`/`play_height` are the resolution the style was designed
    against. libass scales everything to the real frame from these, so passing
    the video's own dimensions keeps a 48pt caption 48pt whatever it is burned
    into.
    """
    look = style or Style()
    header = _ass_header(look, play_width, play_height)
    events = []
    for cue in cues:
        # A placed cue blocks before it speaks: the backdrop on the layer
        # below, the replacement text above it. Without the backdrop a
        # translation narrower than its original floats over source text the
        # reader can still see at both ends.
        cover = backdrop(cue, look, play_width, play_height)
        if cover is not None:
            events.append(_dialogue(cue.start_ms, cue.end_ms, look.name, cover))
        layer = 1 if cover is not None else 0
        if look.highlight_active_word and cue.words:
            events.extend(_highlight_events(cue, look, layer=layer))
        else:
            # The override goes on after escaping, not before: `escape_ass`
            # escapes braces, and these braces are the ones that have to
            # survive as an override block.
            events.append(_dialogue(
                cue.start_ms, cue.end_ms, look.name,
                placement(cue, play_width, play_height)
                + escape_ass(_cased(cue.text, look)),
                layer=layer,
            ))
    return header + "\n".join(events) + "\n"


def _ass_header(look: Style, width: int, height: int) -> str:
    # WrapStyle 2 turns off libass's own wrapping. The lines were already
    # broken by the cue engine, on rules libass does not know about, and
    # letting it re-wrap would silently undo that work.
    return f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {look.name},{look.font},{look.size},\
{ass_colour(look.colour)},{ass_colour(look.highlight_colour)},\
{ass_colour(look.outline_colour, look.outline_alpha)},\
{ass_colour(look.back_colour, look.back_alpha)},\
{-1 if look.bold else 0},{-1 if look.italic else 0},0,0,100,100,{look.spacing},0,\
{look.border},{look.outline},{look.shadow},{ALIGNMENT.get(look.alignment, 2)},\
{look.margin_h},{look.margin_h},{look.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _dialogue(
    start_ms: int, end_ms: int, style_name: str, text: str, *, layer: int = 0
) -> str:
    return (
        f"Dialogue: {layer},{ass_time(start_ms)},{ass_time(end_ms)},"
        f"{style_name},,0,0,0,,{text}"
    )


def _highlight_events(cue: Cue, look: Style, *, layer: int = 0) -> list[str]:
    """One event per word, with that word lit up.

    The alternative is ASS karaoke (`\\k`), which is one event and less work -
    but it fills a word progressively from its left edge, which is a singing
    effect rather than the hard word-by-word switch social captions use. Doing
    it as separate events also means the highlight lands on the word's own
    measured timing rather than on a duration accumulated from the cue's start,
    so it cannot drift across a long cue.
    """
    highlight = ass_colour(look.highlight_colour)
    base = ass_colour(look.colour)
    events: list[str] = []
    for position, word in enumerate(cue.words):
        # The word's own span, but never past where the next word begins, and
        # never past the end of the cue it belongs to.
        start = max(cue.start_ms, word.start_ms)
        if position + 1 < len(cue.words):
            end = min(cue.end_ms, max(start + 1, cue.words[position + 1].start_ms))
        else:
            end = cue.end_ms
        if end <= start:
            continue
        rendered = " ".join(
            f"{{\\c{highlight}}}{escape_ass(_cased(other.text, look))}{{\\c{base}}}"
            if index == position
            else escape_ass(_cased(other.text, look))
            for index, other in enumerate(cue.words)
        )
        events.append(_dialogue(start, end, look.name, rendered, layer=layer))
    return events


def _cased(text: str, look: Style) -> str:
    return text.upper() if look.uppercase else text
