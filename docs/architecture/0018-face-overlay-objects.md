# ADR 0018: Attach objects to faces, from a catalogue anyone can add to

Status: Accepted and built - `face_overlay` in the effect registry, the overlay
catalogue and its drop-in folder, three tiers of landmark placement, and the
gallery picker in the editing suite.

## Context

The editing suite could already hide a face two ways, and both destroy it: the
blur mosaics every face it finds, and the selective blur mosaics everyone except
the subject. That is the right answer to "this person did not consent to being
in my video" and the wrong answer to the case that actually comes up more often,
which is a creator who does not want to be on camera but still wants the video
to be worth watching. A mosaic where a face should be reads as a redaction. A
sticker reads as a choice.

Three things make this harder than it sounds.

**A bounding box cannot place a prop.** The blur only needs a rectangle to fill.
Sunglasses sit on the eye line and have to tilt with it; a moustache sits above
the upper lip; ears sit above the skull, not above the box. Those need points,
and the tilt needs two of them.

**The obvious asset format makes the built-ins unreviewable.** An overlay pack
is normally a folder of PNGs. Fixed at whatever resolution somebody exported,
soft the moment a 4K close-up asks for a sticker larger than the file, and
opaque in a changeset — "moved the left eye two percent" is a binary diff.

**"Covers a face" is a claim, not a size.** The library and the publish path both
ask for the `blurred` version kind by name. Anything filed under that kind is
treated as a face that has been dealt with.

## Decision

**Landmarks in three tiers, and the tier is reported.** MediaPipe's Face
Landmarker is the best of them and is an optional extra plus a model bundle, so
it is not assumed. Below it, YuNet already returns five points — both eyes, the
nose and both mouth corners — with every box it finds, and the face blur has
been discarding them since it was written; reading them costs nothing and is
enough to place and rotate a prop convincingly. Below that, the parts of a face
are estimated at their usual fractions of the detection box: upright only, and
better than refusing to run. Which tier placed an object travels with the
result, because an object that cannot lean with a tilted head is a visible
limitation that no size or position control can fix.

The two landmark sets are *not* interchangeable in one respect worth recording:
MediaPipe's eye points are the outer corners and YuNet's are the eye centres, so
a face is 1.55 eye-spans wide by one measure and 2.2 by the other. Reading both
with one ratio makes every prop placed off YuNet about a third too small.

**Built-in objects are declared as shapes in a unit square** — a few ellipses,
polygons and rectangles each — and rasterised on demand at exactly the size the
frame needs. That gives one renderer instead of two: the gallery in the browser
shows a PNG produced by the same function that burns the object into the clip,
so the thumbnail is the render rather than an artist's impression of it. It also
gives any resolution without softness, and a diff a reviewer can read.

**The extension point is a folder, not an API.** An RGBA PNG dropped into
`.data/overlays` joins the catalogue, with an optional JSON sidecar saying where
on a face it hangs. The catalogue is read from disk on every request rather than
cached, so a new object is offered *and accepted* without a restart — the effect
registry's choice options and its validation both read the same live list, which
is what stops the picker offering something the validator will reject.

**Whether an object hides a face is declared per object, and re-checked against
how it was set up.** A render is filed as `blurred` only when the chosen object
claims to cover a face *and* has not been faded below opacity 0.95. A party hat
is an `edited` version. An eye bar is an `edited` version, and says on its own
tile that the jaw and hairline are still there to be recognised.

For a drop-in, that claim arrives in a JSON file next to a picture, so it is
checked rather than taken: an object narrower than about a face width, or hung
off the mouth rather than the face, cannot be covering one, and the claim is
downgraded with a note saying why. Trusting it would let a sticker the size of a
nose file its render under the kind that means "this face has been dealt with".

### The sidecar

`<name>.png` is enough on its own — a bare PNG is placed over the whole face at
its own proportions. `<name>.json` beside it overrides that. Every field is
optional, out-of-range numbers are clamped rather than rejected, and unknown
fields are ignored so a sidecar written against a later version still loads.

| Field | Default | What it does |
| --- | --- | --- |
| `label` | The filename, prettified | The name on the tile |
| `group` | `Your own` | Which section of the gallery it appears in |
| `anchor` | `face` | `eyes`, `face`, `mouth`, `nose`, `forehead` or `chin` |
| `width_in_faces` | `1.4` | How wide it is drawn, in face widths |
| `aspect` | The PNG's own | Height over width |
| `offset` | `[0, 0]` | Nudge along the head's own axes, in face widths |
| `follows_roll` | `true` | Whether it leans with a tilted head |
| `occludes` | `false` | Whether it covers the face — checked, see above |
| `note` | none | A line shown under the tile |

The filename is the object's id, so it has to be lower-case letters, digits,
dashes or underscores; it cannot take a built-in's id; and the PNG cannot exceed
4096px a side, since a sprite is never drawn larger than that. A file that fails
any of those is reported by `GET .../face-overlay/objects` and shown in the
picker, because a file that silently fails to appear is the one case an operator
has nothing to act on.

**The choice is a gallery, not a dropdown**, and it opens beside a real frame
from the clip with the object placed on it. A dozen picture-shaped options in a
`<select>` is a list of words describing images the reader cannot see, and no
gallery can answer the question that actually matters — whether this object sits
right on this face. A still answers it for the cost of a decode.

## Consequences

Placement quality depends on what is installed, and differs visibly between
tiers. That is the price of not making a 60MB wheel mandatory for a feature most
installs will not use, and it is mitigated by saying which tier ran rather than
letting someone guess.

The middle tier is no longer the exception it was written as. ADR 0021 has setup
fetch YuNet, so an install that reaches the network gets landmarks by default
and the box tier became the rare fallback rather than the normal state — which
is what it was always supposed to be. The three tiers stand; the difference is
which one a typical machine lands on.

The `blurred` version kind is no longer synonymous with "was blurred". It means
"the face was covered", which is what its consumers were always asking. Anything
new that files under that kind has to make the same claim honestly.

**One subject can be several tracks, and asking for "the main face" has to mean
all of them.** The tracker starts a new track whenever it loses a face for
longer than it will bridge, so a person who turns away for a second, or walks
behind something, comes back as a second track. Taking the single best track put
an object on the clip up to that moment and nothing after it — measured at 43%
of a clip the subject was visible throughout, which reads as a broken effect
rather than as tracking working exactly as designed.

Tracks are therefore joined when they never once appear together: two faces
detected on the same frame are two people and stay apart, while two that take
turns are one person either side of a dropout, which is the only reading that
makes sense of a single-subject clip. Size guards the rest, because a face in
the background is not the subject however politely it waits its turn. Where the
joined tracks both guess a position on the same frame, one object is drawn, not
two.

Two further behaviours diverge deliberately from the blur's tracking, which is
otherwise reused wholesale. The blur holds a detection open-endedly at each end of a
track, which is the safe choice when the risk is an exposed face and the wrong
one here — it leaves a sticker parked on empty air after the subject walks out
of shot, so the hold is bounded. And picking the main face ignores tracks
present in under 15% of the clip, because the detectors here are known to call a
patterned shirt a face for a frame or two, and such a detection is often
enormous.

Object names and group headings are not translated. They are catalogue content
rather than interface chrome — an operator adds to the catalogue by dropping a
file in a folder, and no dictionary shipped here could name what they dropped —
so they arrive from the API in English and stay as the catalogue wrote them, the
same treatment an asset's own filename gets. The effect's own labels, help and
fixed choices are translated as usual.
