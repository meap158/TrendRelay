# ADR 0023: An outside assistant reaches the workspace over MCP, to write copy

Status: Accepted and built - `services/api/src/trendrelay_api/integrations/mcp/`,
`scripts/mcp_server.py`, surfaced in the Tools tab as **Assistant Access (MCP)**.

## Context

A campaign queues a post before anyone writes its caption. The scheduler skips
an unwritten post, the runner refuses to approve one, and the interface marks it
- all agreeing on one sentence, `PLACEHOLDER_BODY`. Writing that copy is work a
capable assistant could do, if it could see what the post is for: the clip, the
attached product and what it pays, the networks it will post to, and the
campaign's own brief.

The Model Context Protocol is how an assistant that is not this program reads and
writes here. AdRelay solved the same problem first, and its `TUNNEL_AND_MCP.md`
records both the shape that worked and the mistakes on the way. This is that
shape, in TrendRelay's stack (FastAPI, not Electron), so its principles are
adopted rather than its code.

## Decision

**The server serves one workspace on loopback; a tunnel dials outward.** Nothing
binds a public address. The listener is `127.0.0.1:8765`; reaching it from
outside is a tunnel the operator configures, dialing out to a control plane, so
no inbound connection from the internet is accepted and no public hostname is
minted. It runs as a supervised subprocess - a module-global handle under a lock,
an atomically written status file, a reader that cross-checks the file against
the process being alive - the same pattern the Douyin connection uses, started
and stopped from the Tools tab.

**What a caller may do is a policy, not the tool listing.** `policy.py` classifies
every operation on purpose and defaults to refusal. Reads and workspace writes
that stay in the workspace - a draft caption, a first comment, a thread reply -
are allowed. Two refusals hold however many operations are added:

- Credentials and sessions are refused. Signing in and connecting an account open
  a window on the operator's screen; not a remote caller's to do.
- Approval and execution are refused. Approving, publishing and deploying are a
  person's decision, made in the app. A model may write copy, never send it.

The refused operations are named in the policy, not merely absent, and are never
registered on the server - so they do not appear in the listing and are refused
when a caller names one directly. The check is re-run at call time, because a
caller may name any tool it likes, including one it was never offered.

**Parity is by reuse, not a second list.** The reads reuse the interface's own
serializers (the queue view, the destination view, the campaign status); the
writes go through `apply_queue_item_edits`, extracted from the edit route so the
route and the assistant apply copy through the same code. A caption an assistant
writes is validated and stored exactly as one a person types. Full surface
parity with the GUI is the longer goal; the first surface is copy, and it is
built from shared handlers so widening it is adding classified operations, not
reconciling two maps.

**Operating guidance is selected by action.** Reviewed Markdown procedures live
under `docs/sops/`, with a canonical action and unique aliases in front matter.
One validated loader exposes them through both MCP tools (`list_sops`, `get_sop`)
and resources (`trendrelay://sops`, `trendrelay://sops/{action}`). Adding another
SOP is a documentation change, not another bespoke server handler. Procedures
guide the use of allowed operations but never add authority to the MCP policy.

**It ships as an optional extra.** `pip install -e services/api[mcp]`. A machine
that never exposes its workspace does not carry the server or its transport, and
the Tools tab reports it unavailable until installed - the same stance as the
vision and face extras.

## Consequences

- An assistant can be pointed at a running TrendRelay and fill in the captions,
  first comments and thread replies a campaign is missing, with the context to
  write them well, and a person still approves every post.
- The boundary is enforced where it is decided and tested against a caller that
  ignores the menu: `tests/test_mcp.py` proves a refused operation is absent from
  the listing and refused by name, and that a read and a copy write work against
  the same models the interface uses.
- No key by default: a loopback listener is reachable by every process on the
  machine, and the tunnel client has nowhere to carry an `Authorization` header,
  so what holds the boundary is the loopback bind, the outward-dialing tunnel and
  the policy - the same conclusion AdRelay reached the hard way.
- The outward tunnel is built and started by the launcher. `scripts/tunnel.py`
  ensures the server is up, runs `tunnel-client` with the arguments AdRelay
  verified (the API key in the child's environment, never its arguments), and
  restarts it with rising backoff; `scripts/dev.py` adds it as a supervised
  service only when `CONTROL_PLANE_TUNNEL_ID` and `CONTROL_PLANE_API_KEY` are both
  set, so an unconfigured machine starts nothing and keeps the server on loopback.
- Not yet built: surfaces beyond copy, and the Tools-tab reading of the tunnel's
  own status. The transport is proven end to end on loopback - a client connects,
  lists the allowed tools and calls them - and the supervisor's spawn, backoff and
  parent-watch are exercised with a stubbed client.
