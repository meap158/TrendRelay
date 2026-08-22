# TrendRelay action SOP catalog

This top-level folder holds reviewed operating procedures for actions performed by people
or assistants in TrendRelay. Each procedure is one Markdown file. The MCP server
discovers the files recursively at read time, so a new SOP does not require a
new Python handler or server restart during development.

## Choosing an SOP

SOPs are selected by the action being attempted. MCP callers should:

1. Call `list_sops` or read `trendrelay://sops`.
2. Select the closest canonical `action`.
3. Call `get_sop` or read `trendrelay://sops/{action}` before acting.
4. Use live workspace context as the source of truth where the SOP directs it.

An SOP is guidance, not additional authority. MCP policy still controls which
reads and writes are allowed.

## Adding an SOP

Place a `.md` file anywhere below this directory, except the reserved
`README.md`, `MCP_GUIDE.md`, and `CONTROL_TOWER.md` files, with this front
matter:

```yaml
---
id: campaigns.fill-needs-copy
action: campaigns.fill-needs-copy
title: Fill campaign posts that need copy
summary: Write only missing campaign copy from live campaign and post context.
version: 1
tags: [campaigns, copywriting]
aliases: [fill-campaign-needs-copy]
---
```

Required fields are `id`, `action`, `title`, and `summary`. IDs, actions, and
aliases must be unique across the catalog. Use a namespaced, imperative action
such as `campaigns.fill-needs-copy`; keep aliases for natural task variants.
Increment `version` when behavior changes materially. The catalog loader rejects
missing metadata, empty bodies, invalid lists, and ambiguous selectors.

`MCP_GUIDE.md` is the AI's general operating entry point and
`CONTROL_TOWER.md` routes intended actions to SOPs. The MCP server reads those
exact files into its initialization instructions and exposes them as resources;
keep detailed action procedures in separately cataloged files.
