---
id: publishing.inspect-and-preview
action: publishing.inspect-and-preview
title: Inspect Publish capabilities, preview a post and read delivery jobs
summary: Read the Publish configuration and validate a composer request before any delivery action.
version: 1
tags: [publishing, context, preview]
aliases: [publishing.preview]
---
# Inspect and preview a Publish post

1. Call `get_publishing_context` for this connection's workspace ID, configured
   engine readiness and capability matrix. Reading it does not probe accounts.
2. Build the same request the Publish composer uses: `caption`, ISO datetime
   `date`, `targets`, and video or image paths when needed. Each target names
   `platform`, `integration_id` and, when needed, `provider` and `post_type`.
   Optional platform fields and delivery settings follow the GUI PublishRequest.
3. Call `preview_publishing` with that object in `request`. The connection sets
   its workspace ID. Do not supply campaign provenance fields: those belong to
   the campaign workflow. Validation includes the Library media credit, network
   limits and engine capability rules used by the GUI.
4. Correct any validation error before proceeding. A preview is not a saved
   post or a submission. These tools do not send anything.
5. Use `list_publishing_jobs` with a limit from 1 to 100 to find recent jobs,
   then `get_publishing_job` to read one job's delivery state and errors.
   Another workspace's job cannot be read through this connection.

Standalone submission is not yet exposed. Campaign drafts can be proposed
through `create_campaign_post` and pinned with `pin_post_slot`, following
`campaigns.add-post-with-media`. Do not treat a preview as proof of delivery.
