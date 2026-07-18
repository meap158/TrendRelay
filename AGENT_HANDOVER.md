# Agent Handover

Last updated: 2026-07-18

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises the backend and frontend in one terminal with prefixed logs, staggered startup, LAN binding, process-tree cleanup, Uvicorn hot reload, and Next.js fast refresh.
- `start.cmd` installs missing dependencies and invokes the unified runner; pass `--desktop` to include Electron.
- PostgreSQL/pgvector, Redis, and S3-compatible storage remain the planned data services, installed locally or provided as managed services when required.
- Architecture, plugin, platform-capability, worker, and link-router boundaries are documented.
- `Research/` and `References/` are local-only and ignored by Git.
- No production features, database migrations, authentication, Temporal setup, or external connectors exist yet.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory.
- Python/FastAPI is the control-plane runtime; Python also powers compute-heavy workers.
- The core calls capability interfaces; provider logic belongs in isolated plugins.
- Start with a modular monolith and retain `workspace_id` on every business record.
- Keep original/reference media immutable and separate from publishable derivatives.
- The first vertical slice is auth, workspace roles, audit events, secret references, plugin registry, and workflow-run contracts.

## Validation completed

- FastAPI package installed successfully in the project `.venv` under Python 3.14.
- `pytest -p no:cacheprovider services/api/tests` — one health endpoint test passed.
- `ruff check scripts/dev.py services/api` and `ruff format --check scripts/dev.py services/api` — passed.
- `npm run typecheck`, `npm run lint`, and `npm run build` — passed.
- Unified runner live test — backend and frontend returned HTTP 200; hot-reload processes started; prefixed UTF-8 logs remained active; the spawned process tree was cleaned up.
- `start.cmd` smoke check — passed without leaving development servers running.
- Tracked implementation and documentation were scanned for superseded runtime artifacts; none remain.

## Next recommended action

Implement the first vertical slice contract-first: define Pydantic workspace/auth schemas and database migrations, add FastAPI domain modules, then connect a minimal login/workspace flow in the web app. Add the Temporal Python SDK when the first durable workflow is implemented. Update README and this file with commands and results in the same atomic commit.
