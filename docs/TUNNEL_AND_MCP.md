# Reaching this workspace from outside the machine

How TrendRelay answers an assistant that is not on this computer, what each piece
does, and why it is built the way it is. Modelled on AdRelay's note of the same
name — the design it records is the one adopted here, in a FastAPI stack rather
than an Electron one.

The short version: TrendRelay serves one workspace over MCP on loopback, and
`tunnel-client` dials OpenAI's control plane outward and forwards inbound
requests to it. Nothing binds a public address, nothing accepts an inbound
connection from the internet, and what a caller may do is decided here rather
than by whoever reaches the tunnel.

The first surface is copy: an assistant selects the reviewed SOP for the action,
then reads the campaign posts queued without a caption yet — with the video, the
attached product, the destination platform and the campaign's own configuration
for context — and writes the caption, first comment and thread replies back. It
never approves or publishes them; that stays a person's decision, in the app.

---

## The parts

| Piece | File | What it is |
| --- | --- | --- |
| Exposure policy | `integrations/mcp/policy.py` | Which operations an MCP caller may invoke. Default is refusal. |
| Read context | `integrations/mcp/context.py` | The reads: the posts needing copy, and the context to write it. |
| Copy writes | `integrations/mcp/writes.py` | The one kind of write — a post's copy — through the interface's own edit helper. |
| SOP catalog | `integrations/mcp/sops.py`, `docs/sops/` | Validated action metadata and reviewed Markdown procedures, exposed as tools and resources. |
| MCP server | `integrations/mcp/server.py` | Serves the allowed tools over Streamable HTTP on `127.0.0.1`. |
| Server supervision | `integrations/mcp/service.py` | Starts/stops the server subprocess, an atomic status file, a status reader. |
| Tunnel config & health | `integrations/mcp/tunnel.py` | The tunnel's settings, command line and `doctor` check, in one place. |
| Server entry point | `scripts/mcp_server.py` | Resolves the workspace, builds the server, runs it. |
| Tunnel supervisor | `scripts/tunnel.py` | Ensures the server is up, runs the client, restarts it with backoff. |
| Launch wiring | `scripts/dev.py` | Adds the Tunnel service to every launch, when it is configured. |

The interface is **Tools → Assistant Access (MCP) → Setup**.

---

## The boundary

Decided in `policy.py`, and re-checked at the moment of every call in `server.py`
rather than trusted from the tool listing — a caller may name any tool it likes,
including one it was never offered.

**Refused, always:**

- **Credentials and sessions.** Signing in, connecting an account, saving an
  engine key. These open a window on the operator's screen, which is not a
  remote caller's to do.
- **Approval and execution.** Approving a held post, publishing, deploying a
  campaign. An approval is only an approval if a person gives it; a caller that
  can propose a change and also approve it has not been governed, it has been
  given a longer arm.

**Allowed:** reads, and workspace writes that stay in the workspace — a draft
caption, a first comment, a thread reply. Writing copy is a draft the operator
still approves in the app.

Anything not named is refused by `DEFAULT_REFUSAL`: *"Not named as reachable over
MCP. The default is refusal, so a new operation is argued for here rather than
inherited."* The refused operations are named in the policy, not merely absent,
so a caller that asks for one by name is told why — and so a test can prove the
boundary against a caller that ignores the menu.

| | Count |
| --- | --- |
| Reads | 6 |
| Workspace writes (copy) | 5 |
| Refused — credentials and sessions | 4 |
| Refused — approval, execution, deployment | 6 |

### Parity, by reuse rather than a second list

The reads reuse the interface's own serializers — the queue view, the destination
view, the campaign status — so what the assistant sees is what the operator sees.
The writes go through `apply_queue_item_edits`, extracted from the edit route so
the route and the assistant apply copy through the same code; a caption an
assistant writes is validated and stored exactly as one a person types. Full
surface parity with the GUI is the longer goal; the first surface is copy, and it
is built from shared handlers, so widening it is classifying new operations, not
reconciling two maps.

Parity does **not** mean equal authority. An operation being reachable from the
window in front of the operator is not an argument for it being reachable by a
model over a tunnel, and the two refusals above hold however many operations are
added.

### SOPs are selected by action

Reviewed procedures live as Markdown under `docs/sops/`. Each file declares a
canonical action plus unique aliases in YAML front matter. `list_sops` and
`get_sop` expose the catalog as read-only tools; `trendrelay://sops` and
`trendrelay://sops/{action}` expose the same source as MCP resources. Both paths
use one loader, so guidance cannot drift between tool and resource clients.

The loader scans recursively and validates every entry on each read. A future
SOP is therefore added as one reviewed Markdown file, while duplicate selectors,
missing metadata, and empty procedures fail visibly. An SOP guides an allowed
operation; it does not widen the MCP policy or grant execution authority.

---

## No key, loopback, and the tunnel

The server takes **no key**. A loopback listener is still reachable by every
process on the machine, but `tunnel-client` forwards what the control plane sends
it and has nowhere to carry an `Authorization` header — a key there would prevent
the only caller there is, not protect the workspace. What holds the boundary is
unchanged: a loopback-only listener, a tunnel that dials outward and mints no
public hostname, and the policy refusing credentials, approvals and anything
reaching a provider whoever is asking.

The server also answers **RFC 9728 resource metadata** at both
`/.well-known/oauth-protected-resource` paths, advertising no authorization
server because there is none. `tunnel-client` asks on every connection; the
difference between answering and a 404 is whether its warning describes a
deliberate configuration or a broken one.

---

## Configuration

Everything is an environment variable, read from `.env` like every other key in
TrendRelay — the tunnel credentials live where `buffer_api_key` and the rest do,
rather than in a separate store.

The Tools tab edits all of them, writing through `env_store` the way the
publishing credential screens do. Hand-editing `.env` still works and is still
read; what changed is that it is no longer the only way. The credentials were
by-hand on purpose, but the purpose was that a key should not be casually
pasted — not that `TUNNEL_CLIENT_BIN` and `TUNNEL_LOG_LEVEL` should be
reachable only by knowing they existed. An operator whose client was not on
PATH got "tunnel-client could not be run" and no hint that a path setting was
available anywhere.

Two things follow from writing through `env_store`. It refreshes the cached
settings, so a saved value takes effect without restarting the API. And the
saved secret is described rather than returned: the key box starts empty with
the masked value as its placeholder, so there is no path where a row of dots is
submitted and stored over a working key. A field left untouched is omitted from
the request entirely, which is what makes "keep the key that is saved" the
default rather than something to remember.

**Test tunnel connection saves first, then checks.** The doctor also calls
`refresh_settings()` itself, so it answers for a `.env` edited by hand a moment
ago rather than for whatever was configured when the API booted.

| Variable | Notes |
| --- | --- |
| `MCP_PORT` | The loopback port the server binds. Default `8765` — not `8080`, which is the API's and a contested port besides. |
| `MCP_WORKSPACE_ID` | Which workspace a caller reaches. Empty resolves to the local operator's own workspace at launch. |
| `CONTROL_PLANE_TUNNEL_ID` | `tunnel_` and 32 hex characters, from platform.openai.com. Shape-checked, so a wrong value is named here, not seconds after the client launches. |
| `CONTROL_PLANE_API_KEY` | A runtime key with Tunnels Read and Use — not an admin key. Travels in the client's environment, never its arguments. |
| `TUNNEL_CLIENT_BIN` | Optional; empty means `tunnel-client` on PATH. |
| `TUNNEL_LOG_LEVEL` | `warn` by default. `--log.level` is refused unless `--log.format` is set too, so the supervisor passes `struct-text`. |
| `TUNNEL_HEALTH_PORT` | Optional; a free port is chosen when unset. The port polled has to be the one bound. |

Both the tunnel id and the key, or neither: the launcher starts the tunnel only
when both are present, so an unconfigured machine keeps the server on loopback
and starts nothing.

---

## Supervision

Every launcher (`start.cmd`, `start-electron.bat`, `update-and-start.cmd`) runs
`dev.py`, which supervises the API, the web app and the worker. It adds a
**Tunnel** service too, but only when the tunnel is configured. `scripts/tunnel.py`
is the supervisor:

- **It ensures the server is up.** Configuring a tunnel is all it takes to serve
  the workspace; the supervisor starts the MCP server and stops it when it ends.
- **It watches the launcher by pid.** When the launcher is gone the tunnel stops,
  so a hard stop of the stack does not leave a client dialing outward forever.
- **Backoff:** 2s, 5s, 10s, 30s, 60s. Rising, because the second failure is
  usually the first one again. The count resets only after the client has stayed
  up a minute — the line between a fault and a flap.
- **A missing binary or a malformed tunnel id is named**, not failed silently.

The client is launched with the arguments AdRelay verified against a live control
plane, pinned rather than left to upstream defaults and sized for one local
operator:

```
tunnel-client run
  --control-plane.tunnel-id <id>
  --mcp.server-url http://127.0.0.1:8765/mcp
  --log.format struct-text
  --log.level warn
  --health.listen-addr 127.0.0.1:<free port>
  --mcp.max-concurrent-requests 4
  --mcp.connection-max-ttl 30m
  --control-plane.poll-timeout 30s
```

with `CONTROL_PLANE_API_KEY` in the environment, never on the argument list.

---

## The connection test

**Tools → Assistant Access (MCP) → Setup → Test tunnel connection** runs the
client's own `tunnel-client doctor --json` and shows what it reports. Reading the
client's answer beats restating its rules here: it knows which flags it accepts
and which checks it runs — the health port, the control plane, the reachability
of the MCP server — and it validates them without connecting for real. `doctor`
takes the server url in `url=` form, unlike `run`; both are the client's own
contract, kept beside each other in `tunnel.py` so they cannot drift.

---

## What is verified

- The transport is proven end to end on loopback: a client connects, lists the
  allowed tools and action-oriented SOP resources, and calls them against the
  real workspace. The reads return the post that needs copy, its attached
  product and commission, its destinations and where a follow-up lands; a write
  flips the post to having copy.
- The boundary is exercised against a caller that ignores the menu: a refused
  operation is absent from the listing and refused by name, on the running
  server, while a read in the same session answers normally.
- The tunnel is exercised without the real binary: the command matches the
  reference and the key stays out of the arguments; the supervisor's spawn,
  status, backoff and parent-watch run with a stubbed client; and `doctor`'s
  output is parsed into the Tools tab's result.

`tests/test_mcp.py` holds the boundary and the handlers; the transport and the
supervisor are the tool's own launch.

---

## How this differs from AdRelay

- **Configuration is `.env`, not an OS-encrypted vault with a Settings form.**
  TrendRelay keeps every key in `.env`, so the tunnel credentials live there too,
  shown-but-not-editable in the Tools tab — consistent with the rest of the app.
- **Supervision is a launcher service, not a desktop main-process supervisor.**
  `dev.py` is the process supervisor every launcher runs, so the tunnel is one
  more supervised service beside the worker rather than an Electron singleton.

What is the same is the part that matters: loopback listener, outward-dialing
tunnel, a policy that refuses credentials, approvals and execution, and a client
launched with the key in its environment and the arguments AdRelay proved.
