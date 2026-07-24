# ADR 0011: Meta Ads intelligence is read-only

## Status

Accepted.

## Decision

TrendRelay incorporates the exact pinned Meta Ads Kit source as an account-intelligence reference and installs its renamed Social Flow CLI runtime in an isolated tool directory. A native Python adapter exposes only five bounded read reports: account status, active campaigns, campaign performance, ad performance, and daily fatigue fields.

The Research page presents three distinct roles: Last 30 Days executes recent-topic discovery, Agent Reach provides local channel-capability diagnostics, and Meta Ads Kit provides first-party campaign validation. Agent Reach readiness must never be represented as successful live research or authentication.

Meta Ads briefings are loopback-only and explicitly confirmed. The adapter does not construct or accept arbitrary commands, and mutation verbs are absent from its command builder. Provider errors are sanitized; token values and provider stderr are not returned. Authentication remains in the isolated CLI's own user-controlled profile.

## Consequences

TrendRelay can compare market interest with owned campaign signals without granting its control plane authority to pause ads, alter budgets, create campaigns, or upload creatives. Any future write capability requires a separate ADR, workspace authorization, immutable proposal, spend-impact preview, idempotency strategy, and explicit approval.