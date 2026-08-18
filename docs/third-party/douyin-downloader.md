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

**Persistent profile.** The enumerator uses a persistent browser profile (`.data/douyin/browser-profile`, `launch_persistent_context`), not a throwaway context. A normal browser tab shows a signed-out profile's grid; an incognito tab does not, and neither does a fresh automation context — the difference is the profile's accumulated cookies, localStorage, and IndexedDB, which Douyin reads as trust. Without them it serves the `服务异常` ("service exception") page in place of the feed. Measured, a persistent profile cleared that block and loaded the grid where a fresh context got the error and almost nothing; it also warms across runs. Real Chrome is used when present (least detectable), falling back to bundled Chromium. Playwright's `--enable-automation` default is dropped so Chrome shows neither its automation infobar nor an unsupported-flag banner, and `webdriver` is hidden in the init script instead.

After the first page Douyin re-raises the sign-up prompt behind a scroll-locking backdrop; each scroll round the enumerator hides the login panels and drops that backdrop (never the grid, never by clicking) so the feed keeps advancing — the same thing a person does by closing the popup.

**Infinite scroll is bot-gated; the enumerator is human-assisted.** Probing established that Douyin withholds a profile's infinite scroll from an automated session even when the client-side tells are clean (`navigator.webdriver` undefined, no CDP artifacts in `window`/`document`, real plugins/languages/UA) and the grid container is scrolled all the way to its bottom: no second `/aweme/post/` request fires for any input — trusted synthetic keyboard (End, Ctrl+End), trusted wheel, or `scrollBy` alike. A real hand on the same window does trigger it and loads through to `暂时没有更多了` ("no more for now"). The gate is deeper than JavaScript can reach (CDP-attach or session/IP trust), so it is not defeated in automation.

The enumerator therefore drops a banner into the window asking the operator to scroll to the bottom (they are already there closing the sign-up prompt), keeps its own scroll attempts for sessions that are not flagged, and harvests continuously until it sees the `暂时没有更多了` end marker or the grid stops growing. It is patient about idling so a person has time to reach the window. If nobody finishes the scroll, it returns the first page and the floor guard keeps the profile link so the provider still fetches that page.

The window is visible on purpose: Douyin serves a headless context an empty feed, a person watching can clear a captcha the observer missed, and - as above - only a person's scroll carries a flagged session through the whole profile. `--headless` exists for experiments but is not used by the download flow.

The upstream `browser_fallback` is **disabled** (`browser_fallback.enabled: false`): it opens its own browser but only closes captchas, not the sign-up prompt, so it froze on the prompt and harvested almost nothing. Our enumerator replaces it. The login browser and this profile-read browser are the only browsers TrendRelay opens.

Users are responsible for platform terms, privacy, copyright, consent, and having permission to download or reuse content. The login browser may require manual CAPTCHA completion and is used only to capture cookies.
