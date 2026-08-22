# TrendRelay MCP control tower

This file routes an AI connected over MCP to the correct operating procedure.
The server reads this exact file into its initialization instructions and exposes
it at `trendrelay://mcp/control-tower`.

## Routing rule

Identify the action first, then load its SOP. Do not begin with whichever write
tool looks convenient.

| Intended action | Canonical action | First live operation |
| --- | --- | --- |
| Fill missing Campaigns copy | `campaigns.fill-needs-copy` | `list_posts_needing_copy` |

For an action not listed in this table, call `list_sops`. Match its canonical
action or an alias. If no reviewed SOP exists, use the MCP server's general
instructions and current explicit user direction; do not invent a procedure or
broaden authority.

## Campaign copy route

For `campaigns.fill-needs-copy`:

1. Load the SOP before reading or writing the queue.
2. Read the live campaign and each post's context.
3. Write only fields explicitly reported as missing.
4. Refresh the queue after each batch.
5. Use a final fresh queue read as completion evidence.

The live `SOP/` catalog remains authoritative as more actions are added. This
control tower is a concise route map, not a second copy of each procedure.
