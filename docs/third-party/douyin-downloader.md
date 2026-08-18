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

Without a login, an anonymous profile fetch stops at that first page (~20 videos) - the ceiling Douyin serves. A browser enumerator that tried to scroll past it (`scripts/douyin_profile_enum.py`) was built and then **retired**: every automated browser (Playwright, raw CDP, real Chrome over CDP, undetected-chromedriver) is capped or challenge-redirected by the anti-bot, so it only ever harvested an unpredictable 8-26 videos and opened a popup window mid-download for no gain. Profile URLs now pass straight to the provider's signed-API post mode, which returns the first page reliably with no browser. The record of why the browser path can't beat the anti-bot is below (it was a long investigation and is kept so nobody re-treads it).

The upstream `browser_fallback` is **disabled** (`browser_fallback.enabled: false`) - it opened a window the anti-bot capped anyway. The login browser is the only browser TrendRelay opens.

Whole profiles need a caller Douyin trusts, and the two legitimate ways to be one are a **signed-in account** (which pages the profile through the API) or a **managed data API** (e.g. TikHub `fetch_user_post_videos`, paid). Reverse-engineering the anti-bot to forge that trust is out of scope. A free route exists but is manual - a console script run in the operator's own (trusted) browser to harvest the list, pasted into the Download tab, which accepts up to 400 links.

---

The remainder of this section is the **investigation record** for why anonymous deep pagination is blocked. It is history, not current behaviour.

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
