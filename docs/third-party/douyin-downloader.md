# jiji262/douyin-downloader integration

TrendRelay uses [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) as an isolated `media.download` provider for Douyin videos, galleries, collections, music, and profile batches.

- Upstream revision: `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`
- Upstream version at integration: 2.0.0
- License: MIT, copyright © 2026 jiji262
- Installation location: `.tools/douyin-downloader/` (ignored)
- Download location: `.data/downloads/douyin/` (ignored)
- TrendRelay entry point: `npm run douyin --`

The upstream source and its dependencies are installed into a dedicated virtual environment. TrendRelay does not expose its API internals to the core application. Cookies are read from environment variables or `.data/douyin/cookies.json`, written only to an ephemeral runtime configuration, redacted from dry-run output, and deleted after execution.

### Authentication

Douyin blocks unauthenticated media detail requests (empty HTTP 200 / anti-bot). Downloads therefore require cookies:

1. Click **Connect Douyin** in the media console. The app installs isolated login support when needed, opens Chromium, detects session cookies automatically, and closes it after capture.
2. CLI equivalent: `npm run douyin -- connect` (no Enter-key checkpoint).
3. Or set `DOUYIN_COOKIE` (full header) / `DOUYIN_TTWID` + `DOUYIN_ODIN_TT` + `DOUYIN_PASSPORT_CSRF_TOKEN`

Jobs fail when cookies are missing or when the provider exits without writing media. Upstream can return exit code 0 even on failed fetches; TrendRelay treats empty output folders as failure.

### Session strength

A captured session comes in two strengths:

- **Anonymous** (`ttwid` + `odin_tt` + `passport_csrf_token`, set by merely visiting the site): downloads single links and reads the hot board. Douyin's API serves it only the **first page** of a profile's posts (about 20) and answers later cursored pages with a bare `{"status_code": 0}` — an anti-bot refusal, not an end-of-list. Topic search is refused entirely (`2483`).
- **Signed in** (adds `sessionid`, set only by an actual login): the API paginates a whole profile, and topic search works.

The anonymous first-page cap is **server-side, client-independent, and cookie-freshness-independent**: probed directly, the pinned client, current upstream, and a session whose cookies were freshly harvested from a live browser (valid `__ac_signature`) all get page 1 (20 items) and then a bare `{"status_code": 0}` for the cursored page 2, consistently. Upgrading the provider or re-capturing cookies does not lift it. Only the session's trust level does — a signed-in session (which adds `sessionid`) paginates a profile through the API cleanly.

Without a login, TrendRelay recovers more than the first page on a **best-effort** basis by reading the profile grid, which keeps loading as a real browser scrolls. `scripts/douyin_profile_enum.py` opens the profile in the provider's headed Chromium, hides the sign-up prompt the instant it mounts (a `MutationObserver` installed before page scripts run, so the scroll never freezes), scrolls with real wheel events at a human pace, and harvests every video id the page renders (from `/video/` and `/note/` links, sniffed `/aweme/post/` responses, and ids in the page HTML). `scripts/douyin.py` calls it for a `/user/` URL in post mode when the session is anonymous, then downloads each harvested video through the per-video path — which an anonymous session is allowed to use.

**Best-effort, not guaranteed.** Measured against one profile of ~100 posts, the harvest varied run to run (8–26 ids) because Douyin actively throttles repeated automated access; a person's own browser, with a warmed session, sees the whole list. So the harvest is used only when it **clears the first-page floor** (the ~20 a plain API fetch returns): a smaller harvest means the browser was throttled below what the provider would get anyway, so `expand_profiles` keeps the profile link and lets the provider fetch its first page instead — anonymous is never made worse than the plain fetch, only better when the browser genuinely beats it. Downloads are incremental, so retrying continues rather than repeating. For a dependable whole-profile fetch, sign in.

The window is visible on purpose: Douyin serves a headless/automation-flagged context an empty feed, and a person watching can clear a captcha or close a prompt the observer missed. `--headless` exists on the enumerator for experiments but is not used by the download flow, precisely because it is served the empty feed.

The upstream `browser_fallback` is **disabled** (`browser_fallback.enabled: false`): it opens its own browser but only closes captchas, not the sign-up prompt, so it froze on the prompt and harvested almost nothing. Our enumerator replaces it. The login browser and this profile-read browser are the only browsers TrendRelay opens.

Users are responsible for platform terms, privacy, copyright, consent, and having permission to download or reuse content. The login browser may require manual CAPTCHA completion and is used only to capture cookies.
