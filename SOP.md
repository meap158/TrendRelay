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

1. Begin by reading `README.md`, `SOP.md`, and `AGENT_HANDOVER.md` if it is present, then inspect `git status` before editing. The handover is local working notes and is not in the repository, so a fresh clone will not have one; `README.md` and `docs/architecture/` are the record that travels.
2. Preserve unrelated user changes. Work in the smallest independently verifiable slice.
3. Never commit credentials, private source media, or local research. `Research/` and `References/` remain ignored.
4. Record important architectural decisions in `docs/architecture/`.
5. **Always maintain `README.md` as the project entry point.** Keep its project goal, technology stack, repository structure, setup commands, and current release scope accurate. Update it in the same commit whenever any of those change.

## Atomic commits and handover (mandatory)

1. **Always commit atomically.** Each commit contains one coherent change that can be reviewed, tested, and reverted independently.
2. **Always use descriptive commit messages.** Use an imperative subject that states the outcome; add a body when motivation or tradeoffs are not obvious.
3. Do not mix formatting, refactors, dependency upgrades, and product behavior unless inseparable.
4. Before every commit, inspect the staged diff and run relevant validation. Never claim checks that were not run.
5. **Always maintain `AGENT_HANDOVER.md`.** It is git-ignored working notes rather than project history, so it is kept current but never committed. Anything that belongs in the repository's own record - a decision, a setup step, a behaviour worth knowing - goes to `README.md` or `docs/architecture/` in the commit that changes it, and does not rely on the handover to survive.
6. Keep the handover concise and current. Replace stale status instead of accumulating a session diary.
7. End every session with completed work, validation, blockers, and the next recommended action recorded in the handover.

## Interface work (mandatory)

Visual design is part of the deliverable on every task that touches the interface, not a follow-up pass. A control that works but breaks its container, or the wrong control for the job, is unfinished work.

1. **Always pick the conventional control for the interaction.** A switch for state that takes effect immediately; a checkbox for selection inside a form or set; a radio group for one of several. A control that promises the wrong interaction is wrong even when it functions.
2. **Always build a reusable control once, in `apps/web/app/ui/`.** Do not inline a new control in a page. The next use must be able to reach for the same component rather than reproduce it.
3. **Always keep native semantics.** Replace a control visually, never structurally: the input stays in the tree so focus order, keyboard behaviour, and the accessible role come from the platform.
4. **Always verify layout at the widths the design actually uses**, with the content it will actually hold — a long error message from an upstream service, a long name, an empty state. Assuming a layout holds is not verifying it. When a page cannot be reached (for example, it is behind a session you do not have), check the markup against the real stylesheets in a throwaway harness and delete the harness in the same change.
5. **Always use CSS logical properties** (`inset-inline-start`, `margin-inline-end`, `padding-block`) rather than physical ones. The app ships Arabic; a physical property is a second rule waiting to be forgotten.
6. **Always add every new user-facing string to all seven dictionaries** in `apps/web/lib/i18n/messages/`, with the plural categories that language uses. `scripts/i18n_scan.py` must still report 100%.
7. Motion should honour `prefers-reduced-motion`. Focus indicators must remain visible.
8. **Always preserve layout stability across interaction states.** Selecting rows, starting work, completing work, or revealing contextual actions must not insert a new toolbar or status row that pushes the primary content. Search, selection counts, and bulk actions belong in one reserved toolbar slot; keep controls in a predictable position and disable them when unavailable. Use an intentional disclosure, dialog, or overlay only when the user explicitly asks to reveal additional content, and verify the content's document position before and after ordinary state changes at desktop and narrow widths.
9. **Always follow the established product design when adding or changing interface elements.** Start with `DESIGN.md`, the semantic tokens, and the reusable controls in `apps/web/app/ui/`; extend the shared component when a needed state is missing instead of creating page-local styling or falling back to browser-default chrome. New elements must match the app's typography, spacing, radius, colour, focus, motion, responsive, and RTL conventions. Any intentional exception must state why the existing system cannot serve the interaction and must be documented and verified at the same quality gates as the rest of the interface.

## Definition of done

A change is complete only when its implementation, relevant checks, README, documentation, and handover are current; its diff contains no unrelated work; and it is recorded in an atomic descriptive commit when committing is authorized. For interface work, it is complete only when the rules above have been applied and the layout has been verified rather than assumed.

`npm test` runs both suites: the Python tests and the browser app's own. A decision the interface makes - what a rule does to a caption, what order a list ends up in, which route a destination takes - belongs in a plain `.ts` module beside `lib/publish-rules.ts` and gets a test there. It is the only part of the interface that can be checked without a browser, and the parts that fail quietly are exactly these: a disclosure prepended twice still looks like a disclosure, and a post sent through an engine with no quota still looks sent.
