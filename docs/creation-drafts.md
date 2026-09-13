# Creation drafts

A resumable, editable draft for a video a creation feature draws — so AutoCut and
Storytelling (and whatever creation feature comes next) can be saved half-built,
reopened later, and picked up by an assistant, the way a campaign post already
can. Added 2026-09-09.

## Why

AutoCut and Storytelling both turn media and a feature-specific plan into a
video, yet neither could save a work-in-progress. The configuration lived only
in the browser and, at the moment of rendering, a durable-job snapshot pruned
within a day. An assistant could compose a campaign post over MCP and leave it as
a draft, but had no way to build or continue a video at all.

## Shape

One generic substrate, not two bespoke ones:

- **`creation_models.py`** — `CreationDraft` (one row per work-in-progress video,
  keyed by `kind`, holding the full spec as JSON, its status, and who edited it
  last) and `CreationDraftMedia` (media the draft owns that is not a Library
  asset).
- **`creation_drafts.py`** — the store and the per-kind **adapter registry**. A
  kind (`autocut`, `storytelling`, …) registers an adapter: a spec schema
  (shape-checked on save), a summary (for a list row), and a render (hands the
  spec to that feature's own `enqueue_render`). Adding a kind is one adapter and
  a registry line — no new table, no new endpoint. Completeness is enforced only
  at render, which raises the feature's own readable errors.
- **`creation_drafts_api.py`** — `/api/workspaces/{ws}/creations`: list, create,
  get, patch, render/preview, archive, plus attach/list owned media. Reads need
  membership; writes need an editing role and are audited.
- **`integrations/mcp/drafts.py`** + eight MCP tools — an assistant lists the
  in-progress drafts, reads one, creates and edits one from a spec, adds media
  the draft needs, and renders it to the Library. Classified in `policy.py` as
  reads and workspace writes; **rendering is allowed** (it makes workspace
  content and publishes nothing) while publishing the finished video stays
  refused, the same boundary the campaign tools hold. The `creations.make-a-
  video-draft` SOP guides callers to it.

## Self-contained media

A draft references Library media by asset id. Anything not in the Library is kept
by the draft: the bytes under an approved media root (their type decided by the
bytes), a `CreationDraftMedia` row, and a `draft:<id>` ref in the spec's asset
list. At render each owned ref is ingested through the same pipeline an import
uses and resolves to a Library asset id, cached so a re-render does not import
twice — so the feature's renderer only ever sees Library assets, and the media a
draft carried never had to be in the Library to be saved and resumed.

## Extending it

To add a creation kind: define its spec schema and a `render` in
`creation_drafts.py`, register it in `ADAPTERS`. It gains the whole HTTP and MCP
surface for free. The renderer it maps to needs an `enqueue_render(workspace_id,
actor_user_id, *, …, preview, kinds, factory)` in the shape AutoCut and
Storytelling already share.

## Web entry points

Both the AutoCut and Storytelling dialogs save their work as a draft (Save draft
creates, then updates the same one) and reopen a saved draft from a list, pulling
its spec and its media (fetched by id, so it resumes even when the dialog opened
on a different selection). The drafts system is complete end-to-end — web, HTTP
and MCP — for both kinds.

## A render from the dialog is still the draft's render

The dialogs render through their feature's own routes (`/autocut/render`,
`/storytelling/render`, `/storytelling/autocreate`), not through
`/creations/{id}/render` — the auto-build has no draft route at all. Until
2026-09-13 that meant a reopened draft never entered *rendering*, never settled,
and was still offered under Drafts after its video was in the Library.

Those routes now accept `draft_id`. The draft is looked up before anything is
queued (a missing one is a 404 and nothing is queued), and the queued job is
handed to `creation_drafts.begin_render`, which is what `render_draft` does for
its own renders. A preview, and a build that stops for review, mark nothing:
neither makes a video. The dialogs send the draft they reopened and forget it
once the render is queued, so the next thing saved is a new draft.

`settle` then follows what it was given. A build that finished by queueing a
render is followed to that render (the draft's `render_job_id` moves with it).
A render that finished by queueing the ingest is not *rendered* until the
ingest has filed the video — *rendered* means in the Library, and the asset the
draft carries does not exist before that. An ingest the Library refused gives
the draft back, like a failed render. The Library page re-reads its drafts
whenever a job settles, so the Drafts chip loses a finished draft without a
reload.

## What a draft, and a video, is called

`creation_titles` names a video for what it is about rather than for the
pacing that drew it: a narration for its first sentence (split the way the
render splits it, cut at a word past sixty characters), a cut for the first of
its clips with a name to lend and how many more there are (a clip titled by a
platform's id is passed over for one with words). A typed name always wins;
the template's name is the last resort, for a cut whose clips have no names.

Drafts take the same names through each adapter's `name`, and only read
"Untitled story" / "Untitled cut" while the spec is about nothing. A name the
server gave follows the spec as it is edited — `update_draft` tells a given
name from a typed one by whether the old title is what the old spec would have
been given — so a draft saved before its script was written is renamed once
the script is there. The Storytelling dialog therefore sends no title of its
own; the AutoCut dialog sends one only when somebody typed it.
