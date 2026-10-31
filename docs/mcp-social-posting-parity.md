# MCP social posting parity audit

Status: implemented and regression-tested; live guidance refresh pending.
Scope covers Publish and Campaigns, including the path
from another app through Streamable HTTP to context, composition, queueing,
optional slot pinning and delivery. Named confirmed actions supplement the
existing draft helpers. Credentials and machine-wide connection management
remain local-only, as they are in the authenticated GUI.

## Evidence and coverage

The GUI sources are `apps/web/app/publish/` and
`apps/web/app/campaigns/autopilot-panel.tsx`. API behavior lives in
`publishing_api.py` and `campaign_autopilot_api.py`. MCP registration, call-time
policy and handlers live in `integrations/mcp/` under the API package.

| GUI workflow | MCP state | Remaining work |
| --- | --- | --- |
| Publish capabilities and readiness | `get_publishing_context`, no account probe | Implemented |
| Connected targets and boards | Confirmed `list_publishing_accounts`, `list_publishing_boards` | Implemented |
| Composer preview | Typed GUI fields, Library credit and local validation; confirmed optional provider validation | Implemented |
| Publish history and results | Bounded job list and workspace-scoped job read | Transport-tested |
| Provider draft, scheduled or immediate delivery | Confirmed `submit_publishing`, durable GUI job path | Tested all modes with fake delivery |
| Campaign context and exact composition | Existing context plus `campaign_read_autopilot` and `campaign_compose_queue_item` | Shared GUI handlers |
| Campaign creation with media and products | Draft helper or confirmed `campaign_add_queue_item`, including product carry-over | Both preserve their documented authority |
| Pin, release, skips and schedule | Existing helpers and confirmed GUI counterparts | Pinned and unpinned creates transport-tested |
| Complete-media and full queue edits | Confirmed `campaign_update_queue_item`, with Library asset IDs | Exact GUI validators, explicit-null and order tests |
| Queue state, removal and delivery | Confirmed single and batch GUI tools | Shared GUI handlers |
| Preview, executions, exceptions, analytics | Typed GUI counterpart for each route | Shared reads and bounds |
| Frozen execution actions | Confirmed approve, dismiss, return, edit and refreeze tools | Existing GUI state restrictions retained |
| Settings, destinations, products, calendars and plans | 53 Campaign GUI counterparts | Route-coverage guard checks every public endpoint |
| Publish scheduling and product matching | 11 Publish GUI counterparts | Shared body models and handler validation |
| Credentials, sign-in, secret reveal, global connection configuration | Local-only | Deliberately excluded |

## Implementation

Publish reads now use the same services as the GUI. Preview receives the GUI
request fields, binds the connection's workspace, rejects caller-supplied
campaign provenance and applies the Library credit before engine validation.
Job reads refuse another workspace's ID with the same response as a missing ID.
Context reads request `probe=False`, so reading readiness does not contact an
engine. `asset_ids` selects ordered Library media without guessing server paths,
using the latest completed edited cut with original fallback. The same option
is available on full campaign queue creation and complete-media replacement.
Import status is now scoped to the MCP connection's workspace as well.

The policy, service boundary note, Tools access badge, SOPs and architecture
updates describe the confirmed workflow. `confirmed_external` tools refuse
without top-level `confirm_external_action=true`. Nested body booleans cannot
override that requirement. The connection acts for the local operator and
checks membership, GUI roles and state rules. It does not invent an AAL2
session: installations requiring MFA refuse governed calls over this connection.
This is a delegated-authentication limitation, not an MFA bypass.

## Verification

The relevant API regression suite passes 1,211 tests, covering MCP, Campaigns,
Publish providers and Tools. Fixtures use isolated databases and fake delivery.
Two Windows child-process checks need an unsandboxed test runner; an initial
sandboxed run passed 1,205 tests and failed those two process inspections.
The final fresh-directory run passes all 1,211. Ruff, web TypeScript and the
prose audit also pass.

`test_mcp_campaign_gui.py` uses an actual MCP ClientSession and Streamable HTTP
transport against an in-process test ASGI server. It proves context discovery,
confirmation refusal, rotation-ready creation, draft creation from Library IDs,
optional pinning and standalone durable submission/result recovery. Additional
tests check full endpoint coverage, foreign-workspace refusal, body/query bounds,
role refusal, explicit-null edits, rollback and latest Library render selection.
`test_mcp_publishing.py` verifies all delivery modes, durable replay without a
second send, credit, provenance rejection and that consent cannot forge MFA.

Queue creation has ordinary GUI create semantics. After an interrupted create,
search the queue before repeating it. Publish job IDs are stable for identical
operations; job reads recover an interrupted delivery. No real social post was
sent during verification.

## Live check and remaining operation

A read-only connection to the operator's running MCP server lists all 122
tools, with no missing operations or input-schema differences from the source.
It exposes 53 Campaign GUI and 11 Publish GUI counterparts. Local evidence is
`.data/social-mcp-live-audit.json`.

The running server's initialization instructions differ from the revised source
guide. The guide is captured when a server instance starts, while guide/SOP
resources read current files. The operator's service must be refreshed before
the full live-completion gate can pass. Repository rules prohibit this agent
from restarting operator-owned services. No restart was performed. The goal
remains active pending that live guidance check; no owner-desk task was added.
