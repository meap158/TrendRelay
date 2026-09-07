"""AutoCut: images into a beat-synced short-form video from a template.

The pieces, in dependency order:

- ``beat_analysis`` reads a music track's tempo and beat grid (numpy + the
  pinned ffmpeg, no heavy audio stack).
- ``templates`` is the catalogue of cut rhythms and transitions, derived from
  the hand-made @ai_videos_tiktok references, each carrying its own music.
- ``planner`` turns a template, a beat grid and a set of pictures into a
  concrete shot list - which picture is on screen from when to when, and the
  transition into it - and scores how well a template fits a given set.
"""
