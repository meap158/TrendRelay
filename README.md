# TrendRelay

TrendRelay is an affiliate trend-to-content orchestrator. It connects trend discovery, demand validation, affiliate matching, creative research, content production, approval, publishing, and revenue attribution in one workflow.

## Project goal

Turn fragmented research and publishing tools into a repeatable, evidence-backed loop:

`Discover → validate demand → match offers → analyze content → create variations → approve → publish → measure revenue → learn`

The first usable release prioritizes reliable research, media handling, publishing, and attribution before costly AI video generation.

## Technology stack

| Layer | Technology |
| --- | --- |
| Web | Next.js, React, TypeScript |
| Desktop | Electron and electron-vite with a hardened renderer boundary |
| Control plane | Go modular monolith using the standard HTTP stack initially |
| Shared contracts | TypeScript packages; Go SDK boundary reserved |
| Data | PostgreSQL with pgvector |
| Cache and coordination | Redis |
| Object storage | S3-compatible storage / MinIO locally |
| Durable workflows | Temporal (planned in the foundation slice) |
| Media | FFmpeg, ffprobe, yt-dlp in isolated workers (planned) |
| AI workers | Python provider adapters and ComfyUI connectors (planned) |
| Local development | npm workspaces and Docker Compose |
| Production direction | Containers and Terraform; Kubernetes only when justified |

## Repository structure

- `apps/web` — Next.js control surface
- `apps/desktop` — Electron shell for local media and browser-assisted workflows
- `services/api` — Go control plane (modular monolith)
- `services/link-router` — first-party attribution redirect boundary
- `workers` — isolated trend, media, AI, and publishing runtimes
- `packages` — shared UI, schemas, SDKs, and workflow definitions
- `plugins` — replaceable provider implementations
- `infra` — local and production infrastructure
- `docs` — architecture decisions and capability contracts

The core depends on capabilities, never platform-specific business logic. See [SOP.md](./SOP.md) for engineering rules and [AGENT_HANDOVER.md](./AGENT_HANDOVER.md) for current state.

## Quick start

Prerequisite for the current web scaffold: Node.js 22+ with npm 10+.

### Windows — easiest

Double-click `start.cmd`. The launcher checks Node/npm, installs dependencies on the first run, and starts TrendRelay at `http://localhost:3000`.

### Command line

```bash
cp .env.example .env
npm install
npm run dev:web
```

Go 1.24+ and Docker with Compose are also required for the API and local infrastructure. Start infrastructure with `docker compose -f infra/compose/docker-compose.yml up -d`. Run the API from `services/api` with `go run ./cmd/server`.

The first product vertical slice is authentication, workspaces/roles, audit logging, secret references, and the plugin registry.

## Local-only material

`Research/` and `References/` are intentionally ignored. They may contain working notes or licensed/source material and are not part of the distributable repository.

## Documentation rule

This README is a living entry point. Any change to the product goal, stack, structure, setup, major commands, or release scope must update this file in the same atomic commit.
