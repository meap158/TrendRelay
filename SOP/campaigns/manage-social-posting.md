---
id: campaigns.manage-social-posting
action: campaigns.manage-social-posting
title: Manage Campaign posting through the GUI-equivalent MCP tools
summary: Read campaign context, create and edit posts, optionally pin them, and confirm queue or execution actions through the same application handlers as the GUI.
version: 1
tags: [campaigns, publishing, approval, schedule]
aliases: [campaigns.manage, campaigns.post]
---
# Manage Campaign social posting

The `Campaign GUI` tool group exposes the application's Campaign operations.
Each tool uses the GUI's request schema, validation, workspace scope, role checks,
audit handling and state transitions. Inspect the live tool schema for exact
fields. A new tool is not authorization to send a post.

## Context and a post

1. Read `list_campaigns`, then `get_campaign_config` and
   `campaign_read_autopilot` for the campaign. The latter includes complete
   destination configuration and queue state. For a single post, use
   `get_post_context` or `campaign_read_queue_item_context`.
2. Find existing media with `list_library_assets`. Import new media with
   `upload_media` only when needed. Read `get_import_status` until every file
   is ready. Keep complete source carousels together and in their intended order.
3. Choose the intended workflow before writing:
   - For an unapproved proposal, `create_campaign_post` creates a draft from
     Library asset IDs. Read `campaigns.add-post-with-media` first.
   - For the same whole-post handoff as the GUI, obtain consent, then call
     `campaign_add_queue_item` with `confirm_external_action=true`. Pass
     ordered Library IDs as top-level `asset_ids` when selecting existing
     media. They resolve to the latest completed Library cut, falling back
     to the original. Omit raw media paths in `body` when using IDs. Its `body`
     uses GUI field names, including `body` for the caption, ordered
     `image_paths` or `video_path`, `asset_id`, `offer_ids`, `carry_offers`,
     `first_comment`, `thread` and `post_type_overrides`. This creates a
     rotation-ready queue item, just as Publish's Add to campaign does. An
     enabled autonomous campaign may subsequently publish it. State this
     consequence before asking for or applying consent.
4. Leave the post unpinned when it should follow the campaign's rotation.
   For a fixed slot, read `get_day_slots` and then use `pin_post_slot`, or the
   confirmed `campaign_assign_queue_item_slot` GUI counterpart. A pin reserves
   the slot; it does not approve a draft. Pass the destination ID when accounts
   share a posting time. Release a pin to return the post to normal rotation.
5. Read the post back after each write. If a create call is interrupted, search
   `list_campaign_posts` by caption before repeating it. Queue creation has the
   GUI's ordinary create semantics; do not assume it is idempotent.

## Compose, preview and manage

Use `campaign_compose_queue_item` to see disclosure, affiliate link placement,
comments and replies as the destination will receive them.
`campaign_preview_autopilot` previews the schedule. `campaign_campaign_analytics`,
`campaign_list_autopilot_executions` and `campaign_list_autopilot_exceptions`
read performance, delivery attempts and the approval inbox. These are distinct
from queued posts: changing a queue item does not rewrite a frozen execution.

The confirmed GUI tools also cover full post edits, complete-media replacement,
hold/approve/remove batches, product membership, destinations, campaign settings,
campaign creation/duplication/status, calendars and publication plans. They use
the GUI's state restrictions. For ordinary caption completion, prefer the
narrow copy tools that leave other fields untouched.

For a frozen proposal, edit it with `campaign_edit_autopilot_execution`, or
refresh it from the queue with `campaign_refreeze_autopilot_execution` before
approval. Re-read the frozen text and media before confirming
`campaign_approve_autopilot_execution` or its batch counterpart. Approval sends
the frozen record under the campaign's rules. Dismiss and return-to-rotation
tools have their GUI meanings; they do not invent replies or approvals.

## Confirmed actions

All tools in this GUI group that mutate data require top-level
`confirm_external_action=true` after user consent. Even a local edit can affect
the next outing of an enabled campaign. The top-level confirmation controls any
nested confirmation field. Credentials and sign-in cannot be managed here.

Immediate queue delivery is `campaign_publish_single_queue_item` or
`campaign_batch_publish_queue`. These can contact the provider immediately.
`campaign_deploy_autopilot` and `campaign_run_autopilot_now` can schedule or send
posts according to the campaign's authority. Obtain consent for content,
destinations and timing before calling them. Verify returned executions and
durable job state; a queued job alone is not proof of publication.

The connection acts for the installation's local operator, scoped to its
configured workspace. Role checks still apply. No caller can claim an AAL2
session by setting a boolean: installations requiring multi-factor assurance
refuse governed MCP actions until delegated authentication is available. Use
the authenticated GUI for those actions.
