---
id: campaigns.fill-needs-copy
action: campaigns.fill-needs-copy
title: Fill campaign posts that need copy
summary: Adaptively write only missing campaign copy from live campaign, post, platform, and product context.
version: 1
tags: [campaigns, copywriting, needs-copy]
aliases: [fill-campaign-needs-copy, campaigns.needs-copy, write-campaign-copy]
---
# Adaptive TrendRelay campaign copy workflow

This SOP applies to every TrendRelay campaign, with campaign-specific behavior determined dynamically from the live MCP context.

## 1. Connect to TrendRelay MCP first

Always use the live TrendRelay MCP before writing anything.

Do not rely on old queue state, previous campaign assumptions, or memory of language, platform, products, or destination behavior.

## 2. Identify the campaign and pull the live queue

Start with the campaign's current `needs copy` list. Use the live queue as the source of truth for which posts need work, which campaign they belong to, and whether caption, first comment, thread, title, or disclosure is actually missing. Do not write fields that are not needed.

## 3. Pull post context before writing

For each post, inspect the available context, including where applicable:

- Campaign name, objective, audience, and market
- Campaign language or post language
- Destination and platform
- Video or media title and source caption
- Products and offers
- Existing copy
- Link placement behavior and disclosure
- Hashtag conventions and delivery mode
- Product-match confidence

Use the strongest available signals first.

## 4. Follow the campaign language dynamically

Never hard-code a language. Use the language specified by the campaign or post context. For example, `vi` means Vietnamese, `en` means English, and `th` means Thai. The writing should sound native to that audience, not like a literal translation from another campaign.

## 5. Adapt the tone to the campaign

Infer tone from the campaign objective, brand or page identity, audience, product category, existing approved posts, platform, and creative style. Appropriate tones can include playful, quirky, aspirational, informational, luxury, minimal, conversational, humorous, direct-response, or community-driven. Do not force every campaign into the same social-caption style.

## 6. Adapt copy to the campaign objective

- **Affiliate sales:** engaging first, commercially adjacent, and without overclaiming
- **Traffic:** stronger curiosity and click intent
- **Engagement:** prioritize comments, reactions, polls, and easy opinions
- **Awareness:** memorable brand or message first
- **Lead generation:** clearer value proposition and action
- **Community growth:** conversational, identity-based, and discussion-oriented

The objective should influence copy structure without making it sound mechanical.

## 7. Use the media as the main creative signal

For video or image posts, use the actual creative context wherever possible: video title, source metadata, visible theme if available, existing source caption, and campaign category. The caption does not need to narrate the media literally; it should complement it.

## 8. Allow controlled creative freedom

When appropriate, captions may be slightly off-topic, relatable, funny, opinion-based, lifestyle-oriented, naturally comment-oriented, or built around a cultural or social observation. They should still feel plausible beside the creative and campaign. Avoid unrelated randomness.

## 9. Adapt engagement mechanics by campaign

Use engagement prompts only when they fit. Options include A versus B, pick one, rate this, agree or disagree, "Who else?", "What would you choose?", a short open-ended question, situational joke, mini confession, or opinion prompt. Do not repeat the same call-to-action structure across every post.

## 10. Vary copy across the queue

Rotate hook types, sentence structures, emoji use, questions, humor style, length, product adjacency, and emotional tone. The campaign should feel like a real social account, not a batch-generated template.

## 11. Respect campaign-specific hashtag rules

Determine hashtags from campaign configuration, existing approved copy, and explicit user instruction. If the campaign has a fixed hashtag set, use it exactly. Otherwise adapt hashtags to language, market, niche, platform, and campaign identity. Do not reuse another campaign's hashtags automatically.

## 12. Respect platform constraints

Check destination behavior before writing. Adapt for Facebook, Instagram, TikTok, Threads, X, and other connected platforms. Consider caption length, first-comment support, thread support, title support, hashtag norms, and link behavior. Do not assume all destinations behave the same.

## 13. Do not manually add affiliate links unless explicitly required

Default rule: **Do not insert affiliate URLs manually when the publishing system is configured to place them automatically.** If campaign context or user instructions explicitly require a manually written link, follow that campaign-specific rule. User instructions override system defaults for copy placement.

## 14. Do not invent first comments

Only write a first comment when the campaign actually needs one, the destination supports it, there is a real content purpose, or the user explicitly requests it. Do not create first comments purely out of habit.

## 15. Do not misrepresent product-video relationships

If product matching is uncertain, avoid language implying the product shown is definitely the linked item. Do not say "this exact dress," "the product in the video," or "shop the look shown here" unless context confirms it. When confidence is weak, write broadly around style, mood, category, occasion, or audience interest.

## 16. Use product relevance intelligently

If a strong product match exists, let the caption reference the relevant category naturally. If the match is weak, prioritize creative engagement over forced product naming. Do not contort the caption merely to improve semantic product matching.

## 17. Avoid unsupported claims

Do not invent price, discount, stock, fabric quality, fit, shipping speed, performance, product benefits, popularity, or exact visual identity. Use only claims supported by live product and post context.

## 18. Use the minimum necessary write action

Prefer the narrowest appropriate write operation: caption only for a missing caption, disclosure only for a missing disclosure, thread only for a missing thread, and a full package only when several fields are needed. Avoid overwriting unrelated fields.

## 19. Preserve existing approved copy

If part of a post is already populated and approved, do not rewrite it unless it is specifically marked as needing copy, the user asks for a revision, or the workflow explicitly requires replacement.

## 20. Process the full current batch

Continue through the live queue unless the user specifies a limit, subset, specific items, or review-first workflow.

## 21. Refresh the queue after each batch

After finishing the current set, call the live `needs copy` list again. New posts may have entered while processing. Continue until the queue is empty or the user's requested scope is complete.

## 22. Verify before declaring completion

Never say "done" based only on the original queue. Final verification must come from a fresh live MCP check.

## 23. Treat each campaign independently

Never automatically carry language, hashtags, tone, link rules, call-to-action style, product assumptions, audience, or posting behavior from one campaign to another. Carry over only rules the user explicitly defined as global.

## 24. Priority order when rules conflict

1. Current explicit user instruction
2. Campaign-specific user rules
3. Live campaign or post configuration
4. Platform and destination constraints
5. Existing approved campaign style
6. General SOP defaults

## 25. Final operating principle

For every campaign: **connect live → inspect campaign → inspect post → adapt language, tone, objective, platform, and product relevance → write only what is needed → avoid unsupported claims → refresh queue → verify completion.**

This keeps the workflow adaptive instead of treating every campaign like a copy of the last one.
