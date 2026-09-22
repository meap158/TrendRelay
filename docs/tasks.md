# Tasks

Operator-requested work that is noted but not yet built. One bullet per task;
remove it when the work lands, and name the commit that did.

- The posting timeline's thumbnails (list view, one per post) should show a
  preview on hover and open in the shared lightbox on click, the way the
  held-post edit dialog's pictures already do (`autopilot-panel.tsx`, the
  `Lightbox` / `useLightboxSet` pair from `app/ui/lightbox`). Asked on
  2026-09-22 from the Storytelling campaign's timeline.
- A post whose delivery failed (the timeline's delivery warnings, e.g.
  "Could not reach api.woopsocial.com: The read operation timed out") should
  offer a way back into the rotation: one post at a time, and a select-all
  the way the approval inbox selects posts. With that, when "Let a post go
  out more than once" is on for a campaign, a failed delivery should not
  count as one of the post's outings - only a delivery the engine confirmed
  did anything. Asked on 2026-09-22.
