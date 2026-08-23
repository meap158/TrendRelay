# OpenAI `openai/fileParams` (files into MCP tools)

- Specification: OpenAI Apps SDK / ChatGPT MCP connector documentation
  (https://developers.openai.com/apps-sdk)
- Kind: a `_meta` convention on MCP tool definitions, not a library
- Status: adopted on the `upload_image` MCP tool
- Verified against: `mcp` Python SDK 1.27.0, which carries a tool's `meta`
  through to the wire listing (checked 2026-08-23)

How ChatGPT hands a user's file attachment to an MCP tool. It is a convention
layered on standard MCP, so a server that adopts it stays an ordinary MCP
server for every other client.

## The convention

A tool that wants to receive files declares which of its parameters carry them,
in the tool definition's `_meta` field:

```json
{
  "name": "upload_image",
  "inputSchema": { "properties": { "image": {}, "title": { "type": "string" } } },
  "_meta": { "openai/fileParams": ["image"] }
}
```

When a ChatGPT user attaches a file and the model calls the tool, ChatGPT
replaces each declared parameter's value with a file reference object:

```json
{ "file_id": "sediment://...", "download_url": "https://..." }
```

The `download_url` is a signed, temporary link to the file's bytes. The server
fetches it; the bytes never travel through the model. In widget/app flows the
Apps SDK's `window.openai.uploadFile()` produces the same kind of reference.

## How TrendRelay uses it

The MCP `upload_image` tool declares `meta={"openai/fileParams": ["image"]}`
(the Python SDK's `meta=` lands on the wire as `_meta`). Two properties of the
`download_url` are treated as facts, not guarantees:

- **It expires and carries an access token in its query string**, so it is
  fetched once and never stored. Anything worth keeping about where an image
  came from goes in the tool's explicit `source_url` parameter.
- **It is a URL the caller chose**, so it passes the same server-side-request
  guard as any `image_url`: direct HTTPS only, no redirects, no private or
  loopback destination, a size cap, and only the image types the Library
  accepts. ChatGPT's own upload URLs pass these because they are direct HTTPS
  links to public storage.

Clients that do not implement the convention (Claude, generic MCP clients)
ignore the `_meta` entry and pass the tool's plain `image_url` parameter
instead; the tool accepts exactly one of the two.

Adopted for the media-intake surface recorded in
[ADR 0024](../architecture/0024-assistant-media-and-draft-posts-over-mcp.md).
