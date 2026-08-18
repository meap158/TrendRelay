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

**Infinite scroll is gated on the browser environment, not the input.** Probing established that Douyin withholds a profile's infinite scroll from a Playwright-launched browser even when every client-side tell is clean (`navigator.webdriver` undefined, no `cdc_`/`__playwright` artifacts on `window`/`document`, real plugins/languages/UA) and the grid is scrolled to its bottom: no second `/aweme/post/` request fires for any input. It is not the input — a real hand on the *same launched window* does not trigger it either; only a genuinely normal browser does. Even a real Chrome started as an ordinary process and driven over CDP was refused (zero feed requests) once the profile and IP had been used for automation. So the gate is the environment/session trust, which a launched-and-controlled browser cannot manufacture, and which heavy automation from one IP actively burns.

Because of that the enumerator uses a real browser, launched the least detectable way, and can be pointed at a genuinely trusted profile:

- **Default** — it starts the system Chrome as an ordinary process (only a debugging port, none of a driver's automation flags) and attaches over CDP, rather than using Playwright's own launch. No configuration; falls back to the bundled Chromium only when system Chrome is not installed.
- `DOUYIN_CHROME_PROFILE` — the profile directory to launch. Point it at a real, daily-use Chrome profile so the session carries that browser's accumulated trust, which is the whole point; that profile must not be open in another Chrome window (the lock). Left unset, a persistent profile beside the cookies warms across runs.
- `DOUYIN_CDP_URL` — attach to a Chrome the operator is already running (start it with `--remote-debugging-port=9222`, then set `DOUYIN_CDP_URL=http://127.0.0.1:9222`). It opens its own tab, leaves other tabs alone, and does not close the browser on exit.

All three harvest the same way; they differ only in how trusted the browser is that Douyin sees. Even the default still clears the service-exception block and returns at least the first page.

The enumerator harvests continuously and stops on the `暂时没有更多了` ("no more for now") end marker or when the grid stops growing. `--headless` exists for experiments but is not used by the download flow, because a headless context is served an empty feed.

The upstream `browser_fallback` is **disabled** (`browser_fallback.enabled: false`): it opens its own browser but only closes captchas, not the sign-up prompt, so it froze on the prompt and harvested almost nothing. Our enumerator replaces it. The login browser and this profile-read browser are the only browsers TrendRelay opens.

### Anti-bot root cause: the `msToken` (investigation, 2026-08-19)

A long live investigation with the operator traced the anonymous pagination cap to a single mechanism, **`msToken`** — Douyin's per-request anti-bot token, minted by the page's own obfuscated JS (`byted_acrawler` / `webmssdk` / the `secsdk` API, all present on the page as `byted_acrawler` with a `frontierSign` method, `useWebSecsdkApi`, `_secsdk_uifid`).

Captured evidence (raw CDP, `Network` domain, comparing an automation-navigated tab against a tab the operator duplicated by hand in the same browser):

- The **automated tab's** `/aweme/v1/web/aweme/post/` request carries `a_bogus`, `verifyFp`, `fp`, `x-secsdk-web-signature`, `timestamp`, `from_user_page`, `webid` — but **no `msToken`**. Douyin caps it at page 1.
- The **hand-duplicated tab's** request carries a full `msToken=…` and pages the whole profile.

Follow-up tests pinned it down:

- In an automated browser (Playwright, raw CDP, real Chrome over CDP, and **undetected-chromedriver** — all four), the `msToken` cookie **never appears** (waited 40s), and requests either omit it or, when a stale one is reused, are **refused** — server returns empty page 2 (65 token-bearing pagination requests fired in one test, videos stayed at 26).
- The differentiator is the navigation/context being programmatic. A genuine user gesture — a typed URL or a **tab duplicate** (a browser-UI action, above the page, which no page-level automation can produce) — mints an `msToken` Douyin **accepts**. `Target.setAutoAttach` to every tab re-flags even the duplicates, confirming the flag is **per-tab devtools attachment + programmatic navigation**, not the browser instance.
- Params-only replay does not work: adding `verifyFp` (= the `s_v_web_id` cookie), `fp`, `webid`, `timestamp`, `from_user_page` to our API client still returns empty page 2, because `a_bogus` and `x-secsdk-web-signature` must be **freshly computed per request** by Douyin's JS over the exact params, and a stale/foreign signature is rejected.
- The cap is therefore **not** IP-based (same machine/IP: the operator's hand-driven Chrome and Edge page fully; every automated browser caps at ~25), **not** cookie-freshness, **not** client version (pinned and current upstream both cap), and **not** `navigator.webdriver`/`cdc_` (all clean).

**Conclusion.** Whole-profile anonymous download needs an `msToken` minted in a context Douyin trusts. It grants that token to a genuine, user-driven browser and withholds/refuses it for any programmatically-driven one. A signed-in session sidesteps the whole thing — `sessionid` makes the API paginate server-side with no browser.

### The browser-free bypass: a REAL msToken (research, 2026-08-19)

The reason the browser path is stuck is that we were reading the token from the browser, where automation is refused one. The mature scrapers do not use a browser at all — they **mint a real msToken over plain HTTP**:

- **The only valid msToken comes from the mssdk `common/report` endpoint** (`mssdk.bytedance.com`), by POSTing an encrypted `strData` payload (`magic`, `version`, `dataType`, `strData`, `ulr`, `tspFromClient`); the real token comes back in the response **cookies** (`msToken`, 164 or 184 chars). No browser. A locally-random "fake" msToken is what our provider's `MsTokenManager` produces, and it is what Douyin refuses on the cursored page — the whole cap.
- **`a_bogus`** (which replaced the deprecated `X-Bogus`, retired June 2024) is a **pure-Python** signature (SM3 + RC4 + custom Base64 over the URL + UA + fingerprint). Our provider already ships an `abogus.py`; it may need refreshing to the current algorithm.
- Reference implementation: **[F2 (`Johnserf-Seed/f2`)](https://github.com/Johnserf-Seed/f2)** — actively maintained Python, `TokenManager.gen_real_msToken()` + `ABogusManager.model_2_endpoint()` do exactly this for the `aweme/post` (user videos) endpoint, no browser. `Douyin_TikTok_Download_API` and `riboly/douyin-bypass-downloader` are similar. Paid APIs (TikHub, TikAPIs, Apify) sell the solved version.

**Tested (2026-08-19), and the exact remaining wall.** Minting a real msToken over HTTP **works** — POST F2's payload to `https://mssdk.bytedance.com/web/r/token?ms_appid=6383&msToken=…` (fields `magic:538969122, version:1, dataType:8, ulr:0, strData:<F2 blob>, tspFromClient:<ms>`) returns a valid 184-char `msToken` in the response cookies, no browser. With that token + our provider's existing `a_bogus`, **page 1 pages anonymously via pure API (18 items, `has_more:true`)** — the browser is not needed for the first page at all. But the **cursored page 2 is still refused**: raw response is a bare `{"status_code": 0}` (not a login error — page 1's response even carries `not_login_module`, so Douyin knowingly serves an anonymous page 1). A fresh msToken per request does not change it; swapping in F2's newer `a_bogus` (GET options `[0,1,8]`) made page 1 fail too, so our `a_bogus` is fine and `a_bogus` is not the page-2 gate.

The page-2 gate is **`x-secsdk-web-signature`** — the browser sends it on every `/aweme/post/` request (generated by the `secsdk` / `useWebSecsdkApi` layer), our client sends none, and Douyin enforces it only on the deep/cursored request. Reproducing it is a separate, harder RE problem than `a_bogus`/msToken, and it was **not** found solved in public tooling; the older `iesdouyin.com/web/api/v2/aweme/post/` endpoint that used to sidestep it is now deprecated (`status_code 11110` / 403).

**Net:** browser-free anonymous fetch is achievable for **page 1** (~18–26 videos, cleaner than the browser enumerator) via real-msToken + `a_bogus`. Whole-profile pagination needs either the `secsdk` signature (open RE problem) or a signed-in session. The realistic build is: adopt the real-msToken + `a_bogus` API path for the first page and drop the browser lottery, and make whole-profile depend on Connect/login. (Verify F2's licence before vendoring its `abogus.py`/token code; our provider already ships an equivalent `abogus.py` + `gmssl`.)

**"Hardcore" attempt exhausted (2026-08-19).** Tried to source the `x-secsdk-web-signature` from a live page and replay it: (1) the same page-1 URL that returns 18 items from a **Python** request returns an **empty 200** from a browser `XMLHttpRequest` on the profile page — so the browser context is the flagged party, not the request, and a real-msToken Python request is the only accepted caller; (2) sending the cursored request via the page's XHR (so the secsdk wraps it) gets **challenge-redirected** to a `secsdk` URL and returns empty — the automated page cannot even produce a valid signature, it is served a challenge; (3) replaying that browser-produced URL from Python still yields `{"status_code": 0}`. Conclusion: the automated browser is denied a valid `secsdk` signature (challenge), and Python cannot compute one, so **anonymous page 2+ is not reachable** without either reversing the `secsdk` VM (a much larger effort than `a_bogus`, and unsolved in public tooling) or a signed-in session. The page-1 clean-API path and Connect/login remain the two real options.

Users are responsible for platform terms, privacy, copyright, consent, and having permission to download or reuse content. The login browser may require manual CAPTCHA completion and is used only to capture cookies.
