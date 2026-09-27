# Tasks

Operator-requested work that is noted but not yet built. One bullet per task;
remove it when the work lands, and name the commit that did.

- A campaign kept posting to an engine that was switched off in Publish.
  Whether an engine is on is asked for delivery, but the planner freezes and
  schedules posts for its destinations regardless, so a switched-off engine
  produces scheduled posts that either fail at delivery or go out anyway.
  With it, the campaign's "Where it posts" pane should mark a destination
  whose engine is off, the way the sidebar now marks a stopped campaign -
  otherwise the only sign is posts not appearing. Asked on 2026-09-27.
- One campaign is creating publish jobs to Buffer far faster than it can have
  posts to make, and every one is refused. 56,176 of the 57,152 failed jobs in
  the database are `Too many requests` or `HTTP 429` from api.buffer.com, each
  one a separate job with `attempt_count=1` rather than one job retrying - so
  something is creating them, not re-running them. The daily count is falling
  as the re-propose fixes land (7,585 on 20 September, 1,323 on the 27th) but
  it has not stopped. A sample carries campaign_4f0680db, a Threads target,
  and a date a day ahead. Found on 2026-09-27 while tracing why the database
  had grown to 645 MB; the jobs themselves are now pruned after thirty days,
  which bounds the disk cost but not the cause.
