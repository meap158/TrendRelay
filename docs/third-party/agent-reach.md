# Panniantong/Agent-Reach

- Repository: https://github.com/Panniantong/Agent-Reach
- Pinned revision: `1494c2ab239e7355a77e7cceaf3271453a1f34b5`
- Audited release line: 1.4.x
- License: MIT
- TrendRelay role: research-channel capability discovery and diagnostics
- Local source: `.tools/catalog/agent-reach/source`

Agent Reach discovers and diagnoses upstream tools for web pages, GitHub, YouTube, RSS, X, Reddit, Bilibili, Xiaohongshu, Instagram, and other channels. Its full installer can add system tools, MCP configuration, agent skills, and browser-session integrations. Cookie-authenticated platforms carry account restriction and credential risk.

TrendRelay installs only the pinned source checkout and never invokes Agent Reach's environment installer automatically. Activation does not import browser cookies or modify user-level agent/MCP configuration. Future integration should consume channel diagnostics behind TrendRelay capability contracts and require explicit setup for every authenticated channel.
