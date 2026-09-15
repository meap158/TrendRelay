# Tasks

Operator-requested work that is noted but not yet built. One bullet per task;
remove it when the work lands, and name the commit that did.

- The carousel steps row sits off-centre in the small post preview. The row of
  dots with a chevron either side, drawn over the pictures just above the
  caption, is pushed to the right of the frame rather than centred on it, and
  the chevrons do not line up with the dots they flank. Seen in the approval
  card on Campaigns, where the preview is at its narrowest; the same row reads
  correctly at the width the Publish panel draws it, so it is the narrow case
  that is wrong. `.post-preview-steps` in `apps/web/app/styles.css` and the
  surface grid it sits in.
