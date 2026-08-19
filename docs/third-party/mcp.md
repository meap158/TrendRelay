# Assistant Access (MCP)

TrendRelay can serve one workspace to an outside assistant over the Model
Context Protocol, so a model can help write the copy a campaign is missing. It
is a first-party capability built on the official `mcp` Python SDK (MIT), not a
third-party checkout; the catalogue lists it so the Tools tab can start, stop
and explain it beside everything else.

The design follows AdRelay's, recorded in that project's `TUNNEL_AND_MCP.md`:
the server listens on loopback and a tunnel the operator configures dials
outward, so nothing here binds a public address, and what a caller may do is
decided in a policy re-checked at every call rather than trusted from the tool
listing.

## The first surface: copy

An assistant helps write the posts a campaign has queued without a caption yet.

**Reads** give it the context to write well:

- `list_campaigns` - every campaign, and how many posts still need a caption.
- `list_posts_needing_copy` - the posts to help with, each a compact card: what
  the clip is and what it sells.
- `get_post_context` - everything for one post: the video, the attached product
  and its commission, every destination the post reaches and where a first
  comment or thread reply lands there, the campaign's brief, and any copy already
  written.
- `get_campaign_config` - the objective, audience, markets, languages, disclosure
  line, product mode and cadence, for tone and rules.

**Writes** set the copy, and only the copy:

- `write_caption`, `write_first_comment`, `write_thread`, and `write_post_copy`
  for several at once. A field left unset is not touched, so filling in a caption
  never blanks a first comment a person already wrote.

They write through the same helper the interface's own edit route uses
(`apply_queue_item_edits`), so a caption an assistant writes is validated and
stored exactly as one a person types, and the two surfaces cannot drift.

## The boundary

Decided in `integrations/mcp/policy.py`, and re-checked at the moment of every
call. Two rules hold however many operations are added:

- **Credentials and sessions are refused.** Signing in and connecting an account
  open a window on the operator's screen. Not a remote caller's to do.
- **Approval and execution are refused.** Approving, publishing and deploying are
  a person's decision, made in the app. A model may write copy, never send it.

Anything not named is refused by the default, so a new capability is argued for
rather than inherited. The refused operations are named in the policy and never
registered on the server, so they are absent from the listing and refused when a
caller names one directly.

| | Count |
| --- | --- |
| Reads | 4 |
| Workspace writes (copy) | 4 |
| Refused - credentials and sessions | 4 |
| Refused - approval, execution, deployment | 6 |

## Running it

The server ships as an optional extra so a machine that never exposes its
workspace does not carry it:

```
pip install -e services/api[mcp]
```

Then in **Tools → Assistant Access (MCP) → Setup**, press **Start server**. It
serves one workspace on `http://127.0.0.1:8765/mcp` (the port is
`mcp_port`; the workspace is `mcp_workspace_id`, or the local operator's own when
that is empty). Setup shows whether it is running, the endpoint, the tools it
exposes and the boundary. **Stop server** ends it.

## Reaching it from outside

A tunnel dials a control plane outward and forwards inbound MCP requests to the
loopback server; nothing binds a public hostname. Set both `CONTROL_PLANE_TUNNEL_ID`
and `CONTROL_PLANE_API_KEY` (a runtime key with Tunnels Read and Use, from
platform.openai.com) in `.env`, and every launcher starts the tunnel and the MCP
server together - `dev.py` adds a supervised `Tunnel` service, and `scripts/tunnel.py`
runs `tunnel-client` with the arguments AdRelay verified, the API key in the
child's environment rather than its arguments, restarting it with rising backoff.
Leave either credential empty and nothing starts; the server stays on loopback
until you press Start in the Tools tab.

`tunnel-client` itself is the operator's to install (`TUNNEL_CLIENT_BIN`, or on
PATH). The supervisor names it clearly when it is missing rather than failing
silently. What a caller may do is still the server's policy, not whoever reaches
the tunnel.

The server also answers RFC 9728 resource metadata at both
`/.well-known/oauth-protected-resource` paths, advertising no authorization
server because there is none. `tunnel-client` asks on every connection; answering
turns a per-connection warning into a deliberate "no auth server here" rather
than a 404 that reads as broken.

## Files

| Piece | File |
| --- | --- |
| Exposure policy | `services/api/src/trendrelay_api/integrations/mcp/policy.py` |
| Read context | `services/api/src/trendrelay_api/integrations/mcp/context.py` |
| Copy writes | `services/api/src/trendrelay_api/integrations/mcp/writes.py` |
| Server | `services/api/src/trendrelay_api/integrations/mcp/server.py` |
| Supervisor + status | `services/api/src/trendrelay_api/integrations/mcp/service.py` |
| Entry point | `scripts/mcp_server.py` |
| Tunnel supervisor | `scripts/tunnel.py`, started from `scripts/dev.py` |
| Tools-tab wiring | `tool_setup.py` (`mcp-server` branch), `config/tool-catalog.json` |
