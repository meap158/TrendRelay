# Roadmap

**Draft, 2026-10-25.** Written from the README ("Project status") and the handover's next recommended
action; there was no roadmap before. The owner has not reviewed it. No account names or credentials
belong in this file.

## Vision

A local-first Windows workspace to find what is trending, collect authorised Douyin media, prepare
every clip locally, and run campaigns that post where an affiliate link can actually be clicked.

## Scope

**In scope**: Discover (topics, posts, ads, news), Douyin download with resume and provenance, the
Library with non-destructive edits and effects, Campaigns with an autopilot queue, Publish through
hosted engines, Attribution (products and the networks' own affiliate URLs), and MCP assistant access
whose policy defaults to refusal.

**Out of scope**: modifying original files (they stay immutable) and posting anywhere without a
connected engine and the operator's account and quota.

## Phases

| Phase                       | Delivers                                                                                          | Exit criteria                                                                    | Status                                                              |
| --------------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| 1. Acquire and prepare      | Discover, Douyin download, Library, effects, durable jobs                                          | Covered by automated checks                                                       | Built                                                               |
| 2. Campaigns and publishing | Autopilot, multi-engine publishing, Shopee offer import, Attribution                               | One real end-to-end autopilot run, from queue to a post with a working link       | Built; the first real run waits on media-hosting credentials and posting slots |
| 3. Drafts at scale          | Draft media completion across products and recipes, Drafts and Library views with filters          | Drafts finish in the agreed priority order; filters match the Library              | In progress (board cards cover the Library filters and card views; Gemini Web video makes real clips, queued run waits on owner task HT-004)  |
| 4. Assistant access         | MCP tools over the same services                                                                   | Policy tested for refusal by default                                              | Built                                                               |

## Dependencies

At least one connected publishing engine with its quota, object storage for engines that fetch media
by URL, FFmpeg, optional research and ads provider keys, and the operator's own accounts.

## Risks

| Risk                                          | What limits it                                                            |
| --------------------------------------------- | ------------------------------------------------------------------------- |
| Misentered storage credentials                | The settings form refuses wrong shapes; the access check explains why      |
| Moderation blocks on generated media          | Recorded per draft; blocked cards are not retried                          |
| Engine quotas and outages                     | Engines are interchangeable; failures are recorded per job                 |

## Owner actions

`node scripts/owner-pages.mjs`, then open `References/owner-pages/index.html`.
