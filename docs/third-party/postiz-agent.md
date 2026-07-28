# Postiz integration

TrendRelay embeds two separately pinned AGPL-3.0 projects:

- [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) 2.21.7 at `7236213ea4520bd67b45688c2787d1f4586b3b51` provides the self-hosted application.
- [gitroomhq/postiz-agent](https://github.com/gitroomhq/postiz-agent) 2.0.15 at `41c5a9dbd6b2776863e7c05c22e7a385c208321c` provides the publishing CLI adapter.

Both source trees stay ignored under `.tools/`. The application uses native Windows PostgreSQL 17, Redis, and Temporal. Postiz Cloud authentication is absent. Private runtime state stays under `.data/postiz-selfhost/`; publishing operation records stay under `.data/postiz/`.

`start.cmd` calls the idempotent native preparation workflow on first run. The unified runner then supervises the Postiz backend, orchestrator, and frontend with hot reload and reuses healthy instances. A local `admin@trendrelay.local` account and API key are created through Postiz's supported endpoints. The local-session route performs server-side login and sets the Postiz browser cookie without returning credentials to TrendRelay's renderer.

Postiz still needs platform-specific OAuth app credentials and user authorization to connect TikTok, Instagram, YouTube, or other destinations. These values belong only in the ignored Postiz `.env`; TrendRelay reports readiness but never returns secret values. The `/publish` workflow lists only connected account names and IDs.

Dry runs never upload or create remote state. Execution requires owner/approver authorization plus explicit external-action confirmation. MP4 files must resolve beneath `PUBLISHING_MEDIA_ROOTS`. Publishing jobs use one execution attempt and content-derived operation IDs because a provider timeout can leave uncertain remote state.