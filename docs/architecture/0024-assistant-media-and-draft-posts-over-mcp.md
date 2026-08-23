# ADR 0024: An assistant brings media in and proposes posts, as drafts

Status: Accepted and built - `services/api/src/trendrelay_api/integrations/mcp/intake.py`,
the `campaigns.add-post-with-media` SOP, surfaced through the same MCP server
as ADR 0023's copy surface.

## Context

ADR 0023 gave an outside assistant the copy surface: it fills in captions on
posts the operator already queued, and named surfaces beyond copy as not yet
built. The next thing an assistant is asked for is the step before copy -
"here is an image, post it to the campaign" - which needs two capabilities the
policy did not have: bringing a file into the workspace, and making a post
exist.

ChatGPT's MCP connectors have a convention for the first: a tool that declares
`_meta["openai/fileParams"]` receives a user's chat attachment as an object
carrying a signed temporary `download_url`, which the server fetches. Other
MCP clients have no such convention and can only pass a URL. Both arrive at
the same place: the server fetching bytes from an address a remote caller
chose.

## Decision

**Uploads go through the operator's own ingest, as their own source.** There is
no second media pipeline. `upload_image` writes the fetched bytes inside the
first approved media root (`mcp-uploads/`, named by content digest) and hands
the path to `create_ingest_job` - the same dedupe-by-digest, immutable
original, and audit trail as a file the operator imports. The asset appears in
the Library under `source_type="mcp-upload"`, so where it came from is visible
wherever the asset is; `platform` is only ever what the caller states, never
invented. The import is asynchronous like every ingest, so `get_import_status`
exists to poll the job for its `asset_id`.

**The fetch is guarded, because the URL is the caller's.** A remote model asking
this machine to fetch a URL is a server-side request with a caller-chosen
target. The guard refuses everything but a direct HTTPS fetch of a public
address: redirects are refused (a public URL answering 302 to loopback is the
standard escape), every resolved address must be global (nothing loopback,
private, link-local or reserved - this machine runs services on loopback that
no outside caller may reach through us), the read stops at 25 MB, and the
content type the server reports - never the URL's own suffix - must be an
image type the Library accepts. ChatGPT's signed upload URLs pass all of this;
they are fetched once and never stored, because they expire and carry an
access token in their query string.

**A post an assistant creates is a draft, never rotation-ready.** This is the
argued exception to "an assistant configures, a person executes". A schedule
change moves work a person already authorised; `create_campaign_post` makes a
post exist - and in a campaign whose authority has been earned up to
autonomous, a rotation-ready post is a published one. So the state is decided
where the boundary lives: the shared `create_queue_item` helper takes the
state, the interface's route passes `approved` (adding media *is* the
operator's decision), and the MCP path hard-codes `draft` - the queue's
parking brake, outside the rotation, promotable only in the app. A caller
cannot ask for another state; the tool has no parameter for it.

**Parity stays by reuse.** `create_queue_item` is extracted from the route the
interface already posts through, so an assistant's post passes the same
validation, the same offer check, and the same rotation bookkeeping as one the
operator adds - and the copy fields pass the same no-links rule as every MCP
copy write, because the campaign carries the link itself. Media is named by
Library asset id, never by filesystem path, which is not a caller's to know.

**The file convention is metadata, not a fork.** `upload_image` declares
`meta={"openai/fileParams": ["image"]}` (the Python SDK carries it to the wire
as `_meta`). A client that understands it routes a chat attachment into the
`image` argument; every other client passes `image_url`. One tool, both
clients, no divergence in what happens after the fetch. The convention is
recorded in `docs/third-party/openai-file-params.md`.

## Consequences

- An assistant can be handed an image in chat and asked to post it: it uploads,
  waits for the asset id, proposes a draft post with copy, and tells the
  operator a draft is waiting. The operator promotes it in the app; nothing an
  assistant does publishes anything.
- Three operations join the policy on purpose: `upload_image` and
  `create_campaign_post` as workspace writes, `get_import_status` as a read.
  The refusals of ADR 0023 are unchanged and re-checked at every call.
- The `campaigns.add-post-with-media` SOP is the procedure an assistant loads
  before acting; `SOP/MCP_GUIDE.md` routes the action to it.
- Uploads are bounded: image types only, 25 MB, into one directory inside an
  already-approved media root. Video upload is deliberately not offered -
  video enters through the Library's own import paths - and can be argued for
  separately if it is ever wanted.
- `tests/test_mcp.py` proves the guard refuses plain HTTP, loopback, private
  and link-local addresses; that a wrong content type is refused on what the
  server said rather than what the URL claimed; that a created post arrives as
  a draft with the placeholder or the given copy; that the no-links rule holds
  on this surface; and that the `openai/fileParams` declaration survives to
  the tool listing, where losing it would silently break the ChatGPT client.
