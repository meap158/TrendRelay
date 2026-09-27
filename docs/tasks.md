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

