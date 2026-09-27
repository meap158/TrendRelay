# Tasks

Operator-requested work that is noted but not yet built. One bullet per task;
remove it when the work lands, and name the commit that did.

- The sidebar's status word should not read "active" for a campaign whose
  autopilot is off: something like "Inactive". The campaign's own status
  (active, draft, archived) and the autopilot's switch are two things today,
  and the row only says the first. Asked on 2026-09-23. Part of this landed
  in fb8b8c4, which marks a stopped campaign with its own glyph rather than
  changing the word - the word is still "active", so the ask stands.
- A post went to the same Facebook page twice at the same minute: "Bát cháo
  trước cửa" and "Người giữ công trường", both 6:00 PM on 27 September, both
  to Mẫu Chuyện Cuộc Sống, both scheduled. The planner's double-booking guard
  reads its own campaign's pending executions and the queue's per-destination
  stamps, so two posts landing on one destination at one moment means one of
  those did not see the other. Asked on 2026-09-27 from the day dialog.
- A campaign kept posting to an engine that was switched off in Publish.
  Whether an engine is on is asked for delivery, but the planner freezes and
  schedules posts for its destinations regardless, so a switched-off engine
  produces scheduled posts that either fail at delivery or go out anyway.
  With it, the campaign's "Where it posts" pane should mark a destination
  whose engine is off, the way the sidebar now marks a stopped campaign -
  otherwise the only sign is posts not appearing. Asked on 2026-09-27.
- A post waiting for media draws a video placeholder whatever it is waiting
  for. The queue already knows the shape - "0 of 8" is a carousel, and a post
  whose media count is more than one cannot be a video - so the placeholder
  should take the pictures' own aspect and icon rather than a play triangle
  in a landscape box. Asked on 2026-09-27 from the Storytelling queue.
- The Storytelling campaign has Instagram and Threads accounts attached and
  posts to neither. Its run notes mention WoopSocial refusing photo carousels
  to Threads and to Instagram, so the destinations may be being skipped for
  the format rather than never considered - but the campaign reads as though
  those accounts are simply idle. Asked on 2026-09-27.
