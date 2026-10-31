# MCP social posting parity audit

Status: in progress. Scope covers Publish and Campaigns, including the path
from another app through Streamable HTTP to context, composition, queueing,
optional slot pinning and delivery. Existing draft-only MCP policy prevents
full GUI parity. The audit does not treat passing draft tests as delivery parity.

## Evidence and coverage

The GUI sources are `apps/web/app/publish/` and
`apps/web/app/campaigns/autopilot-panel.tsx`. API behavior lives in
`publishing_api.py` and `campaign_autopilot_api.py`. MCP registration, call-time
policy and handlers live in `integrations/mcp/` under the API package.

| GUI workflow | MCP state | Remaining work |
| --- | --- | --- |
| Publish capabilities and configured engine readiness | Added `get_publishing_context`, no account probe | Discover connected targets and board options with explicit external confirmation |
| Standalone composer preview | Added `preview_publishing`, using GUI credit and engine validation | Expose a typed composer schema and examples |
| Publish job history and result | Added bounded `list_publishing_jobs` and workspace-scoped `get_publishing_job` | Verify through external client transport |
| Submit a standalone post as provider draft, scheduled or now | Missing | Add confirmed submission through durable Publish jobs |
| Campaign brief, media, products and copy context | Existing tools | Check full destination settings and composition output parity |
| Add campaign post with Library media or words first | Existing draft-only `create_campaign_post` | Product carry-over, approval authority, optional slot in create, idempotency |
| Queue pin, release, skips and posting times | Existing tools | End-to-end pinned and unpinned create tests |
| Edit complete campaign media and full queue fields | MCP restricts media replacement | Deliberate edits with GUI validation and authority checks |
| Queue approve, hold, retire, remove and publish | Missing or refused by policy | Confirmed state and delivery operations, single and batch |
| Campaign preview, execution history, exceptions and analytics | Missing | Add bounded reads and composition preview |
| Execution approve, dismiss, return, edit and refreeze | Missing | Match GUI authority and immutable/frozen execution rules |
| Campaign settings, destinations and product membership | Partial schedule/product tools | Cover ordinary settings separately from credential management |
| Account credentials, sign-in, secret reveal | Refused by policy | Keep local-only |

## Initial changes

Publish reads now use the same services as the GUI. Preview receives the GUI
request fields, binds the connection's workspace, rejects caller-supplied
campaign provenance and applies the Library credit before engine validation.
Job reads refuse another workspace's ID with the same response as a missing ID.
Context reads request `probe=False`, so reading readiness does not contact an
engine. These changes do not submit or publish anything.

The existing MCP instructions, service boundary note, SOPs and architecture
documents describe draft-only authority. Full delivery support must update
that contract together with policy, handlers and tests. Merely registering a
publish tool would leave clients receiving contradictory instructions.

## Verification still required

Use isolated test workspaces and fake publishing providers to verify all
delivery modes, account selection, malformed requests, other-workspace IDs,
confirmation refusal, draft/approval authority, durable job replay, carousel
limits, platform fields, attribution credit and state rollback. Exercise the
registered schemas through a real Streamable HTTP client connected to a test
ASGI server. Do not send a real social post as an audit test.

Completion requires the full workflow matrix above, updated client guidance,
and tests proving GUI/MCP behavior agrees. This audit remains open.
