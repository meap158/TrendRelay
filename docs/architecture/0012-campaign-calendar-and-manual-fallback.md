# ADR 0012: Persist campaign plans and guarantee a manual publication fallback

Status: accepted

## Context

TrendRelay already produces approved local media and can submit governed Postiz operations, but a remote provider must not be the only path from an approved asset to publication. The product concept requires a campaign builder, content calendar, approval lock, full audit trail, duplicate protection, and a manual package for unsupported or unavailable platform capabilities.

## Decision

Campaigns and publication plans are first-party, workspace-scoped SQL records. A campaign holds the objective, audience, markets, languages, and optional affiliate destination. A publication plan binds one campaign to an existing approved MP4, optional cover, platform, caption, hashtags, disclosure, suggested time, timezone, and platform deep link.

Plans enter `needs_approval` and may be approved or rejected only by an owner or approver with governed assurance. The video and optional cover are SHA-256 fingerprinted when the plan is created and verified again before approval and export. A decided plan has no mutation endpoint, making its approved metadata and exact media bytes content-locked. Postiz remains a separate remote execution path.

Every approved plan can produce an idempotent local ZIP package. The package contains:

- the final MP4;
- an optional cover;
- `manifest.json` with posting metadata and the affiliate destination;
- `caption.txt` with caption, hashtags, and disclosure.

Package export is loopback-only, explicitly confirmed, audited with its path and SHA-256 digest, and written below `.data/manual-packages/`. Repeated export of the same locked plan returns the same artifact rather than creating variants.

## Consequences

- Publication planning survives browser, API, and worker restarts.
- Unsupported platforms retain a complete operator handoff instead of becoming dead ends.
- Approval and package export are independently auditable.
- Large videos are copied into ZIP files, which trades local disk usage for a self-contained manual handoff. A future object-storage implementation may replace the local package writer without changing the plan contract.
- Remote Postiz delivery status and the first-party calendar remain distinct until a later publication-attempt model unifies them.
