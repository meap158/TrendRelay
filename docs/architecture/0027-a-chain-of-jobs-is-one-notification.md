# ADR 0027: A chain of jobs is one notification, and it opens on what was made

Status: Accepted and built — `services/api/src/trendrelay_api/jobs.py`
(`ACTIVITY_CHAIN_FIELDS`), `services/api/src/trendrelay_api/media_library.py`
(`create_ingest_job(chain=…)`), `services/api/src/trendrelay_api/storytelling/autocreate.py`,
`services/api/src/trendrelay_api/storytelling/jobs.py`, `services/api/src/trendrelay_api/autocut/jobs.py`,
`apps/web/lib/job-links.ts` (`chainHref`), `apps/web/app/global-nav.tsx` (`chainOf`, `chainHeadline`).

## Context

An auto-built story is three durable jobs. The build fills the script's
pictures from stock and queues a render; the render speaks the script, cuts the
pictures to it, and queues a Library ingest; the ingest files the video. Each is
a separate kind with its own lane, its own lease and its own retry policy, and
that is right: the build waits on a dozen downloads, the render on a paid
synthesis, the ingest on a hash of the file. What was wrong was what the person
who pressed the button saw.

The bell showed three rows — "Story build queued the render", "Story video is
ready", "Library: Storytelling - Explainer" — each succeeded, each unread, and
none of them opened the video. The drawer folds rows on a `batch` marker, which
none of these carried. Its link for a row is the union of every Library entry
the row's jobs report, and what the build and the render report are their
*inputs*: the pool of b-roll, the pictures the render was given. The one job
that made a Library entry was the ingest, and its title was the ingest
describing itself. The render did report the hash of its video so the link
could resolve by hash before the entry existed (commit 940c8c6), but the
activity serializer that slims every job for the bell dropped the hash on the
way out, so the render's row linked to the bare Library. Three notifications,
zero of them the video.

## Decision

**One thing asked for is one row, however many jobs carry it out.** This is
the convention every notification system that is pleasant to use has arrived
at — a pull request's checks are one line, a file upload is one line — and it
is the convention the drawer already holds for batches. A chain is the other
shape: a sequence rather than a set.

**The marker is a job-level fact, not a drawer-level guess.** Every job that
continues another's work carries `payload.chain = {"id": <the job that started
it>}`. The build names itself; `enqueue_render(chain_id=…)` takes the name and
records it; the render passes `payload["chain"]` to `create_ingest_job(chain=…)`.
A render started by hand names itself, so it and its ingest fold too. The
activity serializer keeps the id (and now the render's `sha256`), and nothing
else of either. The drawer is not asked to infer a chain from timing or from
titles, which it could only get wrong.

**The row is the newest step's state and the last step's output.** Folded
across categories on purpose — the steps are different kinds of work, and that
is exactly what is being folded. State, progress, error and time come from the
newest step, so the row moves from finding b-roll to narrating to filed. The
title is the newest step's too, except once the ingest has filed the video:
then the step that *made* it says the useful thing ("Story video is ready"),
and the ingest's "Library: …" is not what anyone was waiting to hear. The link
is the newest step that has an output — the entry once the ingest has made one,
the file by hash from the moment the render finished — and nothing before
that, the same rule a running job has. A step that finished by queueing the
next (`result.render_job_id`) made nothing itself and is skipped, and only a
step's *result* is read for the link, never its payload: a payload names
inputs. The face on the row is the newest step's asset, for the same reason.
Steps are not repeats, so a chain shows no `×3`.

**By hash before by path.** An ingest copies its source into the hash-addressed
store, so the source path it was queued with never matches the entry it
becomes; linking by that path opened the Library and selected nothing. The
hash names the same bytes on both sides of the move, the ingest knows it from
the moment it is queued (`payload.source_sha256`), and it keeps resolving
after the entry lands. `notificationHref` now tries the hash before the path
fallback for every job, not only chains.

## Consequences

- The worker does not hot-reload. The build and the render run there, and they
  are what pass the chain to the render and the ingest; until it is restarted,
  jobs it queues carry no chain and the drawer shows them as before.
- Jobs queued before this change carry no marker and are not folded. They are
  history; nothing rewrites them.
- A new feature that queues a sequence of jobs joins the convention by passing
  the root's id along, one keyword argument per hop. It gets folding and the
  right link without touching the drawer.
- `groupFilter`, unread counting and mark-as-read all work on the group, so a
  chain re-alerts when a new step appears and is read as one thing.
