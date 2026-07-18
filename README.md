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
| Control plane | Python, FastAPI, Uvicorn, Pydantic |
| Shared contracts | TypeScript schemas and Pydantic models |
| Data | PostgreSQL with pgvector |
| Cache and coordination | Redis |
| Object storage | S3-compatible managed or local storage |
| Durable workflows | Temporal Python SDK (planned in the foundation slice) |
| Media | FFmpeg, ffprobe, and isolated Python workers |
| Media download provider | Pinned `jiji262/douyin-downloader` integration |
| AI workers | Python provider adapters and ComfyUI connectors (planned) |
| Local development | npm workspaces and a Python virtual environment |
| Production direction | Managed services and Terraform; orchestration only when justified |

## Repository structure

- `apps/web` — Next.js control surface
- `apps/desktop` — Electron shell for local media and browser-assisted workflows
- `scripts/dev.py` — unified hot-reload supervisor for local development
- `douyin.cmd` — isolated Douyin installation and batch-download entry point
- `services/api` — Python/FastAPI control plane (modular monolith)
- `services/link-router` — first-party attribution redirect boundary
- `workers` — isolated trend, media, AI, and publishing runtimes
- `packages` — shared UI, schemas, TypeScript SDK, and workflow definitions
- `plugins` — replaceable provider implementations
- `infra` — infrastructure guidance and future Terraform modules
- `docs` — architecture decisions and capability contracts

The core depends on capabilities, never platform-specific business logic. See [SOP.md](./SOP.md) for engineering rules and [AGENT_HANDOVER.md](./AGENT_HANDOVER.md) for current state.

## Quick start

Prerequisites: Node.js 22+ with npm 10+, and Python 3.12+.

### Windows — easiest

Double-click `start.cmd`. It validates prerequisites, creates `.venv`, installs both dependency sets, then hands off to the unified runner. Backend and frontend logs stay in one terminal with prefixes, both servers bind to the LAN, and code changes hot reload automatically. Run `start.cmd --desktop` to include the Electron shell.

### Command line

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "services/api[dev]"
npm install
.\.venv\Scripts\python.exe scripts\dev.py
# Add --desktop to also launch Electron.
```

API documentation is available at `http://localhost:8080/docs` during development.

The first product vertical slice is authentication, workspaces/roles, audit logging, secret references, and the plugin registry.

## Douyin batch downloader

TrendRelay integrates the MIT-licensed `jiji262/douyin-downloader` provider at a pinned revision. Its source, dependencies, cookies, database, and downloaded media stay outside Git under `.tools/` and `.data/`.

Install and verify the provider:

```powershell
douyin.cmd install
douyin.cmd check
```

Download a single video or profile:

```powershell
douyin.cmd batch "https://www.douyin.com/video/VIDEO_ID"
douyin.cmd batch "https://www.douyin.com/user/SEC_UID" --mode post --limit 50
```

Run a newline-delimited batch with deduplication and incremental updates:

```powershell
douyin.cmd batch --file .\douyin-urls.txt --mode post --mode mix --limit 50 --incremental
```

The default limit is 50 items per selected profile mode; use `--limit 0` only for an intentional full crawl. Use `--dry-run` to inspect redacted configuration without downloading. For browser fallback, run `douyin.cmd install --browser`, then add `--browser-fallback` to the batch command.

Optional authenticated access reads `DOUYIN_*` variables from the environment or local `.env`; values are never printed and the generated runtime configuration is deleted after each run. Downloads are written to `.data/downloads/douyin/`. Only download and reuse content when permitted by platform terms and applicable rights.

## Local-only material

`Research/` and `References/` are intentionally ignored. They may contain working notes or licensed/source material and are not part of the distributable repository.

## Documentation rule

This README is a living entry point. Any change to the project goal, stack, structure, setup, major commands, or release scope must update this file in the same atomic commit.
