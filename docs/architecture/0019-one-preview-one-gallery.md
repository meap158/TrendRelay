# ADR 0019: One frame preview and one gallery, declared by the effect

Status: Accepted and built - `Effect.preview`, `POST /effects/frame`, the
`folder_from` hook, and the shared gallery dialog in the editing suite.

## Context

ADR 0006 made an effect a declaration and built the editor from it, so adding an
effect meant adding an entry. Two things then grew back the other way.

**Previews became per-effect endpoints.** Face blur got `/face-blur/frame`, and
the object overlay got `/face-overlay/frame` alongside it. Both do the same
thing — decode one frame, apply one effect, encode a JPEG — and differ only in
which settings object they build. Recolouring and the swap would have been a
third and a fourth. Meanwhile the two effects that most need a preview did not
have one: the recolour's fabric threshold is unguessable from its description
and depends on the lighting in the clip, and choosing a portrait for the swap
was a decision made blind, with a full render as the only way to check it.

**The gallery became overlay-specific.** ADR 0018 declared `presentation:
"gallery"` on a parameter and built a picker for it. The declaration was
generic; the implementation was not. When the swap's portrait choice adopted the
same presentation — correctly, it is the same problem — it inherited a dialog
that fetched thumbnails from the overlay sprite endpoint and previewed through
the overlay frame endpoint. The mechanism was already being used by a second
effect and could not work for it.

Both are the same failure. The declaration said what an effect *is*, and the
interface then had to know which one it was looking at anyway.

## Decision

**An effect declares how to render its own frame.** `Effect.preview` takes the
source, the step's values and where in the clip to look, and returns the image,
the position it landed on and a note. One endpoint, `POST /effects/frame`,
validates the step against the registry and serves the result; it knows no
effect by name. A POST because the payload is a step's whole values object — the
same shape stored in a recipe and sent to a render, so a preview cannot drift
from the thing it is previewing.

The bespoke `/face-overlay/frame` is gone. `/face-blur/frame` stays: it serves
the older standalone blur dialog, which has its own coverage control and is not
part of the effect suite.

**The note is written by the effect, not assembled by the interface.** What
makes a frame look wrong is specific to the effect and so is the setting that
would fix it. A recolour that changed nothing and one that changed the whole
room look equally like a broken effect, and the fix is opposite in each; only
the recolour knows to say "lower the fabric threshold" for one and "raise it"
for the other.

**An effect that cannot be previewed says why.** The identity blur decides who
the subject is by clustering faces across the whole clip. A single frame has
nothing to cluster, so a preview could only show a guess arrived at differently
from the render — and be believed. It declares `unpreviewable_reason` instead,
and the endpoint refuses with it. A stated gap is a decision; a silent one is an
omission, and a test now requires every frame effect to have one or the other.

The swap is the interesting case on this line and goes the other way. Its
subject is also chosen over the whole clip, but the question somebody has while
looking at a folder of portraits is not *who* will be replaced — it is whether
*this* face sits convincingly on *this* person, and one frame answers that. So
it previews the largest face on the frame and says in its note that the render
chooses by grouping the whole clip and may choose someone else.

**The gallery is fed entirely by the declaration.** An option carries where to
fetch its own thumbnail, relative to the media-library base, so one dialog shows
overlay sprites and face-swap portraits without knowing which it has. A
parameter fed by a folder carries that folder and the files in it that failed to
become options, through a `folder_from` hook — so the picker can name the
extension point and explain a file that did not appear, for any effect, without
a request of its own. And the dialog's settings panel is the effect's own
remaining parameters, rendered by the same control the editor uses, so it gains
a control when an effect gains one.

### The gallery is a view of the editor, not a dialog on top of it

Built first as its own modal, which was wrong twice. It nested one Radix dialog
inside another, and it inherited the standard dialog width — 640px, which after
padding leaves two columns of about three hundred pixels each. A twelve-object
grid in one of those came out as roughly ninety pixels of visible height: a
scrollbar with two thumbnails behind it, sharing its column with the effect's
settings and losing to them.

So the editor's panel has two views instead. One modal, the full width, and a
way back. The dialog gained a `wide` size for panels whose content is the point
rather than a form, and the editing suite uses it for both views — a stack of
effects carrying their own parameter grids was cramped at the default width too.

Within the gallery view, the settings sit with the picture they change rather
than under the grid. Sharing a column with the grid is what squeezed it, and the
frame is what those settings are being judged against anyway.

## Consequences

Adding a frame effect with a preview and a picture-shaped choice is now four
declarations — `preview`, `options_from`, `presentation`, `folder_from` — and no
frontend change and no new endpoint. That was the original claim of the effect
registry, and it is true again.

The preview contract is a shape rather than a type: a dict with `image`,
`position`, `duration_seconds` and `note`. Loose on purpose, because the effects
that fill it already return richer reports that their renders use, and forcing a
dataclass between them would mean converting in both directions for nothing.
The cost is that a malformed preview fails at the endpoint rather than at
import; the endpoint reads it with `.get` and defaults.

`ParamControl` and the registry types moved out of the editor into
`effect-params.tsx`, because the gallery dialog renders the same settings beside
its preview — the size of a sticker is judged while looking at it, not in a
panel behind the dialog. Two implementations of one control would be two places
for a range or a unit to go stale.

Notes travel percent-encoded in a header. HTTP headers are latin-1 and a note is
prose that will not always be ASCII; the image is the body, and base64-ing it
into a JSON envelope to carry one sentence would inflate every preview by a
third.
