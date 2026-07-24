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

1. `npm run douyin -- install --login-browser`
2. `npm run douyin -- login` — opens Chromium, capture cookies after you log in
3. Or set `DOUYIN_COOKIE` (full header) / `DOUYIN_TTWID` + `DOUYIN_ODIN_TT` + `DOUYIN_PASSPORT_CSRF_TOKEN`

Jobs fail when cookies are missing or when the provider exits without writing media. Upstream can return exit code 0 even on failed fetches; TrendRelay treats empty output folders as failure.

Users are responsible for platform terms, privacy, copyright, consent, and having permission to download or reuse content. The optional login browser may require manual CAPTCHA completion and is used only to capture cookies; media downloads never use a browser fallback.
