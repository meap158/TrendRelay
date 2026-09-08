"""Storytelling: a script and a voice into a narrated video.

The sibling of AutoCut, and deliberately built as one. AutoCut turns pictures
into a video whose cuts land on a music track's beats; this turns a script into
a video whose cuts land on the narration's sentences. Everything after the cut
list is the same work, so it is the same code: both produce the `CutPlan` that
`autocut.renderer` draws.

The pieces, in dependency order:

- ``script`` splits written text into the lines a narrator says, keeping each
  line's character offsets - the alignment below is measured in characters, so
  a line that does not know where it sits cannot be timed.
- ``narration`` turns a line list plus an audio track into timed lines, from
  either of the two honest sources: the synthesiser's own alignment when the
  voice was generated, or the reviewed transcript's word timings when somebody
  recorded themselves.
- ``planner`` turns timed lines and a set of pictures into a `CutPlan` - which
  picture is on screen for which sentence, and the transition into it.

What is deliberately not here: an avatar. The format this follows is faceless
by design, and the research it comes from says the voice is what carries the
trust rather than a synthetic face.
"""
