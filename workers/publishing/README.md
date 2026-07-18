# Publishing worker

Durable, idempotent upload and polling operations with capability checks, retries, audit events, and manual fallbacks.

The available `social.postiz-agent` provider supports explicitly confirmed MP4 drafts and schedules for TikTok, Instagram, and YouTube. Provider source stays isolated under `.tools/`; operation state and audit-safe deduplication live under `.data/postiz/`.