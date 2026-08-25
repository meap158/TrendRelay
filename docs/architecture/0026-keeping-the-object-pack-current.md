# ADR 0026: Keeping the object pack current, and finding the right one in it

Status: Accepted and built — `services/api/src/trendrelay_api/integrations/overlay_catalogue.py`,
`services/api/src/trendrelay_api/integrations/overlay_meshes.py`,
`services/api/src/trendrelay_api/integrations/overlay_match.py`,
`apps/web/app/library/gallery-picker.tsx`.

## Context

"Choose an object" shipped with twelve props and now has fifty-six. Two
problems arrive with that growth and neither is solved by drawing more things.

**The pack goes stale silently.** A face prop is fashion. Butterflies and star
stickers are current as this is written and will not be; a censor bar will
still be useful in five years. Nothing in the codebase distinguished the two,
so "add the trending ones" had no shape: no statement of what counts as
evidence, no place to put a new object, and no guard that would notice one had
been added badly.

**Fifty-six is past browsable.** A gallery exists precisely because nobody
picks a sticker by reading its name, and a gallery long enough to scroll twice
has the same problem the dropdown had. The operator has a specific clip open
and a rough idea; the catalogue has no idea what the clip is about.

There was also a quieter problem underneath both. The pack's vocabulary was
English, and this workspace's library is 2,540 Douyin clips whose captions are
Chinese. Anything built on English words would have passed an English test
suite and been useless on every real clip in the product.

## Decision

### Two kinds of evidence, weighted differently

**Trade press decides the family, not the list.** Reporting on TikTok and
Douyin effects is soft evidence — it is written to be read, not measured, and
searching it returns listicles. It is good for one thing: telling us which of
the durable prop families (animal ears, headwear, eyewear, reactions, seasonal)
is having a moment. Butterflies, face stars and fairy looks entered the pack
that way.

**The workspace's own library is the hard evidence, and it wins.** The clips
this product actually posts are in the database with their captions and
hashtags. Reading them is a query, not a search, and it is specific to the
operator rather than to a market. It is why the pack has a sweatband and a
visor: `健身` / `腹肌` / `马甲线` is the second commonest thing this library is
about and nothing in the pack suited it. **Run the query before drawing
anything** — the top hashtags in `media_assets` are the brief.

### An object is a declaration, never an asset

Built-ins are shapes in a unit square (`Shape`) or triangles in a unit cube
(`overlay_meshes.Mesh`), rasterised on demand. This is settled and ADR-worthy
only because it is what makes the pack maintainable: one renderer, any
resolution, and "move the brim two percent" is a readable line in a diff rather
than a new binary. See the module docstrings for the full argument.

Adding one is therefore a change to `overlay_catalogue.py` and nothing else:
declare it, put it in a group, add it to `BUILT_IN`, give it an entry in
`OBJECT_KEYWORDS`.

**Operators do not need any of this.** A PNG dropped in `.data/overlays`, or
chosen through the picker's own upload, becomes an object with no code at all.
The declarative path is for the shipped pack; the folder is the extension
point, and it stays.

### The vocabulary is bilingual and lives in one table

`OBJECT_KEYWORDS` maps an id to the words a clip that suits it would use, in
English **and** Chinese. It is deliberately separate from the declarations:
geometry is settled once, while "what content does a crown belong on" is a
judgement somebody will revise after watching the suggestions be wrong, and one
block is what makes that revision reviewable.

Chinese is a requirement rather than a courtesy, and the measurement is above.
`campaign_offer_matcher.tokens` already expands a Han run into its characters
and bigrams, so no word segmenter ships.

### Suggestions are additive, and shut up when unsure

`overlay_match.suggest` scores the catalogue against what a clip says about
itself — hashtags first, then caption, title, and any readings the clip has —
using the offer matcher's own arithmetic including its IDF damping. Two
opinions about "which of these fits this content" would drift, and the one
nobody compares is the one that goes wrong.

Three rules keep it honest:

- **It suggests; it never applies.** Choosing a prop is the operator's
  judgement. The point is to save the scroll.
- **It sits above the gallery rather than reordering it.** The gallery's order
  is a privacy decision — the objects that cover a face lead — and resorting
  forty tiles on a guess would move that without anybody asking.
- **Below a measured floor it offers nothing at all.** On nine hundred real
  clips the top scores cluster at 0.81–0.83 and then jump to 0.99. That cluster
  is a single weak bigram landing by accident. A gallery is one click away and
  always right; the least wrong answer costs the trust that makes the next
  suggestion worth reading.

## The process, concretely

1. **Read the library first.** Count the hashtags in `media_assets`. If the
   commonest subject has nothing in the pack that suits it, that is the gap —
   ahead of anything a trend piece says.
2. **Check the trade press for the family**, not for a shopping list.
3. **Declare the object** in `overlay_catalogue.py`: shapes for a flat one, a
   `Mesh` for one with depth, in the group it belongs to. `SOLID` is for
   objects with depth; everything else goes by where it sits on a face.
4. **Add its keywords** to `OBJECT_KEYWORDS`, in both languages.
5. **Look at it.** Render the sprite and open the PNG. Half of the first trend
   batch was wrong in ways no test catches — blobs, smears, an invisible
   near-white band on a light frame — and every one of them was obvious at a
   glance.
6. **Let the tests catch the rest.** They already refuse an object that
   declares no geometry, declares both kinds, falls outside its own sprite,
   uses almost none of it, ships without keywords, or ships English-only.

## Consequences

Adding a trending object is four edits and a look, and the guards make the
careless version fail rather than ship. The keyword table will drift from
fashion — that is the nature of the thing being modelled, and revising one
block is the cheapest form that drift can take.

The matcher is only as good as the vocabulary somebody wrote, and it says so:
every suggestion carries the words it matched on, so a wrong one is
diagnosable rather than mysterious.

Two limits worth stating. The floor was measured on one workspace's library; a
workspace posting in another language and register would want it re-measured
rather than inherited. And nineteen objects added before the translation table
existed still show English labels under translated titles — that is older than
this decision and not fixed by it.
