# gitroomhq/postiz-agent integration

TrendRelay uses [gitroomhq/postiz-agent](https://github.com/gitroomhq/postiz-agent) as an isolated `social.publish` provider for connected social accounts.

- Upstream revision: `41c5a9dbd6b2776863e7c05c22e7a385c208321c`
- Upstream version at integration: 2.0.15
- License: GNU Affero General Public License v3.0, copyright (c) 2024 Nevo David
- Installation location: `.tools/postiz-agent/` (ignored)
- Runtime state: `.data/postiz/` (ignored)
- TrendRelay entry point: `npm run postiz --`

The upstream project is fetched and built unchanged in an isolated directory. TrendRelay communicates with it through its command-line interface and does not copy upstream source into the core application.

Postiz requires connected platform integrations. Authentication uses either `POSTIZ_API_KEY`/`POSTIZ_API_URL` from the local environment or Postiz OAuth device credentials. OAuth credentials are managed by upstream in the user's Postiz credential file and must never be committed.

The short-video adapter uploads MP4 media to Postiz before creating a post, because TikTok, Instagram, and YouTube require a trusted media URL. Dry runs never authenticate, upload, or create remote state. Execution requires explicit confirmation and is protected by a local operation ledger.


## Governed TrendRelay workflow

The authenticated `/publish` screen and workspace publishing API generate offline previews for editors and allow only owners or approvers to discover provider integrations or submit remote drafts/schedules. Every provider-facing action requires explicit confirmation. Submitted operations are stored as `social_publish` jobs in the shared SQL queue and are claimed by the supervised worker.

The API resolves media locally and accepts existing MP4 files only beneath `PUBLISHING_MEDIA_ROOTS` (by default `.data/downloads`, `.data/media`, and `.data/productions`). Publishing jobs intentionally have one execution attempt: provider timeouts may have created remote state, so TrendRelay blocks blind retry and relies on the content-derived Postiz ledger for duplicate and uncertain-operation protection.
