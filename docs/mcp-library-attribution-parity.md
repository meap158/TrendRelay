# Library and Attribution MCP parity audit

The original MCP surface offered compact Library search and product reads,
plus product-creative draft creation and media submission. It did not cover
the GUI's full Library filters, asset details, tagging, tracking/conversion
management, edit recipes, processing controls or creative preview/discard.

## Added coverage

Explicit allowlists in `integrations/mcp/library_attribution.py` now register
typed tools using the same GUI body models and handlers. The groups are
`Library GUI` and `Attribution GUI`; existing compact task helpers remain.
This adds 39 Attribution/Character tools and 94 Library/shared-dialog tools.

| GUI area | MCP coverage |
| --- | --- |
| Library filters, facets, sort, selection IDs and full asset detail | `library_list_assets`, `library_list_asset_ids`, `library_get_asset` |
| Effects, saved recipes, render queue, cancel and run-next | GUI counterparts, with confirmation for writes |
| Transcript review, captions, translation, speech and voice generation | GUI counterparts, provider actions confirmed |
| Music search/suggestions/import and image-to-video generation | Confirmed GUI counterparts and progress reads |
| Bulk processing and archive preparation | Confirmed handlers; background work follows commit |
| Tracking links, statuses, conversions and summary | Attribution GUI counterparts |
| Campaign product tags and media/product links | Offer/campaign tags and asset/product links are separate tools |
| Stored listings and enrichment jobs | Workspace-scoped GUI reads |
| Shopee import, fetch, refresh and probe | Confirmed handlers; credentials remain local |
| Creative preview, complete filters, create/read/submit/discard | Full GUI counterparts in addition to existing helpers |
| Creative video providers, generation and progress | GUI counterparts, generation confirmed |
| Character picker and lifecycle | Read, reviewed registration, confirmed draw/retry/remove |
| AutoCut, Storytelling and text-slide carousels | Typed planning, matching, preview, render and job tools |
| Hooks, saved creation drafts and Studio clip edits | Full JSON application counterparts, confirmations for writes |

Each new mutation or provider read requires top-level confirmation. Body
confirmation fields are normalized from it. The adapter enforces workspace
membership, GUI roles, model/query bounds and transaction rollback. Governed
actions do not fabricate MFA assurance. Background tasks are started only
after the GUI write commits, preserving the HTTP ordering.

## Explicit differences

This is application-operation parity for the covered JSON workflows, not a
claim that HTTP file responses and MCP have identical transport capabilities.
The coverage guard classifies every route in the ten audited GUI routers,
including shared dialogs invoked from these screens.

- Shopee sign-in, session storage/disconnect and browser page opening remain
  operator-local. Readiness can be read without exposing cookies.
- Face-swap licence attestations remain operator-local.
- Server-local file and folder imports stay behind their GUI loopback check.
  Remote clients transfer bytes or a public URL with the existing MCP upload
  flow; the adapter does not forge a loopback origin to import arbitrary paths.
- Raw content streams, frame/sprite previews, caption downloads,
  archives and Shopee CSV exports remain on authenticated GUI HTTP routes.
  Archive preparation is available, but MCP does not transfer the archive bytes.
- Browser multipart uploads are replaced by the existing MCP byte/URL upload
  flow for media. The GUI's overlay multipart upload is not an MCP file input;
  importing an overlay from existing Library media is available.

`BINARY_OR_MULTIPART` and `LOCAL_ONLY` name those exclusions explicitly. A new
GUI endpoint fails the test until it is exposed or its boundary is reviewed.
No credentials, provider calls, generation charges or live Library/Attribution
mutations were used to test this work.

## Verification

`test_mcp_library_attribution.py` checks complete route classification, all
confirmation gates, GUI filter/sort/pagination behavior, foreign-workspace
refusal, reviewed enrichment, recipe persistence, product attachment/removal,
role refusal and commit-before-background execution. A real Streamable HTTP
ClientSession discovers Library/Attribution context and saves a recipe in an
isolated database. Existing GUI and task-helper tests remain regression gates.

The live read-only audit confirms all 255 tools are present, with matching
input schemas and initialization guidance. Its local evidence is
`.data/library-attribution-mcp-live-audit.json`. No operator service was
started, stopped or restarted for that check.

Final validation: 1,232 MCP, Attribution, Library, effects, captions, voice,
character and creation-workflow regression tests pass. Ruff and the prose
audit pass. The suite uses a fresh project-drive test directory. It was run
outside the sandbox to read the installed media-AI runtime and inspect its own
test child processes; a sandboxed attempt failed on those environment limits.
