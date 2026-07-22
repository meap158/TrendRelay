# Agent Reach diagnostics plugin

This adapter reflects the channel registry from the exact pinned Agent Reach source and reports local prerequisite presence. Its permission contract is deliberately empty: no network calls, command execution, storage writes, browser-session access, user configuration, or secrets.

The report does not establish live platform availability. Authenticated platforms need a later channel-specific contract and explicit user setup before TrendRelay may use them.
