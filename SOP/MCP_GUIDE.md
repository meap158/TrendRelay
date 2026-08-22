# TrendRelay MCP guide

This file is the control tower and operating entry point for an AI connected to
TrendRelay over MCP. The server reads this exact file into its initialization
instructions and exposes it at `trendrelay://mcp/guide`.

## Route the action first

Identify the intended action before choosing a tool. Do not begin with whichever
write operation looks convenient.

| Intended action | Canonical action | First live operation |
| --- | --- | --- |
| Fill missing Campaigns copy | `campaigns.fill-needs-copy` | `list_posts_needing_copy` |

For an action not listed here, call `list_sops`. Match its canonical action or
an alias. If no reviewed SOP exists, follow current explicit user direction and
the MCP server's general policy; do not invent a procedure or broaden authority.

## Start here

1. Identify the action using the routing table above.
2. Call `list_sops` to discover reviewed action procedures.
3. Call `get_sop` with the canonical action or read
   `trendrelay://sops/{action}` before using action-specific tools.
4. Read live workspace state immediately before acting. Never substitute memory,
   an old queue, or assumptions from another campaign.
5. Use the narrowest allowed operation that achieves the requested change.
6. Re-read the relevant live state after writing and verify the requested result
   before declaring completion.

For `campaigns.fill-needs-copy`, load the SOP, read the live campaign and each
post's context, write only fields reported as missing, refresh after each batch,
and use a final fresh queue read as completion evidence.

## Authority and safety

An SOP explains how to use authority already granted by the MCP policy; it does
not grant new authority. TrendRelay MCP may read workspace context and write
draft copy that remains inside the workspace. It may not sign in, connect an
account, approve content, publish, deploy, delete, or trigger another external
side effect reserved for the operator.

Current explicit user instructions outrank an SOP. Campaign-specific rules and
live post configuration outrank general defaults. When instructions conflict,
follow the priority order in the selected action SOP.

## Working with future SOPs

The canonical SOP root is the repository's `SOP/` directory. Procedures are
discovered recursively from Markdown front matter, so new action files become
available without adding another MCP handler. Never use a similarly named copy
outside this directory as the source of truth.
