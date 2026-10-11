# Tasks

Operator-requested work that is noted but not yet built. One bullet per task;
remove it when the work lands, and name the commit that did.

- The dev server is holding about 12 GB of memory. Reported on 2026-09-28 by
  another agent working on this machine, whose own scan was nearly killed by
  low memory; it named PID 36600, the web dev server, as the holder. Memory
  rather than disk, so it is a separate question from the storage work that
  landed on 2026-09-27 - what to look at is why a Next dev server grows to
  that: the number of compiled routes held in memory, the file watchers, the
  source maps, and whether anything in `apps/web` keeps per-request state that
  a dev process never drops. The server is the operator's, so this is an
  investigation and a fix, never a restart.
- Campaign distribution rules: a per-account image/video mix, pages that take
  only some media kinds, an "already on this page" ledger, dated burst days
  with their own cap, and spacing for the same product or clip. Requested on
  2026-10-10 after these were done by hand over MCP for one campaign; the plan
  is `docs/design/campaign-distribution-plan.md`.


- Fix Campaign post Review products state/layout UI (requested 2026-10-10):
  align affiliate-offer checkboxes with their labels and links; avoid clipped
  product names under the sticky table header; make expanded offer/listing
  details readable and responsive, and keep the selected count and hidden
  selections clear. Reference: screenshot of May Meo 29 showing three CHERRY
  offers with checkboxes stranded in the middle of the row.
- Complete the full API regression verification before applying the related
  changes live (requested 2026-10-10). Prior progress reported 519 passing
  tests and clean Ruff, with a dedicated test adjusted to pin to enabled d1
  instead of disabled d2. Those results are reported context, not independently
  verified here. No `.data/scratch/full_suite.txt` or recorded full-suite
  outcome was found during this follow-up. From `services/api`, run
  `..\..\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -o console_output_style=classic`,
  capture output in `.data/scratch/full_suite.txt`, verify the exit code and
  final summary, resolve any failures, and record the outcome in the agent
  handover. A completed full-suite run is still unverified; this task does
  not authorize applying changes live.
- Remove the Paste button from the virtual keyboard helper (requested
  2026-10-11).
- Restore native image pasting in the virtual keyboard helper (requested
  2026-10-11): investigate the regression from the previously working behavior
  and verify that pasting an image works through the native clipboard path.
- Panes view: add an X button at the far right of the "Show a session here"
  placeholder, matching the close-button position when a CLI session is
  present. Closing the placeholder should reduce the current view by one
  pane/window (requested 2026-10-11).
