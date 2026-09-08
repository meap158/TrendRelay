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
