# ADR 0020: Photographs are editable, and portraits come from the library

Status: Accepted and built - `Effect.render_still`, `render_still_recipe`,
`media_kinds` enforced server-side, and importing a face to swap in from a
library asset.

## Context

The Library holds pictures as well as clips — Douyin carousels, imported
stills, exported frames — and the editing suite could not touch any of them.
Every effect declared itself video-only, the render path opened a
`VideoCapture` and wrote an mp4, and the approved-source check accepted `.mp4`
and nothing else. A blur that works on frame 400 of a clip could not be applied
to a photograph of the same person, which is difficult to explain to anyone.

The face swap had a second version of the same problem from the other side. Its
source portraits came from `.data/face-swap/faces`, a folder somebody had to
find and copy files into by hand — while the pictures they wanted were already
in the Library, three feet away in the same product. It made the swap feel like
a different application that happened to be installed alongside this one.

## Decision

**A frame effect renders a still, and says so.** `Effect.render_still` takes a
source, a destination and the step's values. There is nothing clever in any of
the four implementations, and that is the point: a frame effect was always a
per-frame operation, so the still path is the effect with the tracking removed.
Tracking exists to stop a result flickering between frames, and a photograph has
one frame.

**Stream effects split by whether they mean anything without a duration.** Flip,
rotate, aspect and colour apply to a picture; speed, trim and volume do not. The
still filtergraph is the same one the clip builds, run with `-frames:v 1` and
without the audio chain — because a flip is a flip, and only the encoder
settings and the timestamps were ever about video.

**`media_kinds` became a boundary rather than a hint.** The editor already
filtered the effects it offered by the asset's kind, but a recipe is stored,
re-run and can be posted directly, so that was a courtesy. `check_media_kinds`
now refuses at the job and at the render. Speeding up a photograph is not
something to fail quietly at.

**The identity blur stays video-only**, for the same reason it has no frame
preview: it decides who the subject is by clustering faces across a clip, and a
photograph has nothing to cluster.

The swap goes the other way again, and the asymmetry is worth stating. On a
still it picks the largest face and reports `subject_chosen_by: "size"`. That is
a weaker notion of "the subject" than a clip gets, and naming it in the report
is what stops somebody comparing two results and concluding the swap is
inconsistent.

**A still renders to PNG.** An edit is a master that may be edited again, and
pushing a photograph through JPEG on every pass spends a generation of quality
each time — the same reasoning that makes the clip path compose its filters into
one encode rather than one per effect.

**A library picture can be made into a portrait, and it is copied.** The import
endpoint takes an asset id and puts the file in the portraits folder. Copied,
not referenced: a recipe stores a portrait by its name and is re-run later, so
pointing at an asset would break an edit the moment that asset was removed, and
would put a workspace-scoped id into a value that is otherwise a filename. One
photograph is a cheap copy.

The name is derived from the asset's title, slugified, and never reused —
reusing one would silently repoint an existing recipe at a different person. The
face is verified at import where it can still be refused, because a portrait
with no findable face in it otherwise fails minutes later at render time and
reads as the swap being broken rather than the picture being wrong.

**The picker offers it, through the declaration.** A folder-fed parameter says
where a library picture may be sent (`import_from_library`), so the gallery
shows an "add from the library" control for face-swap portraits and not for
overlay objects, without knowing which it is showing. After an import the editor
re-reads the catalogue rather than patching its local list: the registry is the
one source of the options, and a locally added option would be the version that
validation does not know about.

## Consequences

Importing a portrait is an auditable act. A photograph of a real, identifiable
person has been copied somewhere it will be applied to other people's footage;
that is recorded with the asset it came from, and a portrait can be removed from
the same place it is offered rather than only by finding the folder on disk.

The editing suite's approved-source check is wider than the publishing one — an
image is editable but a still is not something the publishing path resolves as a
video. The root check, which is the actual security boundary, is unchanged and
tested against the wider suffix list.

Effect reports are no longer uniform: a still render has no `frames`, `coverage`
or `faces_tracked`, and carries `media_kind: "image"` instead. Callers reading a
report should key off that rather than assume the clip shape.
