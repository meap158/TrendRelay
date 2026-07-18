# Agent Handover

Last updated: 2026-07-18

## Current state

- Repository bootstrapped as a hybrid web/desktop, modular-monolith monorepo.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a Go API health endpoint are scaffolded.
- Windows users can double-click `start.cmd` to validate prerequisites, install dependencies on first run, and launch the web app.
- Local PostgreSQL/pgvector, Redis, and MinIO services are defined with Docker Compose.
- Architecture, plugin, platform-capability, worker, and link-router boundaries are documented.
- `Research/` and `References/` are local-only and ignored by Git.
- No production features, database migrations, authentication, Temporal setup, or external connectors exist yet.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory.
- The core calls capability interfaces; provider logic belongs in isolated plugins.
- Start with a modular monolith and retain `workspace_id` on every business record.
- Keep original/reference media immutable and separate from publishable derivatives.
- The first vertical slice is auth, workspace roles, audit events, secret references, plugin registry, and workflow-run contracts.

## Validation completed

- `npm install` — passed; lockfile generated.
- `npm run typecheck` — passed for desktop, web, plugin SDK, and schemas.
- `npm run lint` — passed for the web scaffold.
- `npm run build` — passed for Electron and Next.js; Electron emits expected warnings because its renderer is the separately served web app and the preload is intentionally empty.
- `npm audit` — passed with zero known vulnerabilities.
- `start.cmd` prerequisite and launch command flow was inspected and parsed successfully; an interactive development server was not left running.
- Go and Docker are not installed in the current environment, so API and Compose runtime checks remain pending.

## Next recommended action

Implement the first vertical slice contract-first: define workspace/auth schemas and migrations, add API modules, then connect a minimal login/workspace flow in the web app. Add Temporal to the local environment when the first durable workflow is implemented. Update README and this file with commands and results in the same atomic commit.
