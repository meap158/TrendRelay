# gitroomhq/postiz-agent integration

TrendRelay uses [gitroomhq/postiz-agent](https://github.com/gitroomhq/postiz-agent) as an isolated `social.publish` provider for connected social accounts.

- Upstream revision: `41c5a9dbd6b2776863e7c05c22e7a385c208321c`
- Upstream version at integration: 2.0.15
- License: GNU Affero General Public License v3.0, copyright (c) 2024 Nevo David
- Installation location: `.tools/postiz-agent/` (ignored)
- Runtime state: `.data/postiz/` (ignored)
- TrendRelay entry point: `postiz.cmd`

The upstream project is fetched and built unchanged in an isolated directory. TrendRelay communicates with it through its command-line interface and does not copy upstream source into the core application.

Postiz requires connected platform integrations. Authentication uses either `POSTIZ_API_KEY`/`POSTIZ_API_URL` from the local environment or Postiz OAuth device credentials. OAuth credentials are managed by upstream in the user's Postiz credential file and must never be committed.

The short-video adapter uploads MP4 media to Postiz before creating a post, because TikTok, Instagram, and YouTube require a trusted media URL. Dry runs never authenticate, upload, or create remote state. Execution requires explicit confirmation and is protected by a local operation ledger.
