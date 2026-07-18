# ADR 0002: Manage third-party tools as pinned, isolated capabilities

## Status

Accepted on 2026-07-19.

## Context

TrendRelay combines research, media, creative, and publishing projects from different maintainers. Their licenses, credentials, runtimes, update cadences, and platform risks differ. Copying their code into the modular monolith would obscure provenance and make upgrades, removal, and compliance difficult.

## Decision

Maintain one machine-readable catalog at `config/tool-catalog.json` and one human-readable catalog at `docs/third-party/README.md`. Every upstream repository must declare an exact revision, license posture, capability summary, isolated path, integration maturity, and documentation page.

Installation and activation are separate:

- Installation fetches only a reviewed revision into ignored `.tools/` storage, or delegates to an existing audited TrendRelay wrapper.
- Activation is local state under ignored `.data/` and only makes an installed, permitted tool eligible for orchestration.
- The API permits lifecycle mutations only from loopback clients and requires explicit confirmation for install/uninstall.
- Uninstall removes only the catalog-declared path beneath `.tools/` and deactivates the tool.
- License-incompatible projects remain visible but cannot be installed or activated.

Source installation does not imply provider dependencies, credentials, browser sessions, paid API use, or a production-ready adapter. Those remain capability-specific work with their own approval and audit boundaries.

## Consequences

Users can see provenance and local status in one page and safely remove optional sources. Revisions do not update implicitly. Each production adapter must still define schemas, health checks, secrets, idempotency, and rights/policy handling. AGPL projects require a distribution/network-use compliance review; non-commercial projects cannot enter commercial workflows without separate permission.
