# Postiz social publisher plugin

This local-process plugin adapts pinned `gitroomhq/postiz-agent` functionality to TrendRelay's `social.publish` capability. It supports short-form MP4 drafts and schedules for TikTok, Instagram, and YouTube, plus integration discovery.

Safety rules:

- Local dry run is the default.
- Any upload or remote draft requires `--execute --confirm-external-action`.
- Scheduling also requires `--schedule` and a future timezone-aware date.
- TikTok defaults to `SELF_ONLY`, upload mode, and disabled comments/duet/stitch.
- YouTube defaults to private visibility.
- A local operation ledger blocks duplicate or uncertain retries.
- Approved assets only; publishing downloaded reference media requires rights clearance.
