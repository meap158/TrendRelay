# ADR 0005: Agent Reach diagnostics boundary

## Decision

TrendRelay uses the pinned Agent Reach channel registry as a capability reference, but its adapter performs only local presence checks. It does not import or execute Agent Reach, run commands, probe networks, read user-level configuration, inspect browser sessions, or reveal secret values.

The upstream installer, uninstaller, skill installer, MCP configuration, and cookie-import commands are outside the trusted boundary. Authenticated channels remain `setup-required` until a later channel-specific adapter defines scoped credential storage, account-risk controls, and explicit authorization.

## Consequences

Diagnostics are safe to display in the local control surface and deterministic in tests. A `ready` result means local prerequisites appear present, not that a live platform request or authenticated session has succeeded.
