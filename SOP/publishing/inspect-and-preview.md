---
id: publishing.inspect-and-preview
action: publishing.inspect-and-preview
title: Inspect, preview and submit a Publish post
summary: Read Publish context, validate the typed composer and explicitly confirm provider discovery or durable post submission.
version: 2
tags: [publishing, context, preview]
aliases: [publishing.preview]
---
# Inspect, preview and submit a Publish post

1. Call `get_publishing_context` for this connection's workspace ID, configured
   engine readiness and capability matrix. Reading it does not probe accounts.
2. Build the same request the Publish composer uses: `caption`, ISO datetime
   `date`, `targets`, and ordered Library `asset_ids` when needed. These IDs
   resolve to the latest completed cut, falling back to the original. Explicit
   video/image paths are also accepted, but cannot be mixed with IDs. Each target names
   `platform`, `integration_id` and, when needed, `provider` and `post_type`.
   Optional platform fields and delivery settings follow the GUI PublishRequest.
3. Call `preview_publishing` with that object in `request`. The connection sets
   its workspace ID. Do not supply campaign provenance fields: those belong to
   the campaign workflow. Validation includes the Library media credit, network
   limits and engine capability rules used by the GUI. By default, the preview
   performs local validation only. After consent, `confirm_external_action=true`
   permits optional provider validation, as the GUI preview does.
4. Correct any validation error before proceeding. A preview is not a saved
   post or a submission. For connected targets, `list_publishing_accounts`
   contacts configured engines and requires explicit confirmation. Likewise,
   `list_publishing_boards` reads a connected account's board IDs and names
   only after confirmation. Discovery does not connect a new account.
5. Use `list_publishing_jobs` with a limit from 1 to 100 to find recent jobs,
   then `get_publishing_job` to read one job's delivery state and errors.
   Another workspace's job cannot be read through this connection.

6. To deliver, obtain user consent for the exact content, targets and time.
   Call `submit_publishing` with the typed composer object in `request` and
   top-level `confirm_external_action=true`. `delivery` is `draft`, `schedule`
   or `now`. A provider draft also leaves this machine and requires consent.
   Role and governed-assurance checks match Publish. The tool uses its durable
   job path and returns the job and delivery results; failure remains readable
   through the job ID. Repeating the identical operation uses the same job's
   claim rules, rather than sending it twice.
7. After an interrupted call, check the recent jobs and recover the existing
   operation before creating another. Do not change the timestamp to force a
   retry: that can describe a different operation and make a duplicate post.

The typed schema exposes the GUI composer fields, including multiple targets,
provider selection, platform post type, first comment, thread, topic, board,
subreddit, approval request, visibility and AI/music settings. Workspace and
campaign provenance fields are assigned by the application, not the caller.

For campaign posting, use `campaigns.manage-social-posting`. Credentials,
sign-in and machine-wide provider configuration stay local-only. Where the
installation requires AAL2, confirmed governed actions are refused over this
local-operator connection; confirmation does not create an MFA session.
