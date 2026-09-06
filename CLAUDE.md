# Working rules for agents in this repository

Binding for every agent session. The reasoning and history behind each rule
lives in `docs/agent-handover.md`; this file is the short form that loads
every session. Several agents work this tree at the same time - most rules
exist because of that.

## Deleting is a commit, never a worktree edit

Never delete tracked files silently. On 2026-09-07 the desktop app and all
three shared packages (13 tracked files, four workspace roots) were found
deleted from the working tree - uncommitted, unannounced, unintended, and
load-bearing (`auth-provider` speaks to the desktop bridge). Removing
anything tracked means a commit whose message says why, in the same change
that updates whatever guards it: retiring an npm workspace requires editing
the list in `apps/web/lib/workspace-manifests.test.ts`, which otherwise
fails `npm test` loudly. If you find something deleted that you did not
delete, restore it and say so - do not commit over it.

## Git discipline beside a parallel agent

- Never `git add -A`, and never stage or commit a whole shared file without
  checking whose changes are in it (`git diff -U0 <file> | grep ^@@`).
- The index is shared: a plain `git commit` takes everything staged,
  including another agent's work; `git commit -- <paths>` takes those
  paths' *worktree* content, including their unstaged hunks. When a file
  holds both agents' edits, rebuild the blob as HEAD-plus-your-hunks and
  stage it directly (`git hash-object -w` + `git update-index`), verify
  `git diff --cached` shows only your hunks, then commit.
- After every commit, read `git show --stat HEAD` and confirm what actually
  landed. A swept commit is repaired with `git reset --soft HEAD~1`, never
  ignored.
- Never `git stash` - it sweeps the other agent's uncommitted work.

## The environment

- The dev servers and workers are the operator's. Never start, stop or
  restart them; verify against what is running (curl the port) and say so
  if something is down. The API and web hot-reload from the worktree; the
  worker does not - note when a change needs its restart.
- Database writes ship as Alembic migrations (`python scripts/db.py
  upgrade`), never as direct writes. Read the live SQLite through the
  backup API - it is in WAL mode, and `mode=ro` or `shutil` copies read
  stale snapshots.
- No bash heredocs for code or CJK text - backslashes and multibyte content
  get corrupted. Use the Write/Edit tools.

## Commits and conduct

- Commit atomically, one change per commit, with a message in prose that
  explains why - match the history's voice.
- Secrets live in the git-ignored `.env`. Never commit one, never print one.
- Anything that publishes, posts, sends, spends or reaches an external
  service is confirmed with the operator first, and endpoints that do so
  require `confirm_external_action` - keep that pattern.
- Product copy stays neutral and plain: no dated narratives, no jargon.
  History belongs in `docs/`, not in interface strings.
