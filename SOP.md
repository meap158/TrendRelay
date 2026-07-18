# TrendRelay Standard Operating Procedure

This document contains the durable operating rules for humans and agents working on TrendRelay.

## Product and architecture

1. Build as a modular monolith first. Extract services only after operational evidence justifies it.
2. Keep platform-specific logic behind versioned capability interfaces and plugins.
3. Make every external operation idempotent and auditable.
4. Keep original media immutable and separate reference content from publishable content.
5. Reference credentials through scoped secret identifiers; never embed secrets in source, logs, workflows, or renderer code.
6. Attach evidence to every opportunity score and provide a manual fallback for every publication.
7. Treat API access and regional availability as runtime capabilities, not assumptions.
8. Keep AI providers replaceable. MCP may expose agent tools; durable execution belongs to the workflow engine.
9. Optimize for trend-to-publication latency and attributable revenue, not generated-content volume.

## Development workflow

1. Begin by reading `README.md`, `SOP.md`, and `AGENT_HANDOVER.md`, then inspect `git status` before editing.
2. Preserve unrelated user changes. Work in the smallest independently verifiable slice.
3. Never commit credentials, private source media, or local research. `Research/` and `References/` remain ignored.
4. Record important architectural decisions in `docs/architecture/`.
5. **Always maintain `README.md` as the project entry point.** Keep its project goal, technology stack, repository structure, setup commands, and current release scope accurate. Update it in the same commit whenever any of those change.

## Atomic commits and handover (mandatory)

1. **Always commit atomically.** Each commit contains one coherent change that can be reviewed, tested, and reverted independently.
2. **Always use descriptive commit messages.** Use an imperative subject that states the outcome; add a body when motivation or tradeoffs are not obvious.
3. Do not mix formatting, refactors, dependency upgrades, and product behavior unless inseparable.
4. Before every commit, inspect the staged diff and run relevant validation. Never claim checks that were not run.
5. **Always maintain `AGENT_HANDOVER.md`.** Update it in the same atomic commit whenever project state, decisions, setup, risks, validation, or next steps change.
6. Keep the handover concise and current. Replace stale status instead of accumulating a session diary.
7. End every session with completed work, validation, blockers, and the next recommended action recorded in the handover.

## Definition of done

A change is complete only when its implementation, relevant checks, README, documentation, and handover are current; its diff contains no unrelated work; and it is recorded in an atomic descriptive commit when committing is authorized.
