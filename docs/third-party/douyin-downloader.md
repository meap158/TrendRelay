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

A captured session comes in two strengths, and the difference decides how much of a profile downloads:

- **Anonymous** (`ttwid` + `odin_tt` + `passport_csrf_token`, set by merely visiting the site): downloads single links and reads the hot board, but Douyin serves it exactly one page of a profile's posts (about 20) and answers later pages with an empty list — a 60-post profile quietly arrives as 20 files. Topic search is refused entirely (`2483`).
- **Signed in** (adds `sessionid`, set only by an actual login): profile pagination and topic search work.

The connection status, `npm run douyin -- check`, and the job summary of an anonymous profile fetch all name this limit and the remedy (reconnect and log in).

At this upstream revision, a profile whose paging is cut short triggers a visible Chromium "browser fallback" on the profile page. TrendRelay disables it (`browser_fallback.enabled: false` in the generated config): upstream seeds that browser with the cookie jar minus login cookies, so it renders a signed-out profile with no videos and collects nothing — an unexplained empty browser window mid-download. The login browser remains the only browser TrendRelay opens, and only to capture cookies.

Users are responsible for platform terms, privacy, copyright, consent, and having permission to download or reuse content. The login browser may require manual CAPTCHA completion and is used only to capture cookies.
